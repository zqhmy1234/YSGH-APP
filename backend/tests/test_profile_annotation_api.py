"""D-18 端点测试：画像标注池的读 + 确认/否定。

## 测什么（每条对应一个会骗人的形态）

1. 只列**自己的** pending（不能串号）；
2. 确认 ⇒ **真的写进画像**（若只改 status，「确认了但画像没变」是假状态）；
3. 否定 ⇒ 只标记，**不动画像**（pending 本来就没进画像）；
4. 重复处置 ⇒ 409（不静默覆盖）；
5. 别人的标注 ⇒ 404（不泄露存在性）。
"""
from __future__ import annotations

import uuid

import pytest
from app.core.security import create_access_token
from app.db.models.auth import User
from app.db.models.profile import ProfileAnnotationPool, UserProfile
from fastapi.testclient import TestClient


@pytest.fixture
def client_and_user():
    from app.db.session import SessionLocal
    from app.main import app

    db = SessionLocal()
    uid = str(uuid.uuid4())
    user = User(id=uid, phone=f"139{uuid.uuid4().hex[:8]}", nickname="t")
    db.add(user)
    db.commit()
    token = create_access_token(uid)
    try:
        yield TestClient(app), db, uid, token
    finally:
        db.rollback()
        db.query(ProfileAnnotationPool).filter(ProfileAnnotationPool.user_id == uid).delete()
        db.query(UserProfile).filter(UserProfile.user_id == uid).delete()
        db.query(User).filter(User.id == uid).delete()
        db.commit()
        db.close()


def _add(db, uid: str, dim: str, val: str, conf: float, status: str = "pending"):
    row = ProfileAnnotationPool(
        user_id=uid, dimension=dim, candidate_value=val, confidence=conf,
        raw_text="他喜欢在雨里散步", status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def test_list_only_own_pending(client_and_user):
    client, db, uid, token = client_and_user
    my_id = _add(db, uid, "d_other", "温柔", 0.4)
    # 别人的
    other = str(uuid.uuid4())
    db.add(User(id=other, phone=f"138{uuid.uuid4().hex[:8]}", nickname="o"))
    db.commit()
    _add(db, other, "d_other", "尖锐", 0.9)

    r = client.get("/api/v1/profile/annotations", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    ids = [it["id"] for it in r.json()["data"]]
    assert my_id in ids
    assert len([i for i in ids if i == my_id]) == 1

    # 清理别人
    db.query(ProfileAnnotationPool).filter(ProfileAnnotationPool.user_id == other).delete()
    db.query(User).filter(User.id == other).delete()
    db.commit()


def test_confirm_writes_into_profile(client_and_user):
    """核心：确认必须真的写进 `user_profile.dimensions`（否则是假状态）。"""
    client, db, uid, token = client_and_user
    aid = _add(db, uid, "d_other", "温柔", 0.4)

    r = client.post(
        f"/api/v1/profile/annotations/{aid}",
        json={"action": "confirmed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["written"] is True

    prof = db.query(UserProfile).filter(UserProfile.user_id == uid).one()
    dims = prof.dimensions or {}
    assert "d_other" in dims, f"确认后画像里必须有该维度，实际 {list(dims)}"
    assert dims["d_other"]["value"] == "温柔"
    # source 必须是 user_confirmed（详情页靠它区分「AI 猜的」与「用户确认的」）
    assert dims["d_other"]["source"] == "user_confirmed"


def test_confirm_with_corrected_value(client_and_user):
    """用户可以把 AI 猜的值改掉（「咖啡」→「奶茶」）。"""
    client, db, uid, token = client_and_user
    aid = _add(db, uid, "interest_food", "咖啡", 0.6)
    r = client.post(
        f"/api/v1/profile/annotations/{aid}",
        json={"action": "confirmed", "value": "奶茶"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    prof = db.query(UserProfile).filter(UserProfile.user_id == uid).one()
    assert (prof.dimensions or {})["interest_food"]["value"] == "奶茶"


def test_discard_does_not_touch_profile(client_and_user):
    """否定只标记，不动画像（pending 本来就没进画像）。"""
    client, db, uid, token = client_and_user
    aid = _add(db, uid, "d_other", "温柔", 0.4)
    r = client.post(
        f"/api/v1/profile/annotations/{aid}",
        json={"action": "discarded"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["written"] is False
    row = db.query(ProfileAnnotationPool).filter(ProfileAnnotationPool.id == aid).one()
    assert row.status == "discarded"
    assert db.query(UserProfile).filter(UserProfile.user_id == uid).first() is None


def test_double_decide_conflicts(client_and_user):
    """重复处置 ⇒ 409（不静默覆盖用户的判断）。"""
    client, db, uid, token = client_and_user
    aid = _add(db, uid, "d_other", "温柔", 0.4)
    h = {"Authorization": f"Bearer {token}"}
    assert client.post(f"/api/v1/profile/annotations/{aid}", json={"action": "discarded"}, headers=h).status_code == 200
    r = client.post(f"/api/v1/profile/annotations/{aid}", json={"action": "confirmed"}, headers=h)
    assert r.status_code == 409, r.text


def test_other_users_annotation_is_404(client_and_user):
    """别人的标注 ⇒ 404（不泄露"存在但不属于你"）。"""
    client, db, uid, token = client_and_user
    other = str(uuid.uuid4())
    db.add(User(id=other, phone=f"137{uuid.uuid4().hex[:8]}", nickname="o2"))
    db.commit()
    aid = _add(db, other, "d_other", "尖锐", 0.9)

    r = client.post(
        f"/api/v1/profile/annotations/{aid}",
        json={"action": "confirmed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404, r.text

    db.query(ProfileAnnotationPool).filter(ProfileAnnotationPool.user_id == other).delete()
    db.query(User).filter(User.id == other).delete()
    db.commit()
