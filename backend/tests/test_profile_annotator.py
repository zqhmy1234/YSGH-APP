"""B1 画像标注核心测试（profile_annotator + llm_ops.annotate + profile_schema + 钩子）

覆盖（DoD）：
  - 枚举集 JSON 加载与完整性（51/193 维、双门槛、引用完整性、L1 phrase+disclosure）
  - annotate mock 通道（种子值 + 别名匹配，只映射不生成）
  - 置信度双门槛（普通 ≥0.7 / 超细 ≥0.8）
  - <阈值 → profile_annotation_pool（低置信度池）
  - 同值强度累加 / 异值替换 + 旧值进历史（最近 10 条）
  - 同日同维度节流（annotation 源节流、interview 源豁免）
  - 同义归一（别名表查重）→ 开放枚举直接新增 value（带证据 + l2_evidence 锚点）
  - 集合型维度（multi_value：同值累加、异值追加）
  - pipeline_ext 钩子 annotate_on_ingest（fail-safe）
前置：PG yishu 库 + MOCK_EXTERNAL_AI=true
"""
import pytest
from app.db.models import Content, ProfileAnnotationPool, UserProfile
from app.services.llm_ops.annotate import annotate
from app.services.profile_annotator import (
    HISTORY_LIMIT,
    annotate_content,
    apply_annotation,
    record_hits,
)
from app.services.profile_schema import get_schema
from sqlalchemy import select, text

pytestmark = pytest.mark.integration


def _profile(db, user_id) -> dict:
    row = db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    return (row.dimensions or {}) if row else {}


def _pool_rows(db, user_id) -> list:
    return db.execute(
        select(ProfileAnnotationPool).where(ProfileAnnotationPool.user_id == user_id)
    ).scalars().all()


# ---------------------------------------------------------------- 枚举集校验（DoD）
def test_schema_loads_and_validates():
    schema = get_schema()
    assert schema.l0_count == 51 and schema.l1_count == 193
    assert len(schema.dimensions) == 244
    assert schema.validate() == []
    # 双门槛：relation_core 超细 0.8 / emotional_state 普通 0.7
    assert schema.confidence_threshold("relation_core") == 0.8
    assert schema.confidence_threshold("emotional_state") == 0.7
    assert schema.get("relation_core").is_superfine() is True
    assert schema.get("emotional_state").is_superfine() is False
    # 默认标注池非空且引用完整
    pool = schema.annotate_dims()
    assert len(pool) >= 30
    assert all(schema.get(d.id) is not None for d in pool)


def test_enum_json_integrity():
    """枚举集 JSON 收尾自检：L0 13 维已补 values_detail、L1 全维 phrase+disclosure"""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    l0 = json.loads((root / "docs" / "画像维度枚举集_l0.json").read_text(encoding="utf-8"))
    l1 = json.loads((root / "docs" / "画像维度枚举集_l1_骨架.json").read_text(encoding="utf-8"))
    assert len(l0["dimensions"]) == 51 and len(l1["dimensions"]) == 193
    assert all(d.get("values_detail") for d in l0["dimensions"]), "L0 应全维有 values_detail"
    assert all(d.get("phrase") for d in l0["dimensions"]), "L0 应全维有 phrase"
    assert all(d.get("phrase") and d.get("disclosure") for d in l1["dimensions"]), \
        "L1 应全维有 phrase + disclosure"
    # 无占位符残留
    raw = (root / "docs" / "画像维度枚举集_l0.json").read_text(encoding="utf-8")
    assert "??" not in raw and "TODO" not in raw


# ---------------------------------------------------------------- annotate mock 通道
def test_annotate_mock_maps_and_normalizes():
    hits = annotate("我妈妈和老婆，坚持跑完马拉松，最喜欢拍照片和爬山")
    by_dim = {}
    for h in hits:
        by_dim.setdefault(h["dimension"], []).append(h["enum_value"])
    assert "妈妈" in by_dim.get("relation_core", []), "种子值直接命中"
    assert "伴侣" in by_dim.get("relation_core", []), "老婆 → 别名归一伴侣"
    assert "跑步" in by_dim.get("interest_hobbies", []), "马拉松 → 别名归一跑步"
    assert "摄影" in by_dim.get("interest_hobbies", []), "拍照片 → 别名归一摄影"
    assert all(0 <= h["confidence"] <= 1 for h in hits)


def test_annotate_empty_text():
    assert annotate("") == []
    assert annotate("   ") == []


# ---------------------------------------------------------------- 阈值 + 池
def test_high_confidence_applies(db_user):
    db, user = db_user
    r = apply_annotation(db, user.id, "emotional_state", "开心", 0.9)
    assert r["action"] == "added"
    assert _profile(db, user.id)["emotional_state"]["value"] == "开心"


def test_low_confidence_goes_to_pool(db_user):
    db, user = db_user
    r = apply_annotation(db, user.id, "emotional_state", "开心", 0.5)
    assert r["action"] == "pooled"
    assert _profile(db, user.id) == {}, "低置信不落画像"
    db.commit()  # SessionLocal autoflush=False：显式落库后断言池行
    rows = _pool_rows(db, user.id)
    assert len(rows) == 1
    assert rows[0].candidate_value == "开心" and rows[0].confidence == 0.5
    assert rows[0].status == "pending"


def test_superfine_threshold_higher(db_user):
    """超细性格维度（interpersonal_style，门槛 0.8）：0.75 进池、0.85 落画像"""
    db, user = db_user
    r1 = apply_annotation(db, user.id, "interpersonal_style", "温暖主导", 0.75)
    assert r1["action"] == "pooled"
    r2 = apply_annotation(db, user.id, "interpersonal_style", "温暖主导", 0.85)
    assert r2["action"] == "added"
    assert _profile(db, user.id)["interpersonal_style"]["value"] == "温暖主导"


def test_record_hits_summary(db_user):
    db, user = db_user
    result = record_hits(db, user.id, [
        {"dimension": "emotional_state", "enum_value": "开心", "confidence": 0.9},
        {"dimension": "emotional_state", "enum_value": "焦虑", "confidence": 0.5},
        {"dimension": "not_exist", "enum_value": "x", "confidence": 0.9},
    ])
    assert [r["action"] for r in result["applied"]] == ["added"]
    assert result["pooled"][0]["enum_value"] == "焦虑"
    assert result["skipped"][0]["reason"] == "unknown_dimension", "未知维度记入 skipped"


# ---------------------------------------------------------------- 更新规则
# 注：更新规则类用例用 source="interview"（用户主动作答豁免同日节流），
# 才能在同一次测试会话内连续应用多值；节流单独用 annotation 源验证。
def test_strength_accumulation_same_value(db_user):
    db, user = db_user
    apply_annotation(db, user.id, "emotional_state", "开心", 0.9, source="interview")
    apply_annotation(db, user.id, "emotional_state", "开心", 0.85, source="interview")
    entry = _profile(db, user.id)["emotional_state"]
    assert entry["strength"] == 2, "同值强度累加"
    assert entry["value"] == "开心"


def test_value_replacement_goes_to_history(db_user):
    db, user = db_user
    apply_annotation(db, user.id, "emotional_state", "开心", 0.9, source="interview")
    apply_annotation(db, user.id, "emotional_state", "焦虑", 0.9, source="interview")
    entry = _profile(db, user.id)["emotional_state"]
    assert entry["value"] == "焦虑", "异值替换"
    assert entry["history"] == ["开心"], "旧值进 history"


def test_history_capped_at_limit(db_user):
    db, user = db_user
    vals = [f"状态{i}" for i in range(HISTORY_LIMIT + 5)]
    for v in vals:
        apply_annotation(db, user.id, "emotional_state", v, 0.9, source="interview")
    entry = _profile(db, user.id)["emotional_state"]
    assert len(entry["history"]) == HISTORY_LIMIT
    assert entry["value"] == vals[-1]


def test_throttle_same_dim_same_day(db_user):
    """同日同维度节流：annotation 源第二次被节流；interview 源豁免"""
    db, user = db_user
    r1 = apply_annotation(db, user.id, "emotional_state", "开心", 0.9, source="annotation")
    r2 = apply_annotation(db, user.id, "emotional_state", "焦虑", 0.9, source="annotation")
    assert r1["action"] == "added" and r2["action"] == "throttled", "annotation 源同日节流"
    r3 = apply_annotation(db, user.id, "emotional_state", "低落", 0.9, source="interview")
    assert r3["action"] == "replaced", "interview 源（用户主动作答）豁免节流，异值替换"
    entry = _profile(db, user.id)["emotional_state"]
    assert entry["value"] == "低落"
    assert entry["history"] == ["开心"], "被节流的中间值不写入 history"


# ---------------------------------------------------------------- 同义归一 + 开放枚举
def test_alias_canonicalization(db_user):
    """同义归一：母亲 → relation_core 种子值 妈妈（不新增）"""
    db, user = db_user
    r = apply_annotation(db, user.id, "relation_core", "母亲", 0.9)
    assert r["is_new"] is False
    assert r["enum_value"] == "妈妈"
    entry = _profile(db, user.id)["relation_core"]
    assert [v["value"] for v in entry["values"]] == ["妈妈"], "relation_core 集合型维度"


def test_open_enum_adds_new_value_with_evidence(db_user):
    """开放枚举：确无等价值 → 直接新增 value（带证据锚点 + l2_evidence 表）"""
    db, user = db_user
    content = Content(user_id=user.id, content_type="text", text="我最信任的老板张姐")
    db.add(content)
    db.commit()
    r = apply_annotation(
        db, user.id, "relation_core", "老板", 0.9,
        content_id=str(content.id), evidence_text="我最信任的老板张姐",
    )
    assert r["is_new"] is True and r["enum_value"] == "老板"
    entry = _profile(db, user.id)["relation_core"]
    assert [v["value"] for v in entry["values"]] == ["老板"]
    assert entry["evidence"][0]["content_id"] == str(content.id)
    n = db.execute(
        text("SELECT count(*) FROM profile_l2_evidence WHERE user_id = :uid AND dimension = 'relation_core'"),
        {"uid": user.id},
    ).scalar()
    assert n == 1


def test_multi_value_dim_accumulates(db_user):
    """集合型维度（relation_core）：异值追加、同值累加"""
    db, user = db_user
    apply_annotation(db, user.id, "relation_core", "妈妈", 0.9, source="interview")
    apply_annotation(db, user.id, "relation_core", "伴侣", 0.9, source="interview")
    apply_annotation(db, user.id, "relation_core", "妈妈", 0.85, source="interview")
    entry = _profile(db, user.id)["relation_core"]
    values = {v["value"]: v for v in entry["values"]}
    assert set(values) == {"妈妈", "伴侣"}, "异值追加"
    assert values["妈妈"]["strength"] == 2, "同值累加"


# ---------------------------------------------------------------- 钩子接线
def test_annotate_content_hook(db_user):
    """pipeline_ext 钩子：content.text 入库即标注（文本/语音/照片 caption 共用）"""
    db, user = db_user
    content = Content(user_id=user.id, content_type="text", text="我妈妈和老婆今天都很开心")
    db.add(content)
    db.commit()
    annotate_content(db, content)
    dims = _profile(db, user.id)
    assert "妈妈" in [v["value"] for v in dims["relation_core"]["values"]]
    assert "开心" == dims["emotional_state"]["value"]


def test_hook_no_text_noop(db_user):
    db, user = db_user
    content = Content(user_id=user.id, content_type="voice", text="")
    db.add(content)
    db.commit()
    annotate_content(db, content)
    assert _profile(db, user.id) == {}


def test_hook_fail_safe(db_user, monkeypatch):
    """钩子 fail-safe：annotate 抛异常不阻断（只记日志）"""
    import logging

    from app.services.pipeline_ext import profile as profile_hook

    def boom(db, content):
        raise RuntimeError("mock 标注爆炸")

    # 钩子内为惰性 `from app.services.profile_annotator import annotate_content`，
    # 须 patch 源模块属性（调用时解析）
    monkeypatch.setattr("app.services.profile_annotator.annotate_content", boom)
    logs = []

    class Cap(logging.Handler):
        def emit(self, record):
            logs.append(record.getMessage())

    handler = Cap()
    logger = logging.getLogger("yishu.pipeline_ext.profile")
    logger.addHandler(handler)
    try:
        content = Content(user_id=db_user[1].id, content_type="text", text="妈妈今天很开心")
        profile_hook.annotate_on_ingest(db_user[0], content)
    finally:
        logger.removeHandler(handler)
    assert any("标注失败" in m for m in logs), "异常应被捕获并记录日志"


# ---------------------------------------------------------------- R2#9 并发首标竞态
def test_get_or_create_profile_reuses_existing(db_user):
    """R2#9：profile 已存在（并发赢家已提交）→ 直接复用，不重复插入"""
    from app.services.profile_annotator import get_or_create_profile

    db, user = db_user
    db.add(UserProfile(user_id=user.id, dimensions={}, version=1))
    db.commit()
    p = get_or_create_profile(db, user.id)
    assert p.user_id == user.id
    rows = db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).scalars().all()
    assert len(rows) == 1, "已存在 profile 不应重复插入"


def test_get_or_create_profile_concurrent_first_call_no_500(db_user):
    """R2#9 竞态修复：并发首标不 500——两会话同时 get_or_create 同一用户，
    on_conflict_do_nothing 兜底：无论交错如何，无异常逃逸且仅一行 profile"""
    import threading

    from app.db.session import SessionLocal
    from app.services.profile_annotator import get_or_create_profile

    db, user = db_user
    results: dict = {}

    def worker(n: int):
        s = SessionLocal()
        try:
            p = get_or_create_profile(s, user.id)
            s.commit()
            results[n] = p.user_id
        except Exception as exc:  # noqa: BLE001 —— 记录逃逸异常（不应发生）
            results[n] = exc
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(not isinstance(v, Exception) for v in results.values()), results
    rows = db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).scalars().all()
    assert len(rows) == 1, "并发首标只应有一行 profile"
