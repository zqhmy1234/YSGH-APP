#!/usr/bin/env python3
"""忆述光华 · 压测种子数据（Agent C1，MVP 收尾 · 并发压测域）

用途：为压测创建**专用隔离用户** loadtest-seed（dev mock 登录通道，复用
api_smoke_cases._new_client 的登录方式）+ 内容数据：
  - 文字内容 80 条（含查询词库中的词，供描述性搜索命中）
  - 照片 20 张（本地生成小图，供以图搜图 image_vec 检索）
  - L1 事件 2~3 轮（events/sync 端侧提交语义，供时间轴聚合查询）

隔离铁律：
  - 压测用户 user_id 独立（mock unionid = mock-unionid-loadtest-seed），
    与真机/冒烟数据隔离；残留可接受（参照 api_smoke 历史数据先例）。
  - 向量写入**生产默认 collection**（yishu_contents，真实搜索路径），
    但全部 payload 带 user_id=loadtest 用户，不污染他人检索。

运行方式（在 scripts/loadtest/ 目录下，且后端服务已启动）：
  python seed_data.py            # 幂等：已造数则跳过（marker 前缀 [loadtest]）
  python seed_data.py --force    # 强制用新用户 loadtest-seed-<ts> 重新造数

说明：种子阶段以 mock 外部 AI 语义运行（process_content 直调，同 api_smoke），
避免造数阶段消耗真实 LLM 费用；向量内容与 mock/真实压测档无关（BGE-M3 本地编码）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

# 造数阶段 mock 外部 AI（零费用；文本分类/照片 caption 均为 mock 语义）
os.environ.setdefault("MOCK_EXTERNAL_AI", "true")
# Windows 控制台 GBK 兼容
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

# 查询词库（与 config.json paths.search.params.query_words 保持一致）
QUERY_WORDS = ["咖啡", "出差", "聚会", "生日", "旅行", "晚饭", "孩子", "爬山", "新手机", "体检"]

# 文字内容模板（真实日记风格，命中查询词）
_TEXT_TEMPLATES = [
    "今天喝了一杯{word}，心情好多了。",
    "上周{word}去了趟市里，见了老朋友。",
    "记一下：周末约了{word}，记得提前准备。",
    "{word}的时候拍了几张照片，回头整理。",
    "晚上跟家人吃了{word}，聊了很久。",
    "最近常想那次{word}的经历，很怀念。",
    "{word}让我想起了小时候的事。",
    "明天有个{word}的安排，要早起。",
    "翻到{word}那天的记录，真是难忘。",
    "朋友推荐了一个{word}的好地方，改天去。",
    "{word}的约定别忘了，定个闹钟。",
    "今天工作到很晚，路过{word}小店买了点东西。",
    "把{word}的事记下来，免得忘记。",
    "上周{word}认识的新朋友今天又联系了。",
    "好久没这么放松了，{word}真是个充电的好方式。",
]

# 照片主题（用于生成可区分的合成小图）
_PHOTO_THEMES = ["咖啡", "出差", "聚会", "生日", "旅行", "晚饭", "爬山", "海边", "公园", "街景"]


def _load_config() -> dict:
    with open(HERE / "config.json", encoding="utf-8") as f:
        return json.load(f)


def make_image(path: Path, size: tuple[int, int], seed: int) -> None:
    """生成确定性合成小图（纯代码零依赖外部素材；numpy 可用则快速生成）"""
    import numpy as np
    from PIL import Image

    rnd = np.random.RandomState(seed)
    h, w = size[1], size[0]
    base = rnd.randint(0, 255, (3,)).astype(np.float32)
    grad = rnd.randint(0, 255, (h, w, 3)).astype(np.float32)
    # 渐变 + 噪声：base 色调向四周渐变，叠加高斯噪声，视觉可区分
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    fade = (yy / max(h, 1)) * 0.4 + (xx / max(w, 1)) * 0.3
    img_arr = (base[None, None, :] * (1 - fade[..., None]) + grad * 0.3 + rnd.normal(0, 18, (h, w, 3)))
    img = Image.fromarray(np.clip(img_arr, 0, 255).astype(np.uint8))
    img.save(path, "JPEG", quality=85)


def ensure_assets() -> dict[str, list[Path]]:
    """确保素材存在：压测照片 20 张 + 以图搜图查询图 3 张（生成则跳过）"""
    photos_dir = ASSETS / "seed_photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    photos: list[Path] = []
    for i in range(1, 21):
        p = photos_dir / f"seed_photo_{i:02d}.jpg"
        if not p.exists():
            make_image(p, (320, 240), seed=1000 + i)
        photos.append(p)
    query: list[Path] = []
    for name in ["q1.jpg", "q2.jpg", "q3.jpg"]:
        p = ASSETS / name
        if not p.exists():
            make_image(p, (256, 192), seed=2000 + int(name[1]))
        query.append(p)
    return {"photos": photos, "query": query}


def login(client: httpx.Client, base_url: str, code: str, device_id: str) -> str:
    r = client.post(
        f"{base_url}/api/v1/auth/wechat",
        json={"code": code, "device_id": device_id},
    )
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def _seed_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _post_content(client: httpx.Client, base_url: str, headers: dict, text: str) -> str:
    r = client.post(
        f"{base_url}/api/v1/contents",
        json={"content_type": "text", "text": text, "source": "app"},
        headers=headers,
    )
    r.raise_for_status()
    return r.json()["data"]["id"]


def _upload_photo(client: httpx.Client, base_url: str, headers: dict, img_path: Path, taken_at: str) -> str:
    meta = json.dumps({"taken_at": taken_at, "source": "app"})
    with open(img_path, "rb") as f:
        data = f.read()
    r = client.post(
        f"{base_url}/api/v1/contents/upload",
        files={"file": (img_path.name, data, "image/jpeg")},
        data={"meta": meta},
        headers=headers,
    )
    r.raise_for_status()
    return r.json()["data"]["id"]


def _already_seeded(client: httpx.Client, base_url: str, headers: dict, cfg: dict) -> bool:
    """已造数判定：只认**处理完成**（status=done）的 marker 内容，避免残留 processing 行误判"""
    r = client.get(f"{base_url}/api/v1/contents?content_type=text&limit=100", headers=headers)
    if r.status_code != 200:
        return False
    marker = cfg["seed"]["marker_prefix"]
    items = r.json()["data"].get("items", [])
    done = [it for it in items if (it.get("text") or "").startswith(marker) and it.get("status") == "done"]
    return len(done) >= cfg["seed"]["text_count"]


def _process(cid: str) -> dict:
    """直调管线（与 api_smoke 同款 worker 代码路径；mock 语义零费用）"""
    from app.services.pipeline import process_content

    return process_content(cid)


def _sync_events(
    client: httpx.Client,
    base_url: str,
    headers: dict,
    photo_ids: list[str],
    title: str,
    start: datetime,
) -> None:
    body = {
        "device_id": "loadtest-dev",
        "events": [
            {
                "client_event_id": f"loadtest-ev-{uuid.uuid4().hex[:10]}",
                "title": title,
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(hours=3)).isoformat(),
                "photo_ids": photo_ids,
            }
        ],
    }
    r = client.post(f"{base_url}/api/v1/events/sync", json=body, headers=headers)
    r.raise_for_status()


def _verify(client: httpx.Client, base_url: str, headers: dict, cfg: dict, word: str = "咖啡") -> None:
    r = client.post(
        f"{base_url}/api/v1/search",
        json={"q": word, "limit": 20},
        headers=headers,
    )
    r.raise_for_status()
    hits = r.json()["data"].get("hits", [])
    if not hits:
        print(f"[verify] ⚠️ 搜索未命中（词={word}）——不影响压测端点测量，但数据可能未就绪")
    else:
        print(f"[verify] 搜索命中 {len(hits)} 条（词={word}）✅")
    r = client.get(f"{base_url}/api/v1/events/timeline?level=1", headers=headers)
    if r.status_code == 200:
        evs = r.json()["data"]
        l1 = [e for e in evs if e.get("level") == 1]
        print(f"[verify] 时间轴 L1 事件 {len(l1)} 条 ✅")
    else:
        print(f"[verify] 时间轴响应 {r.status_code}")


def _wait_for_memory(min_free_gb: float = 2.5, timeout_s: int = 1800) -> bool:
    """内存自守：加载 BGE-M3/SetFit 前等待可用内存 ≥ 阈值（并行 Agent 争抢环境下防 OOM）

    并行开发窗口常有其他 Agent 跑 pytest/api_smoke（各自加载 BGE-M3 ~1.3GB），
    本机 16GB 内存经常吃紧 → 用 psutil.available（含可回收 standby）轮询等待。
    """
    import psutil

    deadline = time.time() + timeout_s
    waited = 0
    while time.time() < deadline:
        avail = psutil.virtual_memory().available / 1024 / 1024 / 1024
        if avail >= min_free_gb:
            if waited > 0:
                print(f"[seed] 内存就绪（available={avail:.2f}GB，等待 {waited:.0f}s）")
            return True
        time.sleep(15)
        waited += 15
    print(f"[seed] ⚠️ 等待内存超时（{timeout_s}s 内未到 {min_free_gb}GB，当前 available={avail:.2f}GB）——继续尝试")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="压测种子数据（隔离用户 + 内容 + 事件）")
    parser.add_argument("--force", action="store_true", help="强制重新造数（新用户 loadtest-seed-<ts>）")
    args = parser.parse_args()

    cfg = _load_config()
    base_url = os.environ.get("LOADTEST_BASE_URL", cfg["base_url"])
    seed_cfg = cfg["seed"]
    code = seed_cfg["login_code"]
    if args.force:
        code = f"loadtest-seed-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        (HERE / "results").mkdir(parents=True, exist_ok=True)
        (HERE / "results" / "last_seed_code.txt").write_text(code, encoding="utf-8")
        print(f"[seed] --force：使用新压测用户 {code}")

    # 健康检查
    with httpx.Client(timeout=10) as client:
        try:
            r = client.get(f"{base_url}/healthz")
            r.raise_for_status()
        except Exception as exc:
            print(f"[seed] ❌ 后端不可达 {base_url}（{exc}）——请先启动 uvicorn")
            return 1

        print(f"[seed] 登录压测用户 {code} ...")
        token = login(client, base_url, code, seed_cfg["device_id"])
        headers = _seed_headers(token)
        print("[seed] 登录成功 ✅")

        if _already_seeded(client, base_url, headers, cfg) and not args.force:
            print("[seed] 已造数（marker 前缀 [loadtest]），跳过；--force 可重新造数")
            return 0

        # 内存自守：管线处理将加载 BGE-M3 + SetFit（~1.8GB），等可用内存足够再加载
        _wait_for_memory(min_free_gb=2.5)

        assets = ensure_assets()
        photos = assets["photos"]
        print(f"[seed] 生成素材：照片 {len(photos)} 张 + 查询图 {len(assets['query'])} 张（{ASSETS}）")

        # 1. 文字内容（含查询词）
        text_ids: list[str] = []
        marker = seed_cfg["marker_prefix"]
        for i in range(seed_cfg["text_count"]):
            word = QUERY_WORDS[i % len(QUERY_WORDS)]
            tpl = _TEXT_TEMPLATES[i % len(_TEXT_TEMPLATES)]
            text = f"{marker} #{i:03d} {tpl.format(word=word)}"
            cid = _post_content(client, base_url, headers, text)
            text_ids.append(cid)
        print(f"[seed] 文字内容创建 {len(text_ids)} 条，开始管线处理...")
        ok = 0
        for cid in text_ids:
            if _process(cid).get("status") == "done":
                ok += 1
        print(f"[seed] 文字管线 done {ok}/{len(text_ids)}")

        # 2. 照片内容（以图搜图数据）
        photo_ids: list[str] = []
        base_t = datetime.now(timezone.utc) - timedelta(days=20)
        for i, p in enumerate(photos):
            taken = (base_t + timedelta(days=i % 6, hours=i)).strftime("%Y-%m-%dT%H:%M:%S+08:00")
            cid = _upload_photo(client, base_url, headers, p, taken)
            photo_ids.append(cid)
        print(f"[seed] 照片上传 {len(photo_ids)} 张，开始管线处理...")
        ok = 0
        for cid in photo_ids:
            if _process(cid).get("status") == "done":
                ok += 1
        print(f"[seed] 照片管线 done {ok}/{len(photo_ids)}")

        # 3. L1 事件（端侧提交语义 → 时间轴数据）
        chunk = 8
        titles = ["与老友聚会", "周末爬山", "出差旅途", "生日小聚", "家庭晚饭"]
        for i in range(0, len(photo_ids), chunk):
            group = photo_ids[i : i + chunk]
            _sync_events(
                client,
                base_url,
                headers,
                group,
                titles[(i // chunk) % len(titles)],
                datetime.now(timezone.utc) - timedelta(days=(i // chunk) + 1, hours=2),
            )
        print(f"[seed] L1 事件提交 {len(photo_ids) // chunk} 轮 ✅")

        # 4. 验证
        _verify(client, base_url, headers, cfg)

    print(f"[seed] 造数完成 ✅（隔离用户 code={code}；残留可接受，参照 api_smoke 先例）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
