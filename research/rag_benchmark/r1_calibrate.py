"""R-1 拒答地板标定工具：扫 `retrieval_min_dense_score`，打印误召回 / hit_rate@3 权衡。

## 它解决什么

R-1 要的是「答不出来就不答」（门禁 `negative_error_rate ≤0.05`）。生产管线里原先
**没有任何拒答路径** —— rerank 默认关、`rerank_min_score=0.0`、LLM 精排又明确
「绝不增删候选」，所以只要不触发 filter，非空查询必然返回 top-k。

2026-10-09 给召回层加了地板（`settings.retrieval_min_dense_score`，默认 0.0=关闭）。
**加代码是这件事的一半；另一半是标定出该设多少** —— 就是本脚本。

## 怎么用

```bash
cd <仓库>/backend
python -I -c "pass"   # 或直接：见下方命令
python ../research/rag_benchmark/r1_calibrate.py
```

它复用 `run_eval` 的真评测函数（`_eval_truth_queries`），**不是自己另算一套** ——
门禁数字必须与 `python -m research.rag_benchmark.run_eval --truth-50` 完全同口径，
否则算出来的数对不上就没意义。

## 2026-10-09 的标定结论（务必连同它一起看）

在当时的 **117 条合成语料**上扫描，结果是**不存在双达标的阈值**：

| 阈值 | 仅 truth_50 误召回 | hit@3 | 全部负样本误召回 | hit@3 |
|---|---|---|---|---|
| 0.00（默认） | 0.5714 | 0.8571 | 0.6667 | 0.8571 |
| 0.55 | 0.1429 | 0.8095 | 0.2500 | 0.8095 |
| 0.65 | 0.0000 | 0.5238 | 0.1667 | 0.5238 |
| 0.70 | 0.0000 | 0.4762 | 0.0000 | 0.4762 |

要误召回 ≤0.05 就得开到 ≥0.65，`hit_rate@3` 会从 0.8571 塌到 0.52。
根因是语料规模与成色：正负样本的 top1 稠密分几乎完全重叠（负 0.4888~0.6907，
正 0.4399~1.0000），且语料含大量占位文本（`page-item-0`、`L6索引-…`）。
**换信号也没用** —— 词汇覆盖率、头部区分度（top1−top2）同样不可分，
9 条正样本的词汇覆盖率就是 0.000，与负样本无异。

⇒ **R-1 真正的前置是 §2.1#3「扩真实语料」，不是再调代码。**
真实语料到位后重跑本脚本，得到的地板才可信任；在此之前建议保持默认 0.0（关闭）。
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _s.reconfigure(encoding="utf-8", errors="replace")

# 允许从 <仓库>/backend 或仓库根任一处直接运行
_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (_ROOT, _ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from research.rag_benchmark import run_eval as re  # noqa: E402

BENCH = _ROOT / "research" / "rag_benchmark"
THRESHOLDS = [0.0, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]


def _build_labels(items: list[dict]) -> tuple[dict, dict]:
    labels: dict[str, set[str]] = {"__all__": {it["id"] for it in items}}
    modality: dict[str, str] = {}
    for it in items:
        labels.setdefault(it.get("label") or it["content_type"], set()).add(it["id"])
        modality[it["id"]] = it.get("content_type") or "text"
    return labels, modality


def main() -> int:
    from app.core.config import settings
    from app.services.embedding import encode_dense  # noqa: F401  预热模型

    # 建库（与 run_eval._run_truth_eval 第 1 步同口径）
    items = re._load_json(BENCH / "corpora" / "corpus_b_text.json")["items"]
    items += re._load_json(BENCH / "corpora" / "corpus_c_voice.json")["items"]
    by_ct: dict[str, list[dict]] = {}
    for it in items:
        by_ct.setdefault(it.get("content_type") or "text", []).append(it)
    for ct, group in by_ct.items():
        re._index(None, group, ct, collection=re.TRUTH_COLLECTION)
    print(f"[corpus] {len(items)} 条 -> {re.TRUTH_COLLECTION}")

    labels, modality = _build_labels(items)
    q50 = re._load_truth_queries(BENCH / "truth_queries_50.json")
    a_files = sorted((_ROOT / "research" / "truth-data" / "a").glob("a_v*.json"))
    qa = re._load_truth_queries(a_files[-1]) if a_files else []
    ranker = re._make_ranker(collection=re.TRUTH_COLLECTION)
    print(f"[queries] truth_50={len(q50)}  a={len(qa)}（{a_files[-1].name if a_files else '无'}）")

    rows = []
    original = settings.retrieval_min_dense_score
    try:
        for thr in THRESHOLDS:
            settings.retrieval_min_dense_score = thr
            r50 = re._eval_truth_queries(q50, ranker, labels, modality)["overall"]
            rall = re._eval_truth_queries(q50 + qa, ranker, labels, modality)["overall"]
            rows.append({
                "thr": thr,
                "n50": r50["negative_n"], "err50": r50["negative_error_rate"],
                "hr3_50": (r50.get("retrieval") or {}).get("hit_rate@3"),
                "q50": (r50.get("retrieval") or {}).get("n_queries"),
                "nall": rall["negative_n"], "errall": rall["negative_error_rate"],
                "hr3_all": (rall.get("retrieval") or {}).get("hit_rate@3"),
                "qall": (rall.get("retrieval") or {}).get("n_queries"),
            })
            print(f"  扫过 {thr:.2f}")
    finally:
        settings.retrieval_min_dense_score = original

    print(f"\n{'=' * 100}")
    print("R-1 阈值权衡（hit_rate@3 门禁 ≥0.70；negative_error_rate 门禁 ≤0.05）")
    print("=" * 100)
    print(f"{'阈值':>6} | {'—— 仅 truth_50（与 --truth-50 基线同口径）——':^44} | "
          f"{'—— truth_50 + A 批（全部负样本）——':^44}")
    print(f"{'':>6} | {'误召回 n/N':>14} {'率':>8} {'hit@3':>8} {'n_q':>6} | "
          f"{'误召回 n/N':>14} {'率':>8} {'hit@3':>8} {'n_q':>6}")
    print("-" * 100)
    for r in rows:
        hr50 = f"{r['hr3_50']:.4f}" if r["hr3_50"] is not None else "-"
        hral = f"{r['hr3_all']:.4f}" if r["hr3_all"] is not None else "-"
        mark = "   <== 双达标" if (r["errall"] <= 0.05 and (r["hr3_all"] or 0) >= 0.70) else ""
        print(f"{r['thr']:>6.2f} | {round(r['err50'] * r['n50'])}/{r['n50']:>3} "
              f"{r['err50']:>14.4f} {hr50:>8} {r['q50']:>6} | "
              f"{round(r['errall'] * r['nall'])}/{r['nall']:>3} {r['errall']:>14.4f} "
              f"{hral:>8} {r['qall']:>6}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
