"""锁定标签端到端（B1:139「删除 + 用户锁定，LLM 不再自动写回」）。

为什么有这批用例：客户端 `TagEditPanel` 的「固定」原先只写本机 storage
（`portrait_locked_tags`），后端完全不知情 ⇒ 画像重建时 AI 会把用户删掉/固定的标签
写回来，"锁定"是空承诺。本批把「锁定真的生效」钉成可回归的事实。

覆盖：
  - 服务层规整（去空白 / 去空串 / 去重保序）+ 非法输入显式报错（不静默丢项）
  - 覆盖语义：PUT 是**整体覆盖**（可清空 = 全部解锁），不是并集
  - GET 无副作用（不建空画像行）
  - 写回闸门 ①「不新增/不覆盖」：命中锁定值 → skipped/locked，画像无该值，且**不进
    低置信池**（进池＝换条路放行，等于没锁）；别名（母亲）也必须被规范值（妈妈）拦住
  - 写回闸门 ②「不删除」：单值维度当前值被锁定 → 拒绝异值替换，旧值不被踢进 history
  - **画像重建（用户点「刷新画像」的真实入口）跑完，锁定标签仍在、未被 AI 改写**，
    同句里未锁定的命中照常落库（闸门是点名拦截，不是整批静默）
  - HTTP：GET / PUT /api/v1/profile/locked-tags（鉴权、信封、422 错误码、失败不改集合）

前置：PG yishu 库（integration 分层）。
"""
from __future__ import annotations

import uuid

import pytest
from app.db.models import Content, ProfileAnnotationPool, UserProfile
from app.db.session import SessionLocal
from app.services import profile_rebuild
from app.services.profile_annotator import (
    LOCKED_REASON_CURRENT,
    LOCKED_REASON_VALUE,
    apply_annotation,
    record_hits,
)
from app.services.profile_locked_tags import (
    MAX_TAG_LEN,
    MAX_TAGS,
    get_locked_tags,
    normalize_tags,
    replace_locked_tags,
)
from sqlalchemy import select

pytestmark = pytest.mark.integration

PATH = "/api/v1/profile/locked-tags"


def _dims(db, user_id) -> dict:
    row = db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    return dict(row.dimensions or {}) if row else {}


def _multi_values(dims: dict, dimension: str) -> list[str]:
    entry = dims.get(dimension) or {}
    return [v.get("value") for v in (entry.get("values") or []) if isinstance(v, dict)]


# ---------------------------------------------------------------- 服务层规整
def test_normalize_tags_strips_dedupes_and_keeps_order():
    assert normalize_tags([" 咖啡 ", "咖啡", "", "   ", "露营"]) == ["咖啡", "露营"]
    assert normalize_tags([]) == []
    assert normalize_tags(None) == []


def test_normalize_tags_rejects_invalid_input():
    """非法即抛——静默丢项会让「PUT 成功但集合变了」无从解释。"""
    with pytest.raises(ValueError):
        normalize_tags("咖啡")  # 不是数组
    with pytest.raises(ValueError):
        normalize_tags([1, 2])  # 非字符串
    with pytest.raises(ValueError):
        normalize_tags(["x" * (MAX_TAG_LEN + 1)])
    with pytest.raises(ValueError):
        normalize_tags([f"t{i}" for i in range(MAX_TAGS + 1)])


def test_replace_is_whole_overwrite_not_union(db_user):
    """整体覆盖：不给某个已固定标签 = 取消固定（增量语义无法表达"取消"）。"""
    db, user = db_user
    replace_locked_tags(db, user.id, ["咖啡", "露营"])
    assert get_locked_tags(db, user.id) == ["咖啡", "露营"]

    replace_locked_tags(db, user.id, ["咖啡"])
    assert get_locked_tags(db, user.id) == ["咖啡"], "PUT 是覆盖不是并集"

    replace_locked_tags(db, user.id, [])
    assert get_locked_tags(db, user.id) == [], "空数组 = 全部解锁"


def test_get_locked_tags_has_no_side_effect(db_user):
    """GET 不建空画像行（读接口不该产生写副作用）。"""
    db, user = db_user
    assert get_locked_tags(db, user.id) == []
    db.commit()
    assert db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).scalar_one_or_none() is None


# ---------------------------------------------------------------- 闸门① 不新增/不覆盖
def test_locked_value_is_not_written_by_ai(db_user):
    """命中锁定值 ⇒ 不落画像、也不进低置信池（进池等于换条路放行）。"""
    db, user = db_user
    replace_locked_tags(db, user.id, ["妈妈"])

    r = apply_annotation(db, user.id, "relation_core", "妈妈", 0.9, source="interview")
    assert r["action"] == "skipped" and r["reason"] == LOCKED_REASON_VALUE
    assert _dims(db, user.id) == {}, "锁定值不得被写回画像"

    db.commit()
    pooled = db.execute(
        select(ProfileAnnotationPool).where(ProfileAnnotationPool.user_id == user.id)
    ).scalars().all()
    assert pooled == [], "锁定值连低置信池都不能进（否则周级复核又会写回）"


def test_lock_matches_canonical_value_through_alias(db_user):
    """锁定存的是规范值（妈妈），命中别名（母亲）同样要被拦住。"""
    db, user = db_user
    replace_locked_tags(db, user.id, ["妈妈"])
    r = apply_annotation(db, user.id, "relation_core", "母亲", 0.9, source="interview")
    assert r["action"] == "skipped" and r["reason"] == LOCKED_REASON_VALUE
    assert _dims(db, user.id) == {}


def test_unlocked_values_still_flow(db_user):
    """闸门是点名拦截：同批未锁定的命中照常落库。"""
    db, user = db_user
    replace_locked_tags(db, user.id, ["妈妈"])
    result = record_hits(db, user.id, [
        {"dimension": "relation_core", "enum_value": "妈妈", "confidence": 0.9},
        {"dimension": "emotional_state", "enum_value": "开心", "confidence": 0.9},
    ])
    assert result["locked"][0]["reason"] == LOCKED_REASON_VALUE
    assert [r["enum_value"] for r in result["applied"]] == ["开心"]
    assert _multi_values(_dims(db, user.id), "relation_core") == []


# ---------------------------------------------------------------- 闸门② 不删除
def test_locked_current_value_is_not_replaced(db_user):
    """单值维度：异值替换会把旧值踢进 history = 删掉用户的标签 ⇒ 拒绝。"""
    db, user = db_user
    apply_annotation(db, user.id, "emotional_state", "开心", 0.9, source="interview")
    replace_locked_tags(db, user.id, ["开心"])

    r = apply_annotation(db, user.id, "emotional_state", "焦虑", 0.9, source="interview")
    assert r["action"] == "skipped" and r["reason"] == LOCKED_REASON_CURRENT
    assert r["locked_value"] == "开心"

    entry = _dims(db, user.id)["emotional_state"]
    assert entry["value"] == "开心", "锁定值必须原地保留"
    assert entry.get("history") in (None, []), "锁定值不得被推进 history"


# ---------------------------------------------------------------- 重建（真实入口）
def test_rebuild_does_not_rewrite_locked_tags(db_user):
    """用户点「刷新画像」→ RQ 重建跑完后，锁定标签仍在、未被 AI 改写。"""
    db, user = db_user
    replace_locked_tags(db, user.id, ["妈妈"])
    marker = f"锁定重建{uuid.uuid4().hex[:8]}"
    content = Content(
        user_id=user.id,
        content_type="text",
        text=f"{marker} 我妈妈今天很开心",
        status="done",
        source="app",
    )
    db.add(content)
    db.commit()

    summary = profile_rebuild.rebuild_user_profile(str(user.id))
    assert summary["processed"] == 1 and summary["failed"] == 0

    db.expire_all()
    dims = _dims(db, user.id)
    assert "妈妈" not in _multi_values(dims, "relation_core"), "锁定标签被 AI 写回（闸门失效）"
    assert dims["emotional_state"]["value"] == "开心", "未锁定命中应照常落库"
    assert get_locked_tags(db, user.id) == ["妈妈"], "锁定集合本身不被重建改写"


# ---------------------------------------------------------------- HTTP 契约
def test_api_locked_tags_roundtrip(client, auth_headers):
    user_id, headers = auth_headers("locktags")
    r = client.get(PATH, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"tags": []}

    r = client.put(PATH, json={"tags": [" 咖啡 ", "咖啡", "露营"]}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"tags": ["咖啡", "露营"]}, "服务端规整后返回最新集合"

    assert client.get(PATH, headers=headers).json()["data"] == {"tags": ["咖啡", "露营"]}

    # 真源在库：换一个会话（新 db 连接）读到的仍是同一集合
    db = SessionLocal()
    try:
        assert get_locked_tags(db, user_id) == ["咖啡", "露营"]
    finally:
        db.close()

    r = client.put(PATH, json={"tags": []}, headers=headers)
    assert r.json()["data"] == {"tags": []}, "空数组 = 全部解锁"


def test_api_locked_tags_rejects_invalid_without_mutating(client, auth_headers):
    _user_id, headers = auth_headers("locktags2")
    client.put(PATH, json={"tags": ["咖啡"]}, headers=headers)

    r = client.put(PATH, json={"tags": ["x" * (MAX_TAG_LEN + 1)]}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "PROFILE_002"
    assert client.get(PATH, headers=headers).json()["data"]["tags"] == ["咖啡"], (
        "非法请求不得改动已存集合"
    )


def test_api_locked_tags_requires_auth(client):
    assert client.get(PATH).status_code == 401
    assert client.put(PATH, json={"tags": []}).status_code == 401
