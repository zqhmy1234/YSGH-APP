"""跨模态检索（A/B/C 三方案，2026-11-01）单测

为什么单独一个文件、且**全部不依赖 Qdrant/BGE**：跨模态的三处改动（路由三态 /
以图搜图双路 / 音频端点）都是"编排逻辑"，真正的失效模式是**接线**——
路由吐不出 mixed、合并路被过滤条件吃掉、音频端点压根没注册。这些用 fake store +
fake ASR 就能精确判死，跑得又快，可以进提交前快速门禁；真实召回质量由
`test_rag.py` / `test_image_search.py` 的集成用例守。

覆盖：
  1. `_route_query` 三态：含图词且还剩实词 → mixed（不再锁死照片）；
     纯图指代 → image；无图词 → text。`_resolve_intent` 显式覆盖。
  2. `search()` mixed 意图：照片路 + 全量路双召回，结果**可含非 photo 类型**。
  3. `search_by_image`：图片路 + caption 文本路 RRF 合并，结果**可含文字/语音**。
  4. `POST /api/v1/search/audio`：fake ASR → 与 `POST /api/v1/search` 同构的结果。
  5. 回归：`SearchQuery.intent='image'` 显式传入时仍只回照片。
"""
from __future__ import annotations

import pytest
from app.schemas.search import SearchQuery
from app.services.rag import _resolve_intent, _route_query, search

# ---------------------------------------------------------------------------
# fake 向量库：模拟两件事——
#   · `store.search` 只看 `content_types` 过滤（照片路 vs 全量路）
#   · `search_image` 只命中 photo 点（真实 schema：text/voice 点没有 image_vec）
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.entries = [
            {"content_id": "photo-1", "content_type": "photo", "text": "西湖边的荷花，游船上拍的"},
            {"content_id": "text-1", "content_type": "text", "text": "杭州西湖那天很热，荷花全开了"},
            {"content_id": "voice-1", "content_type": "voice", "text": "语音备忘：西湖游船四十分钟"},
        ]

    @staticmethod
    def _match(entry: dict, filters: dict | None) -> bool:
        """复刻 vector_store._to_filter 的 content_types 语义（含 image→photo 别名）"""
        types = (filters or {}).get("content_types")
        if not types:
            return True
        allowed = {"photo" if t == "image" else t for t in types}
        return entry["content_type"] in allowed

    def search(self, dense, sparse, filters=None, limit=50, collection=None):
        self.calls.append(("search", dict(filters or {})))
        hits = [e for e in self.entries if self._match(e, filters)][:limit]
        return [
            {
                "content_id": e["content_id"],
                "score": round(0.02 - i * 0.001, 4),
                "dense_score": round(0.62 - i * 0.01, 4),
                "sparse_score": 0.2,
                "text": e["text"],
                "content_type": e["content_type"],
            }
            for i, e in enumerate(hits)
        ]

    def search_image(self, vec, filters=None, limit=50, collection=None):
        self.calls.append(("search_image", dict(filters or {})))
        hits = [
            e for e in self.entries
            if e["content_type"] == "photo" and self._match(e, filters)
        ][:limit]
        return [
            {
                "content_id": e["content_id"],
                "score": round(0.72 - i * 0.01, 4),
                "dense_score": round(0.72 - i * 0.01, 4),
                "sparse_score": 0.0,
                "text": e["text"],
                "content_type": "photo",
            }
            for i, e in enumerate(hits)
        ]


@pytest.fixture()
def fake_store(monkeypatch):
    """把 `rag.search` 的向量库/编码器换成 fake（只测编排，不拉 1.2GB 模型）"""
    store = _FakeStore()
    monkeypatch.setattr("app.services.rag.get_store", lambda: store)
    monkeypatch.setattr("app.services.rag.encode_query", lambda text: ([0.1] * 4, {"1": 0.5}))
    return store


# ---------------------------------------------------------------------------
# 1. 路由三态 + 显式 intent
# ---------------------------------------------------------------------------


def test_route_query_yields_mixed_for_image_words_with_content():
    """含图词但还有实词 → mixed（旧实现一律 image ⇒ 文字/语音被条件排除）

    这是 C 方案的核心判据：图词只说明"提到了图片"，不说明"只要图片"。
    """
    assert _route_query("照片里的猫是什么时候拍的") == "mixed"
    assert _route_query("截图上写的那个地址") == "mixed"
    assert _route_query("图片里那家餐厅叫什么") == "mixed"


def test_route_query_keeps_image_for_pure_pointer():
    """纯图指代（图词之外没有实词）→ 仍是 image，保持"找那张图"的精确性"""
    assert _route_query("这张照片") == "image"
    assert _route_query("我的相册") == "image"
    assert _route_query("三张截图") == "image"  # 数词不算实词


def test_route_query_text_when_no_image_words():
    """无图词 → text（与改动前逐字一致）"""
    assert _route_query("考研笔记") == "text"
    assert _route_query("") == "text"


def test_resolve_intent_explicit_wins():
    """显式 intent 覆盖路由：image/text 是硬约束，auto 才用路由结果"""
    assert _resolve_intent("image", "mixed") == "image"
    assert _resolve_intent("text", "image") == "text"
    assert _resolve_intent("auto", "mixed") == "mixed"
    # 未知值（旧客户端/脏数据）按 auto 处理，fail-open
    assert _resolve_intent("bogus", "mixed") == "mixed"


# ---------------------------------------------------------------------------
# 2. C：文字含图词 → 照片路 + 全量路（结果可含文字/语音）
# ---------------------------------------------------------------------------


def test_mixed_query_recalls_photo_and_full_paths(fake_store):
    """mixed 意图走双路召回（照片路 + 全量路），结果**可含非 photo 类型**"""
    result = search(SearchQuery(q="照片里的荷花是什么时候开的", limit=10))

    assert result.intent == "mixed"
    ids = [h.content_id for h in result.hits]
    assert "photo-1" in ids
    assert "text-1" in ids or "voice-1" in ids, f"mixed 应带回文字/语音, got {ids}"

    kinds = [c[0] for c in fake_store.calls]
    assert kinds.count("search") == 2, f"应双路召回, got {kinds}"
    assert any(c[1].get("content_types") == ["photo"] for c in fake_store.calls)


def test_mixed_query_respects_explicit_content_types(fake_store):
    """显式 content_types 是硬约束：mixed 不得被照片路越权覆盖（旧代码会覆盖）"""
    result = search(SearchQuery(q="照片里的荷花", content_types=["text"], limit=10))
    assert all(h.content_type == "text" for h in result.hits)
    # 只打一条通路（用户已明确类型，无需照片路）
    assert len(fake_store.calls) == 1


# ---------------------------------------------------------------------------
# 3. A：以图搜图 → 全模态（图片路 + caption 文本路 RRF 合并）
# ---------------------------------------------------------------------------


def test_search_by_image_returns_non_photo_hits(monkeypatch):
    """以图搜图双路合并后，结果**可包含非 photo 类型**（fake store 注入 text/voice 点）

    真实写入侧的根因：text/voice 点只有 text_vec、没有 image_vec ⇒ 纯图像空间里
    物理上没有文字/语音点。本用例锁住"图片路命中照片 + caption 文本路补全
    '图里的事记在文字/语音里'的那部分"，这就是 A 方案的"双路保底"。
    """
    from app.services.rag import search_by_image

    store = _FakeStore()
    monkeypatch.setattr("app.services.rag.image.get_store", lambda: store)
    monkeypatch.setattr(
        "app.services.embedding.encode_query",
        lambda text: ([0.1] * 4, {"1": 0.5}),
    )
    monkeypatch.setattr(
        "app.services.rag.image._cached_image_caption",
        lambda path: "西湖边的荷花，游船上拍的",
    )

    result = search_by_image("C:/fake/query.jpg", SearchQuery(q="[image]", limit=10))

    assert result.intent == "image"  # 查询模态仍是图片
    ids = [h.content_id for h in result.hits]
    assert "photo-1" in ids, f"图片路应命中照片, got {ids}"
    assert "text-1" in ids or "voice-1" in ids, f"文本路应带来文字/语音, got {ids}"
    # 双路都真的被调用（不是靠单路侥幸命中）
    kinds = [c[0] for c in store.calls]
    assert "search_image" in kinds and "search" in kinds, kinds
    # 溯源可解释"这条是怎么来的"（A 方案可观测性）
    by_id = {h.content_id: h for h in result.hits}
    assert "image" in by_id["photo-1"].trace.get("recall_channels", [])
    non_photo = by_id.get("text-1") or by_id.get("voice-1")
    assert "text" in non_photo.trace.get("recall_channels", [])


def test_search_by_image_degraded_when_caption_fails(monkeypatch):
    """caption 失败 → 降级空结果（既有契约不变）"""
    from app.services.rag import search_by_image

    def boom(path):
        raise RuntimeError("VL 不可用")

    store = _FakeStore()
    monkeypatch.setattr("app.services.rag.image.get_store", lambda: store)
    monkeypatch.setattr("app.services.embedding.encode_query", lambda text: ([0.1] * 4, {"1": 0.5}))
    monkeypatch.setattr("app.services.rag.image._cached_image_caption", boom)

    result = search_by_image("C:/fake/query.jpg", SearchQuery(q="[image]"))
    assert result.degraded is True
    assert result.hits == []


# ---------------------------------------------------------------------------
# 4. 回归：显式 intent='image' 仍只回照片
# ---------------------------------------------------------------------------


def test_explicit_image_intent_stays_photo_only(fake_store):
    """`SearchQuery.intent='image'` 显式指定 → 仍只回照片（精确性回归保护）

    为什么保留这条窄路：混合双路在"我只要照片"的场景会带进文字/语音噪声；
    显式传入即用户明确的收窄要求，必须尊重（语义见 rag/intent._resolve_intent）。
    """
    # 查询本身是纯文字（无图词）——若非显式 intent，路由会给 text
    result = search(SearchQuery(q="西湖荷花", intent="image", limit=10))

    assert result.intent == "image"
    assert [h.content_id for h in result.hits] == ["photo-1"]
    assert all(c[1].get("content_types") == ["photo"] for c in fake_store.calls)


# ---------------------------------------------------------------------------
# 5. B：音频端点（fake ASR + 真检索链路 → 与文本检索同构）
# ---------------------------------------------------------------------------


def _wav_bytes() -> bytes:
    """最小合法 WAV 头（validate_audio_bytes 看 RIFF/WAVE 魔数；fake ASR 不读内容）"""
    return b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"fmt " + (16).to_bytes(4, "little")


@pytest.fixture()
def client():
    """TestClient + **依赖覆盖**（认证/DB 打桩）

    为什么不连真 PG：本文件只验证"音频端点 → ASR → 检索"的接线，与内容表无关；
    真库依赖会让这条用例在无 DB 环境（本机/CI 快速层）整片跳过，接线回归就
    没人守了。需要真库的链路断言在 `test_search.py` / `test_rag.py`。
    """
    from types import SimpleNamespace

    from app.api.deps import get_current_user
    from app.db.session import get_db
    from app.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="cm-test-user")
    app.dependency_overrides[get_db] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def user_headers():
    """依赖已打桩，鉴权头只需存在（真 token 校验在 _auth 依赖里，已覆盖）"""
    return {"Authorization": "Bearer test-token"}


def test_search_audio_endpoint_returns_text_search_shape(client, user_headers, fake_store, monkeypatch):
    """`POST /api/v1/search/audio` 存在，且 fake ASR 下返回与文本检索**同构**的结构"""
    monkeypatch.setattr(
        "app.services.rag.audio.transcribe_for_search",
        lambda path, preferred="auto": "杭州西湖荷花",
    )

    r = client.post(
        "/api/v1/search/audio",
        files={"file": ("q.wav", _wav_bytes(), "audio/wav")},
        params={"limit": 5},
        headers=user_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == "OK"
    data = body["data"]

    # 与文本检索同构：字段集一致
    r2 = client.post("/api/v1/search", json={"q": "杭州西湖荷花", "limit": 5}, headers=user_headers)
    assert r2.status_code == 200, r2.text
    text_data = r2.json()["data"]
    assert set(data.keys()) == set(text_data.keys()), (set(data.keys()), set(text_data.keys()))

    # 转写文本作为 query 回传（用户要能看到"这句话被听成了什么"）
    assert data["query"] == "杭州西湖荷花"
    assert data["intent"] in ("text", "image", "mixed")
    assert isinstance(data["hits"], list)
    assert {"content_id", "content_type", "text", "score", "trace"} <= (
        set(data["hits"][0].keys()) if data["hits"] else set()
    ) or data["hits"] == []


def test_search_audio_rejects_bad_audio(client, user_headers):
    """非音频内容 → 422 ASR_001（魔数校验，复用 ASR 域错误码）"""
    r = client.post(
        "/api/v1/search/audio",
        files={"file": ("q.wav", b"not-an-audio-file", "audio/wav")},
        headers=user_headers,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "ASR_001"


def test_search_audio_requires_auth(client):
    """音频检索是个人记忆检索：未登录 → 401"""
    from app.api.deps import get_current_user
    from app.main import app

    app.dependency_overrides.pop(get_current_user, None)
    r = client.post("/api/v1/search/audio", files={"file": ("q.wav", _wav_bytes(), "audio/wav")})
    assert r.status_code == 401


def test_search_by_audio_empty_transcript_is_not_degraded(fake_store, monkeypatch):
    """转写为空（没说话）→ 空结果但 degraded=False：不说话不是系统降级"""
    from app.services.rag.audio import search_by_audio

    monkeypatch.setattr("app.services.rag.audio.transcribe_for_search", lambda p, preferred="auto": "")
    result = search_by_audio("C:/fake/q.wav", SearchQuery(q="[audio]"))
    assert result.hits == []
    assert result.degraded is False
    assert result.total == 0


def test_search_by_audio_degrades_on_asr_exception(fake_store, monkeypatch):
    """转写抛非 ASR 异常（模型/网络）→ 降级空结果，不冒泡成 500"""
    from app.services.rag.audio import search_by_audio

    def boom(path, preferred="auto"):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("app.services.rag.audio.transcribe_for_search", boom)
    result = search_by_audio("C:/fake/q.wav", SearchQuery(q="[audio]"))
    assert result.hits == []
    assert result.degraded is True
