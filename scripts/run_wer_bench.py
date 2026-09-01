"""ASR WER/CER 评测管线（VOI-001 · 2026-08-19）

数据：AISHELL-1（openslr SLR33 官方划分，中文普通话 16k 单声道，安静室内——
     与本项目手机录音输入分布一致）。说话人包下载于 research/asr_bench/wav/。

流程：解包抽样（默认 20 条，可 --n 调）→ 真实转写（FunASR 优先，失败降级 SenseVoice）
     → 字级 CER（编辑距离/总字数，中文 WER 惯例）→ JSON 报告（CI 金丝雀口径）。

用法：
  MOCK_EXTERNAL_AI=false infisical run --env=dev --silent -- python scripts/run_wer_bench.py --n 20
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

BENCH = Path(__file__).resolve().parent.parent / "research" / "asr_bench"
EXTRACT = Path(__file__).resolve().parent.parent / ".cowork-temp" / "aishell_extract"

# 中文标点归一（转写文本与标注对齐用）
_PUNCT = re.compile(r"[\s，。！？、；："r"''（）《》【】—…·,.!?;:()\-]")


def _clean(text: str) -> str:
    return _PUNCT.sub("", text)


def _cer(ref: str, hyp: str) -> float:
    """字级 CER：Levenshtein 编辑距离 / 参考字数（中文 ASR 惯例）"""
    a, b = _clean(ref), _clean(hyp)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / len(a)


def _extract_once() -> dict[str, str]:
    """解包说话人 tar.gz → {wav_path: 标注文本}（只取 wav/*.wav）"""
    if EXTRACT.exists() and any(EXTRACT.rglob("*.wav")):
        pass
    else:
        EXTRACT.mkdir(parents=True, exist_ok=True)
        for pkg in sorted((BENCH / "wav").glob("*.tar.gz")):
            with tarfile.open(pkg, "r:gz") as tf:
                for m in tf.getmembers():
                    if m.name.endswith(".wav") and m.isfile():
                        tf.extract(m, EXTRACT)
    # 标注文本
    trans = {}
    txt = (BENCH / "aishell_transcript_v0.8.txt").read_text(encoding="utf-8")
    for line in txt.strip().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            trans[parts[0]] = parts[1]
    return trans


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20, help="抽样条数（默认 20）")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    from app.services.external.asr import transcribe

    trans = _extract_once()
    wavs = sorted(EXTRACT.rglob("*.wav"))
    if not wavs:
        print("未找到 wav（请先下载 research/asr_bench/wav/*.tar.gz）", flush=True)
        return 1
    # 只保留有标注的
    paired = [(w, trans.get(w.stem, "")) for w in wavs if trans.get(w.stem)]
    print(f"可用 {len(paired)}/{len(wavs)} 条（有标注）", flush=True)

    import random

    random.seed(args.seed)
    sample = random.sample(paired, min(args.n, len(paired)))

    results = []
    total_cer = 0.0
    for wav, ref in sample:
        t0 = time.perf_counter()
        try:
            r = transcribe(wav)
            hyp = r.text or ""
            cer = _cer(ref, hyp)
            total_cer += cer
            results.append({
                "file": wav.name,
                "channel": r.channel,
                "cer": round(cer, 4),
                "ref_len": len(_clean(ref)),
                "ms": int((time.perf_counter() - t0) * 1000),
                "ref": ref[:50],
                "hyp": hyp[:50],
            })
            print(
                f"[{len(results)}/{len(sample)}] {wav.name} cer={cer:.3f} "
                f"{r.channel} {int((time.perf_counter()-t0)*1000)}ms",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {wav.name}: {type(exc).__name__} {str(exc)[:100]}", flush=True)

    avg_cer = round(total_cer / len(results), 4) if results else 1.0
    report = {
        "dataset": "AISHELL-1 (subset)",
        "n": len(results),
        "avg_cer": avg_cer,
        "results": results,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    out = BENCH / "wer_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"平均 CER: {avg_cer}（{len(results)} 条）→ {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
