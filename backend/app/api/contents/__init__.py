"""内容路由：四类素材上传主链路（API-002）+ 相册直传（COS STS，决策 #10）

真实 DB 接入（S1-02）：
- contents 表入库 + perceptual_hash 去重（Q16，同用户唯一）
- RQ 入队异步 AI 管线（API-016：收件→转写→分类→聚类；API 立即返回）
- 分页游标（API-006）

客户端第一波（2026-08-24，B-BE-1/2）：
- POST /api/v1/contents/upload：照片 multipart 中转上传（客户端→后端→storage→contents→管线）
- 复用 create_content 的去重（409）/护栏（moderate）/类型白名单语义

拆包（重构波 B2，2026-09-24）：原单文件 902 行拆为子包，本模块保留核心 6 端点
（upload / create / list / detail / remark 更新 / delete）。⚠️ `MAX_PHOTO_BYTES` 与
`enqueue_unique` 必须仍是**本模块**级属性（测试 monkeypatch 打在 app.api.contents.*）；
其余端点见 serializers / profile_sensitive / favorites / trash / waveform 子模块，
`router` / `profile_sensitive_router` / `favorites_router` / `trash_router` 在文件末尾再导出。
"""
import io as _io
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.contents.serializers import _attach_thumb, _to_out, router
from app.api.deps import (
    PageParams,
    get_current_user,
    load_alive_content,
    pagination_params,
    uuid4_str,
)
from app.core.errors import (
    ERR_CONTENT_001,
    ERR_CONTENT_002,
    ERR_CONTENT_003,
    ERR_CONTENT_005,
    ERR_CONTENT_006,
    ERR_CONTENT_007,
    ERR_CONTENT_008,
    ERR_CONTENT_009,
    ERR_CONTENT_010,
    ApiError,
)
from app.core.queue import (
    DEFAULT_JOB_TIMEOUT,
    QUEUE_LOW,
    enqueue_unique,
)
from app.db.models import Content, User
from app.db.session import get_db
from app.schemas.common import ApiResponse, Page
from app.schemas.content import (
    TRASH_RETENTION_DAYS,
    ContentCreate,
    ContentDeleteOut,
    ContentOut,
    ContentRemarkUpdate,
)
from app.services import sync_writes
from app.services.errors import NotFoundError
from app.services.external.storage import get_storage_backend
from app.services.file_magic import is_photo_bytes
from app.services.photo_content import (
    DuplicateError,
    ModerateRejectError,
    reflow_violation,
)
from app.services.photo_content import (
    register_photo_content as photo_register,
)
from app.services.pipeline import process_content
from app.services.sync_common import parse_ts
from app.services.upload_meta import (
    ALLOWED_PHOTO_EXTS,
    MAX_PHOTO_BYTES,
    MetaValidationError,
    parse_photo_meta,
)

logger = logging.getLogger("yishu.contents")

# 照片上传共享常量（TD-P2B · S1-H2 收口：MAX_PHOTO_BYTES/ALLOWED_PHOTO_EXTS 收敛到
# services/upload_meta.py，与分片链路 register_photo_content 同源；此处保留模块级名字
# 供 upload_photo 引用与测试 monkeypatch（test_content_upload 缩小上限用））
# PIL 解压炸弹防护（P0-3 · 审查 H3）：40MP 上限（超限拒绝解码）
_MAX_IMAGE_PIXELS = 40_000_000


def _extract_exif_datetime(data: bytes) -> datetime | None:
    """从照片字节提取 EXIF DateTimeOriginal（相机拍摄时间真值）

    客户端 DATE_TAKEN 可能被 MediaProvider 写成扫描时间（2026-08-24 真机实测），
    故以后端 EXIF 解析为准：EXIF 无时区=相机本地时间（本设备 +08），
    显式按 UTC+08:00 解释，与客户端 isoString(+08:00) 一致。
    P0-3（审查 H3）：Image.open 前设 MAX_IMAGE_PIXELS 防解压炸弹（getexif 虽不
    解码像素，但 open 阶段的尺寸检查会触发 DecompressionBombError）。
    """
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        img = Image.open(_io.BytesIO(data))
        exif = img.getexif()
        raw = exif.get(36867)  # DateTimeOriginal
        if raw:
            return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(
                tzinfo=timezone(timedelta(hours=8))
            )
    except Exception:  # noqa: BLE001 —— 非 JPEG/无 EXIF 静默降级
        return None
    return None


def _validate_cos_key(db: Session, user_id: str, cos_key: str) -> None:
    """TD-P3 M4（审查中危）：create_content 自供 cos_key 归属/前缀/存在性校验

    仅允许本用户前缀（photos|voice|thumbnails/{user_id}/）且对象已存在
    （或已登记为本用户同 cos_key 内容——幂等回退），否则 422 CONTENT_009。
    防跨租户对象拉进自己管线（M4：已知他人 key 可被处理留存）与任意 key 触发存储遍历。
    """
    allowed = (f"photos/{user_id}/", f"voice/{user_id}/", f"thumbnails/{user_id}/")
    if not cos_key.startswith(allowed):
        raise ApiError(ERR_CONTENT_009, "cos_key 非法或不属于当前用户", http=422)
    from app.services.external.storage import get_storage_backend

    if get_storage_backend().object_exists(cos_key):
        return
    # 幂等回退：对象可能已被搬移/清理，但同用户已登记同 cos_key 内容（旧客户端重复建）
    existing = db.scalar(
        select(Content).where(
            Content.user_id == user_id,
            Content.cos_key == cos_key,
            Content.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return
    raise ApiError(ERR_CONTENT_009, "cos_key 指向的对象不存在", http=422)


def _resolve_size_bytes(req: ContentCreate) -> int | None:
    """A9（2026-09-09 缺口收口）：确定入库的原件字节数

    优先级：客户端显式声明（ContentCreate.size_bytes，可选字段）>
    带 cos_key 时从存储实测（voice 单段链路字节已在存储里）> None。
    实测失败（KeyError/存储故障等）降级 None——不阻塞建记录，
    与分片 complete 主链（services/upload/register.py size_bytes=len(data)）对齐口径。
    """
    if req.size_bytes is not None:
        return req.size_bytes
    if not req.cos_key:
        return None
    try:
        return len(get_storage_backend().get_object(req.cos_key))
    except Exception:  # noqa: BLE001 —— 降级不阻塞建记录（object_exists 已过存在性校验，此处防并发删对象/存储抖动）
        logger.warning("size_bytes 存储实测失败（降级 None 不阻塞入库）key=%s", req.cos_key)
        return None


@router.post("/upload", response_model=ApiResponse[ContentOut])
def upload_photo(
    file: UploadFile = File(...),
    meta: str = Form("{}"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """照片中转上传（客户端第一波 B3/B4：multipart → storage → contents → 管线）

    multipart 表单：
      file: 照片文件（jpg/jpeg/png/webp/heic/heif，≤20MB）
      meta: JSON 字符串 {taken_at, gps_lat, gps_lng, perceptual_hash, source, extra}

    语义与 create_content 对齐：perceptual_hash 409 去重 / moderate 护栏 /
    source 白名单；照片原件落 storage（cos_key），随后入队 process_content。
    F1/P0-6 双轨收口：去重/护栏/建记录/入队统一委托 services/photo_content.py
    （dedup_key="perceptual_hash" + moderate=True），本函数只做协议适配。
    """
    # 1+3. meta 解析与字段校验（TD-P2B · S1-H2 收口：与分片链路 register_photo_content
    #      同一契约——taken_at ISO/gps 边界/source 白名单统一走 upload_meta.parse_photo_meta；
    #      字段契约错误优先返回 CONTENT_005）
    try:
        photo_meta = parse_photo_meta(meta)
    except MetaValidationError as exc:
        raise ApiError(ERR_CONTENT_005, str(exc), http=422) from exc
    meta_obj = photo_meta.raw

    # 2. 文件校验（类型白名单 + 非空 + 大小上限 + 魔数嗅探）
    ext = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if ext not in ALLOWED_PHOTO_EXTS and not content_type.startswith("image/"):
        raise ApiError(ERR_CONTENT_006, "仅支持照片文件（jpg/png/webp/heic）", http=422)
    data = file.file.read()
    if not data:
        raise ApiError(ERR_CONTENT_006, "文件为空", http=422)
    if len(data) > MAX_PHOTO_BYTES:
        raise ApiError(
            ERR_CONTENT_007,
            f"照片超过大小上限（{MAX_PHOTO_BYTES // 1024 // 1024}MB）",
            http=413,
        )
    # P0-3（审查 H3）：魔数校验——扩展名/content_type 头均不可信，
    # `.jpg` 文件名 + 任意字节（HTML/脚本）必须拒（防内容投毒）
    if not is_photo_bytes(data):
        raise ApiError(
            ERR_CONTENT_006, "文件内容与照片格式不符（魔数校验失败）", http=422
        )

    perceptual_hash = meta_obj.get("perceptual_hash") or None
    extra = meta_obj.get("extra") if isinstance(meta_obj.get("extra"), dict) else None

    # 3+4+5. 照片注册唯一编排（F1/P0-6 双轨收口）：去重（perceptual_hash 409）→
    #       moderate 护栏 → 原件落 storage → 建记录 → enqueue_unique 入队，
    #       统一委托 services/photo_content.py（参数化 dedup_key/moderate/mode）。
    #       EXIF 拍摄时间优先（相机真值；客户端时间可能被扫描污染）。
    exif_taken = _extract_exif_datetime(data)
    ext_safe = ext if ext in ALLOWED_PHOTO_EXTS else ".jpg"
    try:
        content_id = photo_register(
            db,
            user.id,
            dedup_key="perceptual_hash",
            moderate=True,
            mode="original",
            meta_obj=photo_meta.raw,
            photo_meta=photo_meta,
            perceptual_hash=perceptual_hash,
            data=data,
            ext=ext_safe,
            exif_taken_at=exif_taken,
            extra=extra,
            enqueue_thumbnail=False,
        )
    except DuplicateError as exc:
        raise ApiError(ERR_CONTENT_002, exc.message, http=409) from exc
    except ModerateRejectError as exc:
        raise ApiError(ERR_CONTENT_003, exc.message, http=422) from exc
    record = db.get(Content, content_id)
    return ApiResponse(data=_to_out(record))


@router.post("", response_model=ApiResponse[ContentOut])
def create_content(
    req: ContentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容入库：POST → contents 表 → 异步 AI 管线（RQ）→ 状态回写（API-002/API-016）"""
    if req.content_type not in ("photo", "text", "voice", "article"):
        raise ApiError(ERR_CONTENT_001, "不支持的 content_type", http=422)

    # R4#4（创建端点幂等键）：同用户 client_generated_id 已存在 → 幂等返回既有记录
    # （双击/网络重试不重复入库；photo/voice 既有幂等——perceptual_hash 409 / cos_key——
    #  保留为兜底，见下方去重与 voice 分支）
    if req.client_generated_id:
        existing = db.scalar(
            select(Content).where(
                Content.user_id == user.id,
                Content.client_generated_id == req.client_generated_id,
                Content.deleted_at.is_(None),
            )
        )
        if existing is not None:
            return ApiResponse(data=_to_out(existing))

    # TD-P3 M4（审查中危）：自供 cos_key 归属/前缀/存在性校验（防跨租户对象拉取）
    if req.cos_key:
        _validate_cos_key(db, user.id, req.cos_key)

    # 去重（Q16）：同用户 perceptual_hash 唯一（仅照片类有哈希；软删记录不参与，
    # 修复：原实现未过滤 deleted_at → 删除后重传同照片被 409 永久拒绝）
    if req.perceptual_hash:
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == req.perceptual_hash,
                Content.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError(ERR_CONTENT_002, "重复内容（感知哈希已存在）", http=409)

    # 护栏检测（B5b · 2026-08-20 接入普通入库）：reject → 拒绝入库；mask → 打码后入库
    from app.services.external.dashscope import moderate

    check_text = req.text or ""
    if check_text.strip():
        verdict = moderate(check_text)
        if verdict.get("action") == "reject":
            reflow_violation(db, verdict)
            raise ApiError(ERR_CONTENT_003, f"内容含敏感信息未保存：{verdict.get('reason', '')}", http=422)
        if verdict.get("action") == "mask" and verdict.get("masked_text"):
            req.text = verdict["masked_text"]

    # B5a 集成（Wave4 AgentJ 需求 1 配套）：voice 带 cos_key 时幂等——complete 已建 voice
    # 内容，旧客户端仍会二次调用本端点（saveVoiceContent），按 同用户+同 cos_key 去重返回既有记录
    if req.content_type == "voice" and req.cos_key:
        existing_voice = db.scalar(
            select(Content).where(
                Content.user_id == user.id,
                Content.cos_key == req.cos_key,
                Content.deleted_at.is_(None),
            )
        )
        if existing_voice is not None:
            return ApiResponse(data=_to_out(existing_voice))

    record = Content(
        user_id=user.id,
        content_type=req.content_type,
        text=req.text,
        taken_at=req.taken_at,
        gps_lat=req.gps_lat,
        gps_lng=req.gps_lng,
        perceptual_hash=req.perceptual_hash,
        client_generated_id=req.client_generated_id,
        cos_key=req.cos_key,
        thumbnail_key=req.thumbnail_key,
        extra=req.extra,
        source=req.source,
        status="processing",   # AI 管线完成后回写 done（异步）
        # BA1（迁移 f2a3b4c5d6e7）：用户备注（save 系可选上送）
        remark=req.remark,
        # A9（2026-09-09）：原件字节数——客户端声明优先，否则带 cos_key 时实测存储对象
        size_bytes=_resolve_size_bytes(req),
        # BA1：voice 走本端点时 duration 从客户端 extra.duration_ms（毫秒）换算秒——
        # 主落库点在 services/upload/register.py（complete 直建链路），此处为
        # POST /contents 二次调用（B5a saveVoiceContent）兜底对齐
        duration=(
            int(req.extra["duration_ms"]) // 1000
            if req.content_type == "voice"
            and isinstance(req.extra, dict)
            and req.extra.get("duration_ms") is not None
            else None
        ),
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        # 并发冲突（client_generated_id 幂等键 / perceptual_hash 去重）→ 回滚重查
        db.rollback()
        # R4#4：并发同 client_generated_id → 唯一约束冲突 → 幂等返回既有记录
        if req.client_generated_id:
            dup = db.execute(
                select(Content).where(
                    Content.user_id == user.id,
                    Content.client_generated_id == req.client_generated_id,
                    Content.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if dup is not None:
                return ApiResponse(data=_to_out(dup))
        # 审查修复(P1-04)：并发同哈希上传 → 唯一约束冲突 → 回滚重查，返回 409
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == req.perceptual_hash,
                Content.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError(ERR_CONTENT_002, "重复内容（感知哈希已存在）", http=409) from None
        raise
    db.refresh(record)

    # 入队异步 AI 管线（API-016；API 立即返回不阻塞）
    # 审查修复(P1-12)：voice/photo 用户等待 → 高优队列；text/article 低优
    # F4/R5-4#5：enqueue_unique 同 content 键不重复入队（job 级去重）
    if req.content_type in ("voice", "photo"):
        # R9-B6（2026-09-06）：第 2 个 str(record.id) 是函数参数——enqueue_unique 的 key
        # 只是去重键，缺 args = process_content() 零参 TypeError 秒死（failed 262 条同因）
        enqueue_unique(process_content, str(record.id), str(record.id))
    else:
        enqueue_unique(
            process_content,
            str(record.id),
            str(record.id),
            queue_name=QUEUE_LOW,
            job_timeout=DEFAULT_JOB_TIMEOUT,
        )

    return ApiResponse(data=_to_out(record))


@router.get("", response_model=ApiResponse[Page[ContentOut]])
def list_contents(
    content_id: str | None = None,
    page: PageParams = Depends(pagination_params),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容分页列表（API-006 游标分页：数据增删中翻页不错位）

    修复（审查 MAJOR）：原游标仅 created_at，同秒多条翻页错位/重复；
    改为 (created_at, id) 复合游标（id 兜底，UUID 不可比 → 用字符串序）。
    游标格式："<created_at_iso>|<id>"。
    R4#6/#7（分页统一）：limit/cursor 走共享 pagination_params（limit 1..100，cursor 不透明字符串）。
    """
    limit = page.limit
    query = select(Content).where(
        Content.user_id == user.id,
        Content.deleted_at.is_(None),
    ).order_by(Content.created_at.desc(), Content.id.desc())
    # 详情页精确取单条（content_id 为 UUID 字符串；路径 /contents/{id} 已归缩略图端点）
    if content_id:
        query = query.where(Content.id == content_id)

    if page.cursor:
        try:
            cursor_ts_raw, cursor_id = page.cursor.split("|", 1)
        except ValueError:
            # P0-7：游标错误从 CONTENT_003（敏感 422）拆分为独立码 CONTENT_008
            raise ApiError(ERR_CONTENT_008, "游标格式无效", http=422) from None
        # S1-M1 收口：统一走 sync_common.parse_ts（naive 视为 UTC；此前 fromisoformat
        # 裸调，非法游标会 500 且无 naive 处理）
        cursor_dt = parse_ts(cursor_ts_raw)
        if cursor_dt is None:
            raise ApiError(ERR_CONTENT_008, "游标格式无效", http=422)
        # 复合条件：(created_at, id) < (cursor_dt, cursor_id) 元组语义
        query = query.where(
            (Content.created_at < cursor_dt)
            | ((Content.created_at == cursor_dt) & (Content.id < cursor_id))
        )

    items = db.execute(query.limit(limit + 1)).scalars().all()
    has_more = len(items) > limit
    page_items = items[:limit]

    next_cursor = None
    if has_more and page_items:
        last = page_items[-1]
        # UTC 无 + 号格式，避免 URL 中 + 被解析为空格；拼接 id 兜底同秒
        ts_str = last.created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        next_cursor = f"{ts_str}|{last.id}"

    return ApiResponse(
        data=Page(
            items=[_attach_thumb(_to_out(c), c, str(user.id)) for c in page_items],
            cursor=next_cursor,
            has_more=has_more,
        )
    )


@router.get("/{content_id}", response_model=ApiResponse[ContentOut])
def get_content_detail(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容详情（2026-09-09 缺口收口：客户端此前用 GET /contents?content_id= 列表过滤替代）

    归属/存在性：不存在 / 已软删 / 非本人 → 统一 404 CONTENT_010
    （复用 load_alive_content，与 favorite/PATCH/waveform 同口径：IDOR 不区分三种情形；
    软删条目回收站走 GET /api/v1/trash）。畸形 ID 同语义 404（uuid4_str，R4#2）。
    路由顺序：本路由定义在 GET "" （列表，L367）之后，空串与路径参数不歧义；
    与 GET /{content_id}/waveform（两段路径）不冲突。
    出参：_to_out 全字段 + photo 条目补缩略图票（_attach_thumb，与列表同兜底）。
    """
    try:
        cid = uuid4_str(content_id)
    except NotFoundError:
        raise ApiError(ERR_CONTENT_010, "内容不存在或无权访问", http=404) from None
    row = load_alive_content(db, user.id, cid)
    return ApiResponse(data=_attach_thumb(_to_out(row), row, str(user.id)))


@router.patch("/{content_id}", response_model=ApiResponse[ContentOut])
def update_content_remark(
    content_id: str,
    req: ContentRemarkUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新内容备注（BB1）：仅 remark 单字段（schema extra="forbid"，越权字段 422）。

    归属校验复用 load_alive_content：非本人/已软删/不存在 → 404 CONTENT_010
    （IDOR 不区分三种情形）；畸形 ID 同语义 404（uuid4_str，R4#2）。
    """
    try:
        uuid4_str(content_id)
    except NotFoundError:
        raise ApiError(ERR_CONTENT_010, "内容不存在或无权访问", http=404) from None
    row = load_alive_content(db, user.id, content_id)
    now = datetime.now(timezone.utc)
    row.remark = req.remark
    # 双向写穿（功能修复波 簇② Q2 拍板）：REST 权威写入**同时**落同步账本 + 变更日志。
    # ① SFV：为后续**离线编辑**建立 LWW 基准——缺它则离线旧备注在 LWW 下"看起来更新"
    #    从而盖掉这次在线新备注（原实现 REST 不写 SFV ⇒ 基准缺失）；
    # ② 变更日志：他端 pull 到这次修改。
    # ⚠️ 如实标注：客户端镜像当前**丢弃 value**（D08-4/P1 未修）⇒「他端自动收敛」要到
    #    D08-4 落地才真正生效，本次只保证**云端**权威版本与 LWW 基准正确。
    sync_writes.write_field_version(
        db,
        user.id,
        entity_type="content",
        entity_id=str(row.id),
        field="remark",
        value=req.remark,
        updated_at=now,
    )
    sync_writes.log_change(
        db,
        user.id,
        sync_writes.SERVER_DEVICE,
        op_type="upsert_field",
        entity_type="content",
        entity_id=str(row.id),
        updated_at=now,
        field="remark",
        value=req.remark,
    )
    db.commit()
    db.refresh(row)
    return ApiResponse(data=_to_out(row))


@router.delete("/{content_id}", response_model=ApiResponse[ContentDeleteOut])
def delete_content(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """软删内容（W2-1 + 功能修复波 簇② D08-1/2）：走**单一软删写路径** `sync_writes`

    原实现只置 `contents.deleted_at/deleted_by`（不写 `deleted_logs`）⇒ 清理任务
    只认 `deleted_logs` ⇒ **永不彻底清除**，"保留 30 天"承诺在用户路径上未落地。
    `sync_writes.soft_delete_content` 一次做齐四件事：
      ① 置权威表 `deleted_at/deleted_by`（对所有读路径立即 404、回收站可见可恢复）
      ② 写 SFV 墓碑（同步各端）
      ③ **幂等**写 `deleted_logs`（30 天清理真的会选中它）
      ④ 写变更日志（他端 pull 到 delete）
    与 sync `push_ops` 的删除分支**同调同一组原语**（单一软删语义路径）。
    """
    row = load_alive_content(db, user.id, content_id)
    now = datetime.now(timezone.utc)
    sync_writes.soft_delete_content(db, row, user.id, change_ts=now, anchor=now)
    db.commit()
    return ApiResponse(
        data=ContentDeleteOut(
            content_id=row.id,
            deleted=True,
            permanent_at=now + timedelta(days=TRASH_RETENTION_DAYS),
        )
    )


# ---------- 子包再导出（重构波 B2 拆包）----------
# 子模块在**导入时**把各自的 /api/v1/contents/* 端点注册到共享 router（serializers.router），
# 故必须放在核心端点定义之后导入，以保持路由注册顺序与拆包前一致：
# upload → "" → GET "" → GET/{content_id} → PATCH → DELETE → favorite×3 → waveform。
# 四个 router 在此再导出（main.py 依赖 `from app.api.contents import ...` 三 router 与
# `contents.router`；测试依赖 profile_sensitive_router）。
from app.api.contents.favorites import favorites_router  # noqa: E402, F401
from app.api.contents.profile_sensitive import profile_sensitive_router  # noqa: E402, F401
from app.api.contents.trash import trash_router  # noqa: E402, F401
from app.api.contents.waveform import get_content_waveform  # noqa: E402, F401
