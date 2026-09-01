"""构建外部评测集（P1-B2 2026-08-25）：T2Ranking dev → 本 APP 测试输入分布

来源（均已在本地缓存，免大文件下载）：
  - C-MTEB/T2Retrieval corpus（118,605 段，缓存于 HF 数据集缓存）
  - THUIR/T2Ranking data/queries.dev.tsv（10K 查询）+ qrels.dev.tsv（分级相关）+ qrels.retrieval.dev.tsv

抽取口径（对齐本 APP 输入 = 个人记忆碎片：短文字/口语查询）：
  - 查询长度 4~20 字符（短口语查询，如"蜂巢取快递验证码摁错怎么办"）
  - 相关段落去 HTML 标签后 8~120 字符（碎片级，非网页长文）
  - 取 rel≥2 的相关段落优先（qrels.dev.tsv 分级 1-3）
  - 确定性抽样（seed=42）70 条查询，每条 1~2 个相关段落，段落全局去重
  - 输出 ≥60 条查询用例（不足 60 抛错）

输出：
  - research/rag_benchmark/corpora/corpus_ext_t2r.json（{items:[{id,text,label:null}]}）
  - research/rag_benchmark/queries/queries_ext.json（{queries:[{query,expected:[id],layer}]}）

用法：python -m research.rag_benchmark.build_external_corpus
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BENCH_DIR = Path(__file__).resolve().parent

T2R_CORPUS = "C-MTEB/T2Retrieval"
T2RANKING_REPO = "THUIR/T2Ranking"
SEED = 42
MAX_QUERIES = 70
MIN_QUERIES = 60
QUERY_LEN = (4, 20)      # 查询长度区间（字符）
PASSAGE_LEN = (8, 120)   # 段落长度区间（字符，去标签后）

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip(text: str) -> str:
    """去 HTML 标签 + 折叠空白（T2Retrieval 段落带 <br><img> 等标签）"""
    t = _TAG_RE.sub(" ", text)
    t = _WS_RE.sub("", t)
    return t.strip()


def main() -> None:
    from datasets import load_dataset
    from huggingface_hub import hf_hub_download

    print("[ext] 加载本地缓存语料 C-MTEB/T2Retrieval ...")
    corpus_ds = load_dataset(T2R_CORPUS, split="corpus")
    corpus = {row["id"]: row["text"] for row in corpus_ds}
    print(f"[ext] 语料段落 {len(corpus)} 条")

    print("[ext] 读取 T2Ranking dev 查询与 qrels ...")
    snap = Path(hf_hub_download(
        repo_id=T2RANKING_REPO, filename="data/queries.dev.tsv",
        repo_type="dataset", endpoint="https://hf-mirror.com",
    )).parent
    queries: dict[str, str] = {}
    for line in (snap / "queries.dev.tsv").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            queries[parts[0]] = parts[1]
    qrels: dict[str, dict[str, int]] = {}
    for line in (snap / "qrels.dev.tsv").read_text(encoding="utf-8").splitlines():
        parts = line.rstrip().split("\t")
        # 格式：qid \t 0 \t pid \t rel（首行表头 qid \t - \t pid \t rel）
        if len(parts) == 4 and parts[0].isdigit() and parts[2].isdigit() and parts[3].isdigit():
            qrels.setdefault(parts[0], {})[parts[2]] = int(parts[3])
    rel_pairs: set[tuple[str, str]] = set()
    for line in (snap / "qrels.retrieval.dev.tsv").read_text(encoding="utf-8").splitlines():
        parts = line.rstrip().split("\t")
        if len(parts) == 2 and parts[0].isdigit():
            rel_pairs.add((parts[0], parts[1]))
    print(f"[ext] 查询 {len(queries)} 条，qrels 分级 {sum(len(v) for v in qrels.values())} 对，"
          f"binary {len(rel_pairs)} 对")

    # 候选查询：长度合规 + 有 ≥1 个（rel≥2 优先）相关段落通过段落过滤
    def pass_q(qid: str) -> bool:
        q = _strip(queries.get(qid, ""))
        return QUERY_LEN[0] <= len(q) <= QUERY_LEN[1]

    def good_pid(pid: str) -> bool:
        t = _strip(corpus.get(pid, ""))
        return PASSAGE_LEN[0] <= len(t) <= PASSAGE_LEN[1]

    candidates: list[tuple[str, list[tuple[str, int]]]] = []
    for qid in queries:
        if not pass_q(qid):
            continue
        rel = qrels.get(qid, {})
        if not rel:
            continue
        pids = sorted(
            (pid for pid, r in rel.items() if r >= 2 and good_pid(pid)),
            key=lambda p: -rel[p],
        )
        if not pids:
            pids = sorted((pid for pid in rel if good_pid(pid)), key=lambda p: -rel[p])
        if pids:
            candidates.append((qid, [(p, rel[p]) for p in pids[:3]]))

    print(f"[ext] 合格候选查询 {len(candidates)} 条（查询≤{QUERY_LEN[1]}字、相关段落≤{PASSAGE_LEN[1]}字）")

    # 确定性抽样，段落全局去重（S311：评测抽样确定性优先，非加密用途）
    rng = random.Random(SEED)  # noqa: S311
    rng.shuffle(candidates)
    chosen: list[tuple[str, list[str]]] = []
    used_pids: set[str] = set()
    for qid, rel_list in candidates:
        picks = []
        for pid, _r in rel_list:
            if pid not in used_pids:
                picks.append(pid)
                used_pids.add(pid)
            if len(picks) >= 2:
                break
        if picks:
            chosen.append((qid, picks))
        if len(chosen) >= MAX_QUERIES:
            break

    if len(chosen) < MIN_QUERIES:
        raise SystemExit(f"抽样不足 {MIN_QUERIES} 条（实际 {len(chosen)}），需放宽过滤条件")

    # 写 corpus + queries
    items = [
        {"id": f"ext-t2r-{pid}", "text": _strip(corpus[pid]), "label": None}
        for pid in used_pids
    ]
    qs = [
        {"query": _strip(queries[qid]), "expected": [f"ext-t2r-{p}" for p in pids], "layer": "external"}
        for qid, pids in chosen
    ]
    corpus_out = BENCH_DIR / "corpora" / "corpus_ext_t2r.json"
    queries_out = BENCH_DIR / "queries" / "queries_ext.json"
    corpus_out.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    queries_out.write_text(json.dumps({"queries": qs}, ensure_ascii=False, indent=2), encoding="utf-8")

    lens = [len(_strip(queries[qid])) for qid, _ in chosen]
    plens = [len(it["text"]) for it in items]
    print(f"[ext] ✅ 输出 {len(qs)} 条查询 / {len(items)} 段语料")
    print(f"[ext]    查询长度 {min(lens)}~{max(lens)} 字，段落长度 {min(plens)}~{max(plens)} 字")
    print(f"[ext]    {corpus_out}")
    print(f"[ext]    {queries_out}")


if __name__ == "__main__":
    main()
