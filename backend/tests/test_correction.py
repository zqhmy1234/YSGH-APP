"""纠错闭环测试（B5-c 三层裁决 · F2）

覆盖：
  - 记录纠错 → Qdrant + DB 双写
  - 第①层个人规则：相似文本命中 → personal 层生效（用户纠错立即生效）
  - 未命中 → 第②层全局 SetFit
  - 同内容多次纠错以最后一次为准
  - 共性纠错标记（≥2 用户一致 → is_global_candidate）+ 计数
前置：Qdrant（yishu-qdrant）+ BGE-M3 已就绪 + PG yishu 库
"""
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
# 契约：mock 维度必须保持 1024（BGE-M3 dense）——否则掩盖向量链路回归。
# 真实编码冒烟保留在 test_real_encode_dimension_smoke（rag 组，CI 单独跑）。
MOCK_DENSE_VEC = [0.1] * 1024


@pytest.fixture()
def mock_encode(monkeypatch):
    """把 correction.encode_dense 替换为定长 1024 维 mock 向量（R8#7）

    record_correction/apply_personal_rule 消费的向量值不影响被测逻辑
    （相似检索走 mock 向量内部一致；维度契约由断言兜底）。
    """
    import app.services.correction as corr_mod

    def _fake_encode(texts):
        return [list(MOCK_DENSE_VEC)] * len(texts)

    monkeypatch.setattr(corr_mod, "encode_dense", _fake_encode)
    return _fake_encode


def test_mock_encode_dimension_contract(mock_encode):
    """R8#7：mock 向量维度契约 = 1024（BGE-M3 dense），防止"假绿"掩盖向量链路"""
    assert len(MOCK_DENSE_VEC) == 1024
    vec = mock_encode(["任意文本"])[0]
    assert len(vec) == 1024


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
    record_correction(
        db, user.id, content_id=cid,
        text="明天记得去超市买牛奶和鸡蛋", new_label="todo", old_label="mixed",
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
    for i in range(2):
        cid = _new_cid()
        _make_content(db, user.id, cid)
        record_correction(
            db, user.id, content_id=cid,
            text=f"明天记得买牛奶{i}", new_label="todo", old_label="mixed",
            source="echo",
        )
    # 仅 2 次被动一致 → 未达阈值，个人规则不生效（回落全局）
    result = arbitrate(db, user.id, "明天记得买牛奶", "text")
    assert result["layer"] == "global", f"2 次被动确认不应触发个人规则, got {result}"
    # 补第 3 次 → 生效
    cid3 = _new_cid()
    _make_content(db, user.id, cid3)
    record_correction(
        db, user.id, content_id=cid3,
        text="明天记得买牛奶3", new_label="todo", old_label="mixed",
        source="echo",
    )
    result2 = arbitrate(db, user.id, "明天记得买牛奶", "text")
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
