"""pytest 公共 fixture（TD-P1C 2026-08-26：测试基建，供新测试使用）

本文件只新增公共 fixture/工厂，**不改动任何存量测试文件**（存量迁移留后续批次）：
  - client              —— FastAPI TestClient
  - auth_headers        —— 登录工厂（参数化 code 前缀），返回 (user_id, headers)
  - make_user           —— 建用户工厂（唯一 phone，prefix 参数化）
  - cleanup_user_data   —— 统一子表清理（对照 schema.sql 完整 FK 依赖链，
                           含 messages / upload_tasks+upload_chunks / 画像域等）
  - vector_collection   —— 把默认 Qdrant collection 指到 test_* 隔离库
                           （测试不再写生产 yishu_contents）

注意：所有 fixture 均为显式请求（无 autouse），避免影响存量测试行为。
"""
from __future__ import annotations

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sa_text

logger = logging.getLogger("yishu.tests.conftest")

# ---------------------------------------------------------------------------
# client / 认证
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """FastAPI TestClient（app 进程内，单测/集成通用）"""
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    """登录工厂：返回 (user_id, headers)。

    用法：user_id, headers = auth_headers("prefix") —— code 前缀参数化
    （不同前缀在微信 mock 登录下生成独立用户，避免测试间共享用户状态）。
    """

    def _factory(prefix: str = "t"):
        code = f"{prefix}-{uuid.uuid4().hex[:10]}"
        r = client.post(
            "/api/v1/auth/wechat",
            json={"code": code, "device_id": f"{prefix}-dev"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        user_id = data["user"]["id"]
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        return user_id, headers

    return _factory


# ---------------------------------------------------------------------------
# 建用户 / 统一清理
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_user():
    """建用户工厂：make_user(db, prefix) -> User（phone 唯一化）。

    不自动清理——测试自行在 teardown 调 cleanup_user_data(db, user.id)，
    或把 db 交给带自动清理的用例自行管理。
    """

    def _factory(db, prefix: str = "t"):
        from app.db.models import User

        user = User(phone=f"{prefix}-{uuid.uuid4().hex[:10]}", status=1)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _factory


def cleanup_user_data(db, user_id: str) -> list[str]:
    """统一子表清理：按测试用户删除全部关联数据（FK 依赖链，子表优先）。

    对照 backend/sql/schema.sql 完整依赖链：upload_chunks → upload_tasks →
    tags/content_tags/voice_segments/event_tags/event_items/events/event_edit_log →
    contents → 画像域/同步域/消息域/纠错域/设备域等。
    每个表独立 try（缺失表不阻断），失败仅告警——尽力而为。
    """
    uid = str(user_id)
    sqls: list[tuple[str, str]] = [
        (
            "upload_chunks",
            "DELETE FROM upload_chunks WHERE upload_id IN "
            "(SELECT id FROM upload_tasks WHERE user_id = :uid)",
        ),
        ("upload_tasks", "DELETE FROM upload_tasks WHERE user_id = :uid"),
        (
            "voice_segments",
            "DELETE FROM voice_segments WHERE content_id IN "
            "(SELECT id FROM contents WHERE user_id = :uid)",
        ),
        (
            "content_tags",
            "DELETE FROM content_tags WHERE content_id IN "
            "(SELECT id FROM contents WHERE user_id = :uid)",
        ),
        (
            "content_tags(tag)",
            "DELETE FROM content_tags WHERE tag_id IN "
            "(SELECT id FROM tags WHERE user_id = :uid)",
        ),
        (
            "event_tags",
            "DELETE FROM event_tags WHERE event_id IN "
            "(SELECT id FROM events WHERE user_id = :uid)",
        ),
        (
            "event_tags(tag)",
            "DELETE FROM event_tags WHERE tag_id IN "
            "(SELECT id FROM tags WHERE user_id = :uid)",
        ),
        ("tags", "DELETE FROM tags WHERE user_id = :uid"),
        ("event_edit_log", "DELETE FROM event_edit_log WHERE user_id = :uid"),
        (
            "event_items",
            "DELETE FROM event_items WHERE event_id IN "
            "(SELECT id FROM events WHERE user_id = :uid)"
            " OR content_id IN (SELECT id FROM contents WHERE user_id = :uid)",
        ),
        ("events", "DELETE FROM events WHERE user_id = :uid"),
        ("question_history", "DELETE FROM question_history WHERE user_id = :uid"),
        (
            "correction_log",
            "DELETE FROM correction_log WHERE user_id = :uid"
            " OR content_id IN (SELECT id FROM contents WHERE user_id = :uid)",
        ),
        ("echo_history", "DELETE FROM echo_history WHERE user_id = :uid"),
        ("messages", "DELETE FROM messages WHERE user_id = :uid"),
        ("contents", "DELETE FROM contents WHERE user_id = :uid"),
        ("offline_queue", "DELETE FROM offline_queue WHERE user_id = :uid"),
        ("sync_state", "DELETE FROM sync_state WHERE user_id = :uid"),
        ("sync_field_versions", "DELETE FROM sync_field_versions WHERE user_id = :uid"),
        ("deleted_logs", "DELETE FROM deleted_logs WHERE deleted_by = :uid"),
        ("wechat_messages", "DELETE FROM wechat_messages WHERE user_id = :uid"),
        ("profile_annotation_pool", "DELETE FROM profile_annotation_pool WHERE user_id = :uid"),
        ("profile_sensitive", "DELETE FROM profile_sensitive WHERE user_id = :uid"),
        ("profile_l2_evidence", "DELETE FROM profile_l2_evidence WHERE user_id = :uid"),
        ("profile_dimension_pending", "DELETE FROM profile_dimension_pending WHERE user_id = :uid"),
        ("profile_dimension_history", "DELETE FROM profile_dimension_history WHERE user_id = :uid"),
        ("user_profile", "DELETE FROM user_profile WHERE user_id = :uid"),
        ("sensitive_words", "DELETE FROM sensitive_words WHERE user_id = :uid"),
        ("guardrail_logs", "DELETE FROM guardrail_logs WHERE user_id = :uid"),
        ("ai_request_logs", "DELETE FROM ai_request_logs WHERE user_id = :uid"),
        ("audit_log", "DELETE FROM audit_log WHERE user_id = :uid"),
        ("app_settings", "DELETE FROM app_settings WHERE user_id = :uid"),
        ("devices", "DELETE FROM devices WHERE user_id = :uid"),
        ("user_wechat_bindings", "DELETE FROM user_wechat_bindings WHERE user_id = :uid"),
    ]
    cleaned: list[str] = []
    for name, sql in sqls:
        # SAVEPOINT（begin_nested）：单表删除失败仅回滚本语句，
        # 不连带丢弃此前已执行的删除（尽力而为，缺失表不阻断）。
        try:
            with db.begin_nested():
                db.execute(sa_text(sql), {"uid": uid})
            cleaned.append(name)
        except Exception as exc:  # noqa: BLE001 —— 单表清理失败不阻断其余
            logger.warning("清理 %s 失败（跳过）: %s", name, exc)
    db.commit()
    return cleaned


@pytest.fixture()
def cleanup_user():
    """返回统一清理函数 cleanup_user_data（供 teardown 调用）"""
    return cleanup_user_data


# ---------------------------------------------------------------------------
# 向量库测试 collection 隔离（TD-P1C）
# ---------------------------------------------------------------------------


@pytest.fixture()
def vector_collection(monkeypatch):
    """默认 Qdrant collection 指到 test_* 隔离库。

    请求本 fixture 的测试，其 pipeline 索引/搜索默认写 test_ 前缀集合，
    不污染生产 yishu_contents（TD-P1C 高危项）。
    """
    from app.services.vector_store import test_collection_name

    name = test_collection_name()
    monkeypatch.setenv("QDRANT_COLLECTION", name)
    return name
