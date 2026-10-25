"""真负样本采集工具（B-01 · 2026-10-26）

## 为什么需要它

现有 7 条负样本（极光 / 松鼠桂鱼 / 北海道 / 初恋咖啡馆 / 上上周五…）是在
**合成语料**（corpus_b/c/d，几十条手写文本）上设计的 —— 那套语料里确实"没有"这些词，
所以 `negative_error_rate` 在合成集上很好看。但**真库有几千条真实内容**，
同一批查询在真库上误召回实测 **57%**（门禁 ≤5%）。

根因不是"负样本写错了"，而是**评测语料与生产语料不是同一个世界**：
合成语料的"没有"不代表真库的"没有"，而 BGE-M3 的稠密相似度对任何非空查询
都必然返回 top-k（无地板、无拒答）⇒ 误召回几乎全由"语料里到底有没有这个词"决定。

## 本工具做什么

**从真库反向生成**负样本：不是先写下"我觉得这条查不到"，而是
1. 用一批**罕见实体**（真实世界存在、但个人记忆库里几乎不可能出现的专名/术语）作查询；
2. **查库确认**该实体的关键 token 在真库 `contents.text` 里确实 **0 命中**；
3. 只有 0 命中才收进候选 —— 判定依据与命中数一并落盘，可复核、可反驳。

产出 `negatives_candidate.json`（**不动** `truth_queries_50.json`：那是已提交基线，
改它会让历史指标不可比）。

## 判定口径（写死，勿放宽）

- **命中** = `contents.text ILIKE '%<token>%'` 且该行**可被检索**
  （`status='done'` + `deleted_at IS NULL` + `sensitive_status` 非拦截态）。
  这与 `rag` 召回侧的用户可见面一致 —— 库里有一条软删/未完成的行，召回本来就看不到，
  不该算"语料里有"。
- **实体 token 化**：把一个实体拆成若干"必须在文本里同时出现"的关键片段
  （如「松鼠桂鱼」→ `松鼠桂鱼`；「北海道」→ `北海道`）。任一 token 命中即算"语料里有"
  ⇒ **保守排除**（宁少不多：漏掉一条真负样本只是样本变少，收进一条假负样本会污染指标）。
- 纯中文查询不额外做分词 —— 中文没有词边界，`ILIKE` 子串匹配对专名已足够，
  且**比分词更保守**（子串命中范围更大 ⇒ 更容易判"有"⇒ 更容易排除）。

## 用法

    cd D:\\GuangH-App
    python -m research.rag_benchmark.collect_negatives              # 采集 + 落盘
    python -m research.rag_benchmark.collect_negatives --dry-run     # 只打印不落盘
    python -m research.rag_benchmark.collect_negatives --list-only    # 只列内置实体池

退出码：0 正常 / 2 环境不可用（连不上库）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # 既支持直跑，也支持被按包导入
    from gate_io import force_utf8
except ModuleNotFoundError:
    from scripts.gate_io import force_utf8

force_utf8()

REPO = Path(__file__).resolve().parent.parent.parent
BENCH = REPO / "research" / "rag_benchmark"
OUT = BENCH / "negatives_candidate.json"
sys.path.insert(0, str(REPO / "backend"))  # 让 app.* 可导入（脚本从仓库根直跑）

# ---------------------------------------------------------------------------
# 实体池：真实存在、但在**个人记忆库**里几乎不可能出现的专名/术语
#
# 挑选原则（为什么这些该 0 命中）：
#   · 极地/深海/异域地理 —— 普通用户的日常记录不会写
#   · 特定菜系名/古籍/冷门乐器 —— 专名，非日常口语
#   · 专业设备/化学式 —— 与"生活记忆"距离远
# ⚠️ 但**不能只靠"我觉得"**：下列每一条都要经库内命中数验证，
#    命中 >0 的直接丢弃（说明真库里有，当负样本会算成误召回，冤枉检索）。
# ---------------------------------------------------------------------------
ENTITY_POOL: list[dict] = [
    {"entity": "极光", "query": "我记得上次去看过极光", "layer": "descriptive",
     "tokens": ["极光"]},
    {"entity": "松鼠桂鱼", "query": "松鼠桂鱼", "layer": "keyword", "tokens": ["松鼠桂鱼"]},
    {"entity": "北海道", "query": "上上周在北海道滑雪的照片", "layer": "place",
     "tokens": ["北海道"]},
    {"entity": "冰川", "query": "在冰川上徒步的那天", "layer": "descriptive",
     "tokens": ["冰川"]},
    {"entity": "深海潜水", "query": "深海潜水看到的鱼群", "layer": "descriptive",
     "tokens": ["深海潜水"]},
    {"entity": "撒哈拉", "query": "撒哈拉沙漠的星空", "layer": "place", "tokens": ["撒哈拉"]},
    {"entity": "乞力马扎罗", "query": "乞力马扎罗登顶的记录", "layer": "place",
     "tokens": ["乞力马扎罗"]},
    {"entity": "马丘比丘", "query": "马丘比丘的旅行攻略", "layer": "place",
     "tokens": ["马丘比丘"]},
    {"entity": "管风琴", "query": "管风琴独奏音乐会", "layer": "keyword", "tokens": ["管风琴"]},
    {"entity": "马头琴", "query": "马头琴练习曲", "layer": "keyword", "tokens": ["马头琴"]},
    {"entity": "笺注", "query": "古籍笺注的读书笔记", "layer": "keyword", "tokens": ["笺注"]},
    {"entity": "偏微分方程", "query": "偏微分方程期末复习", "layer": "keyword",
     "tokens": ["偏微分方程"]},
    {"entity": "质谱仪", "query": "质谱仪校准流程", "layer": "keyword", "tokens": ["质谱仪"]},
    {"entity": "乙二胺四乙酸", "query": "乙二胺四乙酸配制", "layer": "keyword",
     "tokens": ["乙二胺四乙酸"]},
    {"entity": "卤素灯", "query": "换了卤素灯的客厅", "layer": "keyword", "tokens": ["卤素灯"]},
    {"entity": "塔斯马尼亚", "query": "塔斯马尼亚自驾", "layer": "place",
     "tokens": ["塔斯马尼亚"]},
    {"entity": "复活节岛", "query": "复活节岛石像", "layer": "place", "tokens": ["复活节岛"]},
    {"entity": "摩拉维亚", "query": "摩拉维亚的古镇", "layer": "place", "tokens": ["摩拉维亚"]},
    {"entity": "手冲冰滴", "query": "手冲冰滴咖啡壶", "layer": "keyword", "tokens": ["冰滴"]},
    {"entity": "铁人三项", "query": "铁人三项完赛成绩", "layer": "keyword",
     "tokens": ["铁人三项"]},
]


def _searchable_where() -> str:
    """"可被检索"的 SQL 条件（与 rag 召回侧的用户可见面一致）

    为什么必须带这三个条件：库里存在软删（回收站）、processing/failed、
    以及被护栏拦下的内容 —— 召回本来就看不到它们，把它们的命中算进
    "语料里有"会让负样本**假阴性**（该收的被排除），反之若不算，
    又会让用户真的搜到（因为召回确实看不到）⇒ 这里与召回侧对齐。
    """
    return (
        "c.status = 'done' "
        "AND c.deleted_at IS NULL "
        "AND (c.sensitive_status IS NULL OR c.sensitive_status = '正常') "
        "AND c.text IS NOT NULL AND btrim(c.text) <> ''"
    )


def _count_token_hits(db, token: str) -> int:
    """该 token 在**可检索**内容里出现多少次（ILIKE 子串，大小写不敏感）

    `ILIKE` 的通配符注入风险：token 来自本文件内置常量（非用户输入），
    但仍把 `%` / `_` 转义 —— 免得将来有人把这里改成读外部文件时踩坑。
    """
    from sqlalchemy import text as sa_text

    safe = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    # f-string 只拼本模块常量 `_searchable_where()`（无外部输入）；token 走 :pat 绑定参数。
    # S608 是静态规则的保守误报 —— 此处不接受外部文本拼进 SQL。
    where_clause = _searchable_where()
    sql = f"SELECT count(*) FROM contents c WHERE {where_clause} AND c.text ILIKE :pat ESCAPE '\\'"  # noqa: S608,E501
    return int(db.execute(sa_text(sql), {"pat": f"%{safe}%"}).scalar() or 0)


def collect(limit: int | None = None) -> dict:
    """跑实体池 → 只保留"关键 token 全 0 命中"的 → 返回结构化结果

    返回结构（落盘形状）：
        {
          "_meta": {采集时间/口径/库规模/门禁…},
          "negatives": [
            {"query", "entity", "layer", "tokens", "hits", "verdict", "evidence"}
          ],
          "rejected": [...]   # 命中>0 的显式列出 —— 便于复核"为什么这条没收"
        }
    """
    from app.db.session import SessionLocal
    from sqlalchemy import text as sa_text

    db = SessionLocal()
    try:
        # 注：f-string 里拼的是本模块常量 `_searchable_where()`（**不含任何外部输入**），
        # 用户可控值一律走 :pat 绑定参数。S608 是静态规则的保守误报，见下方 noqa 理由。
        corpus_n = int(
            db.execute(
                sa_text(
                    f"SELECT count(*) FROM contents c WHERE {_searchable_where()}"  # noqa: S608
                )
            ).scalar()
            or 0
        )
        negatives: list[dict] = []
        rejected: list[dict] = []
        for item in ENTITY_POOL[: limit] if limit else ENTITY_POOL:
            hits = {tok: _count_token_hits(db, tok) for tok in item["tokens"]}
            total = sum(hits.values())
            row = {
                "query": item["query"],
                "entity": item["entity"],
                "layer": item["layer"],
                "tokens": item["tokens"],
                "hits": hits,
                "hits_total": total,
            }
            if total == 0:
                row["verdict"] = "true_negative"
                row["evidence"] = (
                    f"关键 token {item['tokens']} 在可检索语料（{corpus_n} 条）中 "
                    f"0 命中 ⇒ 语料确实没有该实体"
                )
                negatives.append(row)
            else:
                row["verdict"] = "rejected_has_hits"
                row["evidence"] = (
                    f"关键 token 在语料中命中 {total} 次（{hits}）⇒ 不能当负样本"
                    f"（否则用户真能搜到却被算成误召回）"
                )
                rejected.append(row)
        return {
            "_meta": {
                "version": 1,
                "kind": "real_db_negatives_candidate",
                "generated_by": "research/rag_benchmark/collect_negatives.py",
                "note": (
                    "从**真库**反向采集的负样本候选（B-01）。判据：查询的关键实体 token 在"
                    "可检索语料中 0 命中。**这是候选集，不是已提交基线** —— 基线是"
                    "truth_queries_50.json，改它会让历史指标不可比，故本文件独立。"
                ),
                "verdict_rule": (
                    "命中 = contents.text ILIKE %token%（status=done + 未软删 + 非敏感拦截 "
                    "+ 文本非空，与 rag 召回用户可见面一致）；任一 token 命中即判『语料里有』"
                    "并移出候选（保守，宁少不多）"
                ),
                "searchable_corpus_n": corpus_n,
                "entity_pool_n": len(item := ENTITY_POOL),
                "true_negatives": len(negatives),
                "rejected": len(rejected),
            },
            "negatives": negatives,
            "rejected": rejected,
        }
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="从真库采集 RAG 负样本候选（B-01）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    ap.add_argument("--list-only", action="store_true", help="只列出实体池，不连库")
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个实体（调试用）")
    args = ap.parse_args()

    if args.list_only:
        print(f"实体池 {len(ENTITY_POOL)} 条：")
        for it in ENTITY_POOL:
            print(f"  · {it['entity']:<12} [{it['layer']:<11}] {it['query']}")
        return 0

    try:
        result = collect(limit=args.limit or None)
    except Exception as exc:  # noqa: BLE001 —— 连不上库属环境错误（退出码 2）
        print(f"[FAIL] 无法连库采集（环境错误）：{type(exc).__name__}: {exc}")
        print("      需 PostgreSQL 可连（backend/.env 的 DATABASE_URL）")
        return 2

    meta = result["_meta"]
    print(f"[collect] 可检索语料 {meta['searchable_corpus_n']} 条；"
          f"实体池 {meta['entity_pool_n']} 个")
    print(f"[collect] 真负样本 **{meta['true_negatives']}** 条；"
          f"因语料已有而剔除 {meta['rejected']} 条")
    print()
    for n in result["negatives"]:
        print(f"  ✓ {n['entity']:<12} [{n['layer']:<11}] {n['query']}")
    if result["rejected"]:
        print()
        print("  被剔除（语料里真有）：")
        for r in result["rejected"]:
            print(f"  ✗ {r['entity']:<12} hits={r['hits']}  {r['query']}")

    if args.dry_run:
        print("\n[dry-run] 未写文件")
        return 0
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[collect] 报告: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
