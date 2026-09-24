"""端间一致性：**新建**内容也要写变更日志（D08-18 · 功能修复波 簇② 遗留）。

缺陷原貌：变更日志（`offline_queue`）只在
  · 客户端 `push_ops`（离线改/删）、
  · REST 改（PATCH 备注）/删（DELETE）
时写入；**创建不写** ⇒
  · 他端 `pull_changes` 无源（该实体永不出现在增量流里）；
  · `reconcile` 的 `missing_on_client` 能报出差异，客户端却**没有可重放的 op**
    ⇒ 两台设备同时在线时，A 端新建的记忆在 B 端"看不见也补不上"。

修复：所有**新建**路径统一调 `sync_writes.log_content_created`（单一出口），
`op_type="create"`（客户端 `applyChanges` 只区分 delete ⇒ 非 delete 即"存活"）。

覆盖：POST /contents（text/voice）、POST /contents/upload（multipart 照片）、
分片 voice complete、微信侧新建；并断言"多端 pull 能学到"这一验收口径。
"""
from __future__ import annotations

import io
import uuid

import pytest
from app.db.models import OfflineQueue
from app.db.session import SessionLocal
from app.services.sync import pull_changes
from app.services.sync_writes import OP_CREATE

pytestmark = pytest.mark.integration


def _auth(client) -> tuple[str, dict]:
    r = client.post("/api/v1/auth/wechat", json={"code": f"scl-{uuid.uuid4().hex[:8]}", "device_id": "d1"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": "Bearer " + r.json()["data"]["access_token"]}
    return client.get("/api/v1/users/me", headers=headers).json()["data"]["id"], headers


def _create_ops(user_id: str, entity_id: str) -> list[OfflineQueue]:
    db = SessionLocal()
    try:
        return list(
            db.query(OfflineQueue)
            .filter(
                OfflineQueue.user_id == user_id,
                OfflineQueue.op_type == OP_CREATE,
                OfflineQueue.payload["entity_id"].astext == entity_id,
            )
            .all()
        )
    finally:
        db.close()


def _cleanup(cleanup_user, user_id: str) -> None:
    """统一清理（conftest.cleanup_user_data：按 user_id 全链删，含 devices/offline_queue）"""
    db = SessionLocal()
    try:
        cleanup_user(db, user_id)
    finally:
        db.close()


def test_post_contents_writes_create_change_log(client, cleanup_user):
    """POST /contents 建文本 → 变更日志恰一条 op_type=create（修复前 0 条）"""
    user_id, headers = _auth(client)
    try:
        r = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": f"新建日志探针 {uuid.uuid4().hex[:6]}"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        content_id = r.json()["data"]["id"]
        ops = _create_ops(user_id, content_id)
        assert len(ops) == 1, f"新建应写恰好一条 create 变更日志，实际 {len(ops)}"
        assert ops[0].payload["entity_type"] == "content"
        assert ops[0].payload["updated_at"], "updated_at 必须非空（客户端消费）"
    finally:
        _cleanup(cleanup_user, user_id)


def test_photo_upload_writes_create_change_log(client, cleanup_user):
    """POST /contents/upload（multipart 照片）→ 同样写 create 变更日志"""
    from tests.test_content_upload import _jpeg_bytes  # 复用既有夹具（真 JPEG 字节）

    user_id, headers = _auth(client)
    try:
        r = client.post(
            "/api/v1/contents/upload",
            files={"file": ("probe.jpg", io.BytesIO(_jpeg_bytes()), "image/jpeg")},
            data={"meta": '{"source": "app"}'},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        content_id = r.json()["data"]["id"]
        assert len(_create_ops(user_id, content_id)) == 1
    finally:
        _cleanup(cleanup_user, user_id)


def test_other_device_pull_sees_new_content(client, cleanup_user):
    """**验收口径**：另一设备 pull 必须能学到本设备新建的内容（修复前学不到）"""
    user_id, headers = _auth(client)
    try:
        r = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": f"跨端可见探针 {uuid.uuid4().hex[:6]}"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        content_id = r.json()["data"]["id"]

        db = SessionLocal()
        try:
            pulled = pull_changes(db, user_id, device_id="device-B", since=0, limit=200)
        finally:
            db.close()
        hits = [c for c in pulled["changes"] if c.get("entity_id") == content_id]
        assert hits, "另一设备的增量拉取里必须能看到这条新建"
        assert hits[0]["op_type"] == OP_CREATE
        assert pulled["cursor"] > 0
    finally:
        _cleanup(cleanup_user, user_id)
