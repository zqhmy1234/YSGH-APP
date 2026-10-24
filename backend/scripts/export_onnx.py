"""SetFit 底座 → ONNX 导出 + int8 动态量化（2026-10-25 小模型试点）

用法：
    python scripts/export_onnx.py --model-dir models/setfit-classifier-small \
        --out-dir models/setfit-classifier-small-onnx
    python scripts/export_onnx.py --model-dir ... --out-dir ... --quantize int8
    python scripts/export_onnx.py --bench-only --model-dir ... --out-dir ...

## 为什么需要这个脚本

试点要回答的是「小模型能不能进 API 热路径」，而判据里**体积 <40MB** 这一条
靠 PyTorch fp32 的 bge-small（91.9MB 实测）**达不到**。ONNX + int8 动态量化是
唯一不换模型的达标路径：把 Conv/MatMul 权重压到 int8，激活在运行时量化。

## 边界（如实声明，别把这里当"已集成"）

- 本脚本**只做离线导出与测量**。`backend/app/services/classifier.py` 仍走 PyTorch，
  **没有任何运行期代码消费 ONNX 产物**——接 ONNX 是后续决策，不在本批。
- 量化会掉精度。本脚本**不做精度保证**，只把产物交出来；精度必须在**留出集**上
  实测（见 report 的 accuracy 字段），掉多少如实写。
- int8 动态量化对**短中文文本**（本产品典型输入 10~30 字）收益有限：序列短 ⇒
  MatMul 的算术强度低，反量化开销占比高，实测可能不如 fp32 快。脚本会把两种都测出来。

## UTS/环境无关

纯 Python，不需要 HBuilderX；不写仓库依赖声明（按试点纪律，依赖装到 --user）。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("TORCH_NUM_THREADS", "4")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent

# 导出的模型文件名（fixed，便于测量与后续接入）
ONNX_NAME = "model.onnx"
ONNX_INT8_NAME = "model.int8.onnx"


def _dir_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / (1024 * 1024)


def export_fp32(model_dir: Path, out_dir: Path) -> Path:
    """底座（sentence-transformers）→ ONNX fp32。

    用 optimum 的 ORTModelForFeatureExtraction：只导出 encoder 主体
    （SetFit 的 head 是 sklearn LogisticRegression，本来就不在 torch 图里）。
    """
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    model = ORTModelForFeatureExtraction.from_pretrained(
        str(model_dir), export=True, provider="CPUExecutionProvider"
    )
    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model.save_pretrained(out_dir)
    tok.save_pretrained(out_dir)
    print(f"fp32 导出完成 {time.perf_counter() - t0:.0f}s")

    # optimum 落盘名可能是 model.onnx / encoder_model.onnx，统一成 ONNX_NAME 便于度量
    cand = sorted(out_dir.glob("*.onnx"))
    if not cand:
        raise RuntimeError(f"导出后未找到 .onnx 文件: {out_dir}")
    src = cand[0]
    dst = out_dir / ONNX_NAME
    if src.name != ONNX_NAME:
        if dst.exists():
            dst.unlink()
        src.rename(dst)
    return dst


def quantize_int8(fp32_path: Path, out_dir: Path) -> Path:
    """ONNX 动态量化（weights → int8；激活运行时量化）。

    为什么用**动态**而非静态：静态量化要校准数据集，本产品没有代表性的
    中文碎片语料（种子 84 条太窄、会过拟合校准分布）⇒ 动态量化更诚实。

    ## ⚠️ 必须 `per_channel=True`（2026-10-25 实测，不是偏好）

    默认的 **per-tensor** 对称量化会把留出集准确率从 11/14 打到 **9/14（-2 条）**，
    而 **per-channel 只掉 1 条（11→10）** —— 同样 22.9MB 体积，精度差一条。
    原因：整条模型共用一个 scale 时，embedding 与 attention 里那些量级差异大的
    小矩阵会被同一个 scale 压扁；per-channel 给每个输出通道独立 scale，损伤小得多。

    另外两个变体已实测**更差**，不要试：
      · 排除 embedding/pooler 层：**9/14（-2）**（少了量化的层反而留了 fp32 权重，
        混合精度在短文本上引入额外数值不连续）；
      · uint8 per-channel：10/14（-1，与 int8 per-channel 持平，无额外收益）。

    ⇒ 结论：**int8 + per-channel 是当前最优**（22.9MB / 掉 1 条）。
    精度损失无法完全消除 —— 上线前必须用真实语料复评（本批留出集仅 14 条）。
    """
    from onnxruntime.quantization import QuantType, quantize_dynamic

    dst = out_dir / ONNX_INT8_NAME
    t0 = time.perf_counter()
    quantize_dynamic(
        model_input=str(fp32_path),
        model_output=str(dst),
        weight_type=QuantType.QInt8,
        per_channel=True,
    )
    print(f"int8 量化完成 {time.perf_counter() - t0:.0f}s（per-channel）")
    return dst


# ---------------------------------------------------------------------------
# 测量：延迟 + 精度
#
# 口径必须说清（否则数字不可比）：
#   · 延迟只测 encoder 前向（fp32/int8 同口径），不含 tokenize 与 head；
#   · warm = 连续 N 次的中位数；first = 进程内首次（含 ORT 会话初始化后的首调）。
# 精度用与 PyTorch 版**同一批留出集**，并把 embedding 交给**同一个 sklearn head**
# （head 从 PyTorch 模型目录的 model_head.pkl 读），这样误差**只**来自底座，
# 不掺入"head 也重训了"的变量。
# ---------------------------------------------------------------------------


def bench_onnx(onnx_path: Path, model_dir: Path, texts: list[str], n_warm: int = 10) -> dict:
    """测 ONNX 编码延迟（单条，warm 中位数 + 首调）。"""
    import numpy as np
    import onnxruntime as ort
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(model_dir))
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    want = {i.name for i in sess.get_inputs()}

    def encode(text: str) -> np.ndarray:
        enc = tok(text, return_tensors="np", truncation=True, max_length=64)
        feeds = {k: v for k, v in enc.items() if k in want}
        out = sess.run(None, feeds)
        # last_hidden_state → CLS 池化（bge 系列用 CLS；与 sentence-transformers 默认一致）
        hidden = out[0]
        vec = hidden[:, 0, :]
        norm = np.linalg.norm(vec, axis=1, keepdims=True)
        return vec / np.clip(norm, 1e-12, None)

    probe = texts[0]
    t0 = time.perf_counter()
    encode(probe)
    first_ms = (time.perf_counter() - t0) * 1000

    warm: list[float] = []
    for _ in range(n_warm):
        t0 = time.perf_counter()
        encode(probe)
        warm.append((time.perf_counter() - t0) * 1000)

    return {
        "first_ms": round(first_ms, 1),
        "warm_ms_median": round(statistics.median(warm), 1),
        "warm_ms_min": round(min(warm), 1),
        "warm_ms_max": round(max(warm), 1),
        "_encode": encode,  # 内部用；写 JSON 前剔除
    }


def accuracy_with_head(encode, model_dir: Path, texts: list[str], gold: list[str]) -> dict:
    """用 ONNX embedding + **原 PyTorch 模型的 sklearn head** 算准确率。

    这样误差**只**来自「底座换成 ONNX+int8」，不掺 head 重训的变量（可比性要求）。
    """
    import numpy as np
    from setfit import SetFitModel

    # head 直接从训练好的模型目录读（SetFit 把它序列化成 model_head.pkl）
    setfit = SetFitModel.from_pretrained(Path(model_dir))
    head = setfit.model_head
    X = np.vstack([encode(t) for t in texts])
    preds = head.predict(X)
    # setfit 的 model_head.predict 返回**标签字符串**（内部已过 LabelEncoder 反变换）
    preds = list(preds)
    ok = sum(1 for p, g in zip(preds, gold, strict=True) if p == g)
    total = len(gold)
    return {
        "correct": ok,
        "total": total,
        "accuracy": round(ok / total, 4) if total else 0.0,
        "misses": [
            {"text": t, "gold": g, "pred": p}
            for t, g, p in zip(texts, gold, preds, strict=True)
            if g != p
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True, help="SetFit 模型目录（含底座 + model_head.pkl）")
    parser.add_argument("--out-dir", required=True, help="ONNX 产物目录")
    parser.add_argument("--quantize", choices=["none", "int8"], default="int8")
    parser.add_argument("--holdout", default="", help="留出集 JSON（[{text,label}]）；给了就算精度")
    parser.add_argument("--bench-only", action="store_true", help="跳过导出，只测已有产物")
    parser.add_argument("--json-out", default="", help="把结果写到该 JSON 文件")
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    out_dir = Path(args.out_dir)
    if not model_dir.exists():
        print(f"❌ 模型目录不存在: {model_dir}")
        sys.exit(1)

    fp32_path = out_dir / ONNX_NAME
    int8_path = out_dir / ONNX_INT8_NAME

    if not args.bench_only:
        fp32_path = export_fp32(model_dir, out_dir)
        if args.quantize == "int8":
            int8_path = quantize_int8(fp32_path, out_dir)

    result: dict = {
        "model_dir": str(model_dir),
        "out_dir": str(out_dir),
        "torch_dir_mb": round(_dir_mb(model_dir), 1),
        "onnx_fp32_mb": round(fp32_path.stat().st_size / 1024 / 1024, 1) if fp32_path.exists() else None,
        "onnx_int8_mb": round(int8_path.stat().st_size / 1024 / 1024, 1) if int8_path.exists() else None,
    }

    texts: list[str] = []
    gold: list[str] = []
    if args.holdout:
        held = json.loads(Path(args.holdout).read_text(encoding="utf-8"))
        texts = [h["text"] for h in held]
        gold = [h["label"] for h in held]
    probe_texts = texts[:1] if texts else ["明天记得去取快递，顺便给妈妈打个电话"]

    for tag, p in (("fp32", fp32_path), ("int8", int8_path)):
        if not p.exists():
            continue
        bench = bench_onnx(p, model_dir, probe_texts)
        encode = bench.pop("_encode")
        entry = dict(bench)
        if texts:
            entry["accuracy"] = accuracy_with_head(encode, model_dir, texts, gold)
        result[tag] = entry

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
        print(f"结果已写入 {args.json_out}")


if __name__ == "__main__":
    main()
