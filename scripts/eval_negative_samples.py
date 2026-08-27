#!/usr/bin/env python3
"""负样本重校 harness（A 批干扰类查询 → 误召回率）

背景
----
RAG 检索只测"该找的找得到"（hit_rate@3），测不出"不该找的乱找"——系统把无关
记忆推给你也显示通过。负样本（A 批干扰类查询）专门测后者：期望结果是 0 条，
只要 Top-k 返回了任何内容就算一次"误召回（false positive）"。

门禁（docs/RAG评测体系与门禁标准.md §5）：
  - 一般负样本误召回率 ≤ 5%（target ≤ 3%）
  - 隐私排除负样本 0% 硬红线（任一条泄漏 = 门禁阻断）
对照基线：hit_rate@3 = 0.9091（外部集 EXT，70 查询，2026-08-25 RAG测评报告）
待重校占位：0.5714（此前 50 条真值集负样本误召回；A 批真实负样本 ≥70% 未交付，
本报告产出后即为其权威值，见 07_晚间例会汇报_20260827.md §7.1）

输入（对齐 docs/真值数据规格标准_v1.md A 批格式）
------------------------------------------------
每条查询：query_id / query / layer / expected / expected_label / expect_empty
         / user_id_hashed / source / collected_at（negative_kind 干扰类必填）
干扰类（负样本）记录须满足：
  - expect_empty = true，expected = []，expected_label = "__none__"
  - negative_kind = "real"（真实负样本，用户真说过但没搜到）| "synthetic"（产品部补造）
  - layer 属 7 层之一（descriptive/keyword/typo/person/place/time/route）
数据分布要求（重校前提）：
  - 干扰类（负样本）≥ 10 条
  - 真实负样本占负样本 ≥ 70%（合成 ≤30%）
无数据时 --dry-run：输出 A 批负样本 JSON 模板 + 说明，退出 0（不报错）。

用法
----
  python scripts/eval_negative_samples.py --dry-run          # 校验 + 模板（无数据自动 dry-run）
  python scripts/eval_negative_samples.py                     # 有数据即真跑检索重校
  python scripts/eval_negative_samples.py --input path.json   # 指定负样本集
  python scripts/eval_negative_samples.py --collection yishu_benchmark --k 3 --out report.json

输出（research/rag_benchmark/negative_samples_report.json 默认）
--------------------------------------------------------------
  - input_stats：总查询 / 负样本数 / 真实 / 合成 / 真实占比 / 负样本占比 / 分层分布
  - validation：errors / warnings（校验结论）
  - baseline：hit_rate@3 对照（现有报告读数，缺失回退 0.9091 外部集基线）+ 待重校占位
  - eval：negative_total / false_recall_total / negative_error_rate / 门禁结论
  - privacy：隐私负样本数与泄漏数（0 硬红线）
  - rows：误召回明细（query → 返回 content_id 列表）

退出码：0 = 校验通过且（dry-run 或 重校完成）；1 = 数据校验有阻断项 / 重校环境未就绪。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "truth-data"
OUT_DIR = ROOT / "research" / "rag_benchmark"

QUERY_LAYERS = {"descriptive", "keyword", "typo", "person", "place", "time", "route"}
A_REQUIRED = {"query_id", "query", "layer", "expected", "expected_label", "expect_empty",
              "user_id_hashed", "source", "collected_at"}
GATE_ERROR_RATE = 0.05      # 一般负样本误召回率门禁 ≤5%
GATE_ERROR_TARGET = 0.03    # target ≤3%
PRIVACY_RED_LINE = 0.0      # 隐私负样本 0% 硬红线
HIT_RATE_BASELINE_FALLBACK = 0.9091   # 外部集 EXT 基线（RAG测评报告_20260825.md）
PLACEHOLDER_FALSE_RECALL = 0.5714     # 待重校占位（07_晚间例会汇报 §7.1）
NEGATIVE_MIN = 10
REAL_RATIO_MIN = 0.70

TEMPLATE = {
    "_readme": "A 批负样本（干扰类查询）JSON 模板——数组顶层，对齐 docs/真值数据规格标准_v1.md。"
               "重校前提：干扰类 ≥10 条且真实负样本占负样本 ≥70%（合成 ≤30%）。"
               "每条：query_id 以 q- 前缀唯一；query 用用户原话不改写；layer 7 层之一；"
               "expect_empty=true 时 expected 必须 []、expected_label 必须 __none__、negative_kind 必填。",
    "queries": [
        {
            "query_id": "q-0042",
            "query": "去年生日那天的照片",
            "layer": "time",
            "expected": [],
            "expected_label": "__none__",
            "expect_empty": True,
            "negative_kind": "real",
            "user_id_hashed": "sha256:...",
            "source": "beta-user",
            "collected_at": "2026-09-10T14:30:00+08:00",
            "note": "真实负样本：用户真的搜过'去年生日照片'，但去年生日没拍照，搜不到",
        },
        {
            "query_id": "q-0043",
            "query": "我去南极的照片",
            "layer": "keyword",
            "expected": [],
            "expected_label": "__none__",
            "expect_empty": True,
            "negative_kind": "synthetic",
            "user_id_hashed": "sha256:...",
            "source": "annotation",
            "collected_at": "2026-09-10T14:35:00+08:00",
            "note": "合成负样本：产品部构造'用户从没去过南极'，测对不存在内容的鲁棒性",
        },
    ],
}


def _load_input(path: Path | None) -> tuple[list[dict] | None, Path | None, str]:
    """读取负样本集：显式 --input 优先；否则 A 批 a_v*.json 版本号最大。
    返回 (records 或 None, 文件路径 或 None, 说明文案)。"""
    if path is not None:
        if not path.exists():
            return None, None, f"指定的输入不存在: {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else data.get("queries")
        if not isinstance(records, list):
            return None, path, f"{path.name}: 顶层必须是数组（或 {{queries:[]}} 对象）"
        return records, path, ""
    files = sorted((DATA_DIR / "a").glob("a_v*.json"))
    if not files:
        return None, None, "未找到 A 批数据（research/truth-data/a/a_v*.json）——进入 dry-run 模板模式"
    f = files[-1]
    data = json.loads(f.read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else data.get("queries")
    if not isinstance(records, list):
        return None, f, f"{f.name}: 顶层必须是数组（或 {{queries:[]}} 对象）"
    return records, f, ""


def _validate(records: list[dict]) -> tuple[list[str], list[str], dict]:
    """A 批负样本校验（对齐 validate_truth_data.py 的 A 批语义 + 分布门槛）。
    返回 (errors 阻断项, warnings 警告, stats 分布统计)。"""
    errors: list[str] = []
    warnings: list[str] = []
    stats = {
        "total_queries": len(records),
        "negative_total": 0,
        "negative_real": 0,
        "negative_synthetic": 0,
        "negative_other": 0,
        "privacy_total": 0,
        "layers": {layer: {"negative": 0, "total": 0} for layer in QUERY_LAYERS},
        "other_layers": {"negative": 0, "total": 0},
    }
    for i, rec in enumerate(records):
        tag = f"#{i+1}"
        if not isinstance(rec, dict):
            errors.append(f"{tag}: 记录必须是对象")
            continue
        qid = rec.get("query_id") or tag
        missing = A_REQUIRED - set(rec.keys())
        if missing:
            errors.append(f"{qid}: 缺必填字段 {sorted(missing)}")
        layer = rec.get("layer")
        stats.setdefault("_layer_total", 0)
        if layer in stats["layers"]:
            stats["layers"][layer]["total"] += 1
        else:
            stats["other_layers"]["total"] += 1
        expect_empty = rec.get("expect_empty")
        if expect_empty is True:
            stats["negative_total"] += 1
            if layer in stats["layers"]:
                stats["layers"][layer]["negative"] += 1
            else:
                stats["other_layers"]["negative"] += 1
            if rec.get("expected"):
                errors.append(f"{qid}: expect_empty=true 时 expected 必须为空 []")
            if rec.get("expected_label") != "__none__":
                errors.append(f"{qid}: expect_empty=true 时 expected_label 必须为 __none__")
            nk = rec.get("negative_kind")
            if nk == "real":
                stats["negative_real"] += 1
            elif nk == "synthetic":
                stats["negative_synthetic"] += 1
            else:
                stats["negative_other"] += 1
                errors.append(f"{qid}: 干扰类必须填 negative_kind（real/synthetic）")
            if rec.get("privacy") is True:
                stats["privacy_total"] += 1
        elif expect_empty is False:
            if "negative_kind" in rec:
                errors.append(f"{qid}: 命中类不应填 negative_kind（仅干扰类）")
        else:
            errors.append(f"{qid}: expect_empty 必须为布尔值")
        if layer not in QUERY_LAYERS:
            errors.append(f"{qid}: layer 非法 {layer}（7 层之一）")

    neg = stats["negative_total"]
    real = stats["negative_real"]
    real_ratio = (real / neg) if neg else 0.0
    stats["negative_real_ratio"] = round(real_ratio, 4)
    stats["negative_share"] = round(neg / len(records), 4) if records else 0.0
    # 分布门槛（重校前提）
    if neg < NEGATIVE_MIN:
        errors.append(f"分布未达标：干扰类（负样本）仅 {neg} 条，需 ≥{NEGATIVE_MIN} 条")
    if neg and real_ratio < REAL_RATIO_MIN:
        errors.append(
            f"分布未达标：真实负样本占比 {real_ratio:.1%}，需 ≥{REAL_RATIO_MIN:.0%}（合成 ≤30%）")
    # 每层负样本覆盖（规格目标，非阻断）
    bare_layers = [ly for ly in QUERY_LAYERS if stats["layers"][ly]["total"] > 0
                   and stats["layers"][ly]["negative"] == 0]
    if bare_layers:
        warnings.append(f"以下层有查询但无负样本（规格目标：每层都要有负样本）: {sorted(bare_layers)}")
    return errors, warnings, stats


def _read_hit_rate_baseline() -> tuple[float | None, str]:
    """读现有评测报告里的 hit_rate@3 作对照；无则回退文档基线 0.9091。"""
    for name in ("evaluation_report.json", "image_search_report.json"):
        p = OUT_DIR / name
        if p.exists():
            try:
                rep = json.loads(p.read_text(encoding="utf-8"))
                hr = rep.get("hit_rate@3")
                if isinstance(hr, (int, float)):
                    return hr, f"{name} 实测 hit_rate@3={hr}"
            except (json.JSONDecodeError, OSError):
                continue
    return HIT_RATE_BASELINE_FALLBACK, f"回退外部集 EXT 基线 hit_rate@3={HIT_RATE_BASELINE_FALLBACK}"


def _eval_retrieval(records: list[dict], collection: str, k: int,
                    content_types: list[str] | None) -> tuple[dict, str | None]:
    """走生产检索链路（eval_image_search.py 同口径），统计误召回。
    返回 (eval 结果, 失败原因 或 None)。"""
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        from app.schemas.search import SearchQuery
        from app.services.rag import search
    except Exception as exc:  # 环境未就绪：不阻断 dry-run 就绪性
        return {}, f"检索后端未就绪（backend 依赖/Qdrant 服务）：{exc}"

    rows: list[dict] = []
    false_recall = 0
    privacy_leaks = 0
    for rec in records:
        if rec.get("expect_empty") is not True:
            continue
        try:
            result = search(
                SearchQuery(q=rec["query"], limit=k,
                            content_types=content_types or None),
                collection=collection,
            )
            returned = [h.content_id for h in result.hits[:k]]
        except Exception as exc:
            returned = []
            rows.append({"query_id": rec.get("query_id"), "query": rec.get("query"),
                         "layer": rec.get("layer"), "negative_kind": rec.get("negative_kind"),
                         "privacy": rec.get("privacy") is True, "error": str(exc)})
            continue
        fp = bool(returned)
        false_recall += fp
        if rec.get("privacy") is True:
            privacy_leaks += fp
        rows.append({
            "query_id": rec.get("query_id"),
            "query": rec.get("query"),
            "layer": rec.get("layer"),
            "negative_kind": rec.get("negative_kind"),
            "privacy": rec.get("privacy") is True,
            "returned_content_ids": returned,
            "false_recall": fp,
        })

    n = len(rows)
    error_rows = sum(1 for r in rows if r.get("error"))
    if n and error_rows == n:
        # 全部查询都报错 → 检索后端不可用，判定环境失败而非 0% 误召回
        return {}, (f"检索后端不可用（{n}/{n} 条查询全部报错，"
                    f"首个错误: {rows[0]['error']}）——请确认 Qdrant 与 backend 服务就绪后重跑")
    rate = round(false_recall / n, 4) if n else 0.0
    privacy_n = sum(1 for r in rows if r.get("privacy"))
    return {
        "negative_total": n,
        "error_rows": error_rows,
        "false_recall_total": false_recall,
        "negative_error_rate": rate,
        "gate": {
            "threshold_≤0.05": GATE_ERROR_RATE,
            "target_≤0.03": GATE_ERROR_TARGET,
            "pass": rate <= GATE_ERROR_RATE,
            "privacy_red_line_0": privacy_n == 0 or privacy_leaks == 0,
            "privacy_total": privacy_n,
            "privacy_leaks": privacy_leaks,
        },
        "rows": rows,
    }, None


def _print_template() -> None:
    print("=" * 70)
    print("负样本重校 harness · dry-run 模板模式（无数据不报错，退出 0）")
    print("=" * 70)
    print("A 批负样本（干扰类查询）JSON 模板——数组顶层，对齐")
    print("docs/真值数据规格标准_v1.md A 批格式：")
    print(json.dumps(TEMPLATE, ensure_ascii=False, indent=2))
    print("-" * 70)
    print("字段要求：query_id(q- 前缀唯一) / query(用户原话不改写) / layer(7 层之一) /")
    print("  expected=[] / expected_label=__none__ / expect_empty=true /")
    print("  negative_kind(real|synthetic) / user_id_hashed / source / collected_at(ISO8601)")
    print("重校前提：干扰类 ≥10 条 且 真实负样本占负样本 ≥70%（合成 ≤30%）")
    print("放置路径：research/truth-data/a/a_v*.json（版本号取最大）")
    print("一键重校：python scripts/eval_negative_samples.py")
    print("门禁口径：一般负样本误召回率 ≤5%（target ≤3%）；隐私负样本 0% 硬红线")
    print("对照基线：hit_rate@3 = 0.9091（外部集 EXT）；待重校占位 = 0.5714")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="负样本重校 harness：A 批干扰类查询 → 误召回率（门禁 ≤5%）")
    parser.add_argument("--input", type=Path, help="负样本集 JSON 路径（默认 A 批 a_v*.json）")
    parser.add_argument("--collection", default="yishu_benchmark", help="Qdrant collection（默认 yishu_benchmark）")
    parser.add_argument("--k", type=int, default=3, help="Top-k（默认 3，对照 hit_rate@3）")
    parser.add_argument("--content-types", help="逗号分隔 content_types 过滤（默认不限模态）")
    parser.add_argument("--out", type=Path, default=OUT_DIR / "negative_samples_report.json",
                        help="报告输出路径")
    parser.add_argument("--dry-run", action="store_true", help="只校验 + 出模板，不跑检索")
    args = parser.parse_args()

    records, path, note = _load_input(args.input)
    if records is None:
        print(f"ℹ {note}")
        _print_template()
        return 0  # 无数据不报错（dry-run 全绿）

    errors, warnings, stats = _validate(records)
    baseline, baseline_note = _read_hit_rate_baseline()
    report: dict = {
        "_meta": {
            "script": "scripts/eval_negative_samples.py",
            "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
            "input": str(path),
            "collection": args.collection,
            "k": args.k,
            "note": "A 批负样本重校 harness 报告（对齐 docs/RAG评测体系与门禁标准.md §5）",
        },
        "input_stats": stats,
        "validation": {"errors": errors, "warnings": warnings},
        "baseline": {
            "hit_rate@3": baseline,
            "baseline_note": baseline_note,
            "placeholder_false_recall_0.5714": PLACEHOLDER_FALSE_RECALL,
            "note": "本报告 negative_error_rate 产出后即取代 0.5714 占位为权威值",
        },
    }

    if args.dry_run or errors:
        # dry-run 或数据有阻断项：只出校验报告，不跑检索
        if not errors:
            report["dry_run"] = True
        status = "✅ 校验通过（dry-run）" if not errors else "❌ 校验有阻断项"
        print(f"[{status}] {path.name}: 共 {stats['total_queries']} 条查询, "
              f"负样本 {stats['negative_total']} 条（real {stats['negative_real']} / "
              f"synthetic {stats['negative_synthetic']}，真实占比 {stats['negative_real_ratio']:.1%}）")
        for w in warnings:
            print(f"  ⚠ {w}")
        for e in errors:
            print(f"  ✗ {e}")
        report["eval"] = None
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告 → {args.out}")
        return 1 if errors else 0

    # 真跑检索重校
    content_types = [s.strip() for s in args.content_types.split(",")] if args.content_types else None
    eval_result, fail_reason = _eval_retrieval(records, args.collection, args.k, content_types)
    if fail_reason:
        report["eval"] = {"error": fail_reason}
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✗ 重校未执行：{fail_reason}")
        print(f"  数据校验已通过（{path.name}），报告 → {args.out}")
        return 1  # 重校环境未就绪
    report["eval"] = eval_result
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    er = eval_result
    print("=" * 70)
    print(f"负样本重校 · {path.name} · {args.collection}@{args.k}")
    print(f"  hit_rate@3 对照: {baseline}（{baseline_note}）")
    print(f"  负样本占比: {stats['negative_share']:.1%}（{stats['negative_total']}/{stats['total_queries']}），"
          f"真实 {stats['negative_real']}/{stats['negative_total']} = {stats['negative_real_ratio']:.1%}")
    print(f"  误召回率: {er['false_recall_total']}/{er['negative_total']} = {er['negative_error_rate']:.4f} "
          f"(门禁 ≤{GATE_ERROR_RATE:.0%} {'✅ PASS' if er['gate']['pass'] else '❌ FAIL'})")
    pr = er["gate"]
    print(f"  隐私负样本: {pr['privacy_total']} 条 / 泄漏 {pr['privacy_leaks']} "
          f"({'✅ 0 泄漏' if pr['privacy_leaks'] == 0 else '❌ 红线泄漏'})")
    print("  误召回明细（query → 返回 content_id）：")
    for r in er["rows"]:
        mark = "⚠" if r["false_recall"] else "·"
        if r["false_recall"] or r.get("error"):
            print(f"    {mark} [{r.get('layer')}] {r.get('query_id')} {r.get('query')[:30]} "
                  f"→ {r.get('returned_content_ids') or r.get('error')}")
    print(f"报告 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
