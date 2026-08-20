"""SetFit 分类器训练脚本（M1 Part 3 · F2 文字碎片分类）

用法：python scripts/train_setfit.py [--epochs 1] [--iterations 5] [--eval-only]

- 数据：backend/data/setfit_seed.json（5 类 × 12 条手写中文样本，10:2 训练/评估划分）
- 底座：BAAI/bge-m3（本地缓存，中文强；SetFit 少样本微调）
- 输出：backend/models/setfit-classifier/（模型 + labels.json）
- 门禁：评估集准确率 ≥75%（M1）
"""
from __future__ import annotations

import argparse
import json

# 限制 torch 线程数（16GB 机器 CPU 训练防 OOM）
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("TORCH_NUM_THREADS", "4")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent
SEED_PATH = BACKEND_DIR / "data" / "setfit_seed.json"
MODEL_DIR = BACKEND_DIR / "models" / "setfit-classifier"
BACKBONE = "BAAI/bge-m3"


def load_seed() -> tuple[list[str], list[str], list[str], list[str]]:
    """加载种子数据 → (train_texts, train_labels, eval_texts, eval_labels)"""
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    classes: list[str] = data["_meta"]["classes"]
    train_texts: list[str] = []
    train_labels: list[str] = []
    eval_texts: list[str] = []
    eval_labels: list[str] = []
    for cls in classes:
        items = data[cls]
        # 每类留 2 条做评估（取第 2/第 5 条，避免与训练样本太像的位置偏差）
        # 修复：样本 <6 条时固定索引越界（IndexError），退化取最后 2 条；<2 条则全训练
        n = len(items)
        if n >= 6:
            eval_idx = {1, 4}
        elif n >= 2:
            eval_idx = {n - 2, n - 1}
        else:
            eval_idx = set()
        for i, text in enumerate(items):
            if i in eval_idx:
                eval_texts.append(text)
                eval_labels.append(cls)
            else:
                train_texts.append(text)
                train_labels.append(cls)
    return train_texts, train_labels, eval_texts, eval_labels


def train(epochs: int, iterations: int, save_dir: Path | None = None) -> float:
    """训练并保存模型，返回评估准确率

    save_dir：输出目录（默认 MODEL_DIR）。共性纠错回流（reflow_global）传 staging
    目录训练，门禁达标后才由调用方换入生产目录——防止脏数据直接覆盖生产模型。
    """
    from datasets import Dataset
    from setfit import SetFitModel, SetFitTrainer

    target_dir = save_dir or MODEL_DIR
    train_texts, train_labels, eval_texts, eval_labels = load_seed()

    train_ds = Dataset.from_dict({"text": train_texts, "label": train_labels})
    eval_ds = Dataset.from_dict({"text": eval_texts, "label": eval_labels})

    print(f"底座: {BACKBONE} | 训练 {len(train_texts)} 条 | 评估 {len(eval_texts)} 条")
    t0 = time.perf_counter()
    model = SetFitModel.from_pretrained(BACKBONE)
    print(f"模型加载 {time.perf_counter() - t0:.0f}s")

    trainer = SetFitTrainer(
        model=model,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        num_iterations=iterations,
        num_epochs=epochs,
        batch_size=8,
        learning_rate=2e-5,
        use_amp=True,  # fp16 混合精度：16GB 机器内存减半、速度翻倍
        column_mapping={"text": "text", "label": "label"},
    )
    t0 = time.perf_counter()
    trainer.train()
    print(f"训练耗时 {time.perf_counter() - t0:.0f}s")

    target_dir.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(target_dir)
    # 标签顺序以模型 id2label 为准（LabelEncoder 字母序，非业务顺序）
    id2label = getattr(trainer.model, "id2label", None) or {}
    label_order = [id2label[i] for i in sorted(id2label)]
    (target_dir / "labels.json").write_text(
        json.dumps(
            {
                "classes": label_order,
                "classes_cn": [
                    {"todo": "待办", "idea": "灵感", "emotion": "情绪", "quote": "引用", "mixed": "混合"}[c]
                    for c in label_order
                ],
                "backbone": BACKBONE,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"模型已保存: {target_dir}")

    return evaluate(model, eval_texts, eval_labels)


def evaluate(model, texts: list[str], labels: list[str]) -> float:
    """评估准确率（模型标签顺序 = 训练集 LabelEncoder 排序 = 固定 classes 顺序）"""
    preds = model.predict(texts)
    correct = sum(1 for p, t in zip(preds, labels, strict=True) if p == t)
    acc = correct / len(labels)
    for text, p, t in zip(texts, preds, labels, strict=True):
        mark = "✅" if p == t else "❌"
        print(f"  {mark} [{t}→{p}] {text}")
    print(f"评估准确率: {acc:.0%} ({correct}/{len(labels)})")
    return acc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--eval-only", action="store_true")
    args = parser.parse_args()

    if args.eval_only:
        from setfit import SetFitModel

        model = SetFitModel.from_pretrained(MODEL_DIR)
        _, _, eval_texts, eval_labels = load_seed()
        acc = evaluate(model, eval_texts, eval_labels)
        sys.exit(0 if acc >= 0.75 else 1)

    acc = train(args.epochs, args.iterations)
    if acc < 0.75:
        print(f"❌ 门禁未达标: {acc:.0%} < 75%")
        sys.exit(1)
    print("✅ 门禁达标（≥75%）")


if __name__ == "__main__":
    main()
