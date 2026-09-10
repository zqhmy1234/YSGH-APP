"""孤儿照片事件回填（2026-09-04 峰宝拍板：timeline/搜索每条记忆带自己照片）

背景：历史版本上传的照片（uploadBatch→aggregateToEvents→syncClientEvents 接线前）
没有任何事件成员（event_items 0 行）→ timeline EventOut.photo_ids/photos[] 恒空 →
客户端卡片照片条永远空白。

本脚本找出「有照片但 0 事件成员」的用户，逐用户跑 full 聚合：
L1 日卡片按天去重并入（_write_l1_days 同日不重复建）+ L2/L3 候选，
幂等（aggregate_user 跳过已挂任意事件的内容，重跑安全）。

用法（在 backend/ 下）：
  python scripts/backfill_orphan_events.py [--user <uuid>] [--dry-run]
  --user    只处理指定用户（含已有成员的用户，按天并入仍幂等）
  --dry-run 只列出待回填用户与照片数，不写库
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import Content, EventItem  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.events.aggregate import run_user_aggregation  # noqa: E402
from sqlalchemy import exists, func, select  # noqa: E402


def find_orphan_users(db) -> list[tuple[str, int]]:
    """有未删照片但这些照片 0 事件成员的用户 → [(user_id, photo_count)]"""
    has_member = exists(select(EventItem.event_id).where(EventItem.content_id == Content.id))
    rows = db.execute(
        select(Content.user_id, func.count().label("n"))
        .where(
            Content.content_type == "photo",
            Content.deleted_at.is_(None),
            ~has_member,
        )
        .group_by(Content.user_id)
    ).all()
    return [(str(r.user_id), int(r.n)) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="孤儿照片事件回填")
    parser.add_argument("--user", type=str, default=None, help="只处理指定用户")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    db = SessionLocal()
    try:
        if args.user:
            targets = [(args.user, -1)]
        else:
            targets = find_orphan_users(db)
        if not targets:
            print("无孤儿照片用户，无需回填")
            return 0
        print(f"待回填用户 {len(targets)} 个：")
        for uid, n in targets:
            print(f"  {uid}  孤儿照片 {n} 张" if n >= 0 else f"  {uid}")
        if args.dry_run:
            return 0
        for uid, _n in targets:
            result = run_user_aggregation(uid, mode="full")
            print(f"  {uid} → {result}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
