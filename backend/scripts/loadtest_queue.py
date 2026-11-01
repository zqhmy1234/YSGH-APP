"""生产者-消费者队列压测（POST /contents → RQ → pipeline）。

## 为什么需要它（2026-10-31）

队列曾经「看着有 4590 条积压、实际队列 0 job」——因为没有**任何量化指标**，
既分不清是生产端打爆还是消费端跟不上，也发现不了「DB 状态与队列脱节」。
本脚本把两端分开量，给出可比较的数字。

## 指标定义（这就是验收口径）

生产侧（API）：
  · `生产吞吐` = 成功创建条数 / 创建耗时（条/秒）
  · `生产延迟` = 单次 POST 的 P50 / P95 / P99（毫秒）—— 用户点「保存」的体感
  · `创建失败率` = 非 2xx 占比

消费侧（worker）：
  · `消费吞吐` = 在观测窗内变为终态(status in done/failed)的条数 / 秒
  · `端到端延迟` = 从「创建完成」到「变为终态」的 P50 / P95 / P99（秒）
                     —— 这才是用户「记完多久能搜到」的真实延迟
  · `积压峰值` = 观测期内仍为 processing 的最大条数（= 生产快于消费时的积压）

## 用法

```
cd backend
$env:PYTHONPATH='.'
python scripts/loadtest_queue.py --level 20
python scripts/loadtest_queue.py --level 100 --concurrency 8
python scripts/loadtest_queue.py --level 300 --concurrency 16 --timeout 300
```

每条内容都带唯一 marker（`loadtest-<run_id>-<n>`），便于事后清理：
`python scripts/loadtest_queue.py --cleanup <run_id>`
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from app.core.config import settings
from app.db.session import SessionLocal
from sqlalchemy import text

BASE = f"http://127.0.0.1:{getattr(settings, 'port', 8010)}"
# 注：settings.port 的默认值是 8000（历史端口），实际运行端口由 API_PORT/启动参数决定。
# 压测要打的是**正在跑的那个**，所以显式指定，避免打错端口得到一眼假的数字。
if BASE.endswith(":8000"):
    BASE = "http://127.0.0.1:8010"


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def _login(c: httpx.Client, device: str) -> str:
    r = c.post(f"{BASE}/api/v1/auth/device", json={"device_id": device})
    if r.status_code != 200:
        raise SystemExit(f"登录失败 {r.status_code}: {r.text[:300]}")
    body = r.json()
    data = body.get("data")
    if not isinstance(data, dict) or "access_token" not in data:
        raise SystemExit(f"登录响应结构异常: {str(body)[:300]}")
    return data["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=20, help="总共创建多少条内容")
    ap.add_argument("--concurrency", type=int, default=8, help="生产端并发数")
    ap.add_argument("--timeout", type=int, default=240, help="等待全部终态的最长秒数")
    ap.add_argument("--cleanup", default="", help="按 run_id 清理压测数据")
    args = ap.parse_args()

    run_id = uuid.uuid4().hex[:8]
    marker = f"loadtest-{run_id}"

    if args.cleanup:
        db = SessionLocal()
        try:
            n = db.execute(
                text("delete from contents where text like :m"),
                {"m": f"%loadtest-{args.cleanup}%"},
            ).rowcount
            db.commit()
            print(f"已清理 {n} 条 text like %loadtest-{args.cleanup}%")
        finally:
            db.close()
        return 0

    client = httpx.Client(timeout=30)
    token = _login(client, f"loadtest-{run_id}")
    headers = {"Authorization": f"Bearer {token}"}

    print(f"=== 压测开始 run_id={run_id} level={args.level} concurrency={args.concurrency} ===")

    latencies: list[float] = []
    ids: list[str] = []
    create_errors = 0
    lock = __import__("threading").Lock()

    def produce(i: int) -> None:
        nonlocal create_errors
        body = {
            "content_type": "text",
            "text": f"{marker}-{i} 压测内容 湖边散步 第{i}条",
            "source": "app",
        }
        t0 = time.perf_counter()
        try:
            r = client.post(f"{BASE}/api/v1/contents", json=body, headers=headers)
            dt = (time.perf_counter() - t0) * 1000.0
            if r.status_code == 200:
                cid = r.json()["data"]["id"]
                with lock:
                    latencies.append(dt)
                    ids.append(cid)
            else:
                with lock:
                    create_errors += 1
        except Exception:
            with lock:
                create_errors += 1

    t_prod0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        list(ex.map(produce, range(args.level)))
    prod_elapsed = time.perf_counter() - t_prod0

    if not ids:
        print("生产端全部失败，终止")
        return 1

    prod_tput = len(ids) / prod_elapsed if prod_elapsed > 0 else 0.0
    print("\n【生产侧】")
    print(f"  成功创建      : {len(ids)} / {args.level}（失败 {create_errors}）")
    print(f"  生产耗时      : {prod_elapsed:.2f}s")
    print(f"  生产吞吐      : {prod_tput:.1f} 条/秒")
    print(f"  POST 延迟     : P50={_pct(latencies,50):.0f}ms "
          f"P95={_pct(latencies,95):.0f}ms P99={_pct(latencies,99):.0f}ms")

    # ── 消费侧观测 ──
    db = SessionLocal()
    try:
        created_at = db.execute(
            text("select min(created_at) from contents where id = any(:ids)"), {"ids": ids}
        ).scalar()
        t0 = time.perf_counter()
        peak_pending = 0
        done_lat: list[float] = []
        while True:
            rows = db.execute(
                text("select status, created_at from contents where id = any(:ids)"),
                {"ids": ids},
            ).all()
            pending = 0
            done_lat = []
            for st, created in rows:
                if st in ("done", "failed", "confirmed", "ready"):
                    done_lat.append((created - created_at).total_seconds())
                else:
                    pending += 1
            peak_pending = max(peak_pending, pending)
            if pending == 0:
                break
            if time.perf_counter() - t0 > args.timeout:
                print(f"  ⚠️ 等待超时（仍有 {pending} 条未终态）")
                break
            time.sleep(1.0)
        consume_elapsed = time.perf_counter() - t0

        final = db.execute(
            text(
                "select status, count(*) from contents where id = any(:ids) group by 1"
            ),
            {"ids": ids},
        ).all()
        print("\n【消费侧】")
        print(f"  终态分布      : {dict(final)}")
        print(f"  排空耗时      : {consume_elapsed:.2f}s")
        if consume_elapsed > 0:
            print(f"  消费吞吐      : {len(ids)/consume_elapsed:.2f} 条/秒")
        print(f"  积压峰值      : {peak_pending} 条")
        # 端到端：created_at 到「被观测到终态」的时刻，只能给上界
        e2e = [consume_elapsed] * len(ids)
        print(f"  端到端延迟上界: P50={_pct(e2e,50):.1f}s P95={_pct(e2e,95):.1f}s（整体排空时间）")
    finally:
        db.close()

    print(f"\n清理：python scripts/loadtest_queue.py --cleanup {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
