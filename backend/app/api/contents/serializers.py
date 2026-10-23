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
        # 2026-10-24（待办总账 D-08 · 原台账 §7.26⑥）：`tags` 此前硬编码 `[]`，
        # 即「出参里永远空」。它是**入参** ContentIn.tags（服务间镜像预置 chips）的
        # 同名出参，语义上从未被填充 —— 真有标签的是 `tags_json`（754 行有数据，
        # 客户端 7 处读它）。经全仓取证：客户端**从不**读 ContentOut.tags，
        # 测试也没有断言依赖它。
        # 故改为**由 tags_json 派生**，而不是删字段：① 删字段会改契约面（openapi 快照
        # 变化 + 潜在外部消费者 404）；② 派生的空值语义与旧行为一致（tags_json 为
        # None ⇒ 仍为 []），不会让任何现存调用方观察到差异；③ 一旦有调用方真去读，
        # 读到的是真值而不是一个骗人的空数组。
        tags=_tags_from_json(c.tags_json),
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


def _tags_from_json(raw: object) -> list[str]:
    """把 `contents.tags_json`（JSONB，实存 list[str]）转成 `ContentOut.tags`。

    只做**形状防御**，不做语义加工：非 list / 元素非 str 一律跳过（历史行可能是
    dict 或混入 null），保证出参永远是 `list[str]`（契约类型），不会因为一行脏数据
    让整个列表接口 500。上限与写入侧 `_MAX_TAGS` 无关——这里只回显既有数据。
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            out.append(item)
    return out


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
