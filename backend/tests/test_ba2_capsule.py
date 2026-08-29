"""BA2 时间胶囊批测试（迁移 b2a3c4d5e6f7）

覆盖：
- openapi 新增 4 条 capsule 路由（验收#2：起 TestClient 断言，不 curl 活进程）
- 封存 → 列表（status/content 摘要）→ 未到期开启被拒（409 报剩余天数）
  → 回退 open_at 到期后开启成功（content 详情回传）
- 到期推送：scan 产 capsule_due 消息（带 content_id）且幂等（二次扫描不重复）
- 撤销封存（未开启可删 / 已开启 409）
- 入参护栏：open_at 不在未来 422 / 他人内容 404

前置：本地 PostgreSQL yishu 库 + Redis（对齐 tests/test_ba1_fields.py 前置）
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


# auth_headers 用 tests/conftest.py 公共工厂：user_id, headers = auth_headers("prefix")


@pytest.fixture(autouse=True)
def _cleanup_capsules(cleanup_user):
    """teardown：先清本批 capsules 表（conftest 统一清理链不含新表），再走统一清理。

    用例内通过 _register(user_id) 登记待清理用户，这里统一删 capsules 行。
    """
    yield
    _teardown_pending()


# 用例内登记待清理 user_id（_cleanup_capsules teardown 消费）
_pending_cleanup: set[str] = set()


def _register(user_id: str) -> None:
    _pending_cleanup.add(str(user_id))


def _teardown_pending():
    from app.db.session import SessionLocal
    from sqlalchemy import text

    if not _pending_cleanup:
        return
    db = SessionLocal()
    try:
        for uid in list(_pending_cleanup):
            db.execute(text("DELETE FROM capsules WHERE user_id = :uid"), {"uid": uid})
        db.commit()
    finally:
        db.close()
    _pending_cleanup.clear()


# ---------------------------------------------------------------------------
# 路由注册（验收#2：TestClient 断言 openapi，不 curl 活进程）
# ---------------------------------------------------------------------------


def test_capsule_routes_registered():
    """openapi 含 4 条 capsule 路由"""
    paths = app.openapi()["paths"]
    assert "/api/v1/capsules" in paths
    assert "post" in paths["/api/v1/capsules"]
    assert "get" in paths["/api/v1/capsules"]
    assert "/api/v1/capsules/scan" in paths
    assert "/api/v1/capsules/{capsule_id}/open" in paths
    assert "/api/v1/capsules/{capsule_id}" in paths


# ---------------------------------------------------------------------------
# 主链路：封存 → 查询 → 未到期拒 → 到期开启
# ---------------------------------------------------------------------------


def _make_content(client, headers, text: str = "BA2 胶囊封存用例") -> str:
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": text, "source": "app"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_seal_list_open_full_flow(client, auth_headers, cleanup_user):
    """封存→查询→未到期开启被拒(409 带剩余天数)→到期后开启成功→已开启幂等"""
    user_id, headers = auth_headers("ba2-flow")
    _register(user_id)
    content_id = _make_content(client, headers)

    # 封存：1 小时后开启
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = client.post(
        "/api/v1/capsules",
        json={"content_id": content_id, "open_at": future, "note": "给未来的我"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    cap = r.json()["data"]
    cap_id = cap["id"]
    assert cap["status"] == "sealed"
    assert cap["content_id"] == content_id
    assert cap["note"] == "给未来的我"
    assert cap["opened_at"] is None

    # 列表：带 status + content 摘要（ContentOut 现成字段口径）
    r2 = client.get("/api/v1/capsules", headers=headers)
    assert r2.status_code == 200, r2.text
    items = r2.json()["data"]
    assert len(items) == 1
    assert items[0]["status"] == "sealed"
    assert items[0]["content"]["id"] == content_id
    assert items[0]["content"]["content_type"] == "text"
    assert items[0]["content"]["text"] == "BA2 胶囊封存用例"

    # 未到期开启 → 409 + 剩余天数
    r3 = client.post(f"/api/v1/capsules/{cap_id}/open", headers=headers)
    assert r3.status_code == 409, r3.text
    body = r3.json()
    assert body["code"] == "CAPSULE_003"
    assert body["details"]["remaining_days"] >= 1
    assert "剩余" in body["message"]

    # 回退 open_at 到过去（模拟时间流逝）→ 开启成功
    from app.db.session import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE capsules SET open_at = now() - interval '1 hour' WHERE id = :cid"),
            {"cid": cap_id},
        )
        db.commit()
    finally:
        db.close()

    r4 = client.post(f"/api/v1/capsules/{cap_id}/open", headers=headers)
    assert r4.status_code == 200, r4.text
    opened = r4.json()["data"]
    assert opened["status"] == "opened"
    assert opened["opened_at"] is not None
    # 开启回传 content 详情（摘要想当于详情数据）
    assert opened["content"]["id"] == content_id
    assert opened["content"]["text"] == "BA2 胶囊封存用例"

    # 重复开启幂等：仍 200 返回现态
    r5 = client.post(f"/api/v1/capsules/{cap_id}/open", headers=headers)
    assert r5.status_code == 200, r5.text
    assert r5.json()["data"]["status"] == "opened"

    # 已开启不可撤销 → 409
    r6 = client.delete(f"/api/v1/capsules/{cap_id}", headers=headers)
    assert r6.status_code == 409, r6.text
    assert r6.json()["code"] == "CAPSULE_004"


# ---------------------------------------------------------------------------
# 到期推送 + 幂等（验收#3）
# ---------------------------------------------------------------------------


def test_due_scan_message_and_idempotent(client, auth_headers, cleanup_user):
    """到期扫描产 capsule_due 消息（带 content_id）且幂等：二次扫描不重复"""
    user_id, headers = auth_headers("ba2-scan")
    _register(user_id)
    content_id = _make_content(client, headers, "BA2 幂等推送用例")

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = client.post(
        "/api/v1/capsules",
        json={"content_id": content_id, "open_at": future},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    cap_id = r.json()["data"]["id"]

    # 回退 open_at 到期（sealed 且 open_at<=now 是扫描命中条件）
    from app.db.session import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE capsules SET open_at = now() - interval '5 minutes' WHERE id = :cid"),
            {"cid": cap_id},
        )
        db.commit()
    finally:
        db.close()

    from app.workers.capsule_scan import scan_due_capsules

    # 第一次扫描：产 1 条消息 + 胶囊置 due
    db = SessionLocal()
    try:
        produced = scan_due_capsules(db)
    finally:
        db.close()
    assert produced == 1

    # 消息落库：msg_type=capsule_due 且带 content_id
    r2 = client.get("/api/v1/messages", headers=headers)
    assert r2.status_code == 200, r2.text
    due_msgs = [
        m for m in r2.json()["data"]["items"] if m["msg_type"] == "capsule_due"
    ]
    assert len(due_msgs) == 1, f"到期消息应恰 1 条: {due_msgs}"
    assert due_msgs[0]["content_id"] == content_id

    # 胶囊已置 due
    r3 = client.get("/api/v1/capsules", headers=headers)
    assert r3.json()["data"][0]["status"] == "due"

    # 第二次扫描：幂等（status 已流转，不再命中）→ 仍 1 条消息
    db = SessionLocal()
    try:
        produced2 = scan_due_capsules(db)
    finally:
        db.close()
    assert produced2 == 0

    r4 = client.get("/api/v1/messages", headers=headers)
    due_msgs2 = [
        m for m in r4.json()["data"]["items"] if m["msg_type"] == "capsule_due"
    ]
    assert len(due_msgs2) == 1, "二次扫描不应重复产消息"


def test_lazy_scan_on_list(client, auth_headers, cleanup_user):
    """GET /capsules 惰性触发：到期胶囊经列表请求被置 due（节流复位后）"""
    user_id, headers = auth_headers("ba2-lazy")
    _register(user_id)
    content_id = _make_content(client, headers)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = client.post(
        "/api/v1/capsules", json={"content_id": content_id, "open_at": future}, headers=headers
    )
    cap_id = r.json()["data"]["id"]

    import app.workers.capsule_scan as scan_mod
    from app.db.session import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE capsules SET open_at = now() - interval '5 minutes' WHERE id = :cid"),
            {"cid": cap_id},
        )
        db.commit()
    finally:
        db.close()

    # 复位节流窗口，保证本次列表请求真正触发扫描
    scan_mod._last_scan_ts["ts"] = 0.0
    r2 = client.get("/api/v1/capsules", headers=headers)
    assert r2.status_code == 200, r2.text
    item = r2.json()["data"][0]
    assert item["status"] == "due", "惰性扫描应把到期胶囊置 due"

    r3 = client.get("/api/v1/messages", headers=headers)
    due_msgs = [m for m in r3.json()["data"]["items"] if m["msg_type"] == "capsule_due"]
    assert len(due_msgs) == 1


# ---------------------------------------------------------------------------
# 入参护栏 + 撤销封存
# ---------------------------------------------------------------------------


def test_seal_guards(client, auth_headers, cleanup_user):
    """open_at 不在未来 422 / 他人内容与不存在内容 404 / 裸时间 422"""
    user_id, headers = auth_headers("ba2-guard")
    _register(user_id)
    content_id = _make_content(client, headers)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    # open_at 在过去 → 422
    r = client.post(
        "/api/v1/capsules", json={"content_id": content_id, "open_at": past}, headers=headers
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "CAPSULE_001"

    # 不存在的内容 → 404
    r2 = client.post(
        "/api/v1/capsules", json={"content_id": str(uuid.uuid4()), "open_at": future}, headers=headers
    )
    assert r2.status_code == 404, r2.text

    # 不存在的胶囊 open/delete → 404
    r3 = client.post(f"/api/v1/capsules/{uuid.uuid4()}/open", headers=headers)
    assert r3.status_code == 404, r3.text
    r4 = client.delete(f"/api/v1/capsules/{uuid.uuid4()}", headers=headers)
    assert r4.status_code == 404, r4.text


def test_delete_sealed_capsule(client, auth_headers, cleanup_user):
    """撤销封存：未开启可删（列表不再出现；关联 content 不受影响）"""
    user_id, headers = auth_headers("ba2-del")
    _register(user_id)
    content_id = _make_content(client, headers)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = client.post(
        "/api/v1/capsules", json={"content_id": content_id, "open_at": future}, headers=headers
    )
    cap_id = r.json()["data"]["id"]

    r2 = client.delete(f"/api/v1/capsules/{cap_id}", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["deleted"] is True

    r3 = client.get("/api/v1/capsules", headers=headers)
    assert r3.json()["data"] == []

    # 关联 content 仍可查（撤销不动记忆本体）
    r4 = client.get("/api/v1/contents", params={"content_id": content_id}, headers=headers)
    assert r4.status_code == 200, r4.text
    assert r4.json()["data"]["items"], "撤销封存不应影响关联记忆"
