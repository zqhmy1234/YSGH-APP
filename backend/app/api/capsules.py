"""时间胶囊 API（BA2 批 · 远期总账 A5）

POST   /api/v1/capsules            —— 封存（content 须属当前用户且未删除）
GET    /api/v1/capsules            —— 列表（惰性触发到期扫描，附 content 摘要）
POST   /api/v1/capsules/scan       —— 手动触发到期扫描（不走节流，测试/运维用）
POST   /api/v1/capsules/{id}/open  —— 开启（未到期 409 报剩余天数）
DELETE /api/v1/capsules/{id}       —— 撤销封存（仅未开启可删）

错误码：CAPSULE_001 open_at 不在未来 / CAPSULE_002 胶囊不存在或无权 /
CAPSULE_003 未到期开启 / CAPSULE_004 已开启不可撤销。
（capsule 域错误码就地定义于本模块——core/errors.py 属共享域，避免越批文件域）
"""
import logging
from datetime import datetime, timezone

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user, load_alive_content
from app.core.errors import ApiError
from app.db.models import Capsule, Content, User
from app.db.session import get_db
from app.schemas.capsule import (
    CapsuleContentBrief,
    CapsuleCreate,
    CapsuleDeleteOut,
    CapsuleOut,
)
from app.schemas.common import ApiResponse
from app.services.external.media_url import content_urls
from app.workers.capsule_scan import maybe_scan_due, scan_due_capsules

logger = logging.getLogger("yishu.capsules")

router = make_router(prefix="/api/v1/capsules", tags=["capsules"])

ERR_CAPSULE_001 = "CAPSULE_001"
ERR_CAPSULE_002 = "CAPSULE_002"
ERR_CAPSULE_003 = "CAPSULE_003"
ERR_CAPSULE_004 = "CAPSULE_004"


def _load_owned_capsule(db: Session, user_id: str, capsule_id: str) -> Capsule:
    """取当前用户的胶囊；不存在/非本人 → 统一 404（IDOR 防护不区分两情形）"""
    row = db.execute(
        select(Capsule).where(Capsule.id == capsule_id, Capsule.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(ERR_CAPSULE_002, "胶囊不存在或无权访问", http=404)
    return row


def _content_brief(db: Session, content_id: str, user_id: str) -> CapsuleContentBrief | None:
    """关联记忆摘要（ContentOut 现成字段投影；缩略图签票失败降级无图）"""
    c = db.execute(select(Content).where(Content.id == content_id)).scalar_one_or_none()
    if c is None:
        return None
    thumb_url = None
    if c.thumbnail_key:
        try:
            _orig, thumb_url = content_urls(None, c.thumbnail_key, str(user_id))
        except Exception:  # noqa: BLE001 —— 签票失败降级无图不阻塞主链路（favorites 同口径）
            logger.warning("胶囊摘要缩略图签票失败 content_id=%s", c.id, exc_info=True)
    return CapsuleContentBrief(
        id=str(c.id),
        content_type=c.content_type,
        text=c.text,
        place=c.place,
        taken_at=c.taken_at,
        thumbnail_url=thumb_url,
    )


def _capsule_out(db: Session, cap: Capsule) -> CapsuleOut:
    return CapsuleOut(
        id=str(cap.id),
        content_id=str(cap.content_id),
        note=cap.note,
        sealed_at=cap.sealed_at,
        open_at=cap.open_at,
        opened_at=cap.opened_at,
        status=cap.status,
        content=_content_brief(db, str(cap.content_id), str(cap.user_id)),
    )


@router.post("", response_model=ApiResponse[CapsuleOut])
def seal_capsule(
    body: CapsuleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """封存记忆（BA2）：content 必须属当前用户且未删除；open_at 必须晚于 now"""
    if body.open_at.tzinfo is None:  # 裸时间按 UTC 解释（客户端契约带 tz；防御显式报错）
        raise ApiError(ERR_CAPSULE_001, "open_at 必须带时区信息", http=422)
    now = datetime.now(timezone.utc)
    if body.open_at <= now:
        raise ApiError(ERR_CAPSULE_001, "open_at 必须晚于当前时刻", http=422)
    # 波D ② 收敛（2026-09-10）：复用 deps.load_alive_content——原内联查询与其逐字等价
    # （同 where 三条件、同 ERR_CONTENT_010、同文案「内容不存在或无权访问」、同 HTTP 404）
    content = load_alive_content(db, user.id, body.content_id)
    cap = Capsule(
        user_id=user.id,
        content_id=content.id,
        note=body.note,
        open_at=body.open_at,
    )
    db.add(cap)
    db.commit()
    db.refresh(cap)
    return ApiResponse(data=_capsule_out(db, cap))


@router.get("", response_model=ApiResponse[list[CapsuleOut]])
def list_capsules(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """胶囊列表（sealed/due/opened 全量，open_at 倒序）

    惰性触发到期扫描（capsule_scan.maybe_scan_due，30s 节流）：项目无进程内
    定时器（RQ worker 只消费队列），选型理由见 app/workers/capsule_scan.py 模块注释。
    """
    maybe_scan_due(db)
    rows = (
        db.execute(
            select(Capsule)
            .where(Capsule.user_id == user.id)
            .order_by(Capsule.open_at.desc())
        )
        .scalars()
        .all()
    )
    return ApiResponse(data=[_capsule_out(db, c) for c in rows])


@router.post("/scan", response_model=ApiResponse[dict])
def trigger_scan(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """手动触发到期扫描（不走节流；测试/运维补扫入口）"""
    produced = scan_due_capsules(db)
    return ApiResponse(data={"produced": produced})


@router.post("/{capsule_id}/open", response_model=ApiResponse[CapsuleOut])
def open_capsule(
    capsule_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """开启胶囊（到期回看）：未到期 409 显式报剩余天数；已开启幂等返回现态"""
    cap = _load_owned_capsule(db, user.id, capsule_id)
    now = datetime.now(timezone.utc)
    open_at = cap.open_at if cap.open_at.tzinfo else cap.open_at.replace(tzinfo=timezone.utc)
    if open_at > now:
        remaining_seconds = int((open_at - now).total_seconds())
        remaining_days = max(1, (open_at - now).days + (1 if (open_at - now).seconds else 0))
        raise ApiError(
            ERR_CAPSULE_003,
            f"胶囊还未到期，剩余 {remaining_days} 天",
            http=409,
            details={"remaining_days": remaining_days, "remaining_seconds": remaining_seconds},
        )
    if cap.status != "opened":  # sealed/due → opened（幂等：已 opened 直接返回现态）
        cap.status = "opened"
        cap.opened_at = now
        db.commit()
    return ApiResponse(data=_capsule_out(db, cap))


@router.delete("/{capsule_id}", response_model=ApiResponse[CapsuleDeleteOut])
def delete_capsule(
    capsule_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """撤销封存（仅未开启可删；已开启 → 409）。被关联 content 不动（软删生态零侵入）"""
    cap = _load_owned_capsule(db, user.id, capsule_id)
    if cap.status == "opened":
        raise ApiError(ERR_CAPSULE_004, "已开启的胶囊不可撤销", http=409)
    db.delete(cap)
    db.commit()
    return ApiResponse(data=CapsuleDeleteOut(capsule_id=capsule_id, deleted=True))
