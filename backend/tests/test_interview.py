"""F7 冷启动访谈测试（B1-7 产品部三问 → 画像维度激活）

覆盖：
  - 三问答案 → 维度激活（relation_core/life_events/values_priority）
  - 画像落库 + 历史记录
  - 未命中枚举 → 维度扩展队列（不落画像，B1 闭集约束）
  - 历史值裁剪（每维度保留最近 10 条）
  - 复述确认文本
  - API 冒烟（questions / answers / profile）
前置：PG yishu 库
"""
import uuid

import pytest
from app.db.models import ProfileDimensionHistory, ProfileDimensionPending, User, UserProfile
from app.db.session import SessionLocal
from app.services.interview import HISTORY_LIMIT, QUESTIONS, get_profile, submit_answers
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"iv-test-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(ProfileDimensionPending).where(ProfileDimensionPending.user_id == user.id))
    db.execute(sa_delete(ProfileDimensionHistory).where(ProfileDimensionHistory.user_id == user.id))
    db.execute(sa_delete(UserProfile).where(UserProfile.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def test_questions_are_three():
    """产品部三问：最重要的人/人生转折/最骄傲的事"""
    keys = [q["key"] for q in QUESTIONS]
    assert keys == ["important_person", "life_turn", "proud_thing"]


def test_submit_activates_dimensions(db_user):
    """三问答案 → L0 维度激活"""
    db, user = db_user
    result = submit_answers(
        db, user.id,
        {
            "important_person": "我妈妈和老婆",
            "life_turn": "高考后去外地读大学，毕业创业",
            "proud_thing": "坚持跑完马拉松",
        },
    )
    assert "家人" in result["dimensions"]["relation_core"]
    assert "伴侣" in result["dimensions"]["relation_core"]
    assert "高考" in result["dimensions"]["life_events"]
    assert "创业" in result["dimensions"]["life_events"]
    assert "坚持" in result["dimensions"]["values_priority"]
    assert "我理解到" in result["confirmation"]


def test_profile_persisted(db_user):
    """画像落库 + 版本递增 + 历史记录"""
    db, user = db_user
    submit_answers(
        db, user.id,
        {
            "important_person": "最好的朋友",
            "life_turn": "转行做程序员",
            "proud_thing": "带大两个孩子",
        },
    )
    profile = db.execute(
        UserProfile.__table__.select().where(UserProfile.user_id == user.id)
    ).first()
    assert profile is not None
    assert profile.version >= 1
    assert "挚友" in profile.dimensions["relation_core"]
    hist = db.execute(
        ProfileDimensionHistory.__table__.select().where(
            ProfileDimensionHistory.user_id == user.id
        )
    ).fetchall()
    assert len(hist) >= 3
    p = get_profile(db, user.id)
    assert p["cold_start_done"] is True


def test_unmapped_goes_to_extension_queue(db_user):
    """B1 闭集（审查 MINOR）：未命中枚举的回答不落画像（无"其他"），进扩展队列

    同一未映射回答重复提交 → count 累计；status 保持 pending。
    """
    db, user = db_user
    r1 = submit_answers(
        db, user.id,
        {
            "important_person": "我的宠物狗豆豆",
            "life_turn": "在海边看了一次日出",
            "proud_thing": "连续冥想一百天",
        },
    )
    # 不落画像维度、不出现"其他"
    assert r1["dimensions"]["relation_core"] == []
    assert r1["dimensions"]["life_events"] == []
    assert r1["dimensions"]["values_priority"] == []
    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    ).scalar_one()
    assert "其他" not in {v for vals in profile.dimensions.values() for v in vals}

    # 扩展队列：三条未映射回答全部入队
    pending = db.execute(
        select(ProfileDimensionPending).where(ProfileDimensionPending.user_id == user.id)
    ).scalars().all()
    assert {p.raw_answer for p in pending} == {
        "我的宠物狗豆豆", "在海边看了一次日出", "连续冥想一百天",
    }
    assert all(p.count == 1 and p.status == "pending" for p in pending)

    # 再次提交同一未映射回答 → count 累计
    # （同会话 Core INSERT...ON CONFLICT 与身份映射交互可能返回陈旧实例，
    #   populate_existing 强制从 DB 刷新——DB 侧 upsert 已正确累计）
    submit_answers(
        db, user.id,
        {"important_person": "我的宠物狗豆豆", "life_turn": "在海边看了一次日出", "proud_thing": "连续冥想一百天"},
    )
    p2 = db.execute(
        select(ProfileDimensionPending)
        .where(
            ProfileDimensionPending.user_id == user.id,
            ProfileDimensionPending.raw_answer == "我的宠物狗豆豆",
        )
        .execution_options(populate_existing=True)
    ).scalar_one()
    assert p2.count == 2


def test_history_trimmed_to_limit(db_user):
    """历史裁剪（审查 MINOR）：每维度仅保留最近 HISTORY_LIMIT 条"""
    db, user = db_user
    for i in range(HISTORY_LIMIT + 5):
        submit_answers(
            db, user.id,
            {
                "important_person": "妈妈",
                "life_turn": "毕业",
                "proud_thing": f"坚持第{i}次",
            },
        )
    n = db.execute(
        select(func.count()).select_from(ProfileDimensionHistory).where(
            ProfileDimensionHistory.user_id == user.id,
            ProfileDimensionHistory.dimension == "relation_core",
        )
    ).scalar()
    assert n == HISTORY_LIMIT, "relation_core 历史应裁剪到 10 条"
    n2 = db.execute(
        select(func.count()).select_from(ProfileDimensionHistory).where(
            ProfileDimensionHistory.user_id == user.id,
            ProfileDimensionHistory.dimension == "life_events",
        )
    ).scalar()
    assert n2 == HISTORY_LIMIT


def test_interview_api_smoke(db_user):
    """API 冒烟：questions / answers / profile"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    client = TestClient(app)

    def fake_user():
        return user

    app.dependency_overrides[deps.get_current_user] = fake_user
    try:
        r0 = client.get("/api/v1/interview/questions")
        assert r0.status_code == 200 and len(r0.json()["data"]) == 3
        r = client.post("/api/v1/interview/answers", json={
            "answers": {"important_person": "我妈妈", "life_turn": "考研上岸", "proud_thing": "创业成功"}
        })
        assert r.status_code == 200, r.text
        assert r.json()["data"]["dimensions"]["relation_core"]
        r2 = client.get("/api/v1/interview/profile")
        assert r2.status_code == 200 and r2.json()["data"]["cold_start_done"] is True
    finally:
        app.dependency_overrides.clear()
