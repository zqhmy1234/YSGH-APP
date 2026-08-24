"""共性纠错回流微调流水线（B5-c-4 · F4 补全）

按量触发（设计已收敛）：共性纠错累计 ≥50 条才触发 SetFit 微调（不足则攒着，
个人规则层已生效）；微调只是"固化共性纠错进全局模型"。

流程：
1. 扫描 correction_log 中 is_global_candidate=True 的记录
2. 计数 < 阈值 → 跳过（打印待补数量）
3. ≥ 阈值 → 组装微调数据集（文本 → new_label；去重取最新）
   → 调用 train_setfit.py 的微调逻辑（增量：种子 + 纠错样本）
   → 记录 finetune_jobs（pending→running→done/failed）

用法：
    python scripts/reflow_global.py [--threshold 50] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime as _datetime
from datetime import timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_THRESHOLD = 50  # B5-c-4：共性纠错 ≥50 条才触发
CORRECTION_LIMIT = 200  # 单次微调最多取样本数（防过拟合抖动）
REFLOW_ACC_GATE = 0.75  # 评估集门禁（M1 口径）：不达标不覆盖生产模型
KEEP_BACKUPS = 3  # 模型备份保留份数（回滚兜底）


def _backup_model(model_dir: Path, backup_root: Path, keep: int = KEEP_BACKUPS) -> Path | None:
    """训练前备份生产模型（回滚兜底；首次无模型则跳过）

    备份到 backup_root/setfit-classifier-<时间戳>，仅保留最近 keep 份。
    """
    if not model_dir.exists():
        return None
    backup_root.mkdir(parents=True, exist_ok=True)
    # 微秒时间戳：同秒多次调用不碰撞（备份名可排序，用于 keep 裁剪）
    stamp = _datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    dest = backup_root / f"setfit-classifier-{stamp}"
    # 防同微秒重复调用碰撞（2026-08-24 门禁 flaky：测试内两次备份同微秒 → FileExistsError）
    suffix = 1
    while dest.exists():
        dest = backup_root / f"setfit-classifier-{stamp}-{suffix}"
        suffix += 1
    shutil.copytree(model_dir, dest)
    olds = sorted(backup_root.glob("setfit-classifier-*"), key=lambda p: p.name, reverse=True)
    for old in olds[keep:]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def _promote_if_gate(staging: Path, model_dir: Path, acc: float, gate: float = REFLOW_ACC_GATE) -> None:
    """评估集门禁达标才覆盖生产模型；不达标清理 staging 并抛错（原模型不动）"""
    if acc < gate:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(f"评估集门禁未过（{acc:.0%} < {gate:.0%}），保留原模型")
    if model_dir.exists():
        shutil.rmtree(model_dir)
    staging.rename(model_dir)


def collect_candidates(db) -> list[dict]:
    """共性纠错候选（is_global_candidate=True，取每个 user+content 最新一条）

    text 从 contents 表关联（correction_log 设计不含文本，B5-c-1）
    """
    from app.db.models import Content, CorrectionLog
    from sqlalchemy import select

    rows = db.execute(
        select(CorrectionLog, Content.text)
        .outerjoin(Content, Content.id == CorrectionLog.content_id)
        .where(CorrectionLog.is_global_candidate.is_(True))
        .order_by(CorrectionLog.created_at.desc())
    ).all()
    seen: dict[str, dict] = {}
    for r, text in rows:
        key = f"{r.user_id}:{r.content_id}"
        if key not in seen:  # 最新一条优先（按 created_at 倒序）
            seen[key] = {"text": text or "", "new_label": r.new_label}
    return list(seen.values())


def build_dataset(candidates: list[dict]) -> dict[str, list[str]]:
    """候选 → 5 类数据集（文本按 new_label 分组；无文本的跳过）"""
    dataset: dict[str, list[str]] = {c: [] for c in ("todo", "idea", "emotion", "quote", "mixed")}
    for c in candidates[:CORRECTION_LIMIT]:
        text = (c.get("text") or "").strip()
        label = c.get("new_label")
        if text and label in dataset:
            dataset[label].append(text)
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--dry-run", action="store_true", help="只统计不训练")
    args = parser.parse_args()

    from app.db.session import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    # 修复（审查 MAJOR）：原流水线断裂——mark_global_candidates 无触发入口
    # （仅测试调用），is_global_candidate 恒 False → 本脚本永远 0 候选。
    # 补：扫描前先执行共性纠错标记（≥2 用户一致的 (old→new) 对 → 全局候选）。
    from app.services.correction import mark_global_candidates

    marked = mark_global_candidates(db)
    if marked:
        print(f"共性纠错标记: 新增 {marked} 条全局候选")

    candidates = collect_candidates(db)
    print(f"共性纠错候选: {len(candidates)} 条（阈值 {args.threshold}）")

    if len(candidates) < args.threshold:
        print(f"❌ 未达阈值，还需 {args.threshold - len(candidates)} 条（个人规则层已生效，微调继续攒）")
        db.close()
        sys.exit(0)

    dataset = build_dataset(candidates)
    total = sum(len(v) for v in dataset.values())
    print(f"微调数据集: {total} 条（按类: { {k: len(v) for k, v in dataset.items()} }）")
    if args.dry_run:
        print("dry-run：跳过训练")
        db.close()
        sys.exit(0)

    # 记录 finetune_jobs
    job_id = db.execute(
        text(
            "INSERT INTO finetune_jobs (trigger, dataset_count, status, started_at) "
            "VALUES ('>=50 条共性纠错', :n, 'running', now()) RETURNING id"
        ),
        {"n": total},
    ).scalar()
    db.commit()
    print(f"finetune_jobs #{job_id} 启动")

    # 修复（审查 MAJOR）：原 ts.train() 直接 save_pretrained 覆盖生产模型
    # （backend/models/setfit-classifier/），纠错数据脏 → 全局模型被污染不可回滚。
    # 改：①训练前备份旧模型 ②训练到 staging ③评估集门禁达标才换入生产目录。
    import scripts.train_setfit as ts

    backup_dir = _backup_model(ts.MODEL_DIR, BACKEND_DIR / "models" / "backups")
    staging = ts.MODEL_DIR.with_name("setfit-classifier-staging")
    tmp_seed: Path | None = None
    try:
        # 组装训练样本：种子 + 共性纠错（纠错样本追加到对应类）
        seed = json.loads((BACKEND_DIR / "data" / "setfit_seed.json").read_text(encoding="utf-8"))
        merged = {cls: list(seed[cls]) for cls in dataset}
        for cls, texts in dataset.items():
            merged[cls].extend(texts)
        tmp_seed = BACKEND_DIR / "data" / "setfit_seed_reflow.json"
        tmp_seed.write_text(
            json.dumps({"_meta": seed["_meta"], **merged}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 复用训练入口（切换 SEED_PATH 指向合并数据集；save_dir=staging 不碰生产模型）
        ts.SEED_PATH = tmp_seed
        acc = ts.train(epochs=1, iterations=5, save_dir=staging)
        _promote_if_gate(staging, ts.MODEL_DIR, acc)
        db.execute(
            text(
                "UPDATE finetune_jobs SET status='done', finished_at=now(), model=:m "
                "WHERE id=:i"
            ),
            {"m": str(ts.MODEL_DIR), "i": job_id},
        )
        db.commit()
        backup_note = f"（旧模型备份: {backup_dir}）" if backup_dir else "（首次训练，无旧模型）"
        print(f"✅ 全局模型微调完成，评估准确率 {acc:.0%} {backup_note}")
    except Exception as exc:  # noqa: BLE001
        db.execute(
            text("UPDATE finetune_jobs SET status='failed', finished_at=now() WHERE id=:i"),
            {"i": job_id},
        )
        db.commit()
        print(f"❌ 微调失败: {exc}")
        sys.exit(1)
    finally:
        # 修复（审查 MINOR）：异常路径 tmp_seed 残留 + staging 残留统一清理
        if tmp_seed is not None:
            tmp_seed.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        db.close()


if __name__ == "__main__":
    main()
