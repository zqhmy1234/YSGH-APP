"""画像「真删除」端点（D07-12 · 2026-09-25 功能修复波 · 用户拍板「按建议来」）。

**缺陷原貌**：画像域**一个写端点都没有** ⇒ B1 铁律「用户操作优先」无载体；客户端
"清除画像数据"的确认弹窗承诺「删除全部画像维度与证据锚点，不可恢复」，实际**只清
手机本地 storage**（弹窗文案失实）。

**本批口径**：`DELETE /api/v1/interview/profile` 真删服务端画像数据
（维度值/历史/未命中答案/低置信池/**证据锚点**），**保留** `profile_sensitive`
（"别再提这些话题"的保护性数据），保留条数如实回报；幂等；只清本人。
"""
from __future__ import annotations

import pytest
from app.db.models import (
    ProfileAnnotationPool,
    ProfileDimensionHistory,
    ProfileDimensionPending,
    ProfileSensitive,
    UserProfile,
)
from app.db.session import SessionLocal
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _seed(db, user_id: str, dimension: str = "personality_core") -> None:
    """铺满画像五处数据（含只有 raw-SQL 写入方的证据锚点表）。"""
    db.add(UserProfile(user_id=user_id, dimensions={dimension: {"values": ["温柔"]}}))
    db.add(ProfileDimensionHistory(user_id=user_id, dimension=dimension, value="温柔"))
    db.add(
        ProfileDimensionPending(
            user_id=user_id, dimension=dimension, raw_answer="他喜欢在雨里散步", count=1
        )
    )
    db.add(
        ProfileAnnotationPool(
            user_id=user_id, raw_text="他喜欢在雨里散步", dimension=dimension,
            candidate_value="温柔", confidence=0.4,
        )
    )
    db.execute(
        text(
            "INSERT INTO profile_l2_evidence (dimension, user_id, evidence_content_ids) "
            "VALUES (:d, :u, '[]'::jsonb)"
        ),
        {"d": dimension, "u": user_id},
    )
    db.add(ProfileSensitive(user_id=user_id, topic="爷爷", disposition="forbid", locked=True))
    db.commit()


def _count(db, model, user_id: str) -> int:
    return db.query(model).filter(model.user_id == user_id).count()


def _evidence_count(db, user_id: str) -> int:
    return db.execute(
        text("SELECT count(*) FROM profile_l2_evidence WHERE user_id = :u"), {"u": user_id}
    ).scalar_one()


def test_clear_profile_deletes_all_portrait_tables_but_keeps_sensitive(client, auth_headers):
    user_id, headers = auth_headers("pclear")
    db = SessionLocal()
    try:
        _seed(db, user_id)
        assert _count(db, UserProfile, user_id) == 1
        assert _evidence_count(db, user_id) == 1

        r = client.delete("/api/v1/interview/profile", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["cleared"]["user_profile"] == 1
        assert data["cleared"]["profile_l2_evidence"] == 1
        assert data["cleared"]["profile_dimension_history"] == 1
        assert data["cleared"]["profile_dimension_pending"] == 1
        assert data["cleared"]["profile_annotation_pool"] == 1
        assert data["total"] == 5
        assert data["sensitive_topics_kept"] == 1, "敏感话题属保护性数据，必须保留并如实回报"

        db.expire_all()
        assert _count(db, UserProfile, user_id) == 0
        assert _count(db, ProfileDimensionHistory, user_id) == 0
        assert _count(db, ProfileDimensionPending, user_id) == 0
        assert _count(db, ProfileAnnotationPool, user_id) == 0
        assert _evidence_count(db, user_id) == 0
        # 保护性数据仍在（清除画像 ≠ 移除"别再提这个话题"的保护）
        assert _count(db, ProfileSensitive, user_id) == 1

        # 画像读取随之回到"空画像"（GET 与 DELETE 口径一致）
        g = client.get("/api/v1/interview/profile", headers=headers)
        assert g.status_code == 200
        assert g.json()["data"]["dimensions"] == {}
    finally:
        db.close()


def test_clear_profile_is_idempotent(client, auth_headers):
    """重复调用安全：无数据时各计数为 0、仍 200（客户端可安心重试）。"""
    _user_id, headers = auth_headers("pclear2")
    first = client.delete("/api/v1/interview/profile", headers=headers)
    assert first.status_code == 200
    second = client.delete("/api/v1/interview/profile", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["total"] == 0


def test_clear_profile_does_not_touch_other_users(client, auth_headers):
    """只清本人（user_id 只取自 token）。"""
    mine, my_headers = auth_headers("pclearA")
    other, _other_headers = auth_headers("pclearB")
    db = SessionLocal()
    try:
        _seed(db, mine, dimension="d_mine")
        _seed(db, other, dimension="d_other")

        assert client.delete("/api/v1/interview/profile", headers=my_headers).status_code == 200

        db.expire_all()
        assert _count(db, UserProfile, mine) == 0
        assert _count(db, UserProfile, other) == 1, "他人的画像不得被连带清除"
        assert _evidence_count(db, other) == 1
    finally:
        db.close()


def test_clear_profile_requires_auth(client):
    assert client.delete("/api/v1/interview/profile").status_code == 401


def test_clear_profile_response_schema_is_stable(client, auth_headers):
    """出参字段稳定（客户端按 cleared/kept/total 展示"删了什么、留下什么"）。"""
    _user_id, headers = auth_headers("pclear3")
    data = client.delete("/api/v1/interview/profile", headers=headers).json()["data"]
    assert set(data) == {"cleared", "sensitive_topics_kept", "total"}
    assert isinstance(data["cleared"], dict)
