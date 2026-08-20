"""150 张照片端到端验收：批量导入 → 统计 → 事件聚合报告。

用法：
    python scripts/ingest_test.py --dir test_album --chunk 20 --interval 0.5
"""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_daemon import photo_time_for  # noqa: E402

SERVER_URL = os.getenv("SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def scan_images(root):
    return [
        p
        for p in Path(root).rglob("*")
        if p.is_file() and p.suffix.lower() in ALLOWED and "classify_report" not in p.name
    ]


def upload_chunk(files):
    resp = requests.post(f"{SERVER_URL}/upload/batch", files=files, timeout=600)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="test_album")
    parser.add_argument("--chunk", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 张（0=全部）")
    parser.add_argument("--report", default="ingest_report.json")
    args = parser.parse_args()

    images = scan_images(args.dir)
    if args.limit:
        images = images[: args.limit]
    print(f"待导入：{len(images)} 张")

    total_start = time.time()
    durations = []
    results = []
    for i in range(0, len(images), args.chunk):
        chunk = images[i : i + args.chunk]
        files = []
        for p in chunk:
            files.append(("files", (p.name, p.read_bytes())))
        t0 = time.time()
        try:
            body = upload_chunk(files)
        except Exception as e:
            for p in chunk:
                results.append({"filename": p.name, "status": "error", "detail": f"请求失败: {e}"})
            continue
        dt = time.time() - t0
        for r in body["results"]:
            durations.append(dt / len(chunk))
            results.append({**r, "filename": r.get("filename")})
        print(f"  批次 {i // args.chunk + 1}: 成功 {body['success']} 失败 {body['failed']} ({dt:.1f}s)")
        time.sleep(args.interval)

    total_time = time.time() - total_start
    success = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "error"]
    blocked = [
        r
        for r in results
        if r["status"] == "error"
        and isinstance(r.get("detail"), dict)
        and r["detail"].get("error") == "SENSITIVE_BLOCKED"
    ]
    failed = [r for r in failed if r not in blocked]
    duplicates = [r for r in success if r.get("duplicate")]

    reasons = {}
    for r in failed:
        d = r.get("detail")
        key = d.get("error") if isinstance(d, dict) else str(d)[:40]
        reasons[key] = reasons.get(key, 0) + 1

    report = {
        "total": len(images),
        "success": len(success),
        "failed": len(failed),
        "blocked_sensitive": len(blocked),
        "duplicates": len(duplicates),
        "success_rate": round(len(success) / (len(images) - len(blocked)), 4) if images and len(images) > len(blocked) else 0,
        "failure_reasons": reasons,
        "total_seconds": round(total_time, 2),
        "p50_seconds": round(statistics.median(durations), 3) if durations else None,
        "p95_seconds": round(sorted(durations)[int(len(durations) * 0.95) - 1], 3) if len(durations) > 5 else None,
    }

    # 事件聚合报告
    try:
        requests.post(f"{SERVER_URL}/events/rebuild?user_id=default", timeout=60)
        evs = requests.get(f"{SERVER_URL}/events?user_id=default&limit=1000", timeout=60).json()["events"]
        counts = [e["photo_count"] for e in evs]
        report["events"] = {
            "event_count": len(evs),
            "avg_photos_per_event": round(sum(counts) / len(counts), 2) if counts else 0,
            "isolated_events": sum(1 for c in counts if c == 1),
        }
    except Exception as e:
        report["events"] = {"error": str(e)}

    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告已写入 {args.report}")


if __name__ == "__main__":
    main()
