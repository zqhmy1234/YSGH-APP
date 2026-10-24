"""SetFit 分类器训练脚本（M1 Part 3 · F2 文字碎片分类）

用法：python scripts/train_setfit.py [--epochs 1] [--iterations 5] [--eval-only]
      [--backbone BAAI/bge-small-zh-v1.5] [--save-dir <dir>] [--holdout-report]

- 数据：backend/data/setfit_seed.json（7 类 × 12 条手写中文样本，10:2 训练/评估划分）
- 底座：默认 BAAI/bge-m3（本地缓存，中文强；SetFit 少样本微调）。
  `--backbone` 可换其它 sentence-transformers 底座（2026-10-25 小模型试点：
  `--backbone BAAI/bge-small-zh-v1.5 --save-dir models/setfit-classifier-small`）。
  ⚠️ **默认值不变**（仍是 bge-m3）——换底座是实验，不是本脚本的行为变更。
- 输出：backend/models/setfit-classifier/（模型 + labels.json）
- 门禁：评估集准确率 ≥75%（M1）

⚠️ 关于"评估集"的诚实边界（2026-10-25 补录，务必先读）：
本脚本的"评估集"是**从同一份种子数据里切出的 14 条**（每类取索引 {1,4}），
与训练集**同源同分布、同一批手写样本**。它**不是留出集（holdout）**——
同一个作者、同一批主题、同一个写作风格，只能证明"模型没训崩"，
**不能**当作泛化准确率对外宣称。真实泛化能力需要独立的留出语料（当前仓库没有）。
`--holdout-report` 会把这个边界显式打印出来，避免数字被误读。
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
# 默认底座（**行为基线，勿改**）。换底座请走 --backbone，不要改这里——
# reflow_global.py 直接 import 本模块并复用 train()，改默认会连带改变线上微调链。
BACKBONE = "BAAI/bge-m3"

# 英文码 → 中文展示名（唯一来源；写 labels.json 与评估输出共用）
CLASSES_CN: dict[str, str] = {
    "todo": "待办",
    "idea": "灵感",
    "emotion": "情绪",
    "quote": "引用",
    "mixed": "混合",
    "question": "问询",
    "fact": "事实",
}


def load_seed() -> tuple[list[str], list[str], list[str], list[str]]:
    """加载种子数据 → (train_texts, train_labels, eval_texts, eval_labels)"""
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    classes: list[str] = data["_meta"]["classes"]
    # 2026-10-23 扩 7 类：种子文件的类别必须与训练脚本的中文名表完全对齐，
    # 否则训练会静默少类（例如只训 5 类），而运行期代码按 7 类解释 → 列数/标签错配。
    missing_cn = [c for c in classes if c not in CLASSES_CN]
    missing_group = [c for c in CLASSES_CN if c not in data]
    if missing_cn or missing_group:
        raise ValueError(
            f"setfit_seed.json 类别不一致：未登记中文名={missing_cn}，缺样本组={missing_group}"
        )
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


def train(
    epochs: int,
    iterations: int,
    save_dir: Path | None = None,
    backbone: str | None = None,
) -> float:
    """训练并保存模型，返回评估准确率

    save_dir：输出目录（默认 MODEL_DIR）。共性纠错回流（reflow_global）传 staging
    目录训练，门禁达标后才由调用方换入生产目录——防止脏数据直接覆盖生产模型。
    backbone：底座模型名/路径（默认 BACKBONE=bge-m3）。2026-10-25 小模型试点的入口；
    **默认 None → 仍用 bge-m3**，保证 reflow_global 等既有调用方行为逐字不变。
    """
    from datasets import Dataset
    from setfit import SetFitModel, SetFitTrainer

    bb = backbone or BACKBONE
    target_dir = save_dir or MODEL_DIR
    train_texts, train_labels, eval_texts, eval_labels = load_seed()

    train_ds = Dataset.from_dict({"text": train_texts, "label": train_labels})
    eval_ds = Dataset.from_dict({"text": eval_texts, "label": eval_labels})

    print(f"底座: {bb} | 训练 {len(train_texts)} 条 | 评估 {len(eval_texts)} 条")
    t0 = time.perf_counter()
    model = SetFitModel.from_pretrained(bb)
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
                "classes_cn": [CLASSES_CN[c] for c in label_order],
                "backbone": bb,
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


# ---------------------------------------------------------------------------
# 诚实声明 + 规模/延迟实测（2026-10-25 小模型试点新增）
#
# 为什么单独成函数并强制打印：上一轮有人把这个 14/14 当成"准确率 100%"往上报，
# 而那 14 条是**从同一份种子数据切出来的**（每类索引 {1,4}，训练用其余 10 条），
# 同作者、同批主题、同种写法 ⇒ 它只能证明"没训崩"，不能证明泛化。
# 这个边界必须每次跑都打在屏幕上，而不是只写在注释里（注释没人读，数字会被引用）。
# ---------------------------------------------------------------------------


def print_honesty_notice(eval_n: int, total_n: int) -> None:
    """打印评估口径的诚实声明（同源切分 ≠ 留出集）。"""
    print("")
    print("=" * 72)
    print("⚠️ 诚实声明：本次评估数字**不是留出集（holdout）准确率**")
    print(f"   训练 {total_n - eval_n} 条 / 评估 {eval_n} 条，两者**来自同一份种子文件**")
    print("   （backend/data/setfit_seed.json，每类 12 条手写样本，按索引 {1,4} 切 2 条做评估）。")
    print("   同作者、同批主题、同写作风格 ⇒ 该数字只能说明「模型没训崩」，")
    print("   **不能**作为泛化准确率对外宣称。真实泛化需独立留出语料（当前仓库没有）。")
    print("=" * 72)
    print("")


def measure_backbone(backbone: str) -> dict:
    """加载指定底座、测**体积 + 单条 CPU 延迟**（warm 中位数 + 冷启动首调）。

    为什么单独测：SetFit 训练完的模型「体积/延迟」几乎全由**底座**决定（head 只是一个
    LogisticRegression：7×隐藏维，几 KB），要回答"能不能进热路径"必须测底座前向。

    为什么测 `model_body.encode()` 而不是 `model.predict()`：
    `predict` = encode + head.predict；未训练的 head 是 `NotFittedError`（实测踩到），
    而我们要对比的正是**换底座**带来的差异 ⇒ 口径定在 encode（两边同口径，差值可比）。
    head 的实际开销另行用训练好的模型测（见 --eval-only 的 latency）。

    延迟口径（写清避免与 classifier.py 注释里那个来源不明的「~27s」混淆）：
      · cold_ms = 进程内**首次** encode（含 torch 算子预热/内存分配）
      · warm_* = 之后连续 10 次的统计（生产热路径看这个）
    """
    import statistics

    from sentence_transformers import SentenceTransformer

    t0 = time.perf_counter()
    body = SentenceTransformer(backbone)
    load_s = time.perf_counter() - t0

    probe = "明天记得去取快递，顺便给妈妈打个电话"
    t0 = time.perf_counter()
    body.encode([probe])
    cold_ms = (time.perf_counter() - t0) * 1000

    warm: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        body.encode([probe])
        warm.append((time.perf_counter() - t0) * 1000)

    # 底座体积：从 transformers 的本地缓存目录量（跟随 HF cache 的 symlink 到 blobs）
    size_mb = 0.0
    try:
        from transformers.utils import cached_file

        cfg_path = cached_file(backbone, "config.json")
        if cfg_path:
            base = Path(cfg_path).parent
            size_mb = sum(
                (p.resolve().stat().st_size if p.is_symlink() else p.stat().st_size)
                for p in base.rglob("*")
                if p.is_file()
            ) / (1024 * 1024)
    except Exception as exc:  # noqa: BLE001 —— 量不到体积不影响延迟结论
        print(f"  （体积测量失败，如实标注未知：{exc}）")

    return {
        "backbone": backbone,
        "size_mb": round(size_mb, 1),
        "load_s": round(load_s, 2),
        "cold_ms": round(cold_ms, 1),
        "warm_ms_median": round(statistics.median(warm), 1),
        "warm_ms_min": round(min(warm), 1),
        "warm_ms_max": round(max(warm), 1),
        "embed_dim": body.get_sentence_embedding_dimension(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--eval-only", action="store_true")
    # 2026-10-25 小模型试点：只加参数，**默认值不变**（bge-m3 / 生产目录）
    parser.add_argument(
        "--backbone",
        type=str,
        default=None,
        help=f"底座模型名或本地路径（默认 {BACKBONE}，即不改既有行为）",
    )
    parser.add_argument(
        "--save-dir",
        type=str,
        default=None,
        help=f"输出目录（默认 {MODEL_DIR}）；试点请指向 models/setfit-classifier-small",
    )
    parser.add_argument(
        "--holdout-report",
        action="store_true",
        help="打印评估口径的诚实声明（同源切分 ≠ 留出集）",
    )
    parser.add_argument(
        "--measure-backbone",
        action="store_true",
        help="只测底座体积/延迟（不训练），用于对比表",
    )
    args = parser.parse_args()

    if args.measure_backbone:
        bb = args.backbone or BACKBONE
        print(f"测量底座: {bb}")
        stats = measure_backbone(bb)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        sys.exit(0)

    save_dir = Path(args.save_dir) if args.save_dir else MODEL_DIR
    bb = args.backbone or BACKBONE

    if args.eval_only:
        from setfit import SetFitModel

        model = SetFitModel.from_pretrained(save_dir)
        train_texts, _, eval_texts, eval_labels = load_seed()
        if args.holdout_report:
            print_honesty_notice(len(eval_texts), len(train_texts) + len(eval_texts))
        acc = evaluate(model, eval_texts, eval_labels)
        sys.exit(0 if acc >= 0.75 else 1)

    acc = train(args.epochs, args.iterations, save_dir=save_dir, backbone=args.backbone)
    if args.holdout_report:
        # 诚实声明放在结果**之后**打印：读的人先看到数字，紧接着就看到边界。
        _, _, eval_texts, _ = load_seed()
        print_honesty_notice(len(eval_texts), 84)
    if acc < 0.75:
        print(f"❌ 门禁未达标: {acc:.0%} < 75%")
        sys.exit(1)
    print(f"✅ 门禁达标（≥75%）｜底座: {bb}｜输出: {save_dir}")


if __name__ == "__main__":
    main()
