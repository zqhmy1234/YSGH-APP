"""把「假 processing」的孤儿子内容关掉（DB 状态与队列一致性巡检）。

## 为什么需要这个脚本（2026-10-31 现场）

走查发现「新写的文字搜不到」。查下去不是搜索引的问题，而是：

```
RQ 队列长度            = 0（rq:queue:* 一个都没有）
processing 抽样 200 条 中，队列里存在 job 的 = 0
processing 总数 4590，其中 >24h = 4579（最老 2026-08-26）
```

即：**DB 写着 `processing`，但队列里根本没有对应 job**。
这类内容永远不会被消费、也永远不会被清理，只会：
  · 让内容没有打标/向量 ⇒ 搜索永远召回不到；
  · 让卡片没有标题；
  · 让「积压」看起来永远清不完，掩盖真正的问题。

根因是**缺少状态与队列的一致性巡检**：入队失败、job 过期、worker 崩溃都会留下这种残骸，
而没有任何机制回头核对。本脚本就是那个巡检（可重复执行，幂等）。

## 判定

对每条 `status='processing'` 且 `created_at` 早于 `--min-age-minutes`（默认 30）的内容：
  · RQ 里存在该 job（queued/started/deferred/scheduled） ⇒ **不动**（真在排队/在跑）
  · 否则 ⇒ 判定为孤儿，`--action failed`（默认）置为 `failed`，或 `--action requeue` 重新入队

## 用法

```
cd backend
$env:PYTHONPATH='.'; python scripts/close_orphan_contents.py --dry-run      # 先看会动多少
$env:PYTHONPATH='.'; python scripts/close_orphan_contents.py                # 执行（置 failed）
$env:PYTHONPATH='.'; python scripts/close_orphan_contents.py --action requeue  # 或重新入队
```
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

import redis
from app.core.config import settings
from app.db.session import SessionLocal
from sqlalchemy import text


def _job_state(r: redis.Redis, content_id: str) -> str | None:
    """返回 job 状态；不存在返回 None。"""
    key = f"rq:job:process_content_{content_id}"
    if not r.exists(key):
        return None
    raw = r.hget(key, "status")
    if raw is None:
        return "unknown"
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-age-minutes", type=int, default=30)
    ap.add_argument("--action", choices=["failed", "requeue"], default="failed")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=10000)
    args = ap.parse_args()

    r = redis.Redis.from_url(settings.redis_url)
    cutoff = datetime.now(UTC) - timedelta(minutes=args.min_age_minutes)

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "select id, content_type, created_at from contents "
                "where status = 'processing' and created_at < :cut "
                "order by created_at asc limit :lim"
            ),
            {"cut": cutoff, "lim": args.limit},
        ).all()

        orphan: list[tuple] = []
        alive = 0
        for cid, ctype, created in rows:
            st = _job_state(r, str(cid))
            if st in ("queued", "started", "deferred", "scheduled"):
                alive += 1
            else:
                orphan.append((cid, ctype, created, st))

        print(f"扫描 processing 且早于 {args.min_age_minutes} 分钟：{len(rows)} 条")
        print(f"  仍在队列/在跑（不动）：{alive}")
        print(f"  孤儿（队列里没有 job）：{len(orphan)}")
        if orphan:
            ages = sorted(str(o[2]) for o in orphan)
            print(f"  最老：{ages[0]}   最新：{ages[-1]}")
            by_type: dict[str, int] = {}
            for _, ctype, _, _ in orphan:
                by_type[str(ctype)] = by_type.get(str(ctype), 0) + 1
            print(f"  按类型：{by_type}")

        if args.dry_run:
            print("[dry-run] 不做任何修改")
            return 0
        if not orphan:
            print("没有孤儿，无需处理")
            return 0

        ids = [str(o[0]) for o in orphan]
        if args.action == "failed":
            # 置 failed 而不是 done：这些内容确实没跑过管线（无打标/无向量），
            # 标成 done 是撒谎，且会让「未处理」永久看不见。
            n = db.execute(
                text(
                    "update contents set status='failed' where id = any(:ids) "
                    "and status='processing'"
                ),
                {"ids": ids},
            ).rowcount
            db.commit()
            print(f"已置为 failed：{n} 条")
        else:
            from app.core.queue import enqueue_unique
            from app.services.pipeline import process_content

            n = 0
            for cid, ctype, *_ in orphan:
                queue_kwargs = {}
                # 与 API 侧同口径：文字走高优，音视频/照片走低优
                if ctype not in ("text", "article"):
                    from app.core.queue import QUEUE_LOW

                    queue_kwargs = {"queue_name": QUEUE_LOW}
                enqueue_unique(process_content, cid, cid, **queue_kwargs)
                n += 1
            print(f"已重新入队：{n} 条")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
