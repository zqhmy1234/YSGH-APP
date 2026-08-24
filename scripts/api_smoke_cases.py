#!/usr/bin/env python3
"""忆述光华 · API 真实用例冒烟（2026-08-24 用户拍板：替代原最小冒烟）

原冒烟只测 healthz/auth/content-create/search 四个最小探针；
本模块按真实用户旅程断言真实行为（TestClient 单进程 + mock 外部 AI，
管线直调 process_content——与 RQ worker 同款代码路径，RQ 传输由 pytest test_queue 覆盖）：

  用例 1  healthz 探针
  用例 2  认证安全：登录签发 token 对；无 token 401；坏 token 401
  用例 3  F2 文字旅程：创建文字 → 管线处理 → 状态 done + 分类回写 → 搜索真实命中
  用例 4  F1 照片旅程（第一波端点）：multipart 上传 3 张（EXIF 拍摄时间）→ 管线 → 状态 done
  用例 5  F8 时间轴：L1 日卡片结构正确（level/title/photo_count/content_count）
  用例 6  去重语义：同用户同感知哈希二次上传 → 409 CONTENT_002

退出码：0 = 全部用例通过；1 = 有失败。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

# Windows 控制台 GBK 兼容（✅/❌ 为 Unicode）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.main import app  # noqa: E402
from app.services.pipeline import process_content  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TEST_PHOTOS = sorted(ROOT.glob(".cowork-temp/test_photos/*.jpg"))[:3]


def _new_client() -> tuple[TestClient, dict]:
    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"smoke-{uuid.uuid4().hex[:8]}", "device_id": "smoke-dev"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["data"]["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def case_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok", body
    assert "env" in body and "mock_external_ai" in body, body


def case_auth_security(client: TestClient) -> None:
    # 无 token → 401
    r = client.get("/api/v1/contents")
    assert r.status_code == 401, r.text
    # 坏 token → 401
    r = client.get("/api/v1/contents", headers={"Authorization": "Bearer bad.token.here"})
    assert r.status_code == 401, r.text
    # 登录签发 token 对（真实 DB + JWT）
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"sec-{uuid.uuid4().hex[:8]}", "device_id": "sec-dev"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["access_token"] and data["refresh_token"], data


def case_text_journey(client: TestClient, headers: dict) -> str:
    """F2：文字创建 → 管线 → done + 分类 → 搜索命中（真实检索链路）"""
    token = uuid.uuid4().hex[:8]
    text = f"冒烟真实用例-{token}-明天记得买咖啡豆"
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": text, "source": "app"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    cid = r.json()["data"]["id"]
    assert r.json()["data"]["status"] == "processing"

    # 管线（worker 同款；mock 模式零费用）
    result = process_content(cid)
    assert result["status"] == "done", result

    # 状态回写 + 分类回写
    r = client.get("/api/v1/contents", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    mine = next((it for it in items if it["id"] == cid), None)
    assert mine is not None, "内容未出现在列表"
    assert mine["status"] == "done", mine
    assert mine["content_class"], f"分类未回写: {mine}"

    # 搜索真实命中（BGE-M3 + Qdrant 真实检索，用户隔离）
    # 查询词含唯一 token（sparse 精确命中）+ limit 放大召回窗口：
    # 避免 Qdrant 累积历史数据后新内容被挤出 top-k（2026-08-24 门禁 flaky）
    r = client.post(
        "/api/v1/search",
        json={"q": f"买咖啡豆 {token}", "limit": 20},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    hits = r.json()["data"].get("hits", [])
    assert any(hit.get("content_id") == cid or hit.get("id") == cid for hit in hits), (
        f"搜索未命中新内容: {hits}"
    )
    return cid


def case_photo_journey(client: TestClient, headers: dict) -> None:
    """F1 第一波：multipart 上传 → 管线 → done（B-BE-1 真实链路）"""
    assert TEST_PHOTOS, "缺少测试照片（先跑 scripts/generate_test_photos.py）"
    cids: list[str] = []
    for f in TEST_PHOTOS:
        taken = f.name.split("_")[2] + "T" + f.name.split("_")[3][:6] + "+08:00"
        meta = json.dumps({"taken_at": taken, "source": "app"})
        r = client.post(
            "/api/v1/contents/upload",
            files={"file": (f.name, f.read_bytes(), "image/jpeg")},
            data={"meta": meta},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["content_type"] == "photo", r.text
        cids.append(r.json()["data"]["id"])
    for cid in cids:
        result = process_content(cid)
        assert result["status"] == "done", result


def case_timeline_structure(client: TestClient, headers: dict) -> None:
    """F8：L1 日卡片结构（level/title/photo_count/content_count）"""
    r = client.get("/api/v1/events/timeline", headers=headers)
    assert r.status_code == 200, r.text
    events = r.json()["data"]
    l1 = [e for e in events if e["level"] == 1]
    assert l1, f"照片上传后应至少 1 张 L1 日卡片: {events}"
    # 照片旅程的 3 张照片应聚入当日卡片（同日并入语义）；同日也可能含文字内容
    photo_cards = [e for e in l1 if e["photo_count"] > 0]
    assert photo_cards, f"应存在含照片的 L1 日卡片: {l1}"
    for e in l1:
        assert e["title"], e
        assert e["content_count"] > 0, e
        assert e["start_time"] and e["end_time"], e


def case_dedup_semantics(client: TestClient, headers: dict) -> None:
    """Q16：同用户同感知哈希 → 409 CONTENT_002（软删过滤语义）"""
    payload = {
        "content_type": "photo",
        "perceptual_hash": f"smoke-dup-{uuid.uuid4().hex[:8]}",
        "cos_key": "photos/smoke.jpg",
        "source": "app",
    }
    r1 = client.post("/api/v1/contents", json=payload, headers=headers)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/v1/contents", json=payload, headers=headers)
    assert r2.status_code == 409, r2.text
    assert r2.json()["code"] == "CONTENT_002", r2.text


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    client, headers = _new_client()

    cases = [
        ("healthz", case_healthz, (client,)),
        ("auth-security", case_auth_security, (client,)),
        ("text-journey", case_text_journey, (client, headers)),
        ("photo-journey", case_photo_journey, (client, headers)),
        ("timeline-structure", case_timeline_structure, (client, headers)),
        ("dedup-409", case_dedup_semantics, (client, headers)),
    ]
    for name, fn, args in cases:
        try:
            fn(*args)
            checks.append((name, True, ""))
        except AssertionError as exc:
            checks.append((name, False, str(exc)[:300]))
        except Exception as exc:  # noqa: BLE001 —— 用例失败需继续跑后续用例
            checks.append((name, False, f"{type(exc).__name__}: {exc}")[:300])

    print("真实用例冒烟:")
    for name, ok, err in checks:
        print(f"  [{'✅' if ok else '❌'}] {name}" + (f" — {err}" if err else ""))
    failed = [n for n, ok, _ in checks if not ok]
    if failed:
        print(f"失败用例: {', '.join(failed)}")
        return 1
    print("全部真实用例通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
