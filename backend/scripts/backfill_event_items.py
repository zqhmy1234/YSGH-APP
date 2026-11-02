"""回填 event_items：修复「事件在、成员空」导致卡片点不进详情。

## 现场（2026-11-01）

```
events: 896    event_items: 0    contents: 10358
```

全表成员为空 ⇒ 时间轴卡片没有 `cover_content_id`，客户端 `openDetail` 拿不到 id，
只弹「这条记忆暂无可查看的内容」就返回（设备实测：走查 6.x/3.3/3.5 共 9 条全被挡住）。
而 `EventItem` 的写入路径本身是可用的（试插成功），所以是**历史数据丢失 + 重复提交不再补写**。

## 回填策略（确定性优先，不做猜测）

1. `client_event_id = ev-c-<contentId>`：键里就带内容 id ⇒ **精确回填**（文字/语音单条卡）；
2. `client_event_id = ev-d-<day>-<指纹>`：同一天该用户的照片 ‖ 语音内容 ⇒ 按天回填
   （端侧聚合日卡的语义就是"这一天的照片"）；
3. 其余/无键事件：跳过并计数，不猜。

## 用法

```
cd backend
$env:PYTHONPATH='.'
python scripts/backfill_event_items.py --dry-run     # 先看会补多少
python scripts/backfill_event_items.py               # 执行
```
"""

from __future__ import annotations

import argparse
import re
import sys

from app.db.models import EventItem
from app.db.session import SessionLocal
from sqlalchemy import text

_RE_C = re.compile(r"^ev-c-([0-9a-fA-F-]{36})$")
_RE_D = re.compile(r"^ev-d-(\d{4}-\d{2}-\d{2})-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        events = db.execute(
            text(
                "select e.id, e.user_id, e.client_event_id, e.start_time, e.level "
                "from events e "
                "where not exists (select 1 from event_items ei where ei.event_id = e.id)"
            )
        ).all()
        print(f"成员为空的事件：{len(events)} 条")

        planned: list[tuple[str, str]] = []
        skipped = 0
        for eid, uid, key, start_ts, _level in events:
            k = key or ""
            m = _RE_C.match(k)
            if m is not None:
                cid = m.group(1)
                exists = db.execute(
                    text(
                        "select 1 from contents where id = :c and user_id = :u "
                        "and deleted_at is null"
                    ),
                    {"c": cid, "u": uid},
                ).first()
                if exists is not None:
                    planned.append((str(eid), cid))
                else:
                    skipped += 1
                continue

            md = _RE_D.match(k)
            if md is not None and start_ts is not None:
                day = md.group(1)
                # 同日同用户的照片/语音内容（端侧日卡语义）
                rows = db.execute(
                    text(
                        "select id from contents where user_id = :u and deleted_at is null "
                        "and content_type = 'photo' "
                        "and (created_at at time zone 'Asia/Shanghai')::date = :d"
                    ),
                    {"u": uid, "d": day},
                ).scalars().all()
                if rows:
                    for cid in rows:
                        planned.append((str(eid), str(cid)))
                else:
                    skipped += 1
                continue

            skipped += 1

        print(f"  可回填成员：{len(planned)} 条（涉及 {len({p[0] for p in planned})} 个事件）")
        print(f"  无法判定跳过：{skipped} 个事件")

        if args.dry_run:
            print("[dry-run] 不写入")
            return 0

        n = 0
        for eid, cid in planned:
            # 幂等：已存在则跳过（复合主键 content_id+event_id）
            exists = db.execute(
                text(
                    "select 1 from event_items where event_id = :e and content_id = :c"
                ),
                {"e": eid, "c": cid},
            ).first()
            if exists is not None:
                continue
            db.add(EventItem(content_id=cid, event_id=eid))
            n += 1
        db.commit()
        print(f"已写入 event_items：{n} 条")

        total = db.execute(text("select count(*) from event_items")).scalar()
        left = db.execute(
            text(
                "select count(*) from events e "
                "where not exists (select 1 from event_items ei where ei.event_id = e.id)"
            )
        ).scalar()
        print(f"复核：event_items 总数 {total}，仍无成员的事件 {left} 条")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
