"""数据导出路由（US-42 · 卡E03 W3 恢复；原实现见 stash 49bed24 考古）

GET /api/v1/export —— 全量 JSON 导出（拍板 D1：元数据+URL，不含图片二进制）
  - 登录鉴权（get_current_user）+ 用户隔离（services 层全部按 user_id 过滤）
  - 分页防护：services/export.py 内按 id 键集分块取数 + 每类上限截断（truncated）
  - 限流防护：进程内滑动窗口（每小时每用户上限，防重操作被刷）
  - 响应：application/json + Content-Disposition attachment（下载文件）

与旧实现差异（2026-09-04 卡E03 恢复时）：
  - format 参数校验改走 FastAPI Query pattern（非法 → 422 VALIDATION_ERROR 信封）；
    旧 ERR_EXPORT_001 不在现行错误登记表（core/errors.py 唯一真源），不新增裸码
  - main.py 已注册本 router（旧版由集成 Agent 接线，本次卡E03 域内自行加注册行）
  - 生产多副本限流如需 Redis 全局语义，把 /api/v1/export 登记进
    core/ratelimit.py 的 _DOMAIN_SCOPES（该文件不在卡E03 域，留主控拍板）
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime
from uuid import UUID

from fastapi import Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.export import build_export_payload

router = make_router(prefix="/api/v1", tags=["export"])

# ---- 导出限流（进程内滑动窗口；单副本 MVP 语义）----
EXPORT_WINDOW_SECONDS = 3600          # 窗口：1 小时
EXPORT_MAX_PER_WINDOW = 5             # 每用户每小时最多 5 次全量导出

_EXPORT_BUCKETS: dict[str, deque] = defaultdict(deque)
_EXPORT_LOCK = threading.Lock()


def _export_allowed(user_id: str) -> bool:
    """进程内滑动窗口放行判定（线程安全；超窗清旧计数）"""
    now = time.monotonic()
    with _EXPORT_LOCK:
        bucket = _EXPORT_BUCKETS[user_id]
        while bucket and now - bucket[0] >= EXPORT_WINDOW_SECONDS:
            bucket.popleft()
        bucket.append(now)
        return len(bucket) <= EXPORT_MAX_PER_WINDOW


def _json_default(obj):
    """datetime/date/UUID → 可 JSON 序列化（ISO8601；UTC 统一 Z 后缀，对齐客户端契约）"""
    if isinstance(obj, (datetime, date)):
        s = obj.isoformat()
        if isinstance(obj, datetime) and obj.tzinfo is not None and s.endswith("+00:00"):
            s = s[:-6] + "Z"
        return s
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"导出负载含不可序列化类型: {type(obj)}")


@router.get("/export")
def export_user_data(
    format: str = Query("json", pattern="^json$", description="导出格式（当前仅支持 json）"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """全量数据导出（US-42）：contents + events + profile + corrections 元数据，
    不含图片二进制（url 为存储引用）。响应为 JSON 文件（Content-Disposition）。

    安全：需登录（get_current_user）；数据按当前用户隔离（services 层按 user_id
    过滤）；分块取数 + 每类上限截断（truncated）+ 每用户限流（超限 429）。同步返回。
    """
    if not _export_allowed(str(user.id)):
        return Response(
            status_code=429,
            content=json.dumps(
                {
                    "code": "RATE_LIMITED",
                    "message": "导出请求过于频繁，请稍后再试",
                    "request_id": "",
                },
                ensure_ascii=False,
            ),
            media_type="application/json",
            headers={"Retry-After": str(EXPORT_WINDOW_SECONDS)},
        )

    payload = build_export_payload(db, str(user.id))
    filename = f"yishu-export-{time.strftime('%Y%m%d')}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, default=_json_default),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
