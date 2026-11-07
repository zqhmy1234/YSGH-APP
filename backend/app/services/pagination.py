"""时间序游标分页（2026-10-30 · P2 B-7 / D-11 共用实现）

为什么单独立一个模块（而不是各端点再抄一份）：
  timeline（`events.start_time DESC`）与对话历史（`chat_messages.created_at DESC`）
  是**同一种**「按时间倒序 + id 兜底」的键集游标；而仓内既有实现（contents / messages）
  已经各手写了一份编解码 + 误差处理。再抄两份就是第四、第五份副本——任何一处格式
  微调都会让另一处的游标静默失效（客户端只能拿到 422 或者更糟：翻页丢行）。

游标格式（**不透明字符串**，客户端不得自行拼装/解析）：
    `<UTC ISO 微秒>Z|<row id>`
  · 时间位统一 UTC、不带 `+`：避免 URL 里 `+` 被解析成空格（同 contents 先例
    `api/contents/__init__.py:452`）。
  · 时间位写 `~` = 该行时间列为 NULL（timeline 确有 start_time 为空的事件，
    B-5 的「1970 假日组」就是它们的下游症状）。

键集分页的不变量（对应 SQL 侧写法见 `services/events/timeline.py::get_timeline_page`
与 `api/chat.py::get_history`）：
  · 排序键最后一个必须**全序且唯一**（这里用主键），否则同秒多条会翻页错位/重复；
  · 取 `limit + 1` 条判断 `has_more`，不多做一次 count 查询（翻页不需要总数）。
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.services.sync_common import parse_ts

# 时间位为 NULL 的哨兵（与真实时间串不可能混淆：ISO 串里不会只出现 ~）
NULL_TS = "~"


def encode_ts_cursor(ts: datetime | None, row_id: str) -> str:
    """(时间, 主键) → 不透明游标；时间为 None 时用 NULL_TS 哨兵。"""
    if ts is None:
        return f"{NULL_TS}|{row_id}"
    return f"{ts.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S.%f')}Z|{row_id}"


def decode_ts_cursor(cursor: str) -> tuple[datetime | None, str]:
    """不透明游标 → (时间, 主键)。

    **非法游标抛 ValueError**，由调用方映射成 422 —— 不允许"解析失败就当没传游标"
    （那会静默从头开始翻，用户看到第一页内容重复出现，却以为翻页生效了）。
    """
    ts_raw, sep, row_id = cursor.partition("|")
    if not sep or not row_id:
        raise ValueError("游标需为 '<时间>|<id>' 两段")
    if ts_raw == NULL_TS:
        return None, row_id
    dt = parse_ts(ts_raw)
    if dt is None:
        raise ValueError("游标时间位不可解析")
    return dt, row_id
