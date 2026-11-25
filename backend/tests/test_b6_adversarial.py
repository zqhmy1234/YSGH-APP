"""B6 信任域 · 审判级对抗性集成测试（2026-11-24）。

与 test_b6_delivery_gate.py（19 个 happy-path 用例）互补：本文件专攻刁钻边界，
真实落 PG 行、走 service 或 API，目标是**发现隐藏 bug**，不走过场。

标 ``[NAIL]`` 的用例故意按「产品应有行为」写断言，初始即允许 FAIL：
每条 docstring 写明它钉住的实现缺口与代码位置（与 test_echo_delivery_gate
的两个钉子用例同型）。其余用例为回归闸门，必须保持绿。

注：本文件编写期间源码被并行 agent 持续修复（vault/consent/export/deps 等），
首轮运行 24 个 NAIL FAIL 已逐项定位；后续 NAIL 转绿即证明修复生效，
docstring 保留原始问题描述作为回归依据。

覆盖：令牌重放/跨用户/非 ASCII 令牌、并发锁定、撤回后重授、重复审计、
审计事务边界、分页 cursor 与 vault 过滤交织、导出媒体链接/ID 泄露、
注销恢复后 B6 表状态、未知 scope 注入、SQL 注入形状 content_id、
超长 note、时间边界、全端点错误信封统一。
"""
from __future__ import annotations

import base64
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.api import deps
from app.db.models import (
    AuditLog,
    ConsentGrant,
    Content,
    CorrectionLog,
    Event,
    EventItem,
    VaultEntry,
)
from app.db.session import SessionLocal
from app.main import app
from app.services import consent as consent_svc
from app.services import vault as vault_svc
from app.services.audit import AUDIT_ACTIONS, list_audit
from app.services.export import build_export_payload, encode_cursor

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("profile_crypto_keys")]

LOCAL_TZ = timezone(timedelta(hours=8))


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """每个用例结束清掉 get_current_user override，杜绝跨用例串号。"""
    yield
    app.dependency_overrides.clear()


def _client(user):
    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    return client


def _content(db, user_id, text: str = "一段记忆", **kw) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=str(user_id),
        content_type=kw.pop("content_type", "text"),
        text=text,
        taken_at=datetime.now(LOCAL_TZ) - timedelta(days=365),
        sensitive_status="正常",
        status="done",
        **kw,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _audit_count(db, user_id, action: str) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.user_id == str(user_id), AuditLog.action == action
            )
        ).scalar()
        or 0
    )


def _fresh_count(model, user_id, **filters) -> int:
    """用独立 Session 查 committed 状态，绕开当前 session 的脏 identity map。"""
    s = SessionLocal()
    try:
        q = select(func.count()).select_from(model).where(model.user_id == str(user_id))
        for k, v in filters.items():
            q = q.where(getattr(model, k) == v)
        return int(s.execute(q).scalar() or 0)
    finally:
        s.close()


def _envelope(body: dict) -> set[str]:
    return set(body.keys())


ERROR_KEYS = {"code", "message", "request_id", "details"}


# =========================================================================== #
# 1. 解锁令牌：重放 / 跨用户 / 畸形字符
# =========================================================================== #
@pytest.mark.parametrize("prior_cycles", [1, 2, 3])
def test_unlock_token_replay_after_relock_must_fail(db_user, prior_cycles):
    """[NAIL] 令牌重放：解锁后重新上锁，旧令牌必须失效。

    暴露问题：app/services/vault.py:28-32 ``_unlock_token`` 是
    HMAC(user_id + content_id) 的确定性值，无 nonce / 无签发轮次 / 无过期。
    一旦令牌泄露（日志、抓包、浏览器历史），用户在任意时间重新锁同一条内容后
    旧令牌依然能开——「重新上锁」的安全语义被架空。产品应有行为：每次 lock
    轮次生成新令牌或令牌带可校验的 nonce/有效期。
    """
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    vault_svc.lock_content(db, uid, c.id)
    first_token = vault_svc.unlock_token_for(db, uid, c.id)
    cur = first_token
    for _ in range(prior_cycles):
        assert vault_svc.unlock_content(db, uid, c.id, cur)["ok"] is True
        vault_svc.lock_content(db, uid, c.id)
        cur = vault_svc.unlock_token_for(db, uid, c.id)
    # 首轮签发的令牌被留用到后续轮次：必须失效
    res = vault_svc.unlock_content(db, uid, c.id, first_token)
    assert res["ok"] is False, (
        f"解锁令牌在 {prior_cycles} 次轮次后重放仍成功（token 与 user/content 绑定的确定性 HMAC，"
        "无轮次/过期语义）；vault.py:28-32"
    )


def test_cross_user_token_cannot_unlock_other_users_vault(db_user, make_user):
    """跨用户令牌隔离：A 的令牌不能开 B 的锁，B 的条目保持锁定。"""
    db, user = db_user
    other = make_user(db, "b6-adv-cross-token")
    try:
        uid, oid = str(user.id), str(other.id)
        mine, foreign = _content(db, uid, "我的"), _content(db, oid, "别人的")
        vault_svc.lock_content(db, uid, mine.id)
        vault_svc.lock_content(db, oid, foreign.id)
        token_a = vault_svc.unlock_token_for(db, uid, mine.id)
        res = vault_svc.unlock_content(db, oid, foreign.id, token_a)
        assert res == {"ok": False, "reason": "wrong_key"}
        assert foreign.id in vault_svc.locked_content_ids(db, oid)
    finally:
        db.execute(Content.__table__.delete().where(Content.user_id == str(other.id)))
        db.execute(VaultEntry.__table__.delete().where(VaultEntry.user_id == str(other.id)))
        db.execute(AuditLog.__table__.delete().where(AuditLog.user_id == str(other.id)))
        db.delete(other)
        db.commit()


def test_cross_user_cannot_unlock_foreign_content_with_own_token(db_user, make_user):
    """跨用户内容 ID：B 拿自己内容的令牌去开 A 的内容，必须失败且不触碰 A 的锁。"""
    db, user = db_user
    other = make_user(db, "b6-adv-cross-content")
    try:
        uid, oid = str(user.id), str(other.id)
        mine, foreign = _content(db, uid), _content(db, oid, "A 的内容")
        vault_svc.lock_content(db, uid, mine.id)
        vault_svc.lock_content(db, oid, foreign.id)
        token_b = vault_svc.unlock_token_for(db, uid, mine.id)
        res = vault_svc.unlock_content(db, uid, foreign.id, token_b)
        assert res["ok"] is False
        assert foreign.id in vault_svc.locked_content_ids(db, oid)
        assert mine.id in vault_svc.locked_content_ids(db, uid)
    finally:
        db.execute(Content.__table__.delete().where(Content.user_id == str(other.id)))
        db.execute(VaultEntry.__table__.delete().where(VaultEntry.user_id == str(other.id)))
        db.execute(AuditLog.__table__.delete().where(AuditLog.user_id == str(other.id)))
        db.delete(other)
        db.commit()


@pytest.mark.parametrize("bad_key", ["", "0" * 64, "A" * 64, "a" * 63, "a" * 65])
def test_ascii_wrong_key_variants_fail_closed(db_user, bad_key):
    """ASCII 错键（空/长度错/大小写错）统一 wrong_key，内容仍在锁中。"""
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    vault_svc.lock_content(db, uid, c.id)
    res = vault_svc.unlock_content(db, uid, c.id, bad_key)
    assert res == {"ok": False, "reason": "wrong_key"}
    assert c.id in vault_svc.locked_content_ids(db, uid)
    assert _audit_count(db, uid, "vault_unlock") == 0


@pytest.mark.parametrize("bad_key", ["中文令牌", "🔑"])
def test_non_ascii_key_must_return_wrong_key_not_500(db_user, bad_key):
    """[NAIL] 非 ASCII 令牌必须走业务失败分支，不能抛 TypeError。

    暴露问题：app/services/vault.py:70 ``hmac.compare_digest(key, expected)``
    对含非 ASCII 字符的 str 直接抛 TypeError
    （comparing strings with non-ASCII characters is not supported）。
    API 层没有捕获 → 500 INTERNAL_ERROR，攻击者可用一个中文字符打穿错误语义，
    且审计/限流/监控都把它当服务端故障而非「猜错key」。产品应有行为：400 wrong_key。
    """
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    vault_svc.lock_content(db, uid, c.id)
    res = vault_svc.unlock_content(db, uid, c.id, bad_key)
    assert res["ok"] is False, f"非 ASCII key {bad_key!r} 触发异常而非业务失败；vault.py:70"


def test_token_endpoint_never_issues_token_for_foreign_content(db_user, make_user):
    """取令牌端点也做 IDOR：不能给别人的 content_id 下发任何令牌。"""
    db, user = db_user
    other = make_user(db, "b6-adv-token-idor")
    try:
        foreign = _content(db, str(other.id), "别人的")
        c = _client(user)
        r = c.get(f"/api/v1/trust/vault/{foreign.id}/token")
        assert r.status_code == 404
        assert _envelope(r.json()) == ERROR_KEYS
        assert "key" not in r.text
    finally:
        db.execute(Content.__table__.delete().where(Content.user_id == str(other.id)))
        db.execute(AuditLog.__table__.delete().where(AuditLog.user_id == str(other.id)))
        db.delete(other)
        db.commit()


# =========================================================================== #
# 2. 并发锁定
# =========================================================================== #
def test_concurrent_lock_same_content_is_idempotent(db_user):
    """[NAIL] 两个 Session 同时锁同一条内容，应幂等收敛为一行且不报错。

    暴露问题：app/services/vault.py:47-60 是「先 SELECT 再 INSERT」的非原子
    check-then-act；唯一约束 uq_vault_entry_user_content 在 flush 时才兜底抛
    IntegrityError，调用方看到 500 而非「已锁」。lock_content 文档承诺幂等，
    并发下承诺失效。产品应有行为：INSERT ... ON CONFLICT DO NOTHING 或捕获
    IntegrityError 后回读既有行。
    """
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    barrier = threading.Barrier(2)
    engine = db.get_bind()
    errors: list[Exception] = []

    @event.listens_for(engine, "after_execute")
    def _sync_vault_select(conn, cursor, statement, parameters, context, executemany):
        if not isinstance(statement, str):  # executemany 时 SQLAlchemy 传 list
            return
        head = statement.lstrip().upper()
        if head.startswith("SELECT") and "vault_entry" in statement:
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

    s2 = SessionLocal()
    try:
        def work(session):
            try:
                vault_svc.lock_content(session, uid, c.id)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(s,)) for s in (db, s2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        event.remove(engine, "after_execute", _sync_vault_select)
        s2.close()

    assert not errors, (
        f"并发 lock 抛异常（{type(errors[0]).__name__}: {errors[0]!s:.160}），幂等承诺在竞态下失效；"
        "vault.py:47-60"
    )
    assert _fresh_count(VaultEntry, uid, content_id=c.id) == 1


def test_sequential_double_unlock_has_no_second_audit(db_user):
    """顺序重复解锁：第二次 not_locked，不写第二条审计。"""
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    vault_svc.lock_content(db, uid, c.id)
    key = vault_svc.unlock_token_for(db, uid, c.id)
    assert vault_svc.unlock_content(db, uid, c.id, key)["ok"] is True
    assert vault_svc.unlock_content(db, uid, c.id, key) == {"ok": False, "reason": "not_locked"}
    assert _audit_count(db, uid, "vault_unlock") == 1


# =========================================================================== #
# 3. 撤回后重新授予 / 重复审计
# =========================================================================== #
@pytest.mark.parametrize("scope", sorted(consent_svc.VALID_SCOPES))
def test_regrant_lifecycle_all_scopes(db_user, scope):
    """5 个 scope 各自走 grant → revoke → re-grant，状态与时间戳语义一致。"""
    db, user = db_user
    uid = str(user.id)
    first = consent_svc.set_consent(db, uid, scope, True)
    assert first["state"] == "granted" and first["granted"] is True
    row_id = db.execute(
        select(ConsentGrant).where(ConsentGrant.user_id == uid, ConsentGrant.scope == scope)
    ).scalar_one().id
    consent_svc.set_consent(db, uid, scope, False)
    assert not consent_svc.is_granted(db, uid, scope)
    again = consent_svc.set_consent(db, uid, scope, True)
    assert again["granted"] is True
    assert consent_svc.get_consent(db, uid, scope) == "ok"
    row = db.get(ConsentGrant, row_id)
    assert row.revoked_at is None, "重新授予必须清掉 revoked_at"
    assert row.granted_at is not None
    assert len(db.execute(select(ConsentGrant).where(ConsentGrant.user_id == uid)).scalars().all()) == 1


@pytest.mark.parametrize("scope", ["model_training", "family_share"])
def test_repeated_grant_no_duplicate_audit(db_user, scope):
    """[NAIL] 同一用途重复授予（状态无变化）不应重复写审计。

    暴露问题：app/services/consent.py:95-110 每次调用无条件 record_audit，
    即使 row.state 已是 granted。用户点两次勾选（前端重试/网络重发）就在
    「我的数据活动」里留下两条同义记录，审计Noise淹没真正变更。产品应有行为：
    状态未变时不写新审计（幂等变更日志）。
    """
    db, user = db_user
    uid = str(user.id)
    for _ in range(3):
        consent_svc.set_consent(db, uid, scope, True)
    n = _audit_count(db, uid, "consent_grant")
    assert n == 1, f"重复授予产生 {n} 条 consent_grant 审计（consent.py:104），应为 1"


def test_repeated_revoke_no_duplicate_audit(db_user):
    """[NAIL] 同一用途重复撤回（状态已是 revoked）不应重复写审计。

    暴露问题同 test_repeated_grant_no_duplicate_audit：consent.py:99-110
    不比较前后状态；重复点「关闭」会刷出多条 consent_revoke。
    """
    db, user = db_user
    uid = str(user.id)
    consent_svc.set_consent(db, uid, "research_analysis", True)
    for _ in range(3):
        consent_svc.set_consent(db, uid, "research_analysis", False)
    n = _audit_count(db, uid, "consent_revoke")
    assert n == 1, f"重复撤回产生 {n} 条 consent_revoke 审计（consent.py:104），应为 1"


# =========================================================================== #
# 4. 审计事务边界
# =========================================================================== #
def test_audit_failure_must_rollback_consent(db_user, monkeypatch):
    """[NAIL] 审计写失败时，主操作（授权变更）不得落库。

    暴露问题：app/services/consent.py:103-111 先 flush 主行再 record_audit，
    但异常路径没有 db.rollback()。上层若按常规在 finally 里 commit
    （或同一 Session 还有别的操作），就会提交一条**没有审计的授权变更**——
    安全审计可被「让审计存储报错」绕过。产品应有行为：审计与主操作同一事务，
    失败整体回滚。
    """
    db, user = db_user
    uid = str(user.id)

    def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(consent_svc, "record_audit", boom)
    with pytest.raises(RuntimeError):
        consent_svc.set_consent(db, uid, "model_training", True)
    # 调用方继续提交（模拟未显式 rollback 的常规用法）
    db.commit()
    leaked = _fresh_count(ConsentGrant, uid, scope="model_training")
    assert leaked == 0, "审计失败后授权变更仍被提交，审计可绕过；consent.py:103-111"


def test_audit_failure_must_rollback_vault_lock(db_user, monkeypatch):
    """[NAIL] 审计失败时保险箱锁定不得落库（同事务边界）。

    暴露问题：app/services/vault.py:55-60 flush VaultEntry 后写审计；
    record_audit 抛错时无 rollback，调用方 commit 就得到「锁了但无审计」的幽灵条目。
    """
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)

    def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(vault_svc, "record_audit", boom)
    with pytest.raises(RuntimeError):
        vault_svc.lock_content(db, uid, c.id)
    db.commit()
    leaked = _fresh_count(VaultEntry, uid, content_id=c.id)
    assert leaked == 0, "审计失败后锁定条目仍被提交，审计可绕过；vault.py:55-60"


def test_audit_failure_must_rollback_vault_unlock(db_user, monkeypatch):
    """[NAIL] 审计失败时解锁不得生效（unlocked_at 不得被提交）。

    暴露问题：app/services/vault.py:79-87 先把 unlocked_at 写脏并 flush，
    再 record_audit；审计抛错后无 rollback，调用方 commit 会把内容「无审计解锁」，
    且 entry 仍在（unlocked_at 非空）导致 locked_content_ids 误判为已解锁。
    """
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    vault_svc.lock_content(db, uid, c.id)
    key = vault_svc.unlock_token_for(db, uid, c.id)

    def boom(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(vault_svc, "record_audit", boom)
    with pytest.raises(RuntimeError):
        vault_svc.unlock_content(db, uid, c.id, key)
    db.commit()
    s = SessionLocal()
    try:
        row = s.execute(
            select(VaultEntry).where(VaultEntry.user_id == uid, VaultEntry.content_id == c.id)
        ).scalar_one_or_none()
    finally:
        s.close()
    assert row is not None and row.unlocked_at is None, (
        "审计失败后 unlocked_at 仍被提交（无审计解锁）；vault.py:79-87"
    )


def test_audit_success_is_committed_with_operation(db_user):
    """成功路径：授权变更与对应审计都可在独立 Session 查到。"""
    db, user = db_user
    uid = str(user.id)
    consent_svc.set_consent(db, uid, "personalization", True)
    assert _fresh_count(ConsentGrant, uid, scope="personalization") == 1
    assert _fresh_count(AuditLog, uid, action="consent_grant") == 1


def test_export_api_must_persist_audit(db_user):
    """[NAIL] 导出是关键数据动作，API 调用后审计必须真实落库。

    暴露问题：app/api/export.py:101 只 record_audit（flush），之后从未 commit；
    app/db/session.py:38-43 get_db 只 close() 不 commit → 请求结束事务回滚，
    export_requested 审计**永远不落库**（AUDIT_ACTIONS 白名单里写了却没生效）。
    实测 GET /api/v1/export 返回 200 但 audit_log 零行。产品应有行为：留痕可查。
    """
    db, user = db_user
    uid = str(user.id)
    c = _client(user)
    r = c.get("/api/v1/export")
    assert r.status_code == 200, r.text[:200]
    n = _fresh_count(AuditLog, uid, action="export_requested")
    assert n == 1, f"导出 API 成功后审计行数为 {n}（export.py:101 无 commit）；应为 1"


# =========================================================================== #
# 5. 分页 cursor × vault 过滤
# =========================================================================== #
@pytest.mark.parametrize(
    "cursor",
    [
        "!!!not-base64!!!",
        base64.urlsafe_b64encode(b"[]").decode(),
        base64.urlsafe_b64encode(b'{"contents": 123}').decode(),
        base64.urlsafe_b64encode(b'{"contents": null}').decode(),
    ],
)
def test_invalid_cursor_shapes_return_422(db_user, cursor):
    """结构非法的 cursor 必须 422 VALIDATION_ERROR，不能静默从头导出。"""
    db, user = db_user
    r = _client(user).get("/api/v1/export", params={"cursor": cursor})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    # 错误信封缺 details 的问题由 test_error_envelope_uniform[export_bad_cursor] 单独钉住


@pytest.mark.parametrize("evil", ["' OR 1=1 --", "x' UNION SELECT NULL--"])
def test_invalid_cursor_id_shape_must_not_500(db_user, evil):
    """[NAIL] cursor 内 id 形状非法应 422，而不是把注入形状交给 PG 转 UUID。

    暴露问题：app/services/export.py:161-167 ``decode_cursor`` 只校验 value 是
    str，不校验它是否是合法 id；app/api/export.py:111-118 只捕获 ValueError。
    形如 ``{"contents": "' OR 1=1 --"}`` 的游标通过校验后，_collect_capped 的
    ``key > last_key``（export.py:54）让 PG 抛 InvalidTextRepresentation →
    500 INTERNAL_ERROR。参数本身是客户端可控的，属于可被利用的错误语义/DoS 面。
    """
    db, user = db_user
    cursor = encode_cursor({"contents": evil})
    r = _client(user).get("/api/v1/export", params={"cursor": cursor})
    assert r.status_code == 422, (
        f"游标中非法 id {evil!r} 触发 {r.status_code} 而非 422；export.py:161-167 / 54"
    )


@pytest.mark.parametrize("locked_pos", [0, 1, 2])
def test_pagination_with_vault_filter_no_loss_no_duplicate(db_user, monkeypatch, locked_pos):
    """cursor 分页与保险箱过滤交织：不漏、不重、不死循环。

    锁定条目位于首/中/尾三处，逐页拉全量；导出集合应恰好等于未锁定集合。
    """
    import app.services.export as export_svc

    db, user = db_user
    uid = str(user.id)
    monkeypatch.setattr(export_svc, "EXPORT_MAX_PER_TYPE", 2)
    contents = [_content(db, uid, f"memo-{i}") for i in range(5)]
    locked = contents[locked_pos]
    vault_svc.lock_content(db, uid, locked.id)

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        payload = build_export_payload(db, uid, cursor)
        seen.extend(c["content_id"] for c in payload["contents"])
        if not payload.get("truncated"):
            break
        cursor = payload["next_cursor"]
        assert cursor, "truncated=true 必须带 next_cursor"
    else:
        pytest.fail("分页未终止")

    assert len(seen) == len(set(seen)), "分页重复导出同一条"
    assert set(seen) == {c.id for c in contents if c.id != locked.id}, "锁定条目泄漏或未锁条目丢失"


def test_locked_content_leaks_via_events_and_corrections(db_user):
    """[NAIL] 锁住的内容 ID 不应出现在导出包的任何字段，而非只在 contents。

    暴露问题：app/services/export.py:268 仅对 ``contents`` 应用 vault 过滤；
    ``events[].cover_content_id``（export.py:107）、``events[].item_content_ids``
    （export.py:120）、``corrections[].content_id``（export.py:129）仍原样导出。
    保险箱文档（vault.py:5-7）承诺「检索/回响/导出/共享四处必须过滤」，
    实际导出只过滤了一处——锁定内容的存在性与关联关系仍被暴露。
    """
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid, "锁住的回忆", content_type="photo", cos_key="photos/secret.jpg")
    ev = Event(
        id=str(uuid.uuid4()), user_id=uid, level=0, title="私密事件",
        cover_content_id=c.id, status="done", generated_by="cloud",
    )
    db.add(ev)
    db.commit()
    db.add(EventItem(event_id=ev.id, content_id=c.id))
    db.add(CorrectionLog(user_id=uid, content_id=c.id, new_label="隐私", source="active"))
    db.commit()
    vault_svc.lock_content(db, uid, c.id)

    payload = build_export_payload(db, uid)
    blob = json.dumps(payload, default=str, ensure_ascii=False)
    assert c.id not in blob, (
        f"锁定内容 {c.id} 仍出现在导出包 events/corrections 字段；export.py:268 过滤不完整"
    )


# =========================================================================== #
# 6. 导出媒体链接
# =========================================================================== #
def test_locked_photo_has_no_download_url(db_user, monkeypatch):
    """锁定照片即使开启媒体链接，也不得给签名下载链接。"""
    import app.services.external.storage as storage_mod

    class FakeStorage:
        def get_download_url(self, key, user_id="", ttl=900):
            return f"https://cdn.example/{key}?uid={user_id}"

    monkeypatch.setattr(storage_mod, "get_storage_backend", lambda: FakeStorage())
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid, content_type="photo", cos_key="photos/secret.jpg")
    vault_svc.lock_content(db, uid, c.id)
    payload = build_export_payload(db, uid, include_media_links=True)
    blob = json.dumps(payload, default=str)
    assert c.id not in blob and "photos/secret.jpg" not in blob


def test_unlocked_photo_media_link_restored(db_user, monkeypatch):
    """解锁后照片及其签名链接恢复到导出包。"""
    import app.services.external.storage as storage_mod

    class FakeStorage:
        def get_download_url(self, key, user_id="", ttl=900):
            return f"https://cdn.example/{key}?uid={user_id}"

    monkeypatch.setattr(storage_mod, "get_storage_backend", lambda: FakeStorage())
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid, content_type="photo", cos_key="photos/open.jpg")
    vault_svc.lock_content(db, uid, c.id)
    key = vault_svc.unlock_token_for(db, uid, c.id)
    vault_svc.unlock_content(db, uid, c.id, key)
    payload = build_export_payload(db, uid, include_media_links=True)
    item = next(i for i in payload["contents"] if i["content_id"] == c.id)
    assert "photos/open.jpg" in (item.get("download_url") or "")


def test_export_port_page_shape(db_user):
    """DbExportPort.page 返回 ExportPage，status/cursor/items 结构正确。"""
    from app.ports.ports_extra import ExportPage
    from app.services.b6_ports import DbExportPort

    db, user = db_user
    uid = str(user.id)
    _content(db, uid)
    page = DbExportPort(db).page(uid)
    assert isinstance(page, ExportPage)
    assert page.status == "ok" and page.cursor == ""
    assert isinstance(page.items, tuple) and len(page.items) == 1


# =========================================================================== #
# 7. 注销 / 恢复后 B6 表状态
# =========================================================================== #
def test_request_delete_keeps_b6_tables(db_user):
    """30 天撤销窗口内，注销申请不得清空 B6 三表（否则无法恢复用户偏好）。"""
    from app.services.b6_ports import DbLifecyclePort

    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    consent_svc.set_consent(db, uid, "model_training", True)
    vault_svc.lock_content(db, uid, c.id)
    assert DbLifecyclePort(db).request_delete(uid)
    assert _fresh_count(ConsentGrant, uid) == 1
    assert _fresh_count(VaultEntry, uid) == 1
    assert _fresh_count(AuditLog, uid) >= 1


def test_restore_keeps_b6_tables(db_user):
    """撤销注销后，B6 三表状态原样保留。"""
    from app.services.b6_ports import DbLifecyclePort

    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    consent_svc.set_consent(db, uid, "research_analysis", True)
    vault_svc.lock_content(db, uid, c.id)
    port = DbLifecyclePort(db)
    port.request_delete(uid)
    assert port.restore(uid) is True
    assert consent_svc.is_granted(db, uid, "research_analysis")
    assert c.id in vault_svc.locked_content_ids(db, uid)
    assert _fresh_count(AuditLog, uid) >= 1


@pytest.mark.parametrize("direction", ["request_delete", "restore"])
def test_lifecycle_port_must_emit_audit(db_user, direction):
    """[NAIL] 通过 B6 LifecyclePort 做注销/恢复，必须留审计。

    暴露问题：app/services/b6_ports.py:90-96 的 DbLifecyclePort.request_delete /
    restore 直接调 purge 服务，**没有**写审计；只有 app/api/users.py:86/102 的
    HTTP 路径写了 account_purge_requested / account_restored。端口是 B6 定义的
    上层入口，绕过它即可做无审计的注销/恢复，审计白名单
    （audit.py:23）形同虚设。
    """
    from app.services.b6_ports import DbLifecyclePort

    db, user = db_user
    uid = str(user.id)
    _content(db, uid)  # 让 request_delete 有内容可软删
    port = DbLifecyclePort(db)
    action = "account_purge_requested" if direction == "request_delete" else "account_restored"
    if direction == "restore":
        port.request_delete(uid)
        port.restore(uid)
    else:
        port.request_delete(uid)
    n = _audit_count(db, uid, action)
    assert n == 1, f"{direction} 经端口调用后 {action} 审计为 {n} 条；b6_ports.py:90-96"


def test_purge_clears_all_b6_rows(db_user):
    """真正执行 purge 后三表清零（与「申请/恢复保留」形成对照）。"""
    from app.domain.memory.purge import purge_memory_layers

    db, user = db_user
    uid = str(user.id)
    for scope in ("model_training", "family_share", "personalization"):
        consent_svc.set_consent(db, uid, scope, True)
    for _ in range(3):
        c = _content(db, uid)
        vault_svc.lock_content(db, uid, c.id)
    flush = db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.user_id == uid)).scalar()
    assert flush >= 3
    purge_memory_layers(db, uid)
    db.commit()
    for model in (ConsentGrant, VaultEntry, AuditLog):
        assert _fresh_count(model, uid) == 0, f"{model.__name__} 未清理"


def test_audit_actions_cover_lifecycle():
    """静态闸门：审计白名单必须覆盖注销/恢复两个关键动作。"""
    assert {"account_purge_requested", "account_restored", "export_requested"} <= AUDIT_ACTIONS


# =========================================================================== #
# 8. 未知 scope / SQL 注入形状 / 超长 note / 时间边界
# =========================================================================== #
@pytest.mark.parametrize(
    "evil_scope",
    [
        "",
        "all",
        "MODEL_TRAINING",
        " model_training",
        "model_training\n",
        "model_training'; DROP TABLE consent_grant; --",
        "x" * 300,
    ],
)
def test_unknown_scope_injection_rejected_by_api(db_user, evil_scope):
    """未知 scope（含 SQL 注入形状/超长/大小写变体）一律 422 且不落库。"""
    db, user = db_user
    uid = str(user.id)
    r = _client(user).put(
        "/api/v1/trust/consents", json={"scope": evil_scope, "granted": True}
    )
    assert r.status_code == 422
    assert _envelope(r.json()) == ERROR_KEYS
    assert _fresh_count(ConsentGrant, uid) == 0


@pytest.mark.parametrize(
    "evil_id", ["' OR 1=1 --", "1'; DROP TABLE contents; --", "../../etc/passwd"]
)
def test_sql_injection_shaped_content_id_must_not_500(db_user, evil_id):
    """[NAIL] 注入形状的 content_id 必须是可控 4xx，不能 500。

    暴露问题：app/api/deps.py:217-230 load_alive_content 用
    ``model.id == entity_id`` 直接绑定字符串；Content.id 是 PG UUID 列，
    畸形字符串触发 psycopg InvalidTextRepresentation → 未处理 → 500
    INTERNAL_ERROR。安全上不应把「参数格式非法」升级成服务端异常，
    且异常还会让事务进入 aborted 状态。产品应有行为：422/404。
    """
    db, user = db_user
    r = _client(user).post("/api/v1/trust/vault/lock", json={"content_id": evil_id})
    assert r.status_code in (400, 404, 422), (
        f"注入形状 content_id 得到 {r.status_code}（deps.py:217-230 无格式校验）；{r.text[:160]}"
    )


@pytest.mark.parametrize("endpoint", ["lock", "token"])
def test_wellformed_foreign_uuid_returns_404(db_user, make_user, endpoint):
    """格式合法但非本人的 content_id 统一 404，不泄露存在性。"""
    db, user = db_user
    other = make_user(db, "b6-adv-idor")
    try:
        foreign = _content(db, str(other.id), "别人的")
        c = _client(user)
        if endpoint == "lock":
            r = c.post("/api/v1/trust/vault/lock", json={"content_id": foreign.id})
        else:
            r = c.get(f"/api/v1/trust/vault/{foreign.id}/token")
        assert r.status_code == 404
        assert _envelope(r.json()) == ERROR_KEYS
    finally:
        db.execute(Content.__table__.delete().where(Content.user_id == str(other.id)))
        db.execute(AuditLog.__table__.delete().where(AuditLog.user_id == str(other.id)))
        db.delete(other)
        db.commit()


@pytest.mark.parametrize("length,expected", [(0, 200), (1, 200), (200, 200), (201, 422), (5000, 422)])
def test_note_length_boundary_via_api(db_user, length, expected):
    """API 层 note 长度边界：≤200 放行，>200 统一 422 信封。"""
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    r = _client(user).post(
        "/api/v1/trust/vault/lock", json={"content_id": c.id, "note": "x" * length}
    )
    assert r.status_code == expected
    if expected == 200:
        assert r.json()["data"]["note"] == "x" * length
    else:
        assert _envelope(r.json()) == ERROR_KEYS


def test_service_note_length_must_match_api_limit(db_user):
    """[NAIL] service 层 note 限制应与 API 契约一致（纵深防御）。

    暴露问题：app/schemas/trust.py:36 在 Pydantic 层限制 note<=200，但
    app/services/vault.py:41-54 ``lock_content`` 直接把任意长度 note 写入
    Text 列，没有任何校验。任何绕过 HTTP 的调用方（内部服务、脚本、端口
    DbVaultPort.lock）都能塞入超长内容，污染审计 detail 与导出数据。
    产品应有行为：service 层同样拒绝 >200。
    """
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    with pytest.raises(ValueError):
        vault_svc.lock_content(db, uid, c.id, note="x" * 201)


def test_regrant_clears_revoked_at_and_refreshes_granted_at(db_user):
    """时间边界：撤回后再授予，revoked_at 清空、granted_at 更新为最新授予时刻。"""
    db, user = db_user
    uid = str(user.id)
    first = consent_svc.set_consent(db, uid, "support_access", True)
    t0 = first["updated_at"]
    consent_svc.set_consent(db, uid, "support_access", False)
    second = consent_svc.set_consent(db, uid, "support_access", True)
    assert second["granted"] is True
    assert second["updated_at"] >= t0
    row = db.execute(
        select(ConsentGrant).where(
            ConsentGrant.user_id == uid, ConsentGrant.scope == "support_access"
        )
    ).scalar_one()
    assert row.revoked_at is None
    assert row.granted_at >= row.updated_at - timedelta(seconds=1)


def test_audit_ordering_and_timezone(db_user):
    """审计按 created_at 倒序；API 返回带时区的 ISO8601。"""
    db, user = db_user
    uid = str(user.id)
    for action in ("vault_lock", "vault_unlock", "export_requested"):
        from app.services.audit import record_audit
        record_audit(db, uid, action=action, target="t")
    db.commit()
    items = list_audit(db, uid)
    assert len(items) >= 3
    assert all(items[i]["created_at"] >= items[i + 1]["created_at"] for i in range(len(items) - 1))
    r = _client(user).get("/api/v1/trust/audit")
    assert r.status_code == 200
    stamps = [i["created_at"] for i in r.json()["data"]["items"]]
    assert stamps and all(s.endswith("+00:00") or s.endswith("Z") for s in stamps)


@pytest.mark.parametrize("limit", [1, 50, 200])
def test_vault_list_limit_boundary(db_user, limit):
    """list_vault limit 边界不报错，且不超过请求值。"""
    db, user = db_user
    uid = str(user.id)
    for _ in range(3):
        c = _content(db, uid)
        vault_svc.lock_content(db, uid, c.id)
    assert len(vault_svc.list_vault(db, uid, limit=limit)) == min(3, limit)


# =========================================================================== #
# 9. 错误信封 / 成功信封统一
# =========================================================================== #
@pytest.mark.parametrize(
    "case",
    [
        ("consent_unknown_scope", "PUT", "/api/v1/trust/consents", {"scope": "nope", "granted": True}, None, 422),
        ("consent_extra_field", "PUT", "/api/v1/trust/consents", {"scope": "model_training", "granted": True, "x": 1}, None, 422),
        ("consent_missing_granted", "PUT", "/api/v1/trust/consents", {"scope": "model_training"}, None, 422),
        ("audit_unknown_action", "GET", "/api/v1/trust/audit", None, {"action": "nope"}, 422),
        ("audit_limit_zero", "GET", "/api/v1/trust/audit", None, {"limit": 0}, 422),
        ("audit_limit_large", "GET", "/api/v1/trust/audit", None, {"limit": 201}, 422),
        ("unlock_wrong_key", "POST", "/api/v1/trust/vault/unlock", {"content_id": "<CID>", "key": "0" * 64}, None, 400),
        ("lock_foreign", "POST", "/api/v1/trust/vault/lock", {"content_id": "<FOREIGN>"}, None, 404),
        ("token_foreign", "GET", "/api/v1/trust/vault/<FOREIGN>/token", None, None, 404),
        ("export_bad_cursor", "GET", "/api/v1/export", None, {"cursor": "!!!"}, 422),
    ],
    ids=lambda c: c[0] if isinstance(c, tuple) else str(c),
)
def test_error_envelope_uniform(db_user, make_user, case):
    """所有 /trust/* 错误统一 {code,message,request_id,details}。

    ``export_bad_cursor`` 子用例同时是 [NAIL]：app/api/export.py:114-118 的
    422 手工 Response 只有 code/message/request_id，**缺 details**，与统一
    错误信封（core/errors.py:259-272 validation_error_handler）不一致。
    """
    _, method, path, payload, params, expected = case
    db, user = db_user
    other = make_user(db, "b6-adv-envelope")
    try:
        mine = _content(db, str(user.id))
        foreign = _content(db, str(other.id), "别人的")
        path = path.replace("<CID>", mine.id).replace("<FOREIGN>", foreign.id)
        if payload is not None:
            payload = {k: (v.replace("<CID>", mine.id).replace("<FOREIGN>", foreign.id)
                           if isinstance(v, str) else v) for k, v in payload.items()}
        c = _client(user)
        r = c.request(method, path, json=payload, params=params)
        assert r.status_code == expected, r.text[:200]
        body = r.json()
        assert _envelope(body) == ERROR_KEYS
        assert isinstance(body["code"], str) and body["code"]
        assert isinstance(body["message"], str) and body["message"]
        assert isinstance(body["request_id"], str)
    finally:
        db.execute(Content.__table__.delete().where(Content.user_id == str(other.id)))
        db.execute(AuditLog.__table__.delete().where(AuditLog.user_id == str(other.id)))
        db.delete(other)
        db.commit()


@pytest.mark.parametrize(
    "case",
    [
        ("consents_get", "GET", "/api/v1/trust/consents", None),
        ("consents_put", "PUT", "/api/v1/trust/consents", {"scope": "model_training", "granted": True}),
        ("vault_list", "GET", "/api/v1/trust/vault", None),
        ("vault_lock", "POST", "/api/v1/trust/vault/lock", "<CID>"),
        ("vault_token", "GET", "/api/v1/trust/vault/<CID>/token", None),
        ("vault_unlock", "POST", "/api/v1/trust/vault/unlock", "<UNLOCK>"),
        ("audit", "GET", "/api/v1/trust/audit", None),
    ],
    ids=lambda c: c[0] if isinstance(c, tuple) else str(c),
)
def test_success_envelope_uniform(db_user, case):
    """所有 /trust/* 成功端点统一 {code,message,data,request_id}，code=OK。"""
    name, method, path, payload = case
    db, user = db_user
    uid = str(user.id)
    c = _content(db, uid)
    if name in ("vault_lock", "vault_token", "vault_unlock"):
        vault_svc.lock_content(db, uid, c.id)
    if payload == "<CID>":
        payload = {"content_id": c.id, "note": "ok"}
    if payload == "<UNLOCK>":
        payload = {"content_id": c.id, "key": vault_svc.unlock_token_for(db, uid, c.id)}
    path = path.replace("<CID>", c.id)
    r = _client(user).request(method, path, json=payload if isinstance(payload, dict) else None)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert _envelope(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == "OK" and body["message"] == "success"
    assert "data" in body
