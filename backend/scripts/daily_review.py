"""每日复盘入队（S4-07 · 22:00 push，mock 通道）

遍历活跃用户 → notify.generate_daily_review 生成复盘消息（mock 推送日志占位）。
供 RQ 定时任务 / Windows 计划任务 / cron 调用；幂等：可重复跑（生成新消息不重复覆盖）。

用法：
    python scripts/daily_review.py [--day YYYY-MM-DD] [--user <uuid>] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.notify import REVIEW_TZ, generate_daily_review  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="22:00 每日复盘入队（mock 推送）")
    parser.add_argument("--day", type=str, default=None, help="复盘日期 YYYY-MM-DD（默认今天，本地日界）")
    parser.add_argument("--user", type=str, default=None, help="只跑指定用户（uuid）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不生成")
    args = parser.parse_args()

    from app.db.models import User
    from app.db.session import SessionLocal
    from sqlalchemy import select

    day: date = (
        date.fromisoformat(args.day) if args.day else datetime.now(REVIEW_TZ).date()
    )

    db = SessionLocal()
    try:
        query = select(User).where(User.status == 1)
        if args.user:
            query = query.where(User.id == args.user)
        users = db.execute(query).scalars().all()
        print(f"复盘日期: {day} | 候选用户: {len(users)}")

        sent = skipped = 0
        for u in users:
            if args.dry_run:
                print(f"  [dry-run] user={u.id}")
                continue
            msg = generate_daily_review(db, u.id, day=day)
            if msg is not None:
                print(f"  ✅ user={u.id} msg#{msg.id} {msg.title} ({msg.channel}/{msg.msg_type})")
                sent += 1
            else:
                skipped += 1
                print(f"  -  user={u.id} 今日无内容，跳过")
        print(f"完成: 生成 {sent} 条 | 跳过 {skipped} 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
