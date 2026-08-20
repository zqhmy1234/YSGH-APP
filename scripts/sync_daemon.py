"""目录同步器：模拟"相册监听 + 照片读取"，自动上传后端。

本机运行：轮询 SYNC_DIR（默认 sync_album/），新图片自动查重并上传，
失败自动重试；本地 sync_state.json 记录每张照片的同步状态。

用法：
    python scripts/sync_daemon.py             # 循环监听
    python scripts/sync_daemon.py --run-once  # 扫描一轮后退出（测试用）
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

SYNC_DIR = Path(os.getenv("SYNC_DIR", "sync_album"))
SERVER_URL = os.getenv("SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
INTERVAL = int(os.getenv("SYNC_INTERVAL", "5"))
STATE_FILE = Path("sync_state.json")
MAX_RETRY = 3
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def scan_files():
    if not SYNC_DIR.exists():
        return []
    return [p for p in SYNC_DIR.rglob("*") if p.is_file() and p.suffix.lower() in ALLOWED]


def check_hashes(hashes):
    resp = requests.post(f"{SERVER_URL}/assets/check", json={"hashes": hashes}, timeout=15)
    resp.raise_for_status()
    return {d["hash"]: d for d in resp.json()["results"]}


def upload_file(path, photo_time):
    with open(path, "rb") as f:
        resp = requests.post(
            f"{SERVER_URL}/upload/batch",
            files={"files": (path.name, f)},
            data={"photo_time": photo_time},
            timeout=300,
        )
        resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else {"status": "error", "detail": "空响应"}


def file_mtime_iso(path):
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def time_from_filename(path) -> str | None:
    """从文件名解析拍摄时间（手机导出常见命名），失败返回 None。

    支持：IMG20260617101329.jpg / IMG_20260731_122109.jpg /
    Screenshot_2026-06-12-01-38-07 / wx_camera_1782455848275.jpg /
    mmexport1781666666675.jpg
    """
    name = path.stem

    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})", name)
    if m:
        try:
            return datetime(*map(int, m.groups())).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    m = re.search(r"(1[6-9]\d{12})", name)  # 毫秒时间戳（13 位）
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)) / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            pass
    return None


def photo_time_for(path):
    """拍摄时间优先级：文件名时间 > 文件修改时间。"""
    return time_from_filename(path) or file_mtime_iso(path)


def run_once():
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    files = scan_files()
    stats = {"found": len(files), "synced": 0, "duplicate": 0, "failed": 0, "skipped": 0}

    todo = []
    for p in files:
        key = str(p)
        rec = state.get(key, {})
        if rec.get("status") in ("synced", "duplicate") or rec.get("retry", 0) >= MAX_RETRY:
            stats["skipped"] += 1
            continue
        todo.append((key, p))

    if todo:
        hashes = {key: hashlib.sha256(p.read_bytes()).hexdigest() for key, p in todo}
        known = check_hashes(list(hashes.values()))
        for key, p in todo:
            h = hashes[key]
            rec = state.setdefault(
                key,
                {"hash": h, "status": "pending", "retry": 0, "server_asset_id": None, "error": None, "updated_at": None},
            )
            if known[h]["exists"]:
                rec.update({"status": "duplicate", "server_asset_id": known[h]["asset_id"], "error": None, "updated_at": now()})
                stats["duplicate"] += 1
                save_state(state)
                continue
            try:
                rec["status"] = "uploading"
                save_state(state)
                res = upload_file(p, photo_time_for(p))
                if res.get("status") == "success":
                    rec.update({"status": "synced", "server_asset_id": res.get("asset_id"), "retry": 0, "error": None, "updated_at": now()})
                    stats["synced"] += 1
                else:
                    rec.update({"status": "failed", "retry": rec.get("retry", 0) + 1, "error": str(res.get("detail")), "updated_at": now()})
                    stats["failed"] += 1
            except Exception as e:
                rec.update({"status": "failed", "retry": rec.get("retry", 0) + 1, "error": str(e), "updated_at": now()})
                stats["failed"] += 1
            save_state(state)
    save_state(state)
    return stats


def main():
    parser = argparse.ArgumentParser(description="目录自动同步器")
    parser.add_argument("--run-once", action="store_true", help="只扫描一轮后退出")
    args = parser.parse_args()

    if args.run_once:
        print("sync once:", run_once())
        return

    print(f"同步器启动：监听 {SYNC_DIR}，间隔 {INTERVAL}s，服务 {SERVER_URL}")
    while True:
        try:
            print(now(), run_once())
        except Exception as e:
            print(now(), "同步失败:", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
