"""AG3 验收：PostgREST 兼容薄层（`storage/database/local_client.py`）对本地 Postgres 的行为。

覆盖上游真实用到的调用形态：
  insert → 取回 id / select + count="exact" / eq·neq·gte 过滤 / order / limit / range /
  关系嵌入 `memory_categories(name)` / maybe_single / update（带条件）/ delete（带条件）
以及两条**安全护栏**：未知列名必须报错（否则静默返回空是最难查的故障）、无条件的
update/delete 必须拒绝（否则是全表事故）。

前置：agent/.env 里的 DATABASE_URL 指向本地 Postgres，且三表已由
backend 的 alembic 迁移（a4b5c6d7e8f9）创建。
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(AGENT_ROOT / ".env", override=False)

from storage.database.local_client import LocalClient  # noqa: E402

USER = f"ag3-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def client() -> LocalClient:
    return LocalClient()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    c = LocalClient()
    c.table("memories").delete().eq("user_id", USER).execute()
    c.table("knowledge_collections").delete().eq("user_id", USER).execute()
    c.table("memory_categories").delete().eq("name", f"AG3分类-{USER}").execute()


def test_insert_select_count_and_embed(client: LocalClient):
    """插入分类与记忆 → 带 count 的查询、关系嵌入、JSON 列原样往返。"""
    cat = client.table("memory_categories").insert({"name": f"AG3分类-{USER}", "icon": "book"}).execute()
    cat_id = cat.data[0]["id"]
    assert cat_id

    mem = client.table("memories").insert(
        {
            "content": "AG3 测试记忆",
            "user_id": USER,
            "mood": "平静",
            "topics": ["学习", "工作"],
            "category_id": cat_id,
        }
    ).execute()
    mem_id = mem.data[0]["id"]
    assert mem.data[0]["topics"] == ["学习", "工作"]  # JSON 列原样往返

    got = client.table("memories").select(
        "id, content, mood, topics, memory_categories(name)", count="exact"
    ).eq("user_id", USER).execute()
    assert got.count == 1
    assert len(got.data) == 1
    row = got.data[0]
    assert row["content"] == "AG3 测试记忆"
    assert row["memory_categories"]["name"] == f"AG3分类-{USER}"

    single = client.table("memories").select("id").eq("id", mem_id).maybe_single().execute()
    assert single.data is not None and single.data["id"] == mem_id

    none_hit = client.table("memories").select("id").eq("id", -1).maybe_single().execute()
    assert none_hit.data is None


def test_filters_order_limit_iso_datetime(client: LocalClient):
    """ISO 字符串过滤（上游大量 `.gte('created_at', datetime.utcnow().isoformat())`）、排序、limit。"""
    for i in range(3):
        client.table("memories").insert({"content": f"AG3 过滤 {i}", "user_id": USER, "importance": i + 1}).execute()

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    future_hits = client.table("memories").select("id", count="exact")
    assert future_hits.eq("user_id", USER).gte("created_at", future).execute().count == 0

    past_hits = client.table("memories").select("id", count="exact")
    assert past_hits.eq("user_id", USER).gte("created_at", past).execute().count == 3

    desc_query = client.table("memories").select("importance").eq("user_id", USER)
    desc_rows = desc_query.order("importance", desc=True).limit(2).execute()
    assert [r["importance"] for r in desc_rows.data] == [3, 2]

    neq_rows = client.table("memories").select("id", count="exact").eq("user_id", USER).neq("importance", 2).execute()
    assert neq_rows.count == 2


def test_update_and_delete_require_conditions(client: LocalClient):
    """带条件 update/delete 正常；无条件则**拒绝**（防全表事故）。"""
    mem_id = client.table("memories").insert({"content": "AG3 待更新", "user_id": USER}).execute().data[0]["id"]

    upd = client.table("memories").update({"summary": "已更新"}).eq("id", mem_id).execute()
    assert upd.data[0]["summary"] == "已更新"

    with pytest.raises(ValueError, match="拒绝全表更新"):
        client.table("memories").update({"summary": "x"}).execute()
    with pytest.raises(ValueError, match="拒绝全表删除"):
        client.table("memories").delete().execute()

    dele = client.table("memories").delete().eq("id", mem_id).eq("user_id", USER).execute()
    assert len(dele.data) == 1
    assert client.table("memories").select("id", count="exact").eq("id", mem_id).execute().count == 0


def test_unknown_column_and_table_fail_loudly(client: LocalClient):
    """未知列/表**立即报错**——若静默返回空，是最难查的一类故障。"""
    with pytest.raises(ValueError, match="不存在列"):
        client.table("memories").select("id").eq("usr_id", USER).execute()
    with pytest.raises(ValueError, match="不存在列"):
        client.table("memories").insert({"cntent": "typo", "user_id": USER}).execute()
    with pytest.raises(ValueError, match="未知表"):
        client.table("not_a_table").select("id").execute()


def test_collection_table_roundtrip(client: LocalClient):
    """knowledge_collections 的写入-读回（收藏域与记忆域同薄层）。"""
    cid = client.table("knowledge_collections").insert(
        {
            "user_id": USER,
            "content": "AG3 收藏内容",
            "content_type": "article",
            "title": "标题",
            "related_memory_ids": [1, 2],
        }
    ).execute().data[0]["id"]
    row = client.table("knowledge_collections").select("*").eq("id", cid).maybe_single().execute().data
    assert row["title"] == "标题"
    assert row["related_memory_ids"] == [1, 2]
