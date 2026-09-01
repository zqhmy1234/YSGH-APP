"""事件服务子包（F5/R1#5 拆包：services/events.py → services/events/）

外部 import 兼容：`from app.services.events import X` 与拆分前行为等价
（本 __init__ 重导出拆分前全部公开函数 + 测试/内部仍按旧路径引用的私有函数）。

模块划分（聚合细节收敛在 aggregate.py 窄端口，不外泄到 pipeline）：
  - aggregate.py —— 事件聚合：云侧 L2/L3 候选（l2l3）+ 全量管线 L1（full）
                     + F3 独立 per-user RQ 任务 run_user_aggregation
  - sync.py      —— 事件上云与拉取：端侧 L1 批量提交幂等 + offline_queue 变更日志
  - timeline.py  —— 时间轴（F8）+ 事件最近活动（L3 生命周期读取时派生）
  - edit.py      —— 用户手动操作：merge / split / confirm / set_cover / 成员明细
"""
from app.services.events.aggregate import (
    _l2l3_candidates_from_photos,
    _l3_confidence,
    _previous_aggregate_result,
    _refresh_upper_candidates,
    _to_raw_photo,
    _write_l1_days,
    _write_upper_candidates,
    _write_upper_events,
    aggregate_user,
    run_user_aggregation,
)
from app.services.events.edit import (
    _get_event,
    _log_edit,
    _refresh_event_window,
    confirm_event,
    get_event_items,
    merge_events,
    set_event_cover,
    split_event,
)
from app.services.events.sync import (
    sync_client_events,
    sync_client_events_safe,
)
from app.services.events.timeline import (
    get_event_last_activity,
    get_timeline,
)

__all__ = [
    # aggregate（聚合）
    "aggregate_user",
    "run_user_aggregation",
    "_write_l1_days",
    "_write_upper_candidates",
    "_write_upper_events",
    "_previous_aggregate_result",
    "_l3_confidence",
    "_l2l3_candidates_from_photos",
    "_refresh_upper_candidates",
    "_to_raw_photo",
    # sync（事件上云与拉取）
    "sync_client_events",
    "sync_client_events_safe",
    # timeline（时间轴）
    "get_timeline",
    "get_event_last_activity",
    # edit（用户手动操作）
    "get_event_items",
    "merge_events",
    "split_event",
    "confirm_event",
    "set_event_cover",
    "_get_event",
    "_log_edit",
    "_refresh_event_window",
]
