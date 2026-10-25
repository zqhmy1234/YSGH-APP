"""真负样本误召回实测（B-01 · 2026-10-26）

## 这个脚本回答什么问题

「RAG 在**真库**上对『语料里根本没有的查询』会不会硬凑出结果？」

门禁是 `negative_error_rate <= 0.05`（误召回 ≤5%）。此前唯一测过的负样本是 7 条**合成**样本，
跑在**合成语料**上（几十条手写文本）—— 那套语料里"没有极光"是显然的，指标好看；
但用户在生产的真库上，同一批查询实测误召回 **57%**。

本脚本用 `collect_negatives.py` 产出的**真负样本**（已验关键 token 在真库 0 命中），
调**真实生产路径** `rag.search`（不是 mock、不是合成语料），如实算误召回率。

## 口径（为什么这样算）

- **误召回（false positive）= 该查询返回了 ≥1 条命中**。
  负样本的定义是"语料里没有相关内容"，那么返回任何结果都是错的 ——
  与 `run_eval._eval_truth_queries` 对 `expect_empty` 的判定**逐字一致**
  （`fp = 1 if ranked else 0`），便于与既有门禁口径对账。
- **必须传 `user_id` + `db`（生产口径）**。这是本评测最容易做错的一点：
  `_assemble_hits` 只在 `db is not None and user_id is not None` 时才做 DB 回查与
  **孤儿剔除**（`recall.py:89-107, 138-143`：Qdrant 里残留的软删/failed/非归属向量，
  回查落空就整条剔除）。传 `db=None` 会**绕过这道闸门**，让一堆"库里根本没有"的
  孤儿向量算成命中 —— 实测真库 `yishu_contents` 有 **72% 的点在 DB 里查不到对应行**，
  用 `db=None` 量出来的误召回是**假高**（我第一版就踩了这个坑：100% vs 真实值）。
  故本脚本默认走生产口径，并把该口径写进报告的 `_meta`。
- 不设 `retrieval_min_dense_score` 地板、不开 `answerability_reject_enabled`
  —— 那是**被测配置**，不是评测设定。两者当前都关闭（`0.0` / `False`），
  正是"误召回为什么高"的一部分答案，报告里会显式记下当时的开关值。
- **`--no-llm` 可选**：`rerank_llm_enabled` 默认 True ⇒ 每次查询一次 qwen 往返。
  20 条负样本 = 20 次调用。想省钱/离线跑就传 `--no-llm`（临时只关 LLM 精排，
  不改 config 默认值），报告里会标注本次是否走了 LLM。
- **`--no-db`（仅调试）**：显式绕过孤儿剔除，用于对照"孤儿向量在多大程度上制造假误召回"。
  **不得**把这个口径的数字当评测结论。

## 用法

    cd D:\\GuangH-App
    python -m research.rag_benchmark.measure_negative_recall              # 真实全档（含 LLM 精排）
    python -m research.rag_benchmark.measure_negative_recall --no-llm     # 省 LLM 费用
    python -m research.rag_benchmark.measure_negative_recall --limit 5    # 先小样试跑

退出码：0 报告产出 / 2 环境不可用（Qdrant 未起等）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    from gate_io import force_utf8
except ModuleNotFoundError:
    from scripts.gate_io import force_utf8

force_utf8()

REPO = Path(__file__).resolve().parent.parent.parent
BENCH = REPO / "research" / "rag_benchmark"
CANDIDATES = BENCH / "negatives_candidate.json"
OUT = BENCH / "negative_recall_report.json"
sys.path.insert(0, str(REPO / "backend"))

GATE = 0.05  # 误召回门禁（truth_queries_50.json 的 _meta.gates.negative_error_rate）


def measure(
    limit: int | None = None,
    *,
    use_llm: bool = True,
    use_db: bool = True,
    include_text: bool = False,
) -> dict:
    """对每条真负样本调真实 `rag.search`，统计误召回

    `use_db=True`（默认）= **生产口径**：传 db + user_id，让 `_assemble_hits` 做
    DB 回查与孤儿剔除。`use_db=False` 仅供对照实验（见模块 docstring 的口径说明）。

    `include_text=False`（默认）= **不把命中的正文写进报告**：报告会进 git，
    而命中正文是真实用户内容（本仓对评测件的口径是不落原始可识别内容）。
    默认只记 `text_len` + `text_sha256_8`（够判别"是不是同一条被反复召回"）；
    本地排查要看原文就传 `--include-text`（那次跑完别提交报告）。
    """
    from app.core.config import settings
    from app.schemas.search import SearchQuery
    from app.services import rag

    if not use_llm:
        # 临时关闭 LLM 精排（只在本次进程内生效，**不改 config 默认值**）
        settings.rerank_llm_enabled = False

    data = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    negatives = data["negatives"]
    if limit:
        negatives = negatives[:limit]

    db = None
    user_id = None
    if use_db:
        from app.db.session import SessionLocal

        db = SessionLocal()
        # 生产里 user_id 是必传的（召回按用户隔离 + 孤儿剔除都以它为条件）。
        # 这里取语料最多的那个用户当作"被评测用户"，避免跨用户混检。
        from sqlalchemy import text as sa_text

        user_id = db.execute(
            sa_text(
                "SELECT user_id::text FROM contents WHERE status='done' "
                "AND deleted_at IS NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"
            )
        ).scalar()

    rows: list[dict] = []
    fp = 0
    try:
        for item in negatives:
            q = SearchQuery(q=item["query"], limit=5)
            try:
                res = rag.search(q, db=db, user_id=user_id)
                hits = getattr(res, "hits", []) or []
                texts = [(h.text or "") for h in hits[:3]]
                degraded = bool(getattr(res, "degraded", False))
            except Exception as exc:  # noqa: BLE001 —— 单条失败记明细，不中断整批
                rows.append({
                    "query": item["query"], "entity": item["entity"],
                    "error": f"{type(exc).__name__}: {exc}", "fp": None,
                })
                continue
            is_fp = 1 if hits else 0
            fp += is_fp
            row = {
                "query": item["query"],
                "entity": item["entity"],
                "n_hits": len(hits),
                "fp": bool(is_fp),
                "degraded": degraded,
                "top_text_len": [len(t) for t in texts],
                "top_text_sha8": [
                    hashlib.sha256(t.encode()).hexdigest()[:8] for t in texts
                ],
            }
            if include_text:
                # 仅本地排查开启；报告含真实正文 ⇒ 跑完不要提交这份报告
                row["top_texts"] = [t[:40] for t in texts]
            rows.append(row)
    finally:
        if db is not None:
            db.close()

    valid = [r for r in rows if r.get("fp") is not None]
    n = len(valid)
    return {
        "_meta": {
            "version": 1,
            "kind": "real_db_negative_recall",
            "source": "research/rag_benchmark/negatives_candidate.json",
            "note": (
                "用**真库**采集的真负样本，跑**真实生产检索路径** rag.search 得到的误召回实测。"
                "口径与 run_eval 的 expect_empty 判定一致：返回≥1 条即误召回。"
            ),
            "evaluation_mode": "production(db+user_id)" if use_db else "DEBUG(no db: orphans leak)",
            # ⚠️ 不落原始 user_id：报告会进 git，而本仓对评测件的既定口径是
            # **只存 sha256 前缀**（见 truth_queries_50.json 的 user_id_hashed）。
            # 原始 UUID 能直接对应到真实账号，属于不该进公共代码库的标识。
            "user_id_sha256_8": (
                hashlib.sha256(str(user_id).encode()).hexdigest()[:8] if user_id else None
            ),
            "n": n,
            "gate": GATE,
            "llm_rerank_enabled": bool(use_llm),
            "config_snapshot": {
                "retrieval_min_dense_score": settings.retrieval_min_dense_score,
                "answerability_reject_enabled": settings.answerability_reject_enabled,
                "rerank_llm_enabled": settings.rerank_llm_enabled,
                "llm_rewrite_enabled": settings.llm_rewrite_enabled,
                "class_routing_enabled": settings.class_routing_enabled,
            },
        },
        "negative_error_rate": round(fp / n, 4) if n else None,
        "false_positives": fp,
        "n": n,
        "gate_pass": (fp / n <= GATE) if n else None,
        "details": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="真负样本误召回实测（B-01）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（试跑用）")
    ap.add_argument("--no-llm", action="store_true", help="临时关闭 LLM 精排（省调用费用）")
    ap.add_argument("--no-db", action="store_true",
                    help="⚠️ 仅调试：绕过孤儿剔除（结果会假高，不得作为评测结论）")
    ap.add_argument("--include-text", action="store_true",
                    help="把命中正文写进报告（含真实用户内容，跑完勿提交）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写报告")
    args = ap.parse_args()

    if not CANDIDATES.exists():
        print(f"[FAIL] 缺负样本候选：{CANDIDATES}")
        print("       先跑：python -m research.rag_benchmark.collect_negatives")
        return 2

    try:
        rep = measure(limit=args.limit or None, use_llm=not args.no_llm,
                      use_db=not args.no_db, include_text=args.include_text)
    except Exception as exc:  # noqa: BLE001 —— 连不上 Qdrant 等属环境错误
        print(f"[FAIL] 检索不可用（环境错误）：{type(exc).__name__}: {exc}")
        print("       需 Qdrant 可连（docker start yishu-qdrant）")
        return 2

    m = rep["_meta"]
    print(f"[neg] n={rep['n']}  LLM精排={m['llm_rerank_enabled']}  "
          f"dense地板={m['config_snapshot']['retrieval_min_dense_score']}  "
          f"answerability={m['config_snapshot']['answerability_reject_enabled']}")
    for r in rep["details"]:
        if r.get("fp") is None:
            print(f"  ERR {r['entity']:<12} {r['error'][:60]}")
            continue
        mark = "✗ 误召回" if r["fp"] else "✓ 正确空"
        print(f"  {mark} {r['entity']:<12} hits={r['n_hits']}  {r['query']}")
        if r["fp"]:
            # 默认只报指纹（报告进 git，正文是真实用户内容）；
            # --include-text 时才打印原文用于本地排查。
            if r.get("top_texts"):
                print(f"       top: {r['top_texts'][0]!r}")
            else:
                print(f"       top: len={r['top_text_len'][0]} sha8={r['top_text_sha8'][0]}")
    rate = rep["negative_error_rate"]
    print()
    if rate is None:
        print("[neg] 无可测样本")
    else:
        verdict = "✅ 达标" if rep["gate_pass"] else "❌ 未达标"
        print(f"[neg] 误召回率 = {rate:.2%}（{rep['false_positives']}/{rep['n']}）"
              f"  门禁 ≤{GATE:.0%}  {verdict}")

    if args.dry_run:
        print("[dry-run] 未写报告")
        return 0
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[neg] 报告: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
