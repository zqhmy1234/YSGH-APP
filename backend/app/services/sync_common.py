"""B4 同步公共工具（审查 P1-09 收敛：sync/reconcile 共用，消除复制粘贴漂移）

原 _parse_ts 在 sync.py 与 reconcile.py 各存一份、TOMBSTONE_FIELD 各定义一次
（注释互相提示"保持一致"）——改一处漏一处。现统一到本模块。
"""
from __future__ import annotations

from datetime import datetime, timezone

# 软删除墓碑字段名（B4-2：entity 级墓碑）
TOMBSTONE_FIELD = "*"


def parse_ts(value: str | None) -> datetime | None:
    """ISO 时间串 → aware datetime

    naive 时间（无时区）统一视为 UTC（审查 P1-01：此前 naive 与 DB aware
    updated_at 比较抛 TypeError → 500）。
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def lww_wins(server_ts: datetime, client_ts: datetime) -> bool:
    """LWW 判定：client 是否更新（防御 TypeError 不 500）"""
    try:
        return client_ts > server_ts
    except TypeError:
        return True
