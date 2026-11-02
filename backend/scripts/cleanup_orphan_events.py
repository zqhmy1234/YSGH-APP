"""清理「空壳事件」：`event_items` 为空、且成员全被删除的 L1 聚合卡。

## 为什么（2026-11-01 走查 3.6）

时间轴渲染的是 **events**，而删除内容只软删 `contents`。
于是事件留下、成员指向已删内容 ⇒ 卡片照旧显示（用户以为"没删掉"），
点进去又因 `_batch_open_content_ids` 过滤 `deleted_at` 而拿不到内容（空壳）。

删除端点已修（`_reconcile_events_after_content_delete`），本脚本处理**存量**。

## 判定（保守：只动确定无意义的）

- `level = 1`（端侧聚合日卡）**且** `event_items` 里一条都没有
  （或成员全指向 `deleted_at` 非空的内容）
- 其它层级/有成员的**一律不动**

## 用法

```
cd backend
$env:PYTHONPATH='.'
python scripts/cleanup_orphan_events.py --dry-run
python scripts/cleanup_orphan_events.py
```
"""

from __future__ import annotations

import argparse
import sys

from app.db.session import SessionLocal
from sqlalchemy import text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "select e.id, e.user_id, e.title from events e "
                "where e.level = 1 and not exists ("
                "  select 1 from event_items ei join contents c on c.id = ei.content_id "
                "  where ei.event_id = e.id and c.deleted_at is null"
                ")"
            )
        ).all()
        print(f"空壳 L1 事件（无任何存活成员）：{len(rows)} 条")
        for r in rows[:5]:
            print(f"  {r[2]!r}")
        if args.dry_run:
            print("[dry-run] 不删除")
            return 0

        ids = [r[0] for r in rows]
        if not ids:
            print("无需清理")
            return 0
        # 先删成员关系，再删事件（避免外键悬挂）
        db.execute(
            text("delete from event_items where event_id = any(:ids)"), {"ids": ids}
        )
        n = db.execute(
            text("delete from events where id = any(:ids)"), {"ids": ids}
        ).rowcount
        db.commit()
        print(f"已删除空壳事件：{n} 条")
        left = db.execute(text("select count(*) from events")).scalar()
        print(f"复核：剩余事件 {left} 条")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
