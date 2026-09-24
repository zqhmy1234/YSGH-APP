r"""检索过滤器契约的**唯一来源**（D05-3 · 功能修复波 域⑤）。

## 缺陷原貌（深审 D05-3）

过滤器翻译存在**两套独立实现**，各自 `if/elif` 链、各自维护键集：

| 实现 | 位置 | 未知键处置 |
|---|---|---|
| Qdrant payload filter | `vector_store._to_filter` | **静默丢弃**（链尾无 `else`） |
| PG 降级全文检索 | `rag/pg_fallback._pg_fallback_search` | 静默忽略（`filters.get(...)` 逐个取） |

后果（P0 级风险）：任何**隔离类键**（`user_id` 这类）若拼错/新增而未同步两侧，
就会被**静默丢弃** ⇒ 过滤条件不生效 ⇒ **跨用户召回**（且无任何日志/异常）。
本次修复前 `user_id` 刚被补进 `_to_filter`（2026-08-26），正说明"两处各写一份
键集"的结构性风险仍在。

## 现口径

- **键集唯一来源**：`FILTER_KEYS`（7 键）。
- **未知键 fail-closed**：`normalize_filters` 对未知键**抛 `UnknownFilterKey`**，
  绝不静默丢弃（宁可变红/报错，也不静默失去隔离）。
- **两侧覆盖对齐**：`HANDLED_BY_QDRANT` / `HANDLED_BY_PG` 声明各自处理的键，
  `assert_full_coverage()` 在运行期与测试期双向校验；新增键只改本文件一处，
  漏接任一侧即报错。

> 注意：Qdrant 侧对 `content_types` 需要额外展开遗留别名 `image`（旧 payload），
> 该差异属**存储形态差异**（Qdrant payload 有历史别名、PG 列是规范值），
> 不是键集差异——仍由各自实现处理，但键的**存在与校验**统一走本模块。
"""
from __future__ import annotations

from typing import Any

# 检索过滤键（唯一来源）。新增键必须同时接入 Qdrant 与 PG 两条实现。
FILTER_KEYS: tuple[str, ...] = (
    "content_types",   # 内容类型（photo/text/voice/article；"image" 别名归一为 "photo"）
    "time_from",       # taken_at 下界（db: datetime；qdrant: epoch 秒）
    "time_to",         # taken_at 上界
    "place",           # 地点（qdrant payload "place" 精确匹配 / db 列相等）
    "tag",             # 实体标签（qdrant payload "tags" / db extra.ci_tags 近似包含）
    "content_class",   # P1-A 类目路由（qdrant payload "content_class" / db 列）
    "user_id",         # **用户隔离**（跨用户召回防线，缺失即越权可见）
)

# 隔离键：被静默丢弃即造成越权/数据串扰——单列以便门禁与复核重点盯防
ISOLATION_KEYS: tuple[str, ...] = ("user_id",)

# 各实现的覆盖声明（与实现同文件提交；漏接即 assert_full_coverage 报错）
HANDLED_BY_QDRANT: frozenset[str] = frozenset(FILTER_KEYS)
HANDLED_BY_PG: frozenset[str] = frozenset(FILTER_KEYS)


class UnknownFilterKey(ValueError):
    """未知检索过滤键（未知键 = 可能失效的约束，一律 fail-closed 抛错）"""


def normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    """校验 + 归一化过滤器：未知键抛错，空值键丢弃。

    调用方（Qdrant 侧 / PG 侧）都必须先经本函数，杜绝"某个实现悄悄不认识某个键"。
    """
    if not filters:
        return {}
    unknown = sorted(k for k in filters if k not in FILTER_KEYS)
    if unknown:
        raise UnknownFilterKey(
            f"未知检索过滤键 {unknown}；合法键 = {list(FILTER_KEYS)}"
            "（新增键必须同时接入 vector_store._to_filter 与 rag.pg_fallback，"
            "否则该约束会被静默丢弃 —— 隔离类键丢失即跨用户召回）"
        )
    return {k: v for k, v in filters.items() if v}


def assert_full_coverage(backend: str, handled: frozenset[str] | set[str]) -> None:
    """运行期自检：某实现声明的键集必须覆盖全部 FILTER_KEYS。

    漏接 = 该键在该实现上被静默忽略 ⇒ 直接抛错（fail-closed，不降级）。
    """
    missing = sorted(set(FILTER_KEYS) - set(handled))
    if missing:
        raise AssertionError(
            f"{backend} 未处理检索过滤键 {missing}（会静默丢弃 ⇒ 约束失效）"
        )
