"""回响 API（P2-ECHO · 去年今日 + B4 合并八闸）

GET  /api/v1/echo/today        —— 去年今日回响（八闸判定，敏感排除）
GET  /api/v1/echo/preference   —— 用户设置（授权/静默/每日上限）
PUT  /api/v1/echo/preference   —— 写入用户设置（用户隔离，字段白名单）
POST /api/v1/echo/{content_id}/dismiss —— 划掉不再出现
"""
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user, load_alive_content
from app.core.errors import ERR_ECHO_001, ApiError
from app.db.models import EchoHistory, EchoPreference, User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.echo import EchoDismissOut, EchoPreferenceIn, EchoPreferenceOut, EchoTodayOut
from app.services.echo import (
    _fingerprint,
    _load_preference,
    dismiss_echo,
    get_today_echo,
)

router = make_router(prefix="/api/v1/echo", tags=["echo"])


@router.get("/preference", response_model=ApiResponse[EchoPreferenceOut])
def echo_preference_get(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """读回响设置（缺行时返回建表默认，不写库）。"""
    return ApiResponse(data=EchoPreferenceOut(**_load_preference(db, str(user.id))))


@router.put("/preference", response_model=ApiResponse[EchoPreferenceOut])
def echo_preference_put(
    payload: EchoPreferenceIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """写回响设置：字段白名单（enabled/quiet_start/quiet_end/max_per_day），用户隔离。

    channels 与 role_visibility 不接受写入：前者服务端白名单，后者 B8 家庭域预留。
    """
    uid = str(user.id)
    row = db.get(EchoPreference, uid)
    if row is None:
        row = EchoPreference(user_id=uid)
        db.add(row)
    def _reject(message: str):
        # 统一错误信封（ApiError → api_error_handler → {code,message,request_id,details}）
        raise ApiError(ERR_ECHO_001, message, http=422)

    # 显式 null 视为 422（不能静默忽略：用户以为关了其实没关）；未提供的字段才跳过。
    null_fields = [k for k in payload.model_fields_set if getattr(payload, k) is None]
    if null_fields:
        _reject(f"字段不可为 null：{', '.join(null_fields)}")
    # 时间值校验：pattern 只管格式，99:99 / 25:00 / 12:60 会放行，导致闸2 静默失效。
    for key in ("quiet_start", "quiet_end"):
        val = getattr(payload, key)
        if val is not None:
            try:
                h, m = (int(x) for x in val.split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
            except (ValueError, AttributeError):
                _reject(f"{key} 非法：{val!r}，须为 00:00–23:59 的 HH:MM")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return ApiResponse(data=EchoPreferenceOut(**_load_preference(db, uid)))


@router.get("/today", response_model=ApiResponse[EchoTodayOut])
def echo_today(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = get_today_echo(db, user.id)
    return ApiResponse(data=result)


@router.post("/{content_id}/dismiss", response_model=ApiResponse[EchoDismissOut])
def echo_dismiss(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """划掉不再出现（R4#15 幂等：同内容已划掉 → 直接返回成功，不重复落 dismiss 记录）

    幂等键 = (user_id, fingerprint)；DB 部分唯一索引 uq_echo_history_dismiss
    保证并发重复请求不叠加记录（IntegrityError → 视为已划掉）。
    content_id 必须是当前用户未删除的内容（IDOR 防护：非本人/已删 → 404）。
    """
    load_alive_content(db, str(user.id), content_id)  # 归属 + 存在性，非本人 404
    fp = _fingerprint(content_id)
    already = db.scalar(
        select(func.count())
        .select_from(EchoHistory)
        .where(
            EchoHistory.user_id == user.id,
            EchoHistory.action == "dismiss",
            EchoHistory.fingerprint == fp,
        )
    )
    if not already:
        from sqlalchemy.exc import IntegrityError
        try:
            dismiss_echo(db, user.id, content_id)
        except IntegrityError:  # 并发双请求：唯一索引兜底，后到者视为已划掉
            db.rollback()
    return ApiResponse(data={"dismissed": True, "content_id": content_id})
