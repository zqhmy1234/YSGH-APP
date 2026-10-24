"""搜索式一次性问答测试（2026-10-16 冻结契约：搜索=回答卡+证据）。

覆盖产品/工程硬要求（见任务卡）：
  ① 有证据 ⇒ 有 answer，且 `answer_sources ⊆ 命中集`（含越界序号剔除）；
  ② 无命中/证据不足 ⇒ `answer is None`（且**不得**误报降级）；
  ③ LLM 不可用（mock/异常/超时/解析失败）⇒ `answer is None` 且 `degraded=True` 且**不抛异常**；
  ④ 回答里混入 DSML/JSON 标记 ⇒ 被 `strip_model_markup` 剥净；
  ⑤ `want_answer=False` ⇒ **零** LLM 调用（默认零影响，最关键的一条）；
  ⑥ `latency_ms` 口径（2026-10-16 协调者拍板）：默认路径**数值不变**（仍只取检索段），
     开启回答的路径**计入回答耗时**（"数字不再失实"的可回归证据）。

分层：
  - `generate_answer` 的用例是纯单元测试（直接构造 SearchHit，不走管线）；
  - 管线接线（门控 + degraded 合并）用例把 `encode_query`/`get_store`/`_rewrite_query`
    打桩为确定性空/假实现 ⇒ 免 PG/Qdrant/BGE-M3，秒级可跑。
"""
from __future__ import annotations

import json

import app.services.llm_ops.search_answer as sa
import pytest
from app.schemas.search import SearchHit, SearchQuery
from app.services.llm_ops.search_answer import generate_answer

# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------


def _hit(cid: str, text: str = "去年夏天和爸妈去杭州西湖坐了手摇船", **kw) -> SearchHit:
    return SearchHit(
        content_id=cid,
        content_type=kw.pop("content_type", "text"),
        text=text,
        taken_at=kw.pop("taken_at", None),
        place=kw.pop("place", None),
        event_id=None,
        event_title=None,
        score=kw.pop("score", 0.9),
        trace={},
        **kw,
    )


# ---------------------------------------------------------------------------
# ① 有证据 ⇒ 有 answer，answer_sources ⊆ 命中集
# ---------------------------------------------------------------------------


def test_answer_with_evidence_and_sources_subset(monkeypatch):
    h1, h2 = _hit("cid-1"), _hit("cid-2", text="另一段无关记忆")
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(
        sa,
        "chat_text",
        lambda system, user: json.dumps({"answer": "你去年夏天和爸妈去了杭州西湖。", "sources": [0]}),
    )

    answer, sources, failed = generate_answer("我去年夏天去哪了", [h1, h2])

    assert answer == "你去年夏天和爸妈去了杭州西湖。"
    assert sources == ["cid-1"]
    assert set(sources) <= {h1.content_id, h2.content_id}, "answer_sources 必须是命中集的子集"
    assert failed is False


def test_answer_sources_drop_out_of_range_and_dedupe(monkeypatch):
    """越界/非法/重复序号一律丢弃 —— 不得出现未召回的 content_id。"""
    h1, h2 = _hit("cid-1"), _hit("cid-2")
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(
        sa,
        "chat_text",
        lambda system, user: json.dumps({"answer": "记下了。", "sources": [0, 99, "1", 0, "x", -1]}),
    )

    answer, sources, failed = generate_answer("q", [h1, h2])

    assert answer == "记下了。"
    assert sources == ["cid-1", "cid-2"]  # 99/x/-1 丢弃，重复 0 去重
    assert set(sources) <= {h1.content_id, h2.content_id}
    assert failed is False


# ---------------------------------------------------------------------------
# ② 无命中 / 证据不足 ⇒ answer is None（且非降级）
# ---------------------------------------------------------------------------


def test_answer_no_hits_returns_none_without_llm_call(monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(sa, "chat_text", lambda system, user: called.append(1) or "{}")

    answer, sources, failed = generate_answer("q", [])

    assert answer is None and sources == []
    assert failed is False, "证据不足是正常无答案，不得误报降级"
    assert called == [], "无证据时不该调用 LLM"


def test_answer_hits_without_text_returns_none(monkeypatch):
    """命中的全是无文本条目（如纯图片未回填 caption）⇒ 证据不足。"""
    called: list[int] = []
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(sa, "chat_text", lambda system, user: called.append(1) or "{}")

    answer, sources, failed = generate_answer("q", [_hit("cid-1", text="   ")])

    assert answer is None and sources == [] and failed is False
    assert called == []


def test_answer_model_judges_insufficient(monkeypatch):
    """模型按提示词返回空 answer（判"证据不足以回答"）⇒ 正常无答案，非降级。"""
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(
        sa, "chat_text", lambda system, user: json.dumps({"answer": "", "sources": []})
    )

    answer, sources, failed = generate_answer("q", [_hit("cid-1")])

    assert answer is None and sources == [] and failed is False


# ---------------------------------------------------------------------------
# ③ LLM 不可用 / 超时 / 异常 / 解析失败 ⇒ answer None + failed True + 不抛异常
# ---------------------------------------------------------------------------


def test_answer_llm_unavailable_degrades(monkeypatch):
    monkeypatch.setattr(sa, "llm_available", lambda: False)

    answer, sources, failed = generate_answer("q", [_hit("cid-1")])

    assert answer is None and sources == []
    assert failed is True, "模型不可用必须如实置降级"


def test_answer_llm_exception_degrades_without_raising(monkeypatch):
    def boom(system, user):
        raise RuntimeError("百炼不可用（模拟）")

    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(sa, "chat_text", boom)

    answer, sources, failed = generate_answer("q", [_hit("cid-1")])  # 不抛异常

    assert answer is None and sources == [] and failed is True


def test_answer_llm_timeout_degrades(monkeypatch):
    """超时（模拟：future.result 抛 TimeoutError 等价路径）⇒ 降级。"""
    import concurrent.futures as cf

    def slow(system, user):
        raise cf.TimeoutError("模拟超时")

    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(sa, "chat_text", slow)

    answer, sources, failed = generate_answer("q", [_hit("cid-1")])

    assert answer is None and sources == [] and failed is True


def test_answer_parse_failure_degrades(monkeypatch):
    """输出非 JSON（解析失败）⇒ 降级（口径见模块文档）。"""
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(sa, "chat_text", lambda system, user: "这是一段不是 JSON 的自由文本")

    answer, sources, failed = generate_answer("q", [_hit("cid-1")])

    assert answer is None and sources == [] and failed is True


# ---------------------------------------------------------------------------
# ④ 回答混入 DSML / 工具调用 JSON ⇒ 被剥净
# ---------------------------------------------------------------------------


def test_answer_strips_dsml_and_tool_call_markup(monkeypatch):
    dirty = (
        '你去年夏天在杭州。<｜DSML｜tool_calls｜DSML｜>'
        '{"tool_calls": [{"name": "search", "arguments": "{}"}]}'
    )
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(
        sa, "chat_text", lambda system, user: json.dumps({"answer": dirty, "sources": [0]})
    )

    answer, sources, failed = generate_answer("q", [_hit("cid-1")])

    assert answer is not None
    assert "DSML" not in answer, "DSML 标记必须剥净（2026-10-15 真机事故防线）"
    assert "tool_calls" not in answer
    assert answer.startswith("你去年夏天在杭州")
    assert failed is False


def test_answer_truncated_to_length_cap(monkeypatch):
    long_answer = "记" * 500
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(
        sa, "chat_text", lambda system, user: json.dumps({"answer": long_answer, "sources": []})
    )

    answer, _sources, _failed = generate_answer("q", [_hit("cid-1")])

    assert answer is not None and len(answer) <= sa._MAX_ANSWER_CHARS


# ---------------------------------------------------------------------------
# ⑤ 管线接线：want_answer 门控 + degraded 合并（打桩免 PG/Qdrant/BGE-M3）
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, raw_hits: list[dict] | None = None):
        self.raw_hits = raw_hits or []
        self.calls = 0

    def search(self, dense, sparse, filters=None, limit=50, collection=None):
        self.calls += 1
        return list(self.raw_hits)


@pytest.fixture()
def stub_pipeline(monkeypatch):
    """把 rag 管线的外部依赖替换为确定性实现（空召回 / 假 store）——免真向量库。"""
    import app.services.rag as rag

    monkeypatch.setattr(rag, "_rewrite_query", lambda q: (q.q, {}, set()))
    monkeypatch.setattr(rag, "encode_query", lambda text: ([0.0], {}))
    store = _FakeStore()
    monkeypatch.setattr(rag, "get_store", lambda: store)
    return store


def test_pipeline_want_answer_false_makes_no_llm_call(stub_pipeline, monkeypatch):
    """【最关键】want_answer=False ⇒ 不引入任何一次回答 LLM 调用（默认零影响）。"""
    import app.services.rag as rag

    calls: list[int] = []
    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(sa, "chat_text", lambda system, user: calls.append(1) or "{}")

    result = rag.search(SearchQuery(q="杭州", limit=3))  # want_answer 默认 False

    assert calls == [], "默认行为不得多一次 LLM 调用（影响 P95 与成本）"
    assert result.answer is None
    assert result.answer_sources == []
    assert stub_pipeline.calls >= 1  # 确认检索确已跑过（否则本断言无意义）


def test_pipeline_want_answer_true_invokes_answer_with_hits(stub_pipeline, monkeypatch):
    """want_answer=True ⇒ 调用回答且入参为本次命中；answer/sources 正确回填。"""
    import app.services.rag as rag

    stub_pipeline.raw_hits = [
        {
            "content_id": "cid-1",
            "text": "去年夏天和爸妈去杭州西湖坐了手摇船",
            "score": 0.9,
            "dense_score": 0.9,
            "sparse_score": 0.0,
        }
    ]
    seen: dict = {}

    def fake_generate(query, hits):
        seen["query"] = query
        seen["hit_ids"] = [h.content_id for h in hits]
        return "你去年夏天去了杭州西湖。", [h.content_id for h in hits], False

    monkeypatch.setattr(sa, "generate_answer", fake_generate)

    result = rag.search(SearchQuery(q="我去年夏天去哪了", limit=3, want_answer=True))

    assert seen["query"] == "我去年夏天去哪了"
    assert seen["hit_ids"] == ["cid-1"]
    assert result.answer == "你去年夏天去了杭州西湖。"
    assert result.answer_sources == ["cid-1"]
    assert set(result.answer_sources) <= {h.content_id for h in result.hits}
    assert result.degraded is False


def test_pipeline_answer_failure_sets_degraded(stub_pipeline, monkeypatch):
    """回答失败（failed=True）⇒ 并入既有 degraded 口径（不新增布尔位）。"""
    import app.services.rag as rag

    monkeypatch.setattr(sa, "generate_answer", lambda query, hits: (None, [], True))

    result = rag.search(SearchQuery(q="杭州", limit=3, want_answer=True))

    assert result.answer is None
    assert result.degraded is True


def test_pipeline_answer_insufficient_does_not_set_degraded(stub_pipeline, monkeypatch):
    """证据不足（failed=False）⇒ answer=None 但 degraded 保持原值（不得谎报降级）。"""
    import app.services.rag as rag

    monkeypatch.setattr(sa, "generate_answer", lambda query, hits: (None, [], False))

    result = rag.search(SearchQuery(q="杭州", limit=3, want_answer=True))

    assert result.answer is None
    assert result.degraded is False


# ---------------------------------------------------------------------------
# ⑥ latency_ms 口径：默认路径不变 + 回答路径计入耗时
# ---------------------------------------------------------------------------


class _FakeClock:
    """受控 `time` 替身：按序返回 `perf_counter` 值，**用尽即抛**。

    用尽即抛 = 一旦 `latency_ms` 多算/少算一次 `perf_counter`（口径漂移），测试当场失败。
    """

    def __init__(self, values: list[float]):
        self._values = list(values)

    def perf_counter(self) -> float:
        if not self._values:
            raise AssertionError("perf_counter 调用次数超出预期 —— latency_ms 口径发生漂移")
        return self._values.pop(0)


def test_latency_default_path_unchanged(stub_pipeline, monkeypatch):
    """【默认零变化的可回归证据】want_answer=False ⇒ latency_ms 仍只取检索段（改动前口径）。

    `perf_counter` 仅被调用 2 次（start + 检索段结束）⇒ latency=(100.25-100.0)*1000=250。
    若有人把 `latency_ms` **无条件**挪到回答步骤之后，就会多出第 3 次调用
    ⇒ `_FakeClock` 抛错 ⇒ 本用例即刻失败（守住"默认路径数值逐字不变"）。
    """
    import app.services.rag as rag

    monkeypatch.setattr(rag, "time", _FakeClock([100.0, 100.25]))
    called: list[int] = []
    monkeypatch.setattr(
        sa, "generate_answer", lambda q, hits: called.append(1) or ("x", [], False)
    )

    result = rag.search(SearchQuery(q="杭州", limit=3))  # want_answer 默认 False

    assert result.latency_ms == 250
    assert called == [], "默认路径不得进入回答子系统"


def test_latency_answer_path_includes_answer_time(stub_pipeline, monkeypatch):
    """【数字不再失实】want_answer=True ⇒ latency_ms 计入回答段（第 3 次 perf_counter）。"""
    import app.services.rag as rag

    stub_pipeline.raw_hits = [
        {
            "content_id": "cid-1",
            "text": "去年夏天杭州西湖",
            "score": 0.9,
            "dense_score": 0.9,
            "sparse_score": 0.0,
        }
    ]
    # ticks: start / 检索段结束 / 回答步骤之后 —— 后两刻度间 250ms 即回答耗时
    monkeypatch.setattr(rag, "time", _FakeClock([100.0, 100.25, 100.5]))
    monkeypatch.setattr(sa, "generate_answer", lambda q, hits: ("答", ["cid-1"], False))

    result = rag.search(SearchQuery(q="杭州", limit=3, want_answer=True))

    assert result.latency_ms == 500, "回答路径的 latency_ms 必须含回答耗时（避免门禁假绿）"


def test_latency_answer_path_includes_real_sleep(stub_pipeline, monkeypatch):
    """实现无关版（协调者建议的 sleep 断言）：回答函数真实 sleep ⇒ latency_ms ≥ sleep。"""
    import time as _time

    import app.services.rag as rag

    stub_pipeline.raw_hits = [
        {
            "content_id": "cid-1",
            "text": "去年夏天杭州西湖",
            "score": 0.9,
            "dense_score": 0.9,
            "sparse_score": 0.0,
        }
    ]

    def slow_generate(query, hits):
        _time.sleep(0.08)
        return "答", ["cid-1"], False

    monkeypatch.setattr(sa, "generate_answer", slow_generate)

    result = rag.search(SearchQuery(q="杭州", limit=3, want_answer=True))

    assert result.latency_ms >= 60, "latency_ms 必须包含回答步骤的真实耗时"


# ---------------------------------------------------------------------------
# ⑦ 超时保护：1.0s 硬上限**真的**在保护主链路（不止写在文档里）
# ---------------------------------------------------------------------------


def test_pipeline_answer_timeout_protects_main_path(stub_pipeline, monkeypatch):
    """喂一个**故意慢**（sleep 2s）的回答 LLM ⇒ 断言 1.0s 硬上限生效，主链路不被拖垮。

    不变量：`/search` 在 ~1.0s 返回（远早于 2s 慢调用）、`answer is None`、`degraded=True`，
    且**确曾调用**该慢 LLM（证明上限路径被真正触发，而非别的原因提前返回）。

    ⚠️ 慢必须打在 `chat_text`（真实 LLM 调用点）上：1.0s 上限由 `_call_with_timeout`
    包在 `chat_text` **外层**实现；若直接 stub `generate_answer` 会**绕过**上限逻辑，
    就测不到本不变量。

    断言口径（2026-10-16 协调者拍板，避免 flaky）：
      · 只用**墙钟上界** `<1.5s` 区分"上限生效(~1.0s)"与"未生效(~2.0s=慢调用自然结束)"；
      · **不设"不早于"下限**（墙钟下限在负载机器上是 flaky 源）；
      · 判别力改由"慢 LLM 确被调用"这一**确定性事实**补偿——否则若回答子系统因
        其它原因（如证据抽空）提前返回 `answer=None/degraded=True`，本用例会假绿。
    """
    import time as _time

    import app.services.rag as rag

    stub_pipeline.raw_hits = [
        {
            "content_id": "cid-1",
            "text": "去年夏天杭州西湖",
            "score": 0.9,
            "dense_score": 0.9,
            "sparse_score": 0.0,
        }
    ]

    invoked: list[int] = []

    # 2026-10-24（A-11）：上限由 1.0s 调到 2.5s（实测 1.35~1.75s 可交付回答）。
    # 本用例的「慢」必须**相对当前上限**定义，否则改上限就会让用例过期
    # （原来 sleep 2.0s，上限抬到 2.5s 后慢调用反而自然结束 ⇒ 断言失效）。
    # 故改为「上限 + 1.5s 的慢」，并相应地按上限推上界。
    _slow_s = sa.ANSWER_TIMEOUT_SECONDS + 1.5

    def slow_chat(system, user):
        invoked.append(1)
        _time.sleep(_slow_s)  # 故意远超当前上限
        return '{"answer": "早该被超时截断", "sources": [0]}'

    monkeypatch.setattr(sa, "llm_available", lambda: True)
    monkeypatch.setattr(sa, "chat_text", slow_chat)

    t0 = _time.perf_counter()
    result = rag.search(SearchQuery(q="去年夏天去哪了", limit=3, want_answer=True))
    elapsed = _time.perf_counter() - t0

    assert invoked == [1], "慢 LLM 必须被真正调用，否则上限未被检验"
    assert result.answer is None
    assert result.degraded is True
    # 上界 = 上限 + 0.5s 余量 ⇒ 证明是**超时截断**、而非等慢调用自然结束（后者要 _slow_s 秒）
    _upper = sa.ANSWER_TIMEOUT_SECONDS + 0.5
    assert elapsed < _upper, (
        f"应被 {sa.ANSWER_TIMEOUT_SECONDS}s 上限截断（远早于 {_slow_s}s 慢调用），实测 {elapsed:.2f}s"
    )
