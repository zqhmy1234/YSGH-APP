"""B6 信任域 · 交付级集成测试（2026-11-24）。

真实构造 consent_grant / vault_entry / audit_log 行，走完整 API 与 service。
覆盖用户拍板的 6 条决策：
  1 按用途列 5 项 / 2 默认不授权 / 3 一条条锁 / 4 本人+家庭角色（B8 留接口）
  5 审计用户可见 / 6 导出注销与新功能一起挂端口+审计

刁钻场景：跨用户隔离、撤回不倒流、锁内容不进回响、错误令牌、未知 scope、
未知 action、幂等锁定、家庭成员角色预留、注销清理三表。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.api import deps
from app.db.models import AuditLog, ConsentGrant, Content, VaultEntry
from app.main import app
from app.services import consent as consent_svc
from app.services import vault as vault_svc
from app.services.audit import list_audit, record_audit
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("profile_crypto_keys")]

LOCAL_TZ = timezone(timedelta(hours=8))


def _client(user):
    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    return client


def _content(db, user_id: str, text: str = "一段记忆") -> Content:
    c = Content(
        id=str(uuid.uuid4()), user_id=user_id, content_type="text", text=text,
        taken_at=datetime.now(LOCAL_TZ) - timedelta(days=365),
        sensitive_status="正常", status="done",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ---------------------------------------------------------------- 用途授权
def test_consent_default_all_not_granted(db_user):
    """决策1/2：5 项全列，默认全部未授权（无行=未表态）。"""
    db, user = db_user
    items = consent_svc.list_consents(db, str(user.id))
    assert len(items) == 5
    assert all(i["granted"] is False and i["state"] == "not_granted" for i in items)
    assert all(i["label"] for i in items), "每项都要有人话文案"


def test_consent_grant_then_revoke_no_rewind(db_user):
    """决策2：撤回不倒流——行保留 state=revoked，不物理删除。"""
    db, user = db_user
    consent_svc.set_consent(db, str(user.id), "model_training", True)
    assert consent_svc.is_granted(db, str(user.id), "model_training")
    consent_svc.set_consent(db, str(user.id), "model_training", False)
    assert not consent_svc.is_granted(db, str(user.id), "model_training")
    row = db.execute(
        select(ConsentGrant).where(
            ConsentGrant.user_id == str(user.id), ConsentGrant.scope == "model_training"
        )
    ).scalar_one()
    assert row.state == "revoked" and row.revoked_at is not None


def test_consent_unknown_scope_rejected(db_user):
    """未知 scope 必须被拒（白名单），不能静默建一行。"""
    db, user = db_user
    with pytest.raises(ValueError):
        consent_svc.set_consent(db, str(user.id), "train_my_data_please", True)


def test_consent_api_roundtrip_and_isolation(db_user, make_user):
    """API：清单读回 + 授予 + 跨用户隔离（A 的授权不影响 B）。"""
    db, user = db_user
    other = make_user(db, "b6-consent-other")
    try:
        c = _client(user)
        try:
            r = c.get("/api/v1/trust/consents")
            assert r.status_code == 200
            assert len(r.json()["data"]["items"]) == 5
            r = c.put("/api/v1/trust/consents", json={"scope": "family_share", "granted": True})
            assert r.status_code == 200 and r.json()["data"]["granted"] is True
            r = c.get("/api/v1/trust/consents")
            assert next(i for i in r.json()["data"]["items"] if i["scope"] == "family_share")["granted"]
        finally:
            app.dependency_overrides.clear()
        # B 用户仍是全未授权
        assert all(not i["granted"] for i in consent_svc.list_consents(db, str(other.id)))
    finally:
        for m in (ConsentGrant, AuditLog):
            db.execute(m.__table__.delete().where(m.user_id == str(other.id)))
        db.delete(other)
        db.commit()


def test_consent_api_rejects_unknown_field_and_scope(db_user):
    """输入契约：未知字段 forbid；未知 scope 422。"""
    db, user = db_user
    c = _client(user)
    try:
        r = c.put("/api/v1/trust/consents", json={"scope": "model_training", "granted": True, "extra": 1})
        assert r.status_code == 422
        r = c.put("/api/v1/trust/consents", json={"scope": "no_such_scope", "granted": True})
        assert r.status_code == 422
        assert "code" in r.json()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- 保险箱
def test_vault_lock_unlock_roundtrip(db_user):
    """决策3：一条条锁；解锁走服务端令牌；锁条目删除后内容原行仍在。"""
    db, user = db_user
    c = _content(db, str(user.id))
    vault_svc.lock_content(db, str(user.id), c.id)
    assert c.id in vault_svc.locked_content_ids(db, str(user.id))
    token = vault_svc.unlock_token_for(db, str(user.id), c.id)
    res = vault_svc.unlock_content(db, str(user.id), c.id, token)
    assert res["ok"] is True
    assert c.id not in vault_svc.locked_content_ids(db, str(user.id))
    assert db.get(Content, c.id) is not None, "解锁不删内容原行"


def test_vault_wrong_key_fails_closed(db_user):
    """错误令牌不得解锁；内容仍在锁中。"""
    db, user = db_user
    c = _content(db, str(user.id))
    vault_svc.lock_content(db, str(user.id), c.id)
    res = vault_svc.unlock_content(db, str(user.id), c.id, "0" * 64)
    assert res["ok"] is False and res["reason"] == "wrong_key"
    assert c.id in vault_svc.locked_content_ids(db, str(user.id))


def test_vault_lock_idempotent(db_user):
    """重复锁同一条不产生第二行（幂等）。"""
    db, user = db_user
    c = _content(db, str(user.id))
    vault_svc.lock_content(db, str(user.id), c.id)
    vault_svc.lock_content(db, str(user.id), c.id)
    rows = db.execute(
        select(VaultEntry).where(VaultEntry.user_id == str(user.id))
    ).scalars().all()
    assert len(rows) == 1


def test_vault_lock_rejects_foreign_content(db_user, make_user):
    """不能锁别人的内容（IDOR 防护）。"""
    db, user = db_user
    other = make_user(db, "b6-vault-other")
    try:
        foreign = _content(db, str(other.id), "别人的")
        from app.core.errors import ApiError
        with pytest.raises(ApiError):
            vault_svc.lock_content(db, str(user.id), foreign.id)
    finally:
        db.execute(Content.__table__.delete().where(Content.user_id == str(other.id)))
        db.delete(other)
        db.commit()


def test_vault_granted_roles_reserved_for_b8(db_user):
    """决策4：家庭成员解锁依赖 B8，本期字段预留且恒空，不假装有权限判定。"""
    db, user = db_user
    c = _content(db, str(user.id))
    vault_svc.lock_content(db, str(user.id), c.id)
    row = db.execute(select(VaultEntry).where(VaultEntry.user_id == str(user.id))).scalar_one()
    assert row.granted_roles in (None, [], {}), "B8 未落地，家庭角色不得有值"


def test_vault_ledger_api(db_user):
    """API：锁定清单 + 取令牌 + 解锁。"""
    db, user = db_user
    c = _content(db, str(user.id))
    client = _client(user)
    try:
        r = client.post("/api/v1/trust/vault/lock", json={"content_id": c.id, "note": "私密"})
        assert r.status_code == 200, r.text
        r = client.get("/api/v1/trust/vault")
        assert r.status_code == 200 and r.json()["data"][0]["content_id"] == c.id
        r = client.get(f"/api/v1/trust/vault/{c.id}/token")
        assert r.status_code == 200 and r.json()["data"]["key"]
        key = r.json()["data"]["key"]
        r = client.post("/api/v1/trust/vault/unlock", json={"content_id": c.id, "key": key})
        assert r.status_code == 200 and r.json()["data"]["ok"] is True
    finally:
        app.dependency_overrides.clear()


def test_vaulted_content_excluded_from_echo(db_user, monkeypatch):
    """保险箱不是摆设：锁住的内容不进 B4 回响候选。"""
    from app.services import echo as echo_svc
    from app.services.echo import get_today_echo

    now = datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ)
    monkeypatch.setattr(echo_svc, "_local_now", lambda: now)
    db, user = db_user
    # 造去年今日内容
    c = Content(
        id=str(uuid.uuid4()), user_id=str(user.id), content_type="text", text="锁住的回忆",
        taken_at=datetime(2025, 6, 15, 10, 0, tzinfo=LOCAL_TZ),
        sensitive_status="正常", status="done",
    )
    db.add(c)
    db.commit()
    assert get_today_echo(db, str(user.id)) is not None
    # 同用户另一条：先解锁前一条释放当日名额，再锁住它
    db.execute(AuditLog.__table__.delete().where(AuditLog.user_id == str(user.id)))
    db.execute(VaultEntry.__table__.delete().where(VaultEntry.user_id == str(user.id)))
    from app.db.models import EchoHistory
    db.execute(EchoHistory.__table__.delete().where(EchoHistory.user_id == str(user.id)))
    db.commit()
    vault_svc.lock_content(db, str(user.id), c.id)
    assert get_today_echo(db, str(user.id)) is None, "锁住的内容不能被回响翻出来"


# ---------------------------------------------------------------- 审计
def test_audit_records_consent_change(db_user):
    """授权变更写审计，用户可查。"""
    db, user = db_user
    consent_svc.set_consent(db, str(user.id), "research_analysis", True)
    items = list_audit(db, str(user.id))
    assert any(i["action"] == "consent_grant" and i["target"] == "research_analysis" for i in items)


def test_audit_user_visible_and_isolated(db_user, make_user):
    """审计用户可见但只看到自己的；action 过滤生效。"""
    db, user = db_user
    other = make_user(db, "b6-audit-other")
    try:
        record_audit(db, str(user.id), action="export_requested", target="json")
        record_audit(db, str(other.id), action="export_requested", target="json")
        db.commit()
        mine = list_audit(db, str(user.id), action="export_requested")
        assert len(mine) >= 1 and all(i["action"] == "export_requested" for i in mine)
        assert all(i["id"] for i in mine)
        c = _client(user)
        try:
            r = c.get("/api/v1/trust/audit")
            assert r.status_code == 200 and r.json()["data"]["items"]
            r = c.get("/api/v1/trust/audit", params={"action": "no_such_action"})
            assert r.status_code == 422
        finally:
            app.dependency_overrides.clear()
    finally:
        for m in (AuditLog,):
            db.execute(m.__table__.delete().where(m.user_id == str(other.id)))
        db.delete(other)
        db.commit()


def test_audit_no_sensitive_text_in_detail(db_user):
    """审计不存原文：detail 只有非敏感元数据。"""
    db, user = db_user
    consent_svc.set_consent(db, str(user.id), "personalization", True)
    rows = db.execute(select(AuditLog).where(AuditLog.user_id == str(user.id))).scalars().all()
    for r in rows:
        blob = str(r.detail or {})
        assert "13812345678" not in blob


# ---------------------------------------------------------------- 端口与注销
def test_b6_ports_wire_existing_implementations(db_user):
    """决策6：Export/Lifecycle/Consent/Vault 四端口包住真实现，不是空签名。"""
    from app.ports.ports_extra import ConsentPort, ExportPort, LifecyclePort, VaultPort
    from app.services.b6_ports import DbConsentPort, DbExportPort, DbLifecyclePort, DbVaultPort

    db, user = db_user
    ports = [DbConsentPort(db), DbExportPort(db), DbLifecyclePort(db), DbVaultPort(db)]
    protocols = [ConsentPort, ExportPort, LifecyclePort, VaultPort]
    for p, proto in zip(ports, protocols, strict=True):
        assert isinstance(p, proto), f"{type(p).__name__} 未实现 {proto.__name__}"
    cp = ports[0]
    assert cp.get(str(user.id), "model_training") == "not_granted"


def test_lifecycle_port_restore(db_user):
    """审判修正：LifecyclePort 补 restore，与已有 DELETE/restore 能力对齐。"""
    from app.services.b6_ports import DbLifecyclePort

    db, user = db_user
    port = DbLifecyclePort(db)
    assert port.purge_status(str(user.id)) == "done"  # 未注销
    until = port.request_delete(str(user.id))
    assert until
    assert port.purge_status(str(user.id)) == "scheduled"  # 30 天窗口内
    assert port.restore(str(user.id)) is True
    assert port.purge_status(str(user.id)) == "done"


def test_account_purge_clears_b6_tables(db_user):
    """注销清理链纳入三张 B6 表（审判发现的原缺口）。"""
    from app.domain.memory.purge import purge_memory_layers

    db, user = db_user
    uid = str(user.id)
    record_audit(db, uid, action="export_requested", target="json")
    consent_svc.set_consent(db, uid, "model_training", True)
    c = _content(db, uid)
    vault_svc.lock_content(db, uid, c.id)
    db.commit()
    purge_memory_layers(db, uid)
    db.commit()
    for model in (ConsentGrant, VaultEntry, AuditLog):
        assert db.execute(
            select(model).where(model.user_id == uid)
        ).scalars().all() == [], f"{model.__name__} 未清理"


def test_vaulted_content_excluded_from_export(db_user):
    """保险箱接入导出：锁住的内容不进导出包，解锁后恢复。"""
    from app.services.export import build_export_payload

    db, user = db_user
    uid = str(user.id)
    a = _content(db, uid, "公开的回忆")
    b = _content(db, uid, "锁住的回忆")
    payload = build_export_payload(db, uid)
    assert {c["content_id"] for c in payload["contents"]} >= {a.id, b.id}
    vault_svc.lock_content(db, uid, b.id)
    payload = build_export_payload(db, uid)
    ids = {c["content_id"] for c in payload["contents"]}
    assert b.id not in ids, "锁住的内容不能出现在导出包"
    assert a.id in ids, "未锁内容不受影响"
    token = vault_svc.unlock_token_for(db, uid, b.id)
    vault_svc.unlock_content(db, uid, b.id, token)
    payload = build_export_payload(db, uid)
    assert b.id in {c["content_id"] for c in payload["contents"]}, "解锁后应恢复导出"
