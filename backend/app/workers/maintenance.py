"""业务 cron 统一入口：先加载指定环境，仅输出计数 JSON，失败返回非零。

从 backend 目录运行 python -m app.workers.maintenance --help。
cleanup 默认只预览；正式删除必须显式传 --execute-cleanup。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID


def _load_env(path: Path | None) -> None:
    if path is not None:
        from dotenv import load_dotenv

        if not path.is_file():
            raise FileNotFoundError("environment file missing")
        # 必须在首次导入 app.core.config / app.db.session 之前执行。
        load_dotenv(path, override=True)


def _capsules(dry_run: bool) -> dict:
    from sqlalchemy import func, select

    from app.db.models import Capsule
    from app.db.session import SessionLocal
    from app.workers.capsule_scan import scan_due_capsules

    with SessionLocal() as db:
        if dry_run:
            count = db.scalar(select(func.count()).select_from(Capsule).where(
                Capsule.status == "sealed", Capsule.open_at <= datetime.now(UTC),
            ))
        else:
            count = scan_due_capsules(db)
    return {"due": count, "failed": 0}


def _reviews(dry_run: bool, day: date | None, user_id: UUID | None) -> dict:
    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import SessionLocal
    from app.services.notify import REVIEW_TZ, generate_daily_review

    day = day or datetime.now(REVIEW_TZ).date()
    generated = skipped = failed = 0
    with SessionLocal() as db:
        query = select(User.id).where(User.status == 1)
        if user_id is not None:
            query = query.where(User.id == str(user_id))
        user_ids = list(db.scalars(query))
        for uid in user_ids:
            if dry_run:
                continue
            try:
                if generate_daily_review(db, str(uid), day=day) is None:
                    skipped += 1
                else:
                    generated += 1
            except Exception:
                db.rollback()
                failed += 1
    return {"candidates": len(user_ids), "generated": generated, "skipped": skipped, "failed": failed}


def _execute(args: argparse.Namespace, dry_run: bool) -> dict:
    if args.job == "cleanup":
        from app.workers.cleanup_job import run_cleanup, run_upload_reaper

        return {
            "cleanup": run_cleanup(older_than_days=30, limit=args.limit, dry_run=dry_run),
            "upload_reaper": run_upload_reaper(older_than_days=7, dry_run=dry_run),
        }
    if args.job == "requeue":
        from app.workers.requeue_job import run_requeue

        return run_requeue(older_than_seconds=3600, limit=args.limit, dry_run=dry_run)
    if args.job == "capsules":
        return _capsules(dry_run)
    return _reviews(dry_run, args.day, args.user)


def _failed(stats: dict) -> int:
    """只接受计数/布尔结果，防后续 job 把异常、用户 ID 或正文带进 cron 日志。"""
    failures = 0
    for key, value in stats.items():
        if isinstance(value, dict):
            failures += _failed(value)
        elif not isinstance(value, (int, bool)) or value < 0:
            raise ValueError("non-counter job result")
        elif key == "failed":
            failures += value
    return failures


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=("cleanup", "requeue", "capsules", "daily-review"))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--limit", type=_positive, default=100)
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--user", type=UUID)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--execute-cleanup", action="store_true", help="确认执行 cleanup 正式删除")
    args = parser.parse_args(argv)
    if args.execute_cleanup and args.job != "cleanup":
        parser.error("--execute-cleanup requires cleanup")
    if args.job != "daily-review" and (args.day is not None or args.user is not None):
        parser.error("--day/--user require daily-review")
    dry_run = args.dry_run or (args.job == "cleanup" and not args.execute_cleanup)
    started, clock = datetime.now(UTC).isoformat(), monotonic()
    stats, error_type, exit_code = {}, None, 0
    previous_disable = logging.root.manager.disable
    try:
        # 底层旧 job / mock push 会记录 ID、异常正文；cron 只留下面的一条计数摘要。
        logging.disable(logging.CRITICAL)
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            _load_env(args.env_file)
            stats = _execute(args, dry_run)
            exit_code = int(_failed(stats) > 0)
    except Exception as exc:
        stats, error_type, exit_code = {}, type(exc).__name__, 1
    finally:
        logging.disable(previous_disable)
    print(json.dumps({
        "job": args.job, "dry_run": dry_run, "started_at": started,
        "duration_ms": round((monotonic() - clock) * 1000),
        "status": "failed" if exit_code else "ok", "stats": stats,
        "error_type": error_type, "exit_code": exit_code,
    }, ensure_ascii=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
