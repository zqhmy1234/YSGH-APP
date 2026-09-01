"""纠错闭环测试（B5-c 三层裁决 · F2）

覆盖：
  - 记录纠错 → Qdrant + DB 双写
  - 第①层个人规则：相似文本命中 → personal 层生效（用户纠错立即生效）
  - 未命中 → 第②层全局 SetFit
  - 同内容多次纠错以最后一次为准
  - 共性纠错标记（≥2 用户一致 → is_global_candidate）+ 计数
前置：Qdrant（yishu-qdrant）+ BGE-M3 已就绪 + PG yishu 库
"""
import hashlib
import uuid

import pytest
from app.db.models import Content, CorrectionLog, User
from app.services.correction import (
    arbitrate,
    global_candidate_count,
    mark_global_candidates,
    record_correction,
)
from sqlalchemy import select

pytestmark = pytest.mark.integration

# R8#7（2026-08-27）：真 BGE-M3 编码降为定长 1024 维 mock（文件合计省 ~7s）。
# D-22 盲区修复（2026-08-29）：原常量向量 [0.1]*1024 使任意两文本 sim≡1.0——
# 层①跨内容互命中、mixed 自我锁死类缺陷全部掩盖（P2 诊断 §2.3-P2）。
# 现改「按文本 sha256 派生前 8 维方向 + 补零」：同文 sim=1.0，异文远低于 0.8，
# 与真编码的命中语义一致；维度契约仍 1024。真实编码冒烟保留 rag 组单独跑。
def _mock_dense(text: str) -> list[float]:
    digs = [
        int.from_bytes(hashlib.sha256(f"{text}|{i}".encode()).digest()[:8], "big")
        for i in range(8)
    ]
    vec = [d / 2**64 for d in digs]
    return vec + [0.0] * (1024 - len(vec))


@pytest.fixture()
def mock_encode(monkeypatch):
    """把 correction.encode_dense 替换为定长 1024 维 mock 向量（R8#7）

    record_correction/apply_personal_rule 消费的向量按文本派生
    （同文命中、异文不互串——D-22 修复后语义；维度契约由断言兜底）。
    """
    import app.services.correction as corr_mod

    def _fake_encode(texts):
        return [_mock_dense(t_) for t_ in texts]

    monkeypatch.setattr(corr_mod, "encode_dense", _fake_encode)
    return _fake_encode


def test_mock_encode_dimension_contract(mock_encode):
    """R8#7 维度契约 1024 + D-22 派生契约：同文稳定、异文方向不同（非常量）"""
    vec = mock_encode(["任意文本"])[0]
    assert len(vec) == 1024
    assert vec == mock_encode(["任意文本"])[0]
    assert vec != mock_encode(["另一文本"])[0]


@pytest.mark.rag
def test_real_encode_dimension_smoke():
    """R8#7：真实 BGE-M3 编码冒烟（保留 1 个真编码，防 mock 假绿）

    与 test_rag.test_embedding_dimension 同属 rag 组（默认套件排除，
    CI full-gate 的 `-m rag` 步骤单独跑）。
    """
    import app.services.correction as corr_mod

    dense = corr_mod.encode_dense(["测试"])[0]
    assert len(dense) == 1024


def _make_content(db, user_id: str, cid: str, text: str = "测试内容") -> None:
    """建真实 contents 行（correction_log.content_id 外键约束）"""
    db.add(
        Content(
            id=cid,
            user_id=user_id,
            content_type="text",
            text=text,
            status="done",
        )
    )
    db.commit()


def _new_cid() -> str:
    return str(uuid.uuid4())


def test_record_correction_dual_write(db_user, mock_encode):
    """记录纠错：Qdrant + correction_log 双写"""
    db, user = db_user
    cid = _new_cid()
    _make_content(db, user.id, cid)
    row = record_correction(
        db, user.id, content_id=cid,
        text="明天记得买牛奶", new_label="todo", old_label="mixed",
    )
    assert row.id > 0
    assert row.qdrant_point_id
    got = db.execute(
        select(CorrectionLog).where(CorrectionLog.id == row.id)
    ).scalar_one()
    assert got.new_label == "todo"
    assert got.user_id == user.id


def test_personal_rule_hit(db_user, mock_encode):
    """第①层：相似文本命中个人纠错 → personal 层生效"""
    db, user = db_user
    cid = _new_cid()
    _make_content(db, user.id, cid)
    # D-22：hash mock 下「近异文」不再互命中（旧常量向量的跨文本命中是假象），
    # 命中语义改为同文 sim=1.0——与真实编码下同文必中的行为一致。
    record_correction(
        db, user.id, content_id=cid,
        text="明天记得买牛奶鸡蛋", new_label="todo", old_label="mixed",
    )
    result = arbitrate(db, user.id, "明天记得买牛奶鸡蛋", "text")
    assert result["layer"] == "personal"
    assert result["label"] == "todo"
    assert result["similarity"] >= 0.8


def test_personal_rule_miss_falls_to_global(db_user, mock_encode):
    """未命中个人规则 → 第②层全局 SetFit"""
    db, user = db_user
    result = arbitrate(db, user.id, "知人者智,自知者明", "text")
    assert result["layer"] == "global"
    assert result["label"] in {"todo", "idea", "emotion", "quote", "mixed"}


def test_last_correction_wins(db_user, mock_encode):
    """同内容多次纠错以最后一次为准"""
    db, user = db_user
    cid = _new_cid()
    _make_content(db, user.id, cid)
    record_correction(db, user.id, content_id=cid, text="随便记一下", new_label="idea", old_label="mixed")
    record_correction(db, user.id, content_id=cid, text="随便记一下", new_label="quote", old_label="idea")
    result = arbitrate(db, user.id, "随便记一下", "text")
    assert result["label"] == "quote"


def test_passive_correction_needs_3_consistent(db_user, mock_encode):
    """审查修复(P1-07，B5-c-5 闸门①)：被动确认（echo/org）纠错需 ≥3 次一致才生效

    1 次被动纠错 → 不生效（回落全局）；补足 3 次 → 生效。
    """
    db, user = db_user
    # D-22 注：闸门①计数按 (user, old→new, type) 对聚合，与向量无关；
    # 向量命中改为「与某条记录同文」（旧常量版用"明天记得买牛奶"跨点是 sim≡1 假象）。
    for i in range(2):
        cid = _new_cid()
        _make_content(db, user.id, cid)
        record_correction(
            db, user.id, content_id=cid,
            text=f"被动确认样本{i}", new_label="todo", old_label="mixed",
            source="echo",
        )
    # 仅 2 次被动一致 → 未达阈值，个人规则不生效（回落全局）
    result = arbitrate(db, user.id, "被动确认样本0", "text")
    assert result["layer"] == "global", f"2 次被动确认不应触发个人规则, got {result}"
    # 补第 3 次 → 生效（命中点仍是样本0 同文）
    cid3 = _new_cid()
    _make_content(db, user.id, cid3)
    record_correction(
        db, user.id, content_id=cid3,
        text="被动确认样本2", new_label="todo", old_label="mixed",
        source="echo",
    )
    result2 = arbitrate(db, user.id, "被动确认样本0", "text")
    assert result2["layer"] == "personal", f"3 次被动一致应触发个人规则, got {result2}"
    assert result2["label"] == "todo"


def test_global_candidate_marking(db_user, mock_encode):
    """第③层：同一 (old→new) 纠错 ≥2 用户 → is_global_candidate"""
    db, user = db_user
    cid1 = _new_cid()
    cid2 = _new_cid()
    _make_content(db, user.id, cid1)
    # 用户1 纠错
    record_correction(db, user.id, content_id=cid1,
                      text="内容A", new_label="emotion", old_label="mixed")
    # 用户2（另一用户）同样 old→new
    user2 = User(phone=f"corr-test-2-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user2)
    db.commit()
    _make_content(db, user2.id, cid2)
    record_correction(db, user2.id, content_id=cid2,
                      text="内容B", new_label="emotion", old_label="mixed")

    marked = mark_global_candidates(db)
    assert marked >= 1
    count = global_candidate_count(db)
    assert count >= 1

    db.execute(CorrectionLog.__table__.delete().where(CorrectionLog.user_id == user2.id))
    db.execute(Content.__table__.delete().where(Content.user_id == user2.id))
    db.delete(user2)
    db.commit()


def test_arbitrate_api_smoke(db_user):
    """API 冒烟：POST /api/v1/classify/arbitrate 入队返回 job_id（P2-01 异步化）"""
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    client = TestClient(app)
    # mock 认证：直接发请求需要 token —— 用依赖覆盖
    from app.api import deps

    def fake_user():
        return user

    app.dependency_overrides[deps.get_current_user] = fake_user
    try:
        resp = client.post("/api/v1/classify/arbitrate", json={"text": "明天记得交房租"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["code"] == "OK"
        assert body["data"]["job_id"]  # 入队成功（P2-01：异步推理）
        assert body["data"]["status"] == "queued"
    finally:
        app.dependency_overrides.clear()


def test_arbitrate_degraded_passthrough(db_user, monkeypatch):
    """D-22（08-29）：层②模型不可用 → 裁决结果 degraded=True 显式暴露，
    mixed+conf0 不再与模型结论混同（与 D-16 emotion_source 同族修复）。"""
    import app.services.classifier as clf_mod
    import app.services.correction as corr_mod

    db, user = db_user
    monkeypatch.setattr(
        clf_mod, "_load",
        lambda: (None, clf_mod.DEFAULT_CLASSES, clf_mod.DEFAULT_CLASSES_CN),
    )
    r = corr_mod.arbitrate(db, user.id, "一段不会被个人规则命中的新文字", "text")
    assert r["layer"] == "global"
    assert r["label"] == "mixed"
    assert r["confidence"] == 0.0
    assert r["degraded"] is True


def test_correction_active_writes_back_content_class(db_user, mock_encode):
    """D-22（P1 回写）：POST /corrections active 即时同步 contents.content_class；
    echo/org 被动信号不得触碰权威字段。"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    cid_a, cid_e = _new_cid(), _new_cid()
    _make_content(db, user.id, cid_a, text="主动纠错内容")
    _make_content(db, user.id, cid_e, text="被动回声内容")
    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    try:
        r = client.post("/api/v1/corrections", json={
            "content_id": cid_a, "text": "主动纠错内容",
            "new_label": "idea", "old_label": "mixed", "source": "active",
        })
        assert r.status_code == 200, r.text
        db.expire_all()
        assert db.scalar(select(Content).where(Content.id == cid_a)).content_class == "idea"
        r2 = client.post("/api/v1/corrections", json={
            "content_id": cid_e, "text": "被动回声内容",
            "new_label": "quote", "old_label": "mixed", "source": "echo",
        })
        assert r2.status_code == 200, r2.text
        db.expire_all()
        assert db.scalar(select(Content).where(Content.id == cid_e)).content_class is None
    finally:
        app.dependency_overrides.clear()

def test_arbitrate_user_pref_echo(db_user, monkeypatch):
    """D-22（09-01）：preferred_label 透传回显——agrees_with_user 仅供客户端参考
    显示；裁决绝不改写用户意图（用户操作优先硬约束的服务端侧证明）。"""
    import app.services.classifier as clf_mod
    import app.services.correction as corr_mod

    db, user = db_user
    monkeypatch.setattr(
        clf_mod, "_load",
        lambda: (None, clf_mod.DEFAULT_CLASSES, clf_mod.DEFAULT_CLASSES_CN),
    )
    # 降级路径 label=mixed：与用户偏好一致 → agrees True
    agree = corr_mod.arbitrate(db, user.id, "偏好一致测试文字甲", "text", preferred_label="mixed")
    assert agree["preferred_label"] == "mixed"
    assert agree["agrees_with_user"] is True
    # 与用户偏好不一致 → agrees False，label 仍为裁决结论（仅参考，不代表覆盖写入）
    diff = corr_mod.arbitrate(db, user.id, "偏好一致测试文字甲", "text", preferred_label="family")
    assert diff["label"] == "mixed"
    assert diff["preferred_label"] == "family"
    assert diff["agrees_with_user"] is False
    # 不带偏好 → 无 echo 键（向后兼容旧客户端）
    plain = corr_mod.arbitrate(db, user.id, "偏好一致测试文字甲", "text")
    assert "preferred_label" not in plain
    assert "agrees_with_user" not in plain

