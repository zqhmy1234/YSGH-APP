"""「刚记录、已 done、但向量还没写」的内容必须仍能搜到（2026-10-15 · 台账 §7.33）。

## 现象（真机 + 集成双实测）

紧跟 `status=done` 的一次检索返回 `200 + hits=[] + degraded=false` —— 把"**还没索引**"
伪装成"**真的没有**" ✗（客户端只能显示"没有找到相关记忆"，而内容明明刚记进去）。

## 根因（代码实证）

worker **先提交 `status=done`、之后才写向量**（`pipeline.py:445-447` → `:461-463`），
而 BGE-M3 编码 + Qdrant upsert 要数十秒（冷启动尤甚）⇒ 检索落在"DB 有、Qdrant 还没点"
的时间窗内；检索侧只对**已召回**的命中做 DB 回查校验，**不会把 DB-only 的内容补进来**
（`recall.py:142-143`）。

## 本用例怎么钉住它

**直接构造那个时间窗**：落一条 `status=done` 但**从不写任何向量**的内容（等价于"索引还没到"），
断言搜索仍能命中原句 —— 即 `rag.__init__` 里"召回为空 ⇒ 走 PG 兜底"那段生效，
且**如实置 `degraded=True`**（否则又会把"尚未索引"伪装成"没有"）。
"""
import uuid

import pytest
from app.db.models import Content
from app.schemas.search import SearchQuery
from app.services.rag import search as rag_search  # 与 api/search.py:17 同一导入方式（函数名是 search）
from app.services.vector_store import default_collection, get_store, point_id_for

pytestmark = pytest.mark.integration


def _make_unindexed_text(db, user) -> tuple[str, str]:
    """落一条 done 文本内容，**不写任何向量**（模拟 done 已提交、索引尚未完成）"""
    marker = f"索引时窗探针{uuid.uuid4().hex[:6]}"
    row = Content(
        user_id=user.id,
        content_type="text",
        source="app",
        text=marker,
        status="done",
    )
    db.add(row)
    db.commit()
    return str(row.id), marker


def test_search_hits_content_without_vector_yet(db_user):
    """直调 `rag_search`（`api/search.py:81` 端点内部调的就是它，免去鉴权脚手架）"""
    db, user = db_user
    cid, marker = _make_unindexed_text(db, user)

    # 前置：它**确实没有**向量点（否则本用例证明不了兜底生效）
    store = get_store()
    pid = point_id_for(cid)
    assert store.client.retrieve(collection_name=default_collection(), ids=[pid]) == []

    result = rag_search(SearchQuery(q=marker, limit=5), db=db, user_id=str(user.id))
    ids = [h.content_id for h in result.hits]
    assert cid in ids, f"索引未就绪的内容也应被兜底捞回；hits={ids} degraded={result.degraded}"
    assert result.degraded is True, "走兜底必须如实置 degraded（否则又把'尚未索引'伪装成'没有'）"
