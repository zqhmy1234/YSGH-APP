"""缩略图回填脚本（B4 Wave3 AgentG · audit #1 缺口修复）

背景：thumbnail_key 列新增写入方（thumbnails.py）之前已入库的照片没有缩略图。
本脚本扫描 photo + cos_key 非空 + thumbnail_key 为空的记录，逐个生成回填。

用法（在 backend/ 下）：
  python scripts/backfill_thumbnails.py [--limit 200] [--force] [--dry-run]
  --force   已存在缩略图也重新生成（覆盖）
  --dry-run 只统计不写
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.services import thumbnails  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="照片缩略图回填")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--force", action="store_true", help="已存在也重新生成")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    db = SessionLocal()
    try:
        rows = thumbnails.list_photos_without_thumbnail(db, limit=args.limit)
        print(f"待回填照片: {len(rows)}（limit={args.limit}）")
        if args.dry_run:
            return 0
        created = exists = skipped = failed = 0
        for content in rows:
            result = thumbnails.generate_thumbnail(db, str(content.id), force=args.force)
            if result["status"] == "created":
                created += 1
            elif result["status"] == "exists":
                exists += 1
            elif result["status"] == "skipped":
                skipped += 1
            else:
                failed += 1
        print(f"回填完成: created={created} exists={exists} skipped={skipped} failed={failed}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
