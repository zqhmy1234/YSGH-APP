"""纠错 7 天准确率提升测量（B5-c · F4 验收测量 · Wave2-F 2026-08-26）

目标（feature_list F4 / audit_B5b #14）：测量纠错系统让"同类型内容分类准确率"
提升 ≥10%。口径待产品部确认（B5c 设计待办），本脚本先按确定性可复现的双模式落地：

测量口径（当前实现）：
- 样本 = correction_log 最近 N 天（默认 7）内、带内容文本的纠错事件；
- before_acc = P(旧标签 old_label == 用户纠错目标 new_label)——纠错发生时系统
  原本的判定是否已正确（真实纠错事件通常 old≠new，故 before 一般偏低）；
- after_acc  = P(当前系统判定 == new_label)——纠错生效后（个人规则/共性回流）
  系统对该同类型内容的判定是否命中用户目标（arbitrate，个人规则命中则快）；
- gain = after_acc - before_acc；门禁 gain ≥ 10%（0.10）→ PASS。
- 按 content_type 分组报告"同类准确率"（B5c 口径：同类型照片/文字/语音分开算）。

双模式（--mode）：
- full   （默认）：窗口内全部纠错样本（after 用 arbitrate，个人规则未命中时回退
  全局 SetFit ~秒级/条——数据量大建议用 sample）；
- sample （--n 默认 50 --seed 42）：确定性抽样（全量重测 vs 抽样的对照口径）。

环境依赖：PG（correction_log/contents）+ Qdrant（个人规则向量）+ SetFit 模型
（arbitrate 回退时）。Qdrant/模型缺失 → 个人规则层失败自动回落全局，仍可出报告。

用法：
  python scripts/measure_correction_gain.py                 # full 7 天
  python scripts/measure_correction_gain.py --mode sample --n 100 --seed 7
  python scripts/measure_correction_gain.py --days 30 --content-type photo
输出：.cowork-temp/correction_gain_report.json + 控制台摘要；退出码 0=门禁过/1=未过。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

GAIN_GATE = 0.10  # 7 天同类准确率提升 ≥10%


def _collect_samples(db, days: int, content_type: str | None) -> list[dict]:
    """窗口内纠错样本：correction_log JOIN contents 取文本（B5-c-1 设计不含文本）"""
    from app.db.models import Content, CorrectionLog
    from sqlalchemy import select

    window_start = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(CorrectionLog, Content.text)
        .outerjoin(Content, Content.id == CorrectionLog.content_id)
        .where(CorrectionLog.created_at >= window_start)
        .order_by(CorrectionLog.created_at.desc())
    )
    if content_type:
        stmt = stmt.where(CorrectionLog.content_type == content_type)
    out: list[dict] = []
    for row, text in db.execute(stmt):
        out.append({
            "id": row.id,
            "user_id": str(row.user_id),
            "content_id": str(row.content_id) if row.content_id else None,
            "text": text or "",
            "old_label": row.old_label,
            "new_label": row.new_label,
            "content_type": row.content_type or "text",
            "source": row.source,
        })
    return out


def _before_correct(sample: dict) -> bool | None:
    """before 判定：old_label 与 new_label 是否一致（无 old_label → None 跳过）"""
    if not sample.get("old_label") or not sample.get("new_label"):
        return None
    return sample["old_label"] == sample["new_label"]


def _after_correct(db, sample: dict) -> bool | None:
    """after 判定：arbitrate 当前判定是否命中 new_label（个人规则/全局回流后）"""
    if not sample.get("new_label") or not sample.get("text"):
        return None
    try:
        from app.services.correction import arbitrate

        result = arbitrate(db, sample["user_id"], sample["text"], sample["content_type"])
        return result.get("label") == sample["new_label"]
    except Exception:  # noqa: BLE001 —— 单样本失败跳过（不影响整体）
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="纠错 7 天准确率提升测量（≥10% 门禁）")
    parser.add_argument("--days", type=int, default=7, help="统计窗口天数（默认 7）")
    parser.add_argument("--content-type", default=None, help="只看某类型（text/photo/voice）")
    parser.add_argument("--mode", choices=["full", "sample"], default="full",
                        help="full=窗口内全部；sample=确定性抽样（对照口径）")
    parser.add_argument("--n", type=int, default=50, help="sample 模式抽样数")
    parser.add_argument("--seed", type=int, default=42, help="sample 模式随机种子")
    args = parser.parse_args()

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        samples = _collect_samples(db, args.days, args.content_type)
        if not samples:
            print(f"窗口 {args.days} 天无纠错样本（correction_log 空）——无数据可测，门禁视作未验证")
            report = {"gate": False, "reason": "no-samples", "samples": 0}
            _dump(report)
            sys.exit(1)
        if args.mode == "sample":
            rng = random.Random(args.seed)  # noqa: S311 —— 抽样可复现性，非密码用途
            samples = rng.sample(samples, min(args.n, len(samples)))
        print(f"[measure] 窗口 {args.days} 天 · 模式 {args.mode} · 样本 {len(samples)}")

        # 分类型统计
        by_type: dict[str, dict] = {}
        for s in samples:
            ct = s["content_type"]
            t = by_type.setdefault(ct, {"n": 0, "before_ok": 0, "before_n": 0, "after_ok": 0, "after_n": 0})
            b = _before_correct(s)
            if b is not None:
                t["before_n"] += 1
                t["before_ok"] += 1 if b else 0
            a = _after_correct(db, s)
            if a is not None:
                t["after_n"] += 1
                t["after_ok"] += 1 if a else 0
            t["n"] += 1

        summary: dict[str, dict] = {}
        for ct, t in sorted(by_type.items()):
            before = round(t["before_ok"] / t["before_n"], 4) if t["before_n"] else None
            after = round(t["after_ok"] / t["after_n"], 4) if t["after_n"] else None
            gain = round(after - before, 4) if (before is not None and after is not None) else None
            summary[ct] = {"n": t["n"], "before_acc": before, "after_acc": after,
                           "gain": gain, "gate": gain is not None and gain >= GAIN_GATE}
            print(f"  [{ct}] n={t['n']} before_acc={before} after_acc={after} gain={gain} "
                  f"({'✅' if summary[ct]['gate'] else '❌' if gain is not None else '⚠'})")

        # 总体（合并所有类型）
        all_before_ok = sum(t["before_ok"] for t in by_type.values())
        all_before_n = sum(t["before_n"] for t in by_type.values())
        all_after_ok = sum(t["after_ok"] for t in by_type.values())
        all_after_n = sum(t["after_n"] for t in by_type.values())
        before = round(all_before_ok / all_before_n, 4) if all_before_n else None
        after = round(all_after_ok / all_after_n, 4) if all_after_n else None
        gain = round(after - before, 4) if (before is not None and after is not None) else None
        gate = gain is not None and gain >= GAIN_GATE
        print(f"[measure] 总体 before_acc={before} after_acc={after} gain={gain}（门禁 ≥{GAIN_GATE:.0%}）→ "
              f"{'✅ PASS' if gate else '❌ 未达门禁' if gain is not None else '⚠ 数据不足'}")
        report = {
            "window_days": args.days,
            "mode": args.mode,
            "samples": len(samples),
            "per_type": summary,
            "overall": {"before_acc": before, "after_acc": after, "gain": gain, "gate": gate,
                        "gate_threshold": GAIN_GATE},
            "note": "口径待产品部确认；before=纠错发生时旧标签是否已正确，after=纠错生效后仲裁是否命中",
        }
        _dump(report)
        sys.exit(0 if gate else 1)
    finally:
        db.close()


def _dump(report: dict) -> None:
    out = ROOT / ".cowork-temp" / "correction_gain_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")


if __name__ == "__main__":
    main()
