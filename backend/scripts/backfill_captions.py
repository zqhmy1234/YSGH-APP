"""caption 回填 runner（2026-10-19 · 一次性）

背景：真库有 1694 条「done 但 text 为空」的照片，但其中
  · 827 条根本没有 cos_key（合成/种子行）
  · 753 条有 key 但对象存储里没有文件
  ⇒ 真正可回填的只有 **114** 条。本脚本只处理这 114（先按 require-file 过滤）。

为什么不用 `requeue_job` 直接投 RQ：`process_content` 尾部会给每个用户 enqueue
聚合任务（L2/L3，走 LLM）。回填只想要 caption，不想顺带触发上百个用户的聚合；
本脚本在进程内直接调 `process_content`，把聚合任务留在（临时）Redis 队列里不消费，
跑完清理即可。

用法（在 backend/ 下）：
    python scripts/backfill_captions.py --dry-run
    python scripts/backfill_captions.py --limit 5          # 先抽 5 张验质量
    python scripts/backfill_captions.py --limit 200        # 全量（114）
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.external.storage import get_storage_backend  # noqa: E402
from app.services.pipeline import process_content  # noqa: E402
from app.workers.requeue_job import REQUEUE_DEFAULT_STALE_SECONDS, _stale_candidates  # noqa: E402


def _select(limit: int):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        cutoff = datetime.now(UTC) - timedelta(seconds=REQUEUE_DEFAULT_STALE_SECONDS)
        candidates = _stale_candidates(db, cutoff, limit, only_empty_done=True)
        storage = get_storage_backend()
        todo = [
            c
            for c in candidates
            if (c.cos_key or "").strip() and storage.object_exists(c.cos_key)
        ]
        # 先把 id 取出来，避免后续 process_content 另开会话时对象过期
        return [str(c.id) for c in todo], len(candidates)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="caption 回填（仅 done 无 text 且对象真实存在的照片）")
    parser.add_argument("--limit", type=int, default=200, help="最多处理条数（候选上限）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不处理")
    parser.add_argument("--sleep", type=float, default=0.2, help="每张之间的间隔秒数")
    parser.add_argument("--list-ids", default="", help="把待处理 id 写入文件后退出（分片用）")
    parser.add_argument("--ids-file", default="", help="从文件读取 id（跳过选择，分片用）")
    parser.add_argument("--shard", default="0/1", help="分片 i/n（0 起）")
    args = parser.parse_args()

    ids, scanned = _select(max(args.limit, 5000))
    print(f"候选 {scanned} 条；其中对象真实存在 {len(ids)} 条（本次最多处理 {args.limit} 条）")
    ids = ids[: args.limit]
    if args.list_ids:
        Path(args.list_ids).write_text("\n".join(ids) + "\n", encoding="utf-8")
        print(f"已写 {len(ids)} 条到 {args.list_ids}")
        return
    if args.ids_file:
        ids = [x.strip() for x in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if x.strip()]
    try:
        si, sn = (int(x) for x in args.shard.split("/", 1))
    except ValueError:
        si, sn = 0, 1
    if sn > 1:
        ids = ids[si::sn]
        print(f"分片 {si}/{sn}：{len(ids)} 条")
    if args.dry_run:
        for cid in ids[:10]:
            print("  [dry-run]", cid)
        return

    ok = failed = empty_after = 0
    started = time.time()
    from app.db.models import Content
    from app.db.session import SessionLocal

    for i, cid in enumerate(ids, 1):
        try:
            res = process_content(cid)
            # process_content 的返回体不含 text ⇒ 用独立会话回读真值（2026-10-19 修正：
            # 原实现读 res["text"] 恒空，把成功误报成 empty）
            check = SessionLocal()
            try:
                row = check.get(Content, cid)
                text = (row.text or "").strip() if row is not None else ""
            finally:
                check.close()
            if text:
                ok += 1
                print(f"[{i}/{len(ids)}] ok {cid[:8]} {text[:40]}")
            else:
                empty_after += 1
                print(f"[{i}/{len(ids)}] empty {cid[:8]} {res}")
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中断整批
            failed += 1
            print(f"[{i}/{len(ids)}] FAIL {cid[:8]} {type(exc).__name__}: {str(exc)[:120]}")
        if args.sleep > 0:
            time.sleep(args.sleep)

    took = time.time() - started
    print(f"完成：ok={ok} empty_after={empty_after} failed={failed} 用时={took:.0f}s")


if __name__ == "__main__":
    main()
