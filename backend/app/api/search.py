"""检索路由：描述性搜索（B2 RAG，F5）+ 溯源 + 以图搜图（B2-4）"""
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import agent_service_user, get_current_user
from app.core.errors import ERR_CONTENT_006, ERR_SEARCH_001, ERR_SEARCH_002, ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.search import SearchQuery, SearchResult
from app.services.file_magic import is_photo_bytes
from app.services.rag import search as rag_search
from app.services.rag import search_by_image

router = make_router(prefix="/api/v1/search", tags=["search"])

# 图片上传上限（以图搜图查询图）
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

# R6#14（安全纵深）：查询图临时文件后缀白名单——suffix 含路径分隔符时
# NamedTemporaryFile 会落到非预期目录（Python 3.11 不拒分隔符），白名单外一律回退 .jpg。
_QUERY_IMAGE_SUFFIX = re.compile(r"^\.(jpg|jpeg|png|webp)$", re.IGNORECASE)


def _safe_image_suffix(filename: str) -> str:
    """查询图临时文件后缀：白名单匹配原样返回，否则默认 .jpg（防路径穿越/格式误判）"""
    raw = Path(filename or "query.jpg").suffix
    return raw if _QUERY_IMAGE_SUFFIX.match(raw) else ".jpg"


@router.post("/image", response_model=ApiResponse[SearchResult])
def search_by_image_api(
    file: UploadFile = File(..., description="查询图片（jpg/png，≤10MB）"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """以图搜图（B2-4）：上传图片 → Qwen3-VL caption → image_vec 相似检索

    查询向量 = BGE-M3(caption)（caption 向量化方案）；返回与描述性搜索同构结果。
    """
    data = file.file.read()
    if len(data) == 0:
        raise ApiError(ERR_SEARCH_001, "空图片文件", http=422)
    if len(data) > _MAX_IMAGE_BYTES:
        raise ApiError(ERR_SEARCH_002, f"图片超过 {_MAX_IMAGE_BYTES // 1024 // 1024}MB 上限", http=422)
    # R6#14（安全纵深）：查询图魔数校验——扩展名/content_type 头均不可信，
    # 与照片上传链路同款嗅探（防 HTML/脚本投毒进 VL 模型）
    if not is_photo_bytes(data):
        raise ApiError(ERR_CONTENT_006, "文件内容与图片格式不符（魔数校验失败）", http=422)
    suffix = _safe_image_suffix(file.filename or "query.jpg")
    with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = search_by_image(tmp_path, SearchQuery(q="[image]", limit=limit), db=db, user_id=user.id)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return ApiResponse(data=result)


@router.post("", response_model=ApiResponse[SearchResult])
def search(
    req: SearchQuery,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """描述性搜索主链路（API-003）：
    query → LLM 改写/路由 → 多路召回（Qdrant dense+sparse RRF）→ payload filter
    → bge-reranker 粗排 → qwen-flash 精排 → 溯源。

    验收：Top3≥70% + P95<3s（M1 门禁，RET-014/RET-018）。

    安全修复（审查 MAJOR）：个人记忆检索必须登录后可用（此前未鉴权）。
    """
    return ApiResponse(data=rag_search(req, db=db, user_id=user.id))


@router.get("/answers/{job_id}", response_model=ApiResponse[dict])
def get_answer_job(
    job_id: str,
    user: User = Depends(get_current_user),
):
    """取异步回答结果（A-11 ③ · 2026-10-24）。

    返回 `{status, answer, sources, reason?, elapsed_ms?}`，`status ∈ pending|done|failed`：

      · `pending` —— 后台还在生成（客户端 300~500ms 后再来）；
      · `done`    —— `answer` 可能为 **None**，含义是「证据不足，本来就没有答案」
                     （与 `failed` 是两回事，客户端应渲染"没查到"而不是"出错了"）；
      · `failed`  —— 系统降级（LLM 不可用/超时/异常），`reason` 给机器口径、
                     客户端据此展示"暂时没生成出来"，**绝不伪造回答**。

    鉴权：必须登录（与 `POST /search` 同口径）。
    ⚠️ 诚实说明：job 快照存 Redis、**没有按 user_id 分键** —— job_id 是 uuid4 hex
    （122 bit 随机）、TTL 仅 10 分钟、内容是该用户自己的检索回答 ⇒ 猜测不可行。
    若要加强，把 user_id 写进快照并在本端点比对即可（成本一行）；当前不做是因为
    那要把一次额外字段读写加进热路径，收益仅是防「自己泄露自己 uuid」。
    **该取舍已登记在总账 A-11。**
    """
    from app.services.search_answer_job import read_job

    data = read_job(job_id)
    if data is None:
        # 过期/不存在/脏数据同一口径（404 + 固定文案）：不给探测者区分信号
        raise ApiError(ERR_SEARCH_002, "回答任务不存在或已过期（有效期 10 分钟）", http=404)
    return ApiResponse(data=data)


@router.post("/agent", response_model=ApiResponse[SearchResult], include_in_schema=False)
def search_for_agent(
    req: SearchQuery,
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """服务间检索：供 agent 服务以某用户身份复用本后端的 RAG 检索（W2 检索升级）。

    ## 为什么单独一条端点（而不是让原端点兼容服务间头）

    两条通道的信任模型完全不同：JWT 是「用户本人」，`X-Agent-Token` 是
    「持有共享密钥的服务、代任意用户行事」。把它们塞进同一个依赖，等于让
    一条可横向越权的通道挂在所有用户端点上。分开后暴露面最小：
    只此一个路径、`include_in_schema=False`（不入 OpenAPI 文档，不给扫描器指路）、
    鉴权逻辑独立可审计（`deps.agent_service_user`）。

    ## 与用户端点的差异（诚实标注）

    · 复用**同一个** `rag_search` 服务函数 ⇒ 召回/改写/精排/溯源完全一致，
      不存在"agent 拿到的是缩水版检索"。
    · agent 侧需自担延迟：本链路默认开 LLM 改写与精排（P95<3s 门禁口径）。
      agent 侧对单轮对话只调一次、limit 夹紧，见其 `search_memories`。
    · `SearchHit` 不含情绪/标签字段（`schemas/search.py:19-31`）——agent 需要
      时拿 content_id 自行补查，本端点不为它扩契约。

    ## 审计

    服务间调用记一条结构化日志（只记 user_id 与查询长度，不记正文——
    与本项目日志隐私纪律一致），便于事后回答"谁在什么时候代谁查了什么"。
    """
    import logging

    logging.getLogger(__name__).info(
        "agent_search user_id=%s q_len=%s limit=%s",
        user.id,
        len(req.q or ""),
        req.limit,
    )
    return ApiResponse(data=rag_search(req, db=db, user_id=user.id))
