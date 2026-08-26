"""pytest 公共 fixture（TD-P1C 2026-08-26：测试基建，供新测试使用）

本文件只新增公共 fixture/工厂，**不改动任何存量测试文件**（存量迁移留后续批次）：
  - client              —— FastAPI TestClient
  - auth_headers        —— 登录工厂（参数化 code 前缀），返回 (user_id, headers)
  - make_user           —— 建用户工厂（唯一 phone，prefix 参数化）
  - cleanup_user_data   —— 统一子表清理（对照 schema.sql 完整 FK 依赖链，
                           含 messages / upload_tasks+upload_chunks / 画像域等）
  - vector_collection   —— 把默认 Qdrant collection 指到 test_* 隔离库
                           （测试不再写生产 yishu_contents）
  - db_user             —— 公共测试用户（R8#2：存量手写 db_user 迁移至此，
                           teardown 统一走 cleanup_user_data）
  - _sensitive_words_state —— R8#12：敏感词模块全局热词状态 autouse 快照/恢复
                           （唯一 autouse，防顺序敏感 flaky，为并行化铺路）

注意：除 _sensitive_words_state（R8#12 明确要求 autouse 隔离进程内全局状态）外，
所有 fixture 均为显式请求（无 autouse），避免影响存量测试行为。
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
# 公共测试用户（R8#2：存量 ~15 份手写 db_user fixture 迁移至本公共版本）
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_user(cleanup_user):
    """公共测试用户：建用户 + 返回 (db, user)，teardown 统一 cleanup_user_data。

    原各测试文件手写 db_user 的 teardown 逐表 sa_delete，每加一张子表都要手工同步
    （漏一张就 FK 报错或残留）；R8#2 迁移到本公共版本——teardown 先丢弃未提交写
    （failed 事务/pending ORM 行，防与清理冲突），再 cleanup_user_data 按 user_id
    全链删（30+ 表），最后删用户行。各文件删除本地副本。
    """
    from app.db.models import User
    from app.db.session import SessionLocal

    db = SessionLocal()
    user = User(phone=f"dbu-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.rollback()  # 丢弃用例未提交的写，避免与清理冲突（如 IntegrityError 后 failed 事务）
    cleanup_user(db, user.id)
    db.delete(user)
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# 敏感词模块全局状态隔离（R8#12）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _sensitive_words_state(monkeypatch):
    """R8#12：敏感词模块全局热词状态快照/恢复（消除顺序敏感 flaky）。

    背景（侦察实测）：add_violation_word 会原地变异 lru_cache 内的事件词集合
    （_load_event_words()）与进程级 _EVENT_REFLUX_WORDS；不恢复则跨用例残留，
    test_sensitive_words 顺序/选择敏感（test_hard_rule_independent 首跑失败），
    pytest-xdist 并行会放大随机失败。
    方案：monkeypatch 把进程级回流词集合换成全新 set（用后自动还原原集合）；
    事件词表缓存内容在 teardown 原地重建为快照（lru_cache 引用不变）。
    autouse 幂等无害：不碰敏感词状态的用例零影响。
    """
    import app.services.external.sensitive_words as sw

    # 快照：事件词表缓存内容（add_violation_word 会原地变异其 set）
    event_words = sw._load_event_words()
    event_snapshot = {cat: set(words) for cat, words in event_words.items()}
    # 进程级回流词集合替换为全新 set：monkeypatch 用例结束后自动还原原集合
    monkeypatch.setattr(sw, "_EVENT_REFLUX_WORDS", set())

    yield

    # 事件词表缓存原地重建（保留 lru_cache 引用；删掉快照外的多余类别）
    for cat in list(event_words.keys()):
        if cat not in event_snapshot:
            del event_words[cat]
    for cat, words in event_snapshot.items():
        cur = event_words.setdefault(cat, set())
        cur.clear()
        cur.update(words)


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
