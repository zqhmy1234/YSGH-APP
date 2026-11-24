"""B5 记忆域 · 审判级对抗性集成测试（2026-11-24）。

与 tests/memory/ 下 29 条单元探针互补：本文件专攻**真实落 PG 行**的刁钻边界，
走 service / API（TestClient），目标是发现隐藏 bug，不走过场。

覆盖：
  * dual write 开关 on/off、env 解析、部分失败（savepoint）、幂等重试、并发同键
  * read_snapshot 三态 × 空数据 / 六层过滤 / limit / evidence / vault 配额
  * person_id 过滤（有无 content_persons）、软删闭合链接、跨用户隔离
  * cursor 分页一致性（非法形状 / 空值 / memory 非 UUID 键 / 不漏不重）
  * 导出与 memory 表（字段完整性 / 保险箱 / 限流 / 格式 / 用户隔离）
  * B6 consent 闸叠加（/memory/agent、/profile/agent/digest、/persons/agent）
  * 历史数据迁移（旧 memories 不进快照、先关后开迁移、FK/CHECK 兜底）

标 ``[NAIL]`` 的用例按「产品应有行为」写断言，初始允许 FAIL；每条 docstring
写明它钉住的实现缺口与代码位置（与 tests/test_b6_adversarial.py 同型）。
其余用例是回归闸门，必须保持绿。

注：编写期间源码被并行 agent 持续修复（ledger/export/read_model/agent_profile 等），
部分 NAIL 首轮 FAIL、复跑已转绿；docstring 保留原始缺口描述作为回归依据。

运行：
  cd /Users/silver/GHapp/backend && \\
  DATABASE_URL="postgresql+psycopg://postgres:local-v3-only@127.0.0.1:55432/yishu" \\
  .venv312/bin/python -m pytest tests/test_b5_adversarial.py -q --no-header
"""
from __future__ import annotations

import base64
import json
import threading
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from app.api import deps
from app.db.models import (
    Content,
    ContentPerson,
    MemoryCorrection,
    MemoryEntry,
    Person,
    UserProfile,
)
from app.db.models.memory import MemoryDraft
from app.db.models.memory_agent import Memory
from app.db.session import SessionLocal
from app.domain.memory.ledger import MemoryWritePort, entry_id_for, evidence_ok
from app.domain.memory.read_model import read_snapshot
from app.domain.person.name_norm import normalize_name
from app.domain.person.service import (
    PersonRuleError,
    create_person,
    list_persons,
    soft_delete_person,
    validate_epistemic,
)
from app.main import app
from app.services import consent as consent_svc
from app.services import export as export_svc
from app.services import vault as vault_svc
from app.services.export import build_export_payload, decode_cursor, encode_cursor
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("profile_crypto_keys")]


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clear_overrides_and_buckets():
    """每个用例结束清掉 TestClient 依赖覆盖 + 导出限流桶，杜绝跨用例串号。"""
    import app.api.export as export_mod

    export_mod._EXPORT_BUCKETS.clear()
    yield
    app.dependency_overrides.clear()
    export_mod._EXPORT_BUCKETS.clear()


@pytest.fixture()
def b5env(db_user):
    """在公共 db_user 之上补清 B5 表（conftest 的清理链未覆盖 memory_*/persons/memories）。"""
    db, user = db_user
    uid = str(user.id)
    yield db, user, uid
    db.rollback()
    for model in (MemoryCorrection, MemoryEntry, MemoryDraft, ContentPerson, Person, Memory):
        try:
            db.execute(delete(model).where(model.user_id == uid))
        except Exception:  # noqa: BLE001 —— 尽力而为，ContentPerson.user_id 不存在时跳过
            db.rollback()
    db.commit()


def _content(db, uid, text: str = "一段记忆", **kw) -> Content:
    c = Content(
        id=kw.pop("id", str(uuid.uuid4())),
        user_id=uid,
        content_type=kw.pop("content_type", "text"),
        text=text,
        taken_at=kw.pop("taken_at", datetime.now(UTC) - timedelta(days=365)),
        sensitive_status="正常",
        status=kw.pop("status", "done"),
        created_at=kw.pop("created_at", datetime.now(UTC) - timedelta(days=1)),
        **kw,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _entry(db, uid, text: str = "今天去了西湖", **kw) -> MemoryEntry:
    e = MemoryEntry(
        entry_id=kw.pop("entry_id", f"m_{uuid.uuid4().hex[:12]}"),
        user_id=uid,
        layer=kw.pop("layer", "narrative"),
        kind=kw.pop("kind", "episode"),
        text=text,
        valid_from=kw.pop("valid_from", date(2026, 1, 1)),
        evidence=kw.pop("evidence", [{"type": "content", "id": str(uuid.uuid4())}]),
        recorded_at=kw.pop("recorded_at", datetime.now(UTC) - timedelta(days=1)),
        **kw,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _correction(db, uid, value: str = "新值", **kw) -> MemoryCorrection:
    r = MemoryCorrection(
        user_id=uid,
        target_type=kw.pop("target_type", "entry"),
        target_id=kw.pop("target_id", "m_target"),
        field=kw.pop("field", "text"),
        value=value,
        created_at=kw.pop("created_at", datetime.now(UTC) - timedelta(days=1)),
        **kw,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _person(db, uid, name: str, **kw) -> Person:
    p = Person(
        user_id=uid,
        display_name=name,
        name_norm=normalize_name(name),
        relation=kw.pop("relation", "other"),
        epistemic=kw.pop("epistemic", "user_confirmed"),
        **kw,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _fresh_count(model, uid, **filters) -> int:
    """独立 Session 查 committed 状态，绕开当前 Session 脏 identity map。"""
    s = SessionLocal()
    try:
        q = select(func.count()).select_from(model).where(model.user_id == str(uid))
        for k, v in filters.items():
            q = q.where(getattr(model, k) == v)
        return int(s.execute(q).scalar() or 0)
    finally:
        s.close()


def _grant(db, uid, scope: str = "personalization") -> None:
    consent_svc.set_consent(db, str(uid), scope, True)


def _api_client(user) -> TestClient:
    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    return client


def _agent_client(user) -> TestClient:
    client = TestClient(app)
    app.dependency_overrides[deps.agent_service_user] = lambda: user
    return client


def _body(r) -> dict:
    return r.json()["data"]


# =========================================================================== #
# 1. read_snapshot 三态 × 空数据 / 六层过滤 / limit
# =========================================================================== #
@pytest.mark.parametrize(
    "dimensions,expected_status",
    [
        (None, "insufficient_data"),
        ({}, "insufficient_data"),
        ({"unknown_dimension": "x"}, "insufficient_data"),
        ({"identity": "退休教师"}, "ok"),
        ({"habits": "早起", "taboo": "不提葬礼"}, "ok"),
    ],
    ids=["no-profile", "empty-dict", "unknown-only", "identity", "two-sections"],
)
def test_read_snapshot_status_with_profile_dimensions(b5env, dimensions, expected_status):
    """画像维度驱动 digest 状态：无/空/未知维度→insufficient，真实维度→ok。"""
    db, _user, uid = b5env
    if dimensions is not None:
        db.add(UserProfile(user_id=uid, dimensions=dimensions))
        db.commit()
    snap = read_snapshot(db, uid)
    assert snap.status == expected_status
    assert snap.user_id == uid
    assert snap.narrative == () and snap.corrections == () and snap.asset_index == ()


@pytest.mark.parametrize(
    "seed,expected_status",
    [
        ("narrative", "ok"),
        ("correction", "ok"),
        ("asset", "ok"),
        ("forbidden", "insufficient_data"),
        ("fact_claim", "insufficient_data"),
        ("superseded", "insufficient_data"),
    ],
)
def test_read_snapshot_layer_filters(b5env, seed, expected_status):
    """只有 narrative(valid/非 forbidden) / correction / alive content 算真实层。"""
    db, _user, uid = b5env
    if seed in ("narrative", "forbidden", "fact_claim", "superseded"):
        kw = {}
        if seed == "forbidden":
            kw["sensitivity"] = "forbidden"
        if seed == "fact_claim":
            kw["layer"] = "fact_claim"
        if seed == "superseded":
            kw["valid_to"] = date(2026, 2, 1)
        _entry(db, uid, "一段叙事", **kw)
    elif seed == "correction":
        _correction(db, uid)
    else:
        _content(db, uid, "可索引文本")
    snap = read_snapshot(db, uid)
    assert snap.status == expected_status
    assert snap.note == ("" if expected_status == "ok" else "empty_account")


def test_read_snapshot_narrative_limit_and_recency_order(b5env):
    """7 条叙事只回 5 条，按 recorded_at 倒序。"""
    db, _user, uid = b5env
    for i in range(7):
        _entry(
            db, uid, f"叙事-{i}",
            recorded_at=datetime.now(UTC) - timedelta(days=10 - i),
        )
    snap = read_snapshot(db, uid)
    assert len(snap.narrative) == 5
    assert [e.text for e in snap.narrative] == [f"叙事-{i}" for i in (6, 5, 4, 3, 2)]


def test_read_snapshot_correction_limit(b5env):
    """12 条纠错只回 10 条，按 created_at 倒序。"""
    db, _user, uid = b5env
    for i in range(12):
        _correction(db, uid, f"值-{i}", created_at=datetime.now(UTC) - timedelta(days=20 - i))
    snap = read_snapshot(db, uid)
    assert len(snap.corrections) == 10
    assert snap.corrections[0].value == "值-11"


def test_read_snapshot_asset_limit(b5env):
    """10 条 content 只回 8 条 asset（按 created_at 倒序），text 截断 200 字。"""
    db, _user, uid = b5env
    for i in range(10):
        _content(db, uid, "长" * 250, created_at=datetime.now(UTC) - timedelta(days=30 - i))
    snap = read_snapshot(db, uid)
    assert len(snap.asset_index) == 8
    assert all(len(a["text"]) == 200 for a in snap.asset_index)


@pytest.mark.parametrize(
    "evidence,expected",
    [
        ([{"id": "c1"}], ("c1",)),
        ([{"content_id": "c2"}], ("c2",)),
        ([{"id": "c1"}, {"content_id": "c2"}, {"note": "no-id"}], ("c1", "c2")),
    ],
)
def test_read_snapshot_episode_evidence_extraction(b5env, evidence, expected):
    """evidence 中 id/content_id 被投影为字符串元组，其余字段忽略。"""
    db, _user, uid = b5env
    _entry(db, uid, "带证据", evidence=evidence, story_arc="夏天")
    snap = read_snapshot(db, uid)
    assert snap.status == "ok"
    assert snap.narrative[0].evidence == expected
    assert snap.narrative[0].story_arc == "夏天"


def test_read_snapshot_cross_user_isolation(b5env, make_user):
    """A 的快照不含 B 的叙事 / 纠错 / 内容。"""
    db, user, uid = b5env
    other = make_user(db, "b5-adv-snap-iso")
    oid = str(other.id)
    try:
        _entry(db, oid, "别人的叙事")
        _correction(db, oid, "别人的纠错")
        _content(db, oid, "别人的内容")
        _entry(db, uid, "我的叙事")
        snap = read_snapshot(db, uid)
        assert [e.text for e in snap.narrative] == ["我的叙事"]
        assert snap.corrections == () and snap.asset_index == ()
    finally:
        db.execute(delete(MemoryEntry).where(MemoryEntry.user_id == oid))
        db.execute(delete(MemoryCorrection).where(MemoryCorrection.user_id == oid))
        db.execute(delete(Content).where(Content.user_id == oid))
        db.delete(other)
        db.commit()


@pytest.mark.parametrize("sensitivity", ["normal", "sensitive"])
def test_read_snapshot_allows_non_forbidden_sensitivities(b5env, sensitivity):
    """normal 与 sensitive 都进快照，仅 forbidden 被排除。"""
    db, _user, uid = b5env
    _entry(db, uid, "可进快照", sensitivity=sensitivity)
    assert read_snapshot(db, uid).status == "ok"


@pytest.mark.parametrize("gate", [False, True])
def test_read_snapshot_vaulted_asset_filtered(b5env, gate):
    """保险箱锁住的内容不进 asset_index；解锁后恢复。"""
    db, _user, uid = b5env
    c = _content(db, uid, "私密文本")
    if gate:
        vault_svc.lock_content(db, uid, c.id)
    snap = read_snapshot(db, uid)
    assert (c.id in [a["content_id"] for a in snap.asset_index]) is (not gate)


def test_read_snapshot_unavailable_never_empty_ok(b5env, monkeypatch):
    """存储异常由 API 层转 unavailable；domain 直接抛错，不静默返回 ok。"""
    import app.api.memory as memory_api

    db, user, uid = b5env
    _grant(db, uid)

    def boom(*_a, **_k):
        raise RuntimeError("pg down")

    monkeypatch.setattr(memory_api, "read_snapshot", boom)
    r = _agent_client(user).get("/api/v1/memory/agent")
    assert r.status_code == 200
    assert _body(r)["status"] == "unavailable"
    assert _body(r)["note"].startswith("store_error:")


def test_read_snapshot_nail_digest_ok_must_not_claim_empty(b5env):
    """[NAIL] digest 已提供真实内容时，快照不能同时写 note='empty_account'。

    暴露问题：app/domain/memory/read_model.py:58 允许 digest.status=ok 时
    status='ok'，但 :139 的 note 只看 narrative/corrections/assets 是否为空，
    完全没有把 digest 计入。结果是「ok（身份：退休教师）+ empty_account」的
    自相矛盾快照——agent 端若读 note 判断数据有无，会误以为用户没有任何记忆。
    产品应有行为：至少一条真实层（含 digest）存在时 note 不应为 empty_account。
    """
    db, _user, uid = b5env
    db.add(UserProfile(user_id=uid, dimensions={"identity": "退休教师"}))
    db.commit()
    snap = read_snapshot(db, uid)
    assert snap.status == "ok"
    assert snap.note != "empty_account", (
        "digest=ok 但 note=empty_account；read_model.py:58 vs :139 口径冲突"
    )


def test_read_snapshot_nail_vaulted_content_must_not_consume_asset_quota(b5env):
    """[NAIL] 保险箱过滤应在 LIMIT 之前，锁住的内容不能吃掉 asset 配额。

    暴露问题：app/domain/memory/read_model.py:105-121 先按 created_at desc
    LIMIT ASSET_LIMIT=8 取内容，再在 :118-119 过滤 locked_content_ids。
    若最新 8 条全被锁定、第 9/10 条未锁，过滤后 assets=() → 状态退化为
    insufficient_data，明明有可见内容却对 agent 谎称「没有记忆」。
    产品应有行为：返回最多 8 条**可见**内容，过滤先于截断。
    """
    db, _user, uid = b5env
    contents = [
        _content(db, uid, f"内容-{i}", created_at=datetime.now(UTC) - timedelta(days=30 - i))
        for i in range(10)
    ]
    for c in contents[2:]:  # 锁定 8 条最新的
        vault_svc.lock_content(db, uid, c.id)
    snap = read_snapshot(db, uid)
    visible = {c.id for c in contents[:2]}
    assert {a["content_id"] for a in snap.asset_index} == visible, (
        "锁定内容吃掉 limit(8) 配额，未锁的两条被漏掉；read_model.py:105-121"
    )


# =========================================================================== #
# 2. dual write 开关 / 部分失败 / 幂等
# =========================================================================== #
@pytest.mark.parametrize("enabled", [False, True], ids=["off", "on"])
def test_dual_write_switch_controls_canonical_row(b5env, enabled):
    """关：只写旧 memories；开：旧表 + 新表各一行。"""
    db, _user, uid = b5env
    port = MemoryWritePort(dual_write_enabled=enabled)
    key = f"switch-{uuid.uuid4().hex[:8]}"
    result = port.write(
        db, user_id=uid, text=f"开关测试-{key}",
        evidence=[{"type": "content", "id": str(uuid.uuid4())}], idempotency_key=key,
    )
    db.commit()
    assert result.receipt.state == "draft"
    assert result.old_memory_id is not None
    assert _fresh_count(Memory, uid) == 1
    assert _fresh_count(MemoryEntry, uid) == (1 if enabled else 0)
    if not enabled:
        assert result.receipt.reason == "shadow_write_disabled"


@pytest.mark.parametrize(
    "env_value,expected",
    [("0", False), ("1", True), ("true", True), ("YES", True), ("maybe", False), ("", False)],
)
def test_dual_write_from_env_parsing(monkeypatch, env_value, expected):
    """MEMORY_DUAL_WRITE 只认 1/true/yes（大小写不敏感），其余一律关。"""
    monkeypatch.setenv("MEMORY_DUAL_WRITE", env_value)
    assert MemoryWritePort.from_env().dual_write_enabled is expected


@pytest.mark.parametrize(
    "text,evidence",
    [
        ("", [{"type": "content", "id": "c1"}]),
        ("   ", [{"type": "content", "id": "c1"}]),
        ("有文本", []),
    ],
    ids=["empty-text", "whitespace-text", "empty-evidence"],
)
def test_dual_write_rejects_missing_text_or_evidence(b5env, text, evidence):
    """无文本/无证据：rejected 且不落任何行。"""
    db, _user, uid = b5env
    result = MemoryWritePort(dual_write_enabled=True).write(
        db, user_id=uid, text=text, evidence=evidence, idempotency_key=f"rej-{uuid.uuid4().hex}",
    )
    db.commit()
    assert result.receipt.state == "rejected"
    assert result.old_memory_id is None
    assert _fresh_count(Memory, uid) == 0 and _fresh_count(MemoryEntry, uid) == 0


def test_dual_write_idempotent_retry_creates_one_row_each(b5env):
    """同 idempotency_key 重试：第二次 committed/idempotent，不产生新行。"""
    db, _user, uid = b5env
    port = MemoryWritePort(dual_write_enabled=True)
    key = f"idem-{uuid.uuid4().hex[:8]}"
    kwargs = dict(user_id=uid, text="同一条记忆", evidence=[{"type": "content", "id": "c1"}], idempotency_key=key)
    first = port.write(db, **kwargs)
    db.commit()
    second = port.write(db, **kwargs)
    db.commit()
    assert first.receipt.idempotent is False
    assert second.receipt.state == "committed" and second.receipt.idempotent is True
    assert _fresh_count(Memory, uid) == 1 and _fresh_count(MemoryEntry, uid) == 1


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [("kind", "bogus_kind"), ("thread_id", "not-a-uuid")],
    ids=["kind-check-constraint", "thread-uuid-cast"],
)
def test_dual_write_partial_failure_keeps_old_authoritative_row(b5env, bad_field, bad_value):
    """影子写违反 DB 约束时只回滚 savepoint，旧表权威行必须保住。"""
    db, _user, uid = b5env
    key = f"partial-{uuid.uuid4().hex[:8]}"
    result = MemoryWritePort(dual_write_enabled=True).write(
        db, user_id=uid, text=f"部分失败-{key}",
        evidence=[{"type": "content", "id": str(uuid.uuid4())}], idempotency_key=key,
        **{bad_field: bad_value},
    )
    db.commit()
    assert result.shadow_failed is True
    assert result.receipt.reason.startswith("shadow_write_failed:")
    assert result.receipt.state == "draft"
    assert _fresh_count(Memory, uid) == 1
    assert _fresh_count(MemoryEntry, uid) == 0


@pytest.mark.parametrize("same_user", [True, False], ids=["same-user", "cross-user"])
def test_entry_id_is_derived_from_user_and_key(b5env, make_user, same_user):
    """entry_id = sha256(user_id:idempotency_key)[:12]：同用户同键稳定，跨用户隔离。"""
    db, user, uid = b5env
    target = uid if same_user else str(make_user(db, "b5-adv-eid").id)
    a = entry_id_for(uid, "same-key")
    b = entry_id_for(target, "same-key")
    assert a.startswith("m_") and len(a) == 14
    assert (a == b) is same_user


@pytest.mark.parametrize(
    "evidence,expected",
    [
        ([{"type": "content", "id": "c1"}], True),
        ([], False),
        ([{"type": "content", "id": "c1"}, {"type": "", "id": ""}], True),
    ],
    ids=["one-valid", "none", "one-valid-plus-malformed"],
)
def test_nail_evidence_ok_at_least_one_valid_item(evidence, expected):
    """[NAIL] evidence_ok 文档承诺「至少一条有效证据」，实现却要求**全部**有效。

    暴露问题：app/domain/memory/ledger.py:56-60 docstring 写
    "At least one evidence item with a non-empty type and id"，但 return
    用 ``all(...)``。一条合法证据 + 一条畸形证据被整体拒绝；而 DB 层的
    CHECK 只要求 jsonb_array_length(evidence)>0（memory.py:58），口径分裂。
    产品应有行为：含至少一条合法 evidence 即放行，或在文档/校验中统一为 all。
    """
    assert evidence_ok(evidence) is expected, (
        f"evidence_ok({evidence!r}) 与契约冲突；ledger.py:56-60"
    )


def test_nail_concurrent_same_key_dual_write_converges(b5env):
    """[NAIL] 并发同键提案应幂等收敛为一行，而不是 IntegrityError/500。

    暴露问题：app/domain/memory/ledger.py:135-155 是
    「db.get(MemoryEntry, entry_id) 判重 → SELECT 旧表判重 → INSERT」的
    check-then-act，无锁无 upsert。两个 Session 同时以同一 idempotency_key
    进入时都看到「不存在」，随后旧表部分唯一索引
    uq_memories_user_hash_bucket（memory_agent.py:50-58）让其中一个崩在
    IntegrityError。产品应有行为：INSERT ... ON CONFLICT DO NOTHING 或捕获
    冲突后回读既有行。
    """
    db, _user, uid = b5env
    key = f"concurrent-{uuid.uuid4().hex[:8]}"
    text = f"并发同键-{key}"
    engine = db.get_bind()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    @event.listens_for(engine, "after_execute")
    def _sync(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if (
            isinstance(statement, str)
            and statement.lstrip().upper().startswith("SELECT")
            and "memory_entries" in statement
        ):
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass

    sessions = [SessionLocal(), SessionLocal()]
    try:
        def work(session):
            try:
                MemoryWritePort(dual_write_enabled=True).write(
                    session, user_id=uid, text=text,
                    evidence=[{"type": "content", "id": str(uuid.uuid4())}],
                    idempotency_key=key,
                )
                session.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(s,)) for s in sessions]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        event.remove(engine, "after_execute", _sync)
        for s in sessions:
            s.close()

    assert not errors, (
        f"并发同键提案抛 {type(errors[0]).__name__}: {errors[0]!s:.160}；ledger.py:135-155"
    )
    assert _fresh_count(Memory, uid) == 1 and _fresh_count(MemoryEntry, uid) == 1


# =========================================================================== #
# 3. person / content_persons / person_id 过滤
# =========================================================================== #
def test_person_create_idempotent_by_normalized_name(b5env):
    """同 user + 同 name_norm 重建返回既有行，不产生第二条。"""
    db, _user, uid = b5env
    p1 = create_person(db, user_id=uid, display_name="张 三", idempotency_key="k-1", relation="family")
    p2 = create_person(db, user_id=uid, display_name="张三", idempotency_key="k-2")
    assert p1.id == p2.id
    assert len(list_persons(db, uid)) == 1


@pytest.mark.parametrize(
    "relation,ok",
    [("other", True), ("family", True), ("friend", True), ("colleague", True), ("classmate", True),
     ("", True), ("father", False), ("mother", False), ("ai_inferred", False),
     (" brother ", False), ("x" * 40, False)],
)
def test_person_relation_validation(relation, ok):
    """relation 白名单仅 family/friend/colleague/classmate/other；未知/超长拒绝。"""
    from app.domain.person.name_norm import valid_relation

    assert valid_relation(relation or "other") is ok


@pytest.mark.parametrize(
    "epistemic,ok",
    [("user_confirmed", True), ("imported", True), ("ai_inferred", False), ("", False), ("USER", False)],
)
def test_person_epistemic_rejects_ai_inference(epistemic, ok):
    """只允许 user_confirmed / imported，杜绝 AI 推断的 person 行。"""
    if ok:
        assert validate_epistemic(epistemic) == epistemic
    else:
        with pytest.raises(PersonRuleError):
            validate_epistemic(epistemic)


def test_person_list_isolates_users(b5env, make_user):
    """list_persons 只回当前用户的 live person。"""
    db, user, uid = b5env
    other = make_user(db, "b5-adv-person-iso")
    try:
        mine = _person(db, uid, "我的家人", relation="mother")
        _person(db, str(other.id), "别人的家人")
        assert [p.id for p in list_persons(db, uid)] == [mine.id]
    finally:
        db.execute(delete(Person).where(Person.user_id == str(other.id)))
        db.delete(other)
        db.commit()


@pytest.mark.parametrize("link_count", [1, 2, 3])
def test_soft_delete_person_closes_all_content_links(b5env, link_count):
    """软删 person 必须把所有仍有效的 content_persons 链接 valid_to 置位。"""
    db, _user, uid = b5env
    p = _person(db, uid, "要删除的人")
    contents = [_content(db, uid, f"关联-{i}") for i in range(link_count)]
    for c in contents:
        db.add(ContentPerson(content_id=c.id, person_id=p.id, epistemic="user_confirmed", source="user"))
    db.commit()
    assert soft_delete_person(db, user_id=uid, person_id=p.id) is True
    links = db.execute(select(ContentPerson).where(ContentPerson.person_id == p.id)).scalars().all()
    assert len(links) == link_count
    assert all(link.valid_to is not None for link in links)
    assert p not in list_persons(db, uid)


def test_person_api_create_list_delete_happy(b5env):
    """Agent L3 person 端点全链路：create → list → delete（TestClient）。"""
    db, user, uid = b5env
    _grant(db, uid)  # 现行实现把 person 端点纳入 personalization 用途闸
    c = _agent_client(user)
    created = c.post("/api/v1/persons/agent", json={
        "idempotency_key": "api-create-1",
        "display_name": "李四",
        "relation": "friend",
    })
    assert created.status_code == 200, created.text
    pid = _body(created)["person_id"]
    assert c.get("/api/v1/persons/agent").json()["data"][0]["person_id"] == pid
    assert c.delete(f"/api/v1/persons/agent/{pid}").status_code == 200
    assert c.get("/api/v1/persons/agent").json()["data"] == []


def test_person_api_delete_unknown_returns_404(b5env):
    """不存在的 person_id → 404，不区分「不属于本人」与「不存在」。"""
    db, user, _uid = b5env
    r = _agent_client(user).delete(f"/api/v1/persons/agent/{uuid.uuid4()}")
    assert r.status_code == 404


def test_person_api_cross_user_cannot_delete(b5env, make_user):
    """B 不能通过 agent 端点软删 A 的 person。"""
    db, user, uid = b5env
    other = make_user(db, "b5-adv-person-del")
    try:
        p = _person(db, uid, "我的家人")
        r = _agent_client(other).delete(f"/api/v1/persons/agent/{p.id}")
        assert r.status_code == 404
        assert p.deleted_at is None
    finally:
        db.execute(delete(Person).where(Person.user_id == str(other.id)))
        db.delete(other)
        db.commit()


def test_nail_person_api_person_id_filter_is_honored(b5env):
    """[NAIL] /persons/agent?person_id=X 必须按 X 过滤，不能返回全部人。

    暴露问题：app/api/agent_profile.py:158-166 声明了
    ``person_id: str = Query("", max_length=64)`` 却完全不使用它，
    list_persons(db, user.id, limit=limit) 无条件回全量。客户端按 person_id
    查询某位家人时拿到所有家人——既是功能失效，也是越权聚合面。
    产品应有行为：传入 person_id 时只回该人（不存在/非本人回空或 404）。
    """
    db, user, uid = b5env
    _grant(db, uid)  # person 端点受 personalization 闸约束；先授权才能验证过滤语义
    _person(db, uid, "甲")  # 另一个人：验证过滤不是"返回全部"
    p2 = _person(db, uid, "乙")
    r = _agent_client(user).get("/api/v1/persons/agent", params={"person_id": p2.id})
    assert r.status_code == 200
    got = [i["person_id"] for i in _body(r)]
    assert got == [p2.id], (
        f"person_id={p2.id} 却返回 {got}（agent_profile.py:158-166 忽略入参）"
    )


@pytest.mark.parametrize("case", ["list", "create"], ids=["list-persons", "create-person"])
def test_nail_person_api_must_honor_consent_gate(b5env, case):
    """[NAIL] Agent person 端点应与 /memory/agent、/profile/agent/digest 一样受
    personalization 用途闸约束；未授权时不得返回画像人物数据。

    暴露问题：app/api/agent_profile.py:90-115（create）与 :158-166（list）
    没有任何 is_granted(..., "personalization") 调用，而同文件 :65 的 digest
    和 app/api/memory.py:49 的记忆快照都做了 B6 闸。用户撤回个性化授权后，
    Agent 仍可枚举/新建该用户的人物关系——B6 授权被旁路。
    产品应有行为：未授权时 list 返回空/unavailable，create 被拒。
    """
    db, user, uid = b5env
    _person(db, uid, "不该泄露的家人")
    c = _agent_client(user)
    if case == "list":
        r = c.get("/api/v1/persons/agent")
        assert _body(r) == [], (
            f"未授权时 person 列表泄露 {len(_body(r))} 条（agent_profile.py:158-166 无 consent 闸）"
        )
    else:
        r = c.post("/api/v1/persons/agent", json={
            "idempotency_key": "consent-denied-1", "display_name": "未授权创建",
        })
        assert r.status_code in (403, 422), (
            f"未授权仍可创建 person（HTTP {r.status_code}）；agent_profile.py:90-115"
        )


# =========================================================================== #
# 4. cursor 分页一致性
# =========================================================================== #
@pytest.mark.parametrize(
    "cursor",
    [
        "!!!not-base64!!!",
        base64.urlsafe_b64encode(b"[]").decode(),
        base64.urlsafe_b64encode(b'{"contents": 123}').decode(),
        base64.urlsafe_b64encode(b'{"contents": null}').decode(),
        base64.urlsafe_b64encode(b'{"contents": [1]}').decode(),
    ],
    ids=["not-b64", "array-root", "number-value", "null-value", "list-value"],
)
def test_export_api_rejects_malformed_cursor(b5env, cursor):
    """结构非法 cursor 统一 422 VALIDATION_ERROR，不静默从头导出。"""
    db, user, _uid = b5env
    r = _api_client(user).get("/api/v1/export", params={"cursor": cursor})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "key",
    ["contents", "events", "corrections", "persons"],
)
def test_cursor_roundtrip_with_uuid_values(key):
    """主键为 UUID 的四类键值可无损往返。"""
    value = str(uuid.uuid4())
    assert decode_cursor(encode_cursor({key: value})) == {key: value}


def test_cursor_roundtrip_with_memory_entry_id():
    """memory_entries.entry_id 是 m_<12hex> 字符串，按自身格式往返。"""
    value = f"m_{uuid.uuid4().hex[:12]}"
    assert decode_cursor(encode_cursor({"memory": value})) == {"memory": value}


@pytest.mark.parametrize(
    "evil",
    ["' OR 1=1 --", "../../etc/passwd", "not-a-uuid"],
    ids=["sql-shape", "path-shape", "plain-text"],
)
def test_export_api_rejects_injection_shaped_cursor_id(b5env, evil):
    """cursor 内 id 形状非法 → 422，不能把注入形状交给 PG 转 UUID。"""
    db, user, _uid = b5env
    cursor = encode_cursor({"contents": evil})
    r = _api_client(user).get("/api/v1/export", params={"cursor": cursor})
    assert r.status_code == 422


def test_nail_empty_cursor_value_must_not_500(b5env):
    """[NAIL] cursor 中值为空串应被当作「无游标」或 422，不能让 PG 抛 500。

    暴露问题：app/services/export.py:194-196 对空值 ``continue`` 跳过 UUID
    校验，但 :228-242 仍把 "" 作为 ``after`` 传给 _collect_capped；
    :68 的 ``key > last_key`` 让 PG 执行 UUID > '' → DataError，
    app/api/export.py:99-112 只捕获 ValueError → 500 INTERNAL_ERROR。
    这是客户端可控参数触发的服务端异常。产品应有行为：忽略空值或直接 422。
    """
    db, _user, uid = b5env
    _content(db, uid, "一条内容")
    cursor = encode_cursor({"contents": ""})
    try:
        payload = build_export_payload(db, uid, cursor)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"空 cursor 值触发 {type(exc).__name__}: {str(exc)[:120]}；"
            "export.py:194-196 / :68"
        )
    assert len(payload["contents"]) == 1


def test_nail_memory_cursor_value_is_not_uuid_validated(b5env, monkeypatch):
    """[NAIL] memory_entries 的 entry_id 形如 m_xxx（非 UUID），但 decode_cursor
    对所有键强制 UUID 校验 → 含 memory 分页的第二页必然 422。

    暴露问题：app/domain/memory/ledger.py:63-65 生成 ``m_<12hex>`` 作为
    entry_id；app/services/export.py:237-239 用 key_attr='entry_id' 把它写进
    next_cursor；但 :193-200 的 decode_cursor 对每个值执行 ``uuid.UUID(value)``，
    第二页请求直接 422。产品应有行为：按列类型分别校验（UUID 列校验 UUID，
    String 列校验字符串），或用统一格式的 id。
    """
    db, _user, uid = b5env
    monkeypatch.setattr(export_svc, "EXPORT_MAX_PER_TYPE", 2)
    for i in range(3):
        _entry(db, uid, f"可分页记忆-{i}", entry_id=f"m_{i:012x}")
    page1 = build_export_payload(db, uid)
    assert page1["truncated"] is True and page1["next_cursor"]
    try:
        cursor_vals = decode_cursor(page1["next_cursor"])
        assert "memory" in cursor_vals
        build_export_payload(db, uid, page1["next_cursor"])
    except ValueError as exc:
        pytest.fail(
            f"含 memory 游标的第二页被拒（{type(exc).__name__}: {exc}）；"
            "entry_id=m_xxx 非 UUID，export.py:193-200 vs ledger.py:63-65"
        )


@pytest.mark.parametrize("cap", [1, 2, 3])
def test_export_pagination_no_loss_no_duplicate(b5env, monkeypatch, cap):
    """contents 分页不漏不重，truncated 必带 next_cursor 且终止。"""
    db, _user, uid = b5env
    monkeypatch.setattr(export_svc, "EXPORT_MAX_PER_TYPE", cap)
    expected = {_content(db, uid, f"memo-{i}").id for i in range(7)}
    seen: list[str] = []
    cursor = None
    for _ in range(20):
        page = build_export_payload(db, uid, cursor)
        seen.extend(c["content_id"] for c in page["contents"])
        if not page["truncated"]:
            break
        cursor = page["next_cursor"]
        assert cursor
    else:
        pytest.fail("分页未终止")
    assert len(seen) == len(set(seen)) and set(seen) == expected


def test_export_ignores_unknown_cursor_key(b5env):
    """未知 cursor 键不影响分页（services 按已知键取 after）。"""
    db, _user, uid = b5env
    _content(db, uid, "一条")
    page = build_export_payload(db, uid, encode_cursor({"unknown": str(uuid.uuid4())}))
    assert len(page["contents"]) == 1 and page["truncated"] is False


def test_export_cursor_with_nonexistent_uuid_starts_after(b5env):
    """合法但不存在的 UUID 作为 after：从该键之后取，不报错。"""
    db, _user, uid = b5env
    low = "00000000-0000-0000-0000-000000000001"
    high = "00000000-0000-0000-0000-000000000002"
    _content(db, uid, "低键内容", id=low)
    _content(db, uid, "高键内容", id=high)
    page = build_export_payload(db, uid, encode_cursor({"contents": low}))
    assert [i["content_id"] for i in page["contents"]] == [high]


# =========================================================================== #
# 5. 导出与 memory 表 / person 表
# =========================================================================== #
def test_export_includes_memory_entries_with_all_fields(b5env):
    """导出包 memory[] 含 entry_id/kind/text/summary/evidence/confidence 等字段。"""
    db, _user, uid = b5env
    e = _entry(db, uid, "导出用叙事", summary="摘要", story_arc="夏天", confidence=0.75)
    page = build_export_payload(db, uid)
    assert len(page["memory"]) == 1
    m = page["memory"][0]
    assert m["entry_id"] == e.entry_id and m["text"] == "导出用叙事"
    assert m["summary"] == "摘要" and m["confidence"] == 0.75
    assert m["evidence"] == e.evidence


@pytest.mark.parametrize("sensitivity", ["normal", "sensitive", "forbidden"])
def test_export_includes_user_own_memory_regardless_of_sensitivity(b5env, sensitivity):
    """用户自有数据导出包含全部 sensitivity（与 agent 快照过滤口径不同）。"""
    db, _user, uid = b5env
    _entry(db, uid, f"记忆-{sensitivity}", sensitivity=sensitivity)
    page = build_export_payload(db, uid)
    assert len(page["memory"]) == 1 and page["memory"][0]["sensitivity"] == sensitivity


def test_export_isolates_memory_and_persons_across_users(b5env, make_user):
    """导出只含当前用户的 memory 与 person。"""
    db, user, uid = b5env
    other = make_user(db, "b5-adv-export-iso")
    try:
        oid = str(other.id)
        _entry(db, oid, "别人的记忆")
        _person(db, oid, "别人的家人")
        _entry(db, uid, "我的记忆")
        _person(db, uid, "我的家人")
        page = build_export_payload(db, uid)
        assert [m["text"] for m in page["memory"]] == ["我的记忆"]
        assert len(page["persons"]) == 1 and page["persons"][0]["display_name"] == "我的家人"
        assert page["user_id_hashed"] != uid
    finally:
        db.execute(delete(MemoryEntry).where(MemoryEntry.user_id == str(other.id)))
        db.execute(delete(Person).where(Person.user_id == str(other.id)))
        db.delete(other)
        db.commit()


def test_export_includes_soft_deleted_person_with_timestamp(b5env):
    """用户自有数据导出保留软删 person 及其 deleted_at（可恢复/可审计）。"""
    db, _user, uid = b5env
    p = _person(db, uid, "已删除的家人")
    soft_delete_person(db, user_id=uid, person_id=p.id)
    page = build_export_payload(db, uid)
    assert page["persons"][0]["person_id"] == p.id
    assert page["persons"][0]["deleted_at"] is not None


def test_export_api_requires_auth(client):
    """未登录访问 /api/v1/export → 401 AUTH_005。"""
    r = client.get("/api/v1/export")
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_005"


def test_export_api_json_envelope_and_attachment(b5env):
    """JSON 导出：attachment 头 + schema_version + 三态字段齐全。"""
    db, user, uid = b5env
    _entry(db, uid, "一条")
    r = _api_client(user).get("/api/v1/export")
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    assert data["schema_version"] == "1.0"
    assert "attachment" in r.headers.get("content-disposition", "")
    for key in ("contents", "events", "corrections", "memory", "persons", "profile",
                "truncated", "next_cursor", "user_id_hashed"):
        assert key in data


@pytest.mark.parametrize("fmt,expected", [("json", 200), ("zip", 200), ("xml", 422), ("ZIP", 422)])
def test_export_api_format_validation(b5env, fmt, expected):
    """format 只允许 json/zip（大小写敏感），其余 422。"""
    db, user, _uid = b5env
    r = _api_client(user).get("/api/v1/export", params={"format": fmt})
    assert r.status_code == expected


def test_export_api_rate_limit_after_five_calls(b5env):
    """进程内滑动窗口：同一用户第 6 次导出被 429（独立用户，桶已 autouse 清空）。"""
    db, user, _uid = b5env
    c = _api_client(user)
    codes = [c.get("/api/v1/export").status_code for _ in range(6)]
    assert codes == [200, 200, 200, 200, 200, 429]


def test_nail_export_must_not_leak_vaulted_content_id_in_memory_evidence(b5env):
    """[NAIL] 锁定内容的 id 不应从导出包的任何字段暴露，包括 memory[].evidence。

    暴露问题：app/services/export.py:217-243 仅对 contents/events/corrections
    应用 vault 过滤（:271-277），:287-300 的 memory[].evidence 原样导出。
    一条 evidence 指向被锁定内容的记忆，会把「用户有一条被锁定的内容」这一
    存在性事实以及内容 id 泄露出去，与 vault.py 的「导出必须过滤」承诺冲突。
    产品应有行为：memory[].evidence 中引用锁定内容的条目/引用一并剔除。
    """
    db, _user, uid = b5env
    c = _content(db, uid, "锁住的回忆")
    vault_svc.lock_content(db, uid, c.id)
    _entry(db, uid, "关于锁定内容的记忆", evidence=[{"type": "content", "id": c.id}])
    page = build_export_payload(db, uid)
    blob = json.dumps(page, default=str, ensure_ascii=False)
    assert c.id not in blob, (
        f"锁定内容 id {c.id} 仍从 memory[].evidence 泄露；export.py:287-300 未过滤"
    )


# =========================================================================== #
# 6. B6 consent 闸叠加（/memory/agent、/profile/agent/digest）
# =========================================================================== #
def test_memory_agent_consent_gate_until_granted(b5env):
    """未授权→unavailable(consent_not_granted)；授权后→真实快照；撤回再不可用。"""
    db, user, uid = b5env
    _entry(db, uid, "私密叙事")
    c = _agent_client(user)
    first = _body(c.get("/api/v1/memory/agent"))
    assert first["status"] == "unavailable"
    assert first["note"] == "consent_not_granted:personalization"
    assert first["narrative"] == []
    _grant(db, uid)
    second = _body(c.get("/api/v1/memory/agent"))
    assert second["status"] == "ok" and second["narrative"][0]["text"] == "私密叙事"
    consent_svc.set_consent(db, uid, "personalization", False)
    third = _body(c.get("/api/v1/memory/agent"))
    assert third["status"] == "unavailable"


@pytest.mark.parametrize("seed", ["none", "narrative", "profile"], ids=["empty", "entry", "digest"])
def test_memory_agent_snapshot_matches_domain(b5env, seed):
    """授权后 API 快照与 domain.read_snapshot 一致（三态 + 字段）。"""
    db, user, uid = b5env
    _grant(db, uid)
    if seed == "narrative":
        _entry(db, uid, "一致叙事")
    elif seed == "profile":
        db.add(UserProfile(user_id=uid, dimensions={"identity": "教师"}))
        db.commit()
    api = _body(_agent_client(user).get("/api/v1/memory/agent"))
    domain = read_snapshot(db, uid)
    assert api["status"] == domain.status
    assert len(api["narrative"]) == len(domain.narrative)
    assert len(api["corrections"]) == len(domain.corrections)
    assert len(api["asset_index"]) == len(domain.asset_index)


@pytest.mark.parametrize("granted", [False, True])
def test_profile_digest_agent_consent_gate(b5env, granted):
    """/profile/agent/digest 同样受 personalization 闸约束。"""
    db, user, uid = b5env
    db.add(UserProfile(user_id=uid, dimensions={"identity": "教师"}))
    db.commit()
    if granted:
        _grant(db, uid)
    snap = _body(_agent_client(user).get("/api/v1/profile/agent/digest", params={"person_id": uid}))
    assert snap["status"] == ("ok" if granted else "unavailable")


@pytest.mark.parametrize(
    "configured,expected",
    [("b5-adversarial-secret", 401), ("", 503)],
    ids=["wrong-token", "token-not-configured"],
)
def test_memory_agent_rejects_wrong_or_missing_service_token(b5env, monkeypatch, configured, expected):
    """服务通道 fail-closed：令牌错 → 401；未配置令牌 → 503，均不泄露快照。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "agent_service_token", configured)
    db, _user, uid = b5env
    r = TestClient(app).get(
        "/api/v1/memory/agent",
        headers={"X-Agent-Token": "wrong", "X-On-Behalf-Of": uid},
    )
    assert r.status_code == expected


# =========================================================================== #
# 7. 历史数据迁移 / FK / CHECK / 并发隔离
# =========================================================================== #
def test_legacy_memories_row_does_not_enter_snapshot(b5env):
    """旧 agent memories 表（string user_id）的行不进 B5 快照——需正式迁移。"""
    db, _user, uid = b5env
    db.add(Memory(user_id=uid, content="旧版记忆", content_format="text", content_hash="x" * 64, dedup_bucket=1))
    db.commit()
    snap = read_snapshot(db, uid)
    assert snap.status == "insufficient_data"
    assert _fresh_count(Memory, uid) == 1


def test_dual_write_off_then_on_migrates_same_key(b5env):
    """先关开关写旧表，再开开关用同一 key 写：canonical 行才出现。"""
    db, _user, uid = b5env
    key = f"migration-{uuid.uuid4().hex[:8]}"
    kwargs = dict(user_id=uid, text=f"迁移测试-{key}",
                  evidence=[{"type": "content", "id": str(uuid.uuid4())}], idempotency_key=key)
    MemoryWritePort(dual_write_enabled=False).write(db, **kwargs)
    db.commit()
    assert _fresh_count(Memory, uid) == 1 and _fresh_count(MemoryEntry, uid) == 0
    MemoryWritePort(dual_write_enabled=True).write(db, **kwargs)
    db.commit()
    assert _fresh_count(Memory, uid) == 1 and _fresh_count(MemoryEntry, uid) == 1


def test_memory_entry_requires_real_user_fk(b5env):
    """canonical memory_entries.user_id 必须引用存在的 users.id（防孤儿身份）。"""
    db, _user, _uid = b5env
    bogus = str(uuid.uuid4())
    s = SessionLocal()
    try:
        with pytest.raises(IntegrityError):
            with s.begin_nested():
                s.add(MemoryEntry(
                    entry_id=f"m_{uuid.uuid4().hex[:12]}", user_id=bogus,
                    kind="episode", text="孤儿", valid_from=date(2026, 1, 1),
                    evidence=[{"type": "content", "id": "c1"}],
                ))
                s.flush()
    finally:
        s.rollback()
        s.close()


def test_memory_draft_requires_evidence_db_check(b5env):
    """DB CHECK 兜底：memory_drafts 无 evidence 直接 IntegrityError。"""
    db, _user, uid = b5env
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(MemoryDraft(user_id=uid, text="无证据草稿", evidence=[]))
            db.flush()
    db.rollback()


def test_concurrent_writes_for_different_users_are_isolated(b5env, make_user):
    """两个用户并发写各自 canonical 行：各落一行，无串号。"""
    db, user, uid = b5env
    other = make_user(db, "b5-adv-parallel")
    oid = str(other.id)
    errors: list[Exception] = []

    def work(target, suffix):
        s = SessionLocal()
        try:
            MemoryWritePort(dual_write_enabled=True).write(
                s, user_id=target, text=f"并行-{suffix}",
                evidence=[{"type": "content", "id": str(uuid.uuid4())}],
                idempotency_key=f"parallel-{suffix}",
            )
            s.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=work, args=(uid, "a")),
               threading.Thread(target=work, args=(oid, "b"))]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        assert _fresh_count(MemoryEntry, uid) == 1 and _fresh_count(MemoryEntry, oid) == 1
    finally:
        db.execute(delete(MemoryEntry).where(MemoryEntry.user_id == oid))
        db.execute(delete(Memory).where(Memory.user_id == oid))
        db.delete(other)
        db.commit()
