"""Wave2-F 评测体系测试（research/rag_benchmark + scripts/build_truth_corpus）

覆盖（RAG评测体系 §6 落地项）：
- 三指标：faithfulness / relevancy / context_precision / evaluate_answer_quality
- 真值评估钩子：expect_empty 负样本、显式 id 检索、行为层占位、跨模态 route 聚合
- B/C/D 语料 ingestion（build_truth_corpus）：稳定 uuid / 模态对齐 / manifest / A 批重映射
- Query 改写层正确率（_check_rewrite_case 路由判定）

纯逻辑测试（无需 Qdrant/BGE/LLM）；改写用例走规则版 _rewrite_query（首次 import ~30s）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))  # 仓库根：research.* / scripts.* 包导入
sys.path.insert(0, str(_ROOT / "research"))
sys.path.insert(0, str(_ROOT / "backend"))

import pytest  # noqa: E402
from rag_benchmark.metrics import (  # noqa: E402
    context_precision,
    evaluate_answer_quality,
    faithfulness,
    relevancy,
)

# ---- 三指标（纯逻辑）----


def test_faithfulness_grounded_and_fabricated():
    assert faithfulness("去年夏天去了杭州西湖。", ["去年夏天去了杭州西湖，坐了手摇船。"]) == 1.0
    assert faithfulness("我们去了月球旅行。", ["去年夏天去了杭州西湖。"]) == 0.0
    assert faithfulness("", ["来源"]) == 0.0
    assert faithfulness("有道理", []) == 0.0
    # 部分支撑：一句有据 + 一句编造 → 0.5
    assert faithfulness("去年去了杭州。然后我们登上了火星。", ["去年去了杭州。"], threshold=0.5) == 0.5


def test_relevancy():
    assert relevancy("杭州旅行", "去年夏天去了杭州西湖") > 0.0
    assert relevancy("马拉松", "今天去买菜") == 0.0
    assert relevancy("", "任意答案") == 0.0
    assert relevancy("查询", "") == 0.0


def test_relevancy_with_embedder():
    def embedder(t):  # 简单位向量：命中关键词得 1
        return [1.0 if "杭州" in t else 0.0, 1.0 if "西湖" in t else 0.0]

    r = relevancy("杭州", "杭州西湖", embedder=embedder)
    assert 0.0 <= r <= 1.0
    # embedder 抛异常 → 回落 n-gram 不抛错
    def bad(t):  # noqa: ANN001
        raise RuntimeError("boom")

    assert relevancy("杭州旅行", "杭州西湖", embedder=bad) > 0.0


def test_context_precision():
    # 相关排前 → 1.0
    assert context_precision([{"is_relevant": True}, {"is_relevant": True}, {"is_relevant": False}]) == 1.0
    # 相关排后 → 低（RAGAS CP 公式）
    ranked_late = [{"is_relevant": False}, {"is_relevant": False}, {"is_relevant": True}]
    assert round(context_precision(ranked_late), 4) == 0.3333
    # 元组输入
    assert context_precision([("a", True), ("b", False)]) == 1.0
    # 无相关 → 0
    assert context_precision([{"is_relevant": False}]) == 0.0
    assert context_precision([]) == 0.0
    # k 截断
    assert context_precision([{"is_relevant": True}, {"is_relevant": True}, {"is_relevant": True}], k=2) == 1.0


def test_evaluate_answer_quality_aggregate():
    records = [
        {"query": "杭州", "answer": "杭州西湖。", "contexts": [{"text": "杭州西湖", "is_relevant": True}]},
        {"query": "月球", "answer": "我们去月球了。", "contexts": [{"text": "杭州西湖", "is_relevant": False}]},
        {"query": "跳过", "answer": "", "contexts": []},  # 无答案 → 跳过
    ]
    agg = evaluate_answer_quality(records)
    assert agg["n"] == 2
    assert agg["faithfulness"] == 0.5  # 1.0 + 0.0 / 2
    assert agg["context_precision_n"] == 2


# ---- 真值评估钩子（_eval_truth_queries）----


def _fake_ranker(ranked_map: dict[str, list[str]]):
    def ranker(q: str):
        return [(rid, 1.0) for rid in ranked_map.get(q, [])]

    return ranker


def test_eval_truth_expect_empty_negative_hook():
    """expect_empty 负样本钩子：返回空 → ok；返回非空 → 误召回计数"""
    from rag_benchmark.run_eval import _eval_truth_queries

    queries = [
        {"query_id": "neg-1", "query": "无相关内容", "layer": "keyword", "expected": [],
         "expected_label": "__none__", "expect_empty": True},
        {"query_id": "neg-2", "query": "误召回", "layer": "keyword", "expected": [],
         "expected_label": "__none__", "expect_empty": True},
    ]
    # ranker 对 neg-2 误召回一条
    ranker = _fake_ranker({"误召回": ["b-todo-04"]})
    res = _eval_truth_queries(queries, ranker, {"__all__": {"b-todo-04"}}, {"b-todo-04": "text"})
    assert res["overall"]["negative_n"] == 2
    assert res["overall"]["negative_error_rate"] == 0.5  # 2 个负样本 1 个误召回


def test_eval_truth_retrieval_and_placeholder():
    """显式 id 检索 + time 行为占位 + neutral 占位"""
    from rag_benchmark.run_eval import _eval_truth_queries

    queries = [
        {"query_id": "r1", "query": "买牛奶", "layer": "keyword", "expected": ["b-todo-04"],
         "expected_label": "todo", "expect_empty": False},
        {"query_id": "t1", "query": "去年去的地方", "layer": "time", "expected": [],
         "expected_label": "__none__", "expect_empty": False},
        {"query_id": "p1", "query": "和妈妈有关", "layer": "person", "expected": [],
         "expected_label": "__none__", "expect_empty": False},
    ]
    corpus_labels = {"__all__": {"b-todo-04"}, "todo": {"b-todo-04"}}
    modality = {"b-todo-04": "text"}
    # r1 命中 → hit_rate@3=1.0；t1 无结果 → time 行为占位 ok；p1 neutral 占位
    ranker = _fake_ranker({"买牛奶": ["b-todo-04"], "去年去的地方": [], "和妈妈有关": ["b-mixed-00"]})
    res = _eval_truth_queries(queries, ranker, corpus_labels, modality)
    assert res["overall"]["retrieval"]["hit_rate@3"] == 1.0
    assert res["layers"]["time"]["behavior_acc"] == 1.0
    assert res["layers"]["person"]["placeholder"] is True
    assert res["overall"]["placeholder_n"] == 1


def test_eval_truth_cross_modal_route():
    """跨模态 route：expected 多 id，top-3 命中 ≥2 模态 → multi_modal_hit"""
    from rag_benchmark.run_eval import _eval_truth_queries

    queries = [
        {"query_id": "rt", "query": "记得给猫买猫粮", "layer": "route",
         "expected": ["c-06", "b-todo-13"], "expected_label": "todo", "expect_empty": False},
    ]
    corpus_labels = {"__all__": {"c-06", "b-todo-13"}}
    modality = {"c-06": "voice", "b-todo-13": "text"}
    ranker = _fake_ranker({"记得给猫买猫粮": ["c-06", "b-todo-13"]})
    res = _eval_truth_queries(queries, ranker, corpus_labels, modality)
    assert res["overall"]["cross_modal"]["n"] == 1
    assert res["overall"]["cross_modal"]["multi_modal_hit"] == 1


def test_load_truth_queries_formats(tmp_path):
    """真值查询加载：A 批顶层数组 + 50 集 {queries:[]} 两种格式"""
    from rag_benchmark.run_eval import _load_truth_queries

    a_file = tmp_path / "a_v1.json"
    a_file.write_text(json.dumps([{"query_id": "q1"}]), encoding="utf-8")
    assert _load_truth_queries(a_file) == [{"query_id": "q1"}]

    obj_file = tmp_path / "obj.json"
    obj_file.write_text(json.dumps({"queries": [{"query_id": "q2"}]}), encoding="utf-8")
    assert _load_truth_queries(obj_file) == [{"query_id": "q2"}]

    # 文件不存在 → 抛错（调用方处理）
    with pytest.raises(FileNotFoundError):
        _load_truth_queries(tmp_path / "missing.json")


# ---- B/C/D 语料 ingestion（build_truth_corpus）----


def _write_truth_batch(tmp_path, batch: str, records: list[dict]) -> Path:
    d = tmp_path / batch
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{batch}_v1.json"
    p.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return p


def test_build_truth_corpus_ingestion(tmp_path, monkeypatch):
    """B/C/D ingestion：稳定 uuid / 模态对齐 / manifest / D 批 caption 拼装"""
    import scripts.build_truth_corpus as bc

    data_dir = tmp_path / "truth-data"
    out_dir = tmp_path / "truth_corpus"
    monkeypatch.setattr(bc, "DATA_DIR", data_dir)
    monkeypatch.setattr(bc, "OUT_DIR", out_dir)

    _write_truth_batch(data_dir, "b", [{"fragment_id": "frag-1", "text": "买牛奶", "label": "todo"}])
    _write_truth_batch(data_dir, "c", [{"clip_id": "clip-1", "transcript": "记得拿快递"}])
    _write_truth_batch(data_dir, "d", [
        {"set_id": "set-1", "expected_l1": [{"theme": "生日"}], "photo_refs": ["p1.jpg"]}
    ])

    manifest = bc.build_corpus()
    b_items = json.loads((out_dir / "corpus_b_truth.json").read_text(encoding="utf-8"))["items"]
    assert b_items[0]["text"] == "买牛奶"
    assert b_items[0]["content_type"] == "text"
    assert b_items[0]["id"] == bc._stable_uuid("b", "frag-1")
    c_items = json.loads((out_dir / "corpus_c_truth.json").read_text(encoding="utf-8"))["items"]
    assert c_items[0]["content_type"] == "voice"
    d_items = json.loads((out_dir / "corpus_d_truth.json").read_text(encoding="utf-8"))["items"]
    assert d_items[0]["content_type"] == "image"
    assert "生日" in d_items[0]["text"]
    assert manifest["entries"]["frag-1"]["uuid"] == b_items[0]["id"]
    assert manifest["entries"]["frag-1"]["modal"] == "text"

    # 幂等：重跑同 id
    bc.build_corpus()
    b2 = json.loads((out_dir / "corpus_b_truth.json").read_text(encoding="utf-8"))["items"]
    assert b2[0]["id"] == b_items[0]["id"]


def test_build_truth_corpus_no_data(tmp_path, monkeypatch):
    """无 B/C/D 数据 → 空语料 + 提示（CI 有数据后跑）"""
    import scripts.build_truth_corpus as bc

    data_dir = tmp_path / "truth-data-empty"
    out_dir = tmp_path / "truth_corpus_empty"
    (data_dir / "b").mkdir(parents=True)
    monkeypatch.setattr(bc, "DATA_DIR", data_dir)
    monkeypatch.setattr(bc, "OUT_DIR", out_dir)
    manifest = bc.build_corpus()
    assert manifest["entries"] == {}
    assert not (out_dir / "corpus_b_truth.json").exists()


def test_remap_a_expected(tmp_path, monkeypatch):
    """A 批 expected 重映射：采集 id → 稳定 uuid"""
    import scripts.build_truth_corpus as bc

    data_dir = tmp_path / "truth-data"
    out_dir = tmp_path / "truth_corpus"
    monkeypatch.setattr(bc, "DATA_DIR", data_dir)
    monkeypatch.setattr(bc, "OUT_DIR", out_dir)
    _write_truth_batch(data_dir, "b", [{"fragment_id": "frag-1", "text": "买牛奶", "label": "todo"}])
    manifest = bc.build_corpus()
    _write_truth_batch(data_dir, "a", [{"query_id": "q1", "query": "买牛奶", "expected": ["frag-1"]}])
    n = bc.remap_a(manifest)
    assert n >= 1
    remapped = json.loads((data_dir / "a" / "a_remapped.json").read_text(encoding="utf-8"))
    assert remapped[0]["expected"] == [bc._stable_uuid("b", "frag-1")]


# ---- Query 改写层正确率（_check_rewrite_case，规则版）----


def test_rewrite_checker_time_kinds():
    """改写检查器：时间窗口 kind 判定（相对 now）"""
    from rag_benchmark.run_eval import _check_rewrite_case

    assert _check_rewrite_case({
        "case_id": "t1", "query": "去年去的地方",
        "expect": {"kind": "last_year", "rewritten": "去的地方"},
    }) is True
    assert _check_rewrite_case({
        "case_id": "t2", "query": "去年夏天去的地方", "expect": {"kind": "last_summer"},
    }) is True
    assert _check_rewrite_case({
        "case_id": "t3", "query": "记得明天下午之前把上个月的工作总结交给领导",
        "expect": {"kind": "mid_query_no_filter"},
    }) is True
    assert _check_rewrite_case({
        "case_id": "t4", "query": "苏州的美食", "expect": {"kind": "place", "place": "苏州"},
    }) is True
    assert _check_rewrite_case({
        "case_id": "t5", "query": "苏州的美食", "params": {"place": "上海"},
        "expect": {"kind": "explicit_param_wins", "place": "上海"},
    }) is True
    # 错误预期 → False
    assert _check_rewrite_case({
        "case_id": "t6", "query": "去年的旅行", "expect": {"kind": "three_years_ago"},
    }) is False
