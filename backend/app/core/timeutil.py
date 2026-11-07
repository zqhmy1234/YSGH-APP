"""云侧「本地自然日」口径的**单一来源**（功能修复波 · D04-1 · 2026-09-25）。

## 为什么需要这个文件（缺陷 D04-1，P0）

同一云侧此前存在**两套日界口径**：

- `services/echo.py::_local_now()` 用 `datetime.now().astimezone()`（其 docstring 记录它修过
  「原按 UTC 日界 ⇒ 本地 0:00-8:00 被算到前一天」）；
- 事件聚合 `event_aggregation/st_dbscan.py::l1_daily_aggregate(...)` 的 `tz_offset_minutes`
  **默认 0（＝UTC 日界）**。

后果：沪区 **00:00–07:59** 拍摄的照片在云侧 L1 日卡片上落到**前一天**（审计 D04-1 原文口径）。

## 口径（本文件即真源）

云侧「本地日」一律按 **`APP_LOCAL_TZ`**（默认 `Asia/Shanghai`）计算，**不读取容器 TZ**——
`deploy/` 未设 TZ ⇒ 容器为 UTC，若沿用"服务器本地时间"则等于没修。

## 边界（已知未覆盖，属产品决策）

真实跨时区用户需要 **per-user 偏移**（端侧已在 `aggregateToEvents(uploads, tzOffsetMin)`
传设备偏移；云侧目前无处可存）⇒ 需"用户时区字段 + 客户端上报"才成立，登记在
`docs/决策台账.md` 待拍板。本模块先把"云侧不再自相矛盾且不依赖容器"这一层收敛掉。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import settings


def app_local_tz_name() -> str:
    """`APP_LOCAL_TZ`（IANA 名；默认 Asia/Shanghai）。"""
    return settings.app_local_tz


def _fixed_offset_tz(minutes: int) -> timezone:
    return timezone(timedelta(minutes=minutes))


def app_local_tz():
    """应用本地时区对象（优先 `zoneinfo`；缺失/未安装时退化为固定偏移）。"""
    name = app_local_tz_name()
    try:
        from zoneinfo import ZoneInfo  # 局部 import：py3.9+ 标准库；缺失时走退化分支

        return ZoneInfo(name)
    except Exception:  # pragma: no cover - 仅环境缺 tzdata 时命中
        return _fixed_offset_tz(local_utc_offset_minutes())


def local_now() -> datetime:
    """当前**应用本地**时间（带 tzinfo）。

    与旧 `echo._local_now()` 的差别：那条取"服务器本地"，在 UTC 容器里＝UTC（等于没修）；
    本条取 `APP_LOCAL_TZ`，与容器 TZ 无关。
    """
    return datetime.now(app_local_tz())


def local_utc_offset_minutes(now: datetime | None = None) -> int:
    """应用本地时区相对 UTC 的**分钟偏移**（L1 日聚合等需要 int 偏移的地方用）。

    与 `local_now()` 同源（同一 `APP_LOCAL_TZ`），故两者永不漂移。
    """
    ref = now or local_now()
    off = ref.utcoffset()
    if off is None:  # pragma: no cover - 理论上不成立（tzinfo 必带偏移）
        return 0
    return int(off.total_seconds() // 60)
