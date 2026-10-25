"""画像 evidence 打通（task-21 · B 方案）回归测试。

## 本批改了什么（背景）

画像详情页「已经了解的」区只能给结论、给不出依据 —— 根因是
`profile_annotator.display_dimensions()` 把结构化 entry 压平成 `{dim: [值]}`，
evidence 在那一层被丢掉（DB 里 1514 个维度带 evidence、共 1561 条，API 一条没有）。

修法：**新增并列字段** `dimension_evidence`，`dimensions` 形状逐字不变。
为什么必须并列而不是改形状：`dimensions: dict[str, list[str]]` 是**已发布契约**，
客户端 `utils/play_interview.uts::flattenDimensions` 与 `profile_labels.uts`
都按 `list[str]` 读 —— 改形状会让老客户端崩；新增字段老客户端读到不认识就忽略。

## 四个测试各自守什么（对应 Lead 的四条要求）

  ① 有 evidence 的维度能拿到
  ② **无 evidence 的维度不出现在 evidence 字典里**（`{}` 而不是 `{dim: []}`）
  ③ **`dimensions` 形状逐字不变**（防回归，最重要）
  ④ **别人的 evidence 不会串到你这里**

## 运行条件

`@pytest.mark.integration` —— 需要 PG（连不上由既有的 skip 逻辑处理）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.db.models import User, UserProfile
from app.db.session import SessionLocal
from app.services.interview import get_profile
from app.services.profile_annotator import display_dimensions, display_evidence
from sqlalchemy import delete as sa_delete

pytestmark = pytest.mark.integration


@pytest.fixture()
def two_users():
    """两个用户各带一个 profile —— 用户 B 用于验证「不串号」"""
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:8]
    a = User(phone=f"ev-a-{suffix}", status=1)
    b = User(phone=f"ev-b-{suffix}", status=1)
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    yield db, a, b
    for u in (a, b):
        db.execute(sa_delete(UserProfile).where(UserProfile.user_id == str(u.id)))
        db.delete(u)
    db.commit()
    db.close()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _put_profile(db, user_id: str, dimensions: dict) -> None:
    db.add(UserProfile(user_id=str(user_id), version=1, dimensions=dimensions, token_usage=0))
    db.commit()


# ══════════════════════════════════════════════════════════════════
# ① 有 evidence 的维度能拿到
# ══════════════════════════════════════════════════════════════════


def test_evidence_returned_for_dimension_that_has_it(two_users):
    """① 单值维度带 evidence ⇒ `dimension_evidence[dim]` 里能拿到 ts/text/content_id"""
    db, a, _ = two_users
    ts = _now_iso()
    cid = str(uuid.uuid4())
    _put_profile(
        db,
        a.id,
        {
            "interest_food": {
                "value": "咖啡",
                "source": "annotation",
                "confidence": 0.85,
                "evidence": [{"ts": ts, "text": "明天记得买咖啡豆", "content_id": cid}],
            }
        },
    )

    out = get_profile(db, str(a.id))

    assert "interest_food" in out["dimension_evidence"]
    rows = out["dimension_evidence"]["interest_food"]
    assert len(rows) == 1
    assert rows[0]["ts"] == ts
    assert rows[0]["text"] == "明天记得买咖啡豆"
    assert rows[0]["content_id"] == cid


def test_evidence_returned_for_multi_value_dimension(two_users):
    """① 集合型维度（values[]）的 evidence 同样能拿到（两条以上，顺序保持）"""
    db, a, _ = two_users
    _put_profile(
        db,
        a.id,
        {
            "interest_hobbies": {
                "source": "annotation",
                "values": [
                    {"value": "摄影", "confidence": 0.8},
                    {"value": "徒步", "confidence": 0.7},
                ],
                "evidence": [
                    {"ts": "2026-08-01T00:00:00+00:00", "text": "周末去拍日出", "content_id": None},
                    {"ts": "2026-08-02T00:00:00+00:00", "text": "爬山十公里", "content_id": None},
                ],
            }
        },
    )

    out = get_profile(db, str(a.id))

    rows = out["dimension_evidence"]["interest_hobbies"]
    assert len(rows) == 2
    assert [r["text"] for r in rows] == ["周末去拍日出", "爬山十公里"]
    # 集合型的值仍在 dimensions 里（两个字段互不影响）
    assert out["dimensions"]["interest_hobbies"] == ["摄影", "徒步"]


# ══════════════════════════════════════════════════════════════════
# ② 无 evidence 的维度**不出现在** evidence 字典里
# ══════════════════════════════════════════════════════════════════


def test_dimension_without_evidence_has_no_key(two_users):
    """② 无 evidence 的维度 ⇒ evidence 字典里**连 key 都没有**（不是 `{dim: []}`）

    为什么这条重要：`{dim: []}` 会被客户端读成"这个维度有依据、但内容空了"
    —— 那是"重要性"信号；真相是"从没记过依据"。两者对用户含义完全不同。
    """
    db, a, _ = two_users
    _put_profile(
        db,
        a.id,
        {
            "interest_food": {
                "value": "咖啡",
                "evidence": [{"ts": _now_iso(), "text": "买咖啡豆", "content_id": None}],
            },
            "habit_schedule": {"value": "熬夜修仙"},  # 无 evidence 键
            "life_stage": {"value": "中年", "evidence": []},  # 空列表
        },
    )

    out = get_profile(db, str(a.id))

    ev = out["dimension_evidence"]
    assert "interest_food" in ev
    assert "habit_schedule" not in ev, "无 evidence 键的维度不得出现在 evidence 字典里"
    assert "life_stage" not in ev, "空 evidence 列表不得产生 `{dim: []}`"
    # 但三个维度**都**在 dimensions 里（不因缺依据而丢维度）
    assert set(out["dimensions"]) == {"interest_food", "habit_schedule", "life_stage"}


def test_evidence_empty_when_profile_has_none(two_users):
    """② 整个 profile 都没 evidence ⇒ `dimension_evidence == {}`（空 dict，非 None）"""
    db, a, _ = two_users
    _put_profile(db, a.id, {"age_bracket": {"value": "40-49"}})

    out = get_profile(db, str(a.id))

    assert out["dimension_evidence"] == {}
    assert out["dimensions"] == {"age_bracket": ["40-49"]}


def test_evidence_empty_for_user_without_profile(two_users):
    """② 无 profile 的用户 ⇒ 两个字段都是空（不得 500、不得 None 崩溃）"""
    db, a, _ = two_users
    out = get_profile(db, str(a.id))

    assert out["dimensions"] == {}
    assert out["dimension_evidence"] == {}
    assert out["version"] == 0
    assert out["cold_start_done"] is False


# ══════════════════════════════════════════════════════════════════
# ③ `dimensions` 形状**逐字不变**（防回归，最重要的一条）
# ══════════════════════════════════════════════════════════════════


def test_dimensions_shape_is_unchanged(two_users):
    """③ `dimensions` 必须是 `dict[str, list[str]]` —— 值里**不得**混入 dict

    这是本批最重要的防回归断言：`dimensions` 是已发布契约，客户端
    `flattenDimensions` 按 `list[str]` 读。若有人图省事把 evidence 塞进
    `dimensions[d]`（变成 `[{value, evidence}]`），老客户端会崩 —— 本测试会红。
    """
    db, a, _ = two_users
    _put_profile(
        db,
        a.id,
        {
            "interest_food": {
                "value": "咖啡",
                "evidence": [{"ts": _now_iso(), "text": "买咖啡豆", "content_id": None}],
            },
            "interest_hobbies": {
                "values": [{"value": "摄影"}],
                "evidence": [{"ts": _now_iso(), "text": "拍照", "content_id": None}],
            },
        },
    )

    out = get_profile(db, str(a.id))
    dims = out["dimensions"]

    assert isinstance(dims, dict)
    for dim, vals in dims.items():
        assert isinstance(vals, list), f"{dim} 的值必须是 list，实际 {type(vals)}"
        for v in vals:
            assert isinstance(v, str), (
                f"{dim} 的列表元素必须是 str（已发布契约 dict[str, list[str]]），"
                f"实际 {type(v)} —— 把 evidence 塞进 dimensions 会让老客户端崩"
            )


def test_dimensions_matches_display_dimensions_exactly(two_users):
    """③ `dimensions` 必须与 `display_dimensions()` 的输出**完全相等**（逐字不变）

    直接对拍：新增 evidence 字段绝不允许影响 dimensions 的任何字节。
    """
    db, a, _ = two_users
    raw = {
        "interest_food": {
            "value": "咖啡",
            "evidence": [{"ts": _now_iso(), "text": "x", "content_id": None}],
        },
        "habit_schedule": {"value": "熬夜修仙"},
        "relation_core": {"values": [{"value": "妈妈"}, {"value": "爸爸"}]},
        "life_stage": [],
    }
    _put_profile(db, a.id, raw)

    out = get_profile(db, str(a.id))

    assert out["dimensions"] == display_dimensions(raw)


def test_evidence_does_not_mutate_source_dimensions(two_users):
    """③ 生成 evidence 不得修改 profile.dimensions（只读契约）

    防的坑：若 `display_evidence` 里对 entry / evidence 列表做了原地操作
    （如 pop/sort），会顺手改掉 ORM 跟踪的 JSONB 内容 ⇒ 下次 commit 落库，
    把用户数据改坏。
    """
    db, a, _ = two_users
    raw = {
        "interest_food": {
            "value": "咖啡",
            "evidence": [{"ts": _now_iso(), "text": "原文", "content_id": None}],
        }
    }
    _put_profile(db, a.id, raw)

    get_profile(db, str(a.id))

    db.refresh(
        db.query(UserProfile).filter(UserProfile.user_id == str(a.id)).one()
    )
    profile = db.query(UserProfile).filter(UserProfile.user_id == str(a.id)).one()
    assert profile.dimensions["interest_food"]["evidence"][0]["text"] == "原文"
    assert profile.dimensions["interest_food"]["value"] == "咖啡"


# ══════════════════════════════════════════════════════════════════
# ④ 别人的 evidence 不会串到你这里
# ══════════════════════════════════════════════════════════════════


def test_other_users_evidence_never_leaks(two_users):
    """④ A 只拿到自己的 evidence，B 的（即使维度同名）绝不出现"""
    db, a, b = two_users
    _put_profile(
        db,
        a.id,
        {
            "interest_food": {
                "value": "咖啡",
                "evidence": [{"ts": _now_iso(), "text": "A 的秘密原话", "content_id": None}],
            }
        },
    )
    _put_profile(
        db,
        b.id,
        {
            "interest_food": {
                "value": "奶茶",
                "evidence": [{"ts": _now_iso(), "text": "B 的私人原话", "content_id": None}],
            }
        },
    )

    out_a = get_profile(db, str(a.id))
    out_b = get_profile(db, str(b.id))

    texts_a = [r["text"] for r in out_a["dimension_evidence"]["interest_food"]]
    texts_b = [r["text"] for r in out_b["dimension_evidence"]["interest_food"]]

    assert texts_a == ["A 的秘密原话"]
    assert texts_b == ["B 的私人原话"]
    assert "B 的私人原话" not in texts_a
    assert "A 的秘密原话" not in texts_b
    # 值也不能串
    assert out_a["dimensions"]["interest_food"] == ["咖啡"]
    assert out_b["dimensions"]["interest_food"] == ["奶茶"]


# ══════════════════════════════════════════════════════════════════
# 纯函数级（无 DB）：字段形态与裁剪
# ══════════════════════════════════════════════════════════════════


def test_display_evidence_trims_long_text():
    """超长 text 被裁到上限（池侧同口径），不把整段 OCR 塞进响应"""
    from app.services.profile_annotator import _EVIDENCE_TEXT_MAX

    long_text = "啊" * (_EVIDENCE_TEXT_MAX + 200)
    out = display_evidence(
        {"d": {"value": "v", "evidence": [{"ts": "t", "text": long_text, "content_id": None}]}}
    )
    assert len(out["d"][0]["text"]) == _EVIDENCE_TEXT_MAX


def test_display_evidence_keeps_stable_field_shape():
    """三个字段始终存在（值可为 None），客户端不必做存在性判断"""
    out = display_evidence({"d": {"value": "v", "evidence": [{"text": "只有文本"}]}})
    row = out["d"][0]
    assert set(row) == {"ts", "text", "content_id"}
    assert row["ts"] is None
    assert row["text"] == "只有文本"
    assert row["content_id"] is None


def test_display_evidence_skips_non_dict_and_empty_entries():
    """非 dict 条目 / 全空条目被安全跳过（不抛错、不产生只有 None 的行）"""
    out = display_evidence(
        {
            "d1": {"value": "v", "evidence": ["裸字符串", 123, None]},
            "d2": {"value": "v", "evidence": [{"ts": None, "text": "", "content_id": None}]},
            "d3": {"value": "v", "evidence": [{"ts": "t", "text": "有内容"}]},
        }
    )
    assert "d1" not in out, "全是非 dict 条目 ⇒ 视作没有依据"
    assert "d2" not in out, "全空条目 ⇒ 视作没有依据"
    assert out["d3"][0]["text"] == "有内容"


def test_display_evidence_handles_legacy_flat_list():
    """旧扁平列表格式的维度（`{dim: [值]}`）不产生 evidence、也不抛错"""
    out = display_evidence({"d": ["值1", "值2"]})
    assert out == {}


def test_display_evidence_on_empty_input():
    """空/None 输入 ⇒ 空 dict（不抛错）"""
    assert display_evidence({}) == {}
    assert display_evidence(None) == {}
