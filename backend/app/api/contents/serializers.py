"""contents 子包共享层（重构波 B2 拆包，2026-09-24）：contents 主 router + 出参序列化

- `router`：contents 主 router（prefix /api/v1/contents）。拆包后 favorites / waveform
  子模块在**同一 router 对象**上注册自己的端点（favorite×3 / waveform），以保证路由对象、
  路径、注册顺序与拆包前（单文件 contents.py）完全一致——子模块不得 import `__init__`
  （循环导入），故共享 router 落在本模块。
- `_to_out` / `_attach_thumb`：出参映射与 photo 缩略图签票兜底（列表/详情/收藏共用）。
"""
import logging

from app.api import make_router
from app.db.models import Content
from app.schemas.content import ContentOut
from app.services.external.media_url import content_urls

router = make_router(prefix="/api/v1/contents", tags=["contents"])

# 2026-10-14（台账 §7.23 · 批次 8 续审）：本模块此前**没有日志设施**，于是缩略图签票失败
# 只能 `except Exception: pass` 静默吞掉（同仓同款容错 recall/favorites 都记日志）。
logger = logging.getLogger("yishu.contents")


def _to_out(c: Content) -> ContentOut:
    return ContentOut(
        id=str(c.id),
        content_type=c.content_type,
        content_class=c.content_class,
        text=c.text,
        taken_at=c.taken_at,
        place=c.place,
        emotion=c.emotion,
        tags=[],
        status=c.status,
        audio_processing=(c.extra or {}).get("audio_processing"),
        created_at=c.created_at,
        # BA1（迁移 f2a3b4c5d6e7）：扩展字段直出（全可空，老数据为 None）
        duration=c.duration,
        remark=c.remark,
        size_bytes=c.size_bytes,
        tags_json=c.tags_json,
        ai_description=c.ai_description,
    )


def _attach_thumb(item: ContentOut, c: Content, user_id: str) -> ContentOut:
    """photo 条目补缩略图票（2026-09-05）：列表/搜索兜底卡片直连回显。
    票签发失败降级无图不阻塞列表（与 recall/favorites 同款容错）。"""
    if c.content_type == "photo" and c.thumbnail_key and item.thumbnail_url is None:
        try:
            _orig, thumb = content_urls(None, c.thumbnail_key, user_id)
            item.thumbnail_url = thumb
        except Exception as exc:  # noqa: BLE001 —— 票失败降级无图，不影响列表主链路（同款容错）
            # 2026-10-14（台账 §7.23 · 批次 8 续审）：**原为 `pass`（零日志、零计数）**。
            # 签票失败会让列表/详情/收藏的 `thumbnail_url` 全站静默为空（客户端表现为空白图），
            # 而 storage/签名配置类故障因此**没有任何可观测信号**。同仓 recall/favorites 同款
            # 容错都记日志 ⇒ 此处补齐（口径同 §7.20：降级要可观测）。
            logger.warning("缩略图签票失败，降级无图 content=%s: %s", c.id, exc)
    return item
