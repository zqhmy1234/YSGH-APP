"""W3（2026-10-08）：agent 镜像记忆的预置 tags → contents.tags_json

覆盖三件事：
1. 契约校验：ContentCreate.tags 去空/去重/保序/封顶 10 条、单条 ≤32 字。
2. 落库条件：source=agent 写 tags_json（App 详情页 chips 的唯一数据源）；
   source=app 忽略 tags（公开端点不因新字段扩面）。
3. 管线合并语义：pipeline_tagging.merge_tags —— 已有预置标签在前、LLM 新标签
   去重并入、封顶 20（否则镜像行被 worker 处理时预置主题被整体覆写冲掉）。

运行前置：本地 PostgreSQL（redis 已 monkeypatch 掉，不依赖）。
"""
import uuid

import pytest
from app.api.contents import create_content
from app.schemas.content import ContentCreate
from app.services.pipeline_tagging import merge_tags
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# 1. 契约校验（纯 pydantic，无 DB）
# ---------------------------------------------------------------------------


def test_tags_validator_strips_dedupes_and_drops_empty():
    req = ContentCreate(
        content_type="text", text="x",
        tags=["旅行", " 旅行 ", "", "健康", "旅行"],
    )
    assert req.tags == ["旅行", "健康"]


def test_tags_all_blank_becomes_none():
    req = ContentCreate(content_type="text", text="x", tags=["", "   "])
    assert req.tags is None


def test_tags_over_10_rejected():
    with pytest.raises(ValidationError):
        ContentCreate(content_type="text", text="x", tags=[f"t{i}" for i in range(11)])


def test_tag_over_32_chars_rejected():
    with pytest.raises(ValidationError):
        ContentCreate(content_type="text", text="x", tags=["长" * 33])


# ---------------------------------------------------------------------------
# 3. 管线合并语义（纯函数）
# ---------------------------------------------------------------------------


def test_merge_tags_keeps_existing_first_and_dedupes():
    assert merge_tags(["旅行"], ["健康", "旅行"]) == ["旅行", "健康"]
    assert merge_tags(None, ["a", "a"]) == ["a"]
    assert merge_tags([], []) == []


def test_merge_tags_caps_at_20():
    out = merge_tags([f"old{i}" for i in range(15)], [f"new{i}" for i in range(15)])
    assert len(out) == 20
    assert out[:15] == [f"old{i}" for i in range(15)]  # 已有在前不被挤掉


# ---------------------------------------------------------------------------
# 2. 落库条件（create_content 直调；redis 入队 monkeypatch 掉）
# ---------------------------------------------------------------------------


@pytest.fixture()
def no_queue(monkeypatch):
    """create_content 尾部的 RQ 入队改 no-op（本地无 redis；打点在模块级，
    见 app/api/contents/__init__.py 头注释的 monkeypatch 口径）。"""
    calls: list[tuple] = []

    def _fake_enqueue(fn, *args, **kwargs):
        calls.append((fn, args, kwargs))
        return None

    monkeypatch.setattr("app.api.contents.enqueue_unique", _fake_enqueue)
    return calls


def test_agent_source_persists_tags_to_tags_json(db_user, no_queue):
    from app.db.models import Content

    db, user = db_user
    resp = create_content(
        ContentCreate(
            content_type="text", text="今天去了香山", source="agent",
            client_generated_id=f"w3-{uuid.uuid4().hex[:8]}",
            tags=["旅行", "健康"],
        ),
        db=db, user=user,
    )
    row = db.get(Content, resp.data.id)
    assert row is not None
    assert row.tags_json == ["旅行", "健康"]


def test_app_source_ignores_tags(db_user, no_queue):
    db, user = db_user
    resp = create_content(
        ContentCreate(
            content_type="text", text="App 路径", source="app",
            client_generated_id=f"w3-{uuid.uuid4().hex[:8]}",
            tags=["不该", "落库"],
        ),
        db=db, user=user,
    )
    from app.db.models import Content

    row = db.get(Content, resp.data.id)
    assert row.tags_json is None
