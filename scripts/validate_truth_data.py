#!/usr/bin/env python3
"""真值数据校验器（规格：docs/真值数据规格标准_v1.md · 2026-08-25 拍板）

扫描 research/truth-data/{a,b,c,d,e}/*_v{n}.json，逐批校验：
  - 必填字段 100% / 枚举合法 / 类型正确
  - A：expected_ids 与 expected_label 二选一（且不能同空）
  - B：label_confidence=disputed 不阻断但警告（不入校准集）
  - C：self_talk_only 必须 true（隐私铁律）；transcript_source=human 才算真值
  - D：consensus ≥2/3 才入真值；expected_l1.date 格式
  - E：predicted ≠ corrected

用法：
  python scripts/validate_truth_data.py            # 全批次
  python scripts/validate_truth_data.py --batch a  # 单批
退出码：0 = 全绿（可入 manifest）；1 = 有阻断项。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "truth-data"

FRAGMENT_LABELS = {"待办", "灵感", "情绪", "引用", "混合"}
QUERY_LAYERS = {"descriptive", "keyword", "typo", "temporal", "route", "mixed"}
BATCH_DIRS = {"a": "a", "b": "b", "c": "c", "d": "d", "e": "e"}

REQUIRED: dict[str, set[str]] = {
    "a": {"query_id", "query", "layer", "expected_ids", "expected_label",
          "user_id_hashed", "source", "collected_at"},
    "b": {"fragment_id", "text", "label", "label_confidence", "source",
          "user_id_hashed", "collected_at"},
    "c": {"clip_id", "audio_ref", "duration_s", "transcript",
          "transcript_source", "noise_level", "self_talk_only",
          "user_id_hashed", "collected_at"},
    "d": {"set_id", "user_id_hashed", "time_span", "photo_refs",
          "expected_l1", "annotator_count", "consensus", "collected_at"},
    "e": {"correction_id", "text", "predicted", "corrected", "context",
          "user_id_hashed", "collected_at"},
}


def _load_batch(batch: str) -> tuple[list[dict], Path]:
    """读 {batch}_v*.json（取版本号最大的文件），返回 (records, path)"""
    files = sorted((DATA_DIR / BATCH_DIRS[batch]).glob(f"{batch}_v*.json"))
    if not files:
        return [], Path()
    path = files[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: 顶层必须是数组")
    return data, path


def _check_required(rec: dict, batch: str, errors: list[str], path: Path) -> None:
    missing = REQUIRED[batch] - set(rec.keys())
    if missing:
        errors.append(f"{path.name}: 缺必填字段 {sorted(missing)}")


def _validate_a(rec: dict, errors: list[str], warnings: list[str], path: Path) -> None:
    if rec.get("layer") not in QUERY_LAYERS:
        errors.append(f"{path.name} {rec.get('query_id')}: layer 非法 {rec.get('layer')}")
    ids = rec.get("expected_ids") or []
    label = rec.get("expected_label")
    if not ids and not label:
        errors.append(f"{path.name} {rec.get('query_id')}: expected_ids 与 expected_label 不能同空")
    if not isinstance(ids, list):
        errors.append(f"{path.name} {rec.get('query_id')}: expected_ids 必须是数组")


def _validate_b(rec: dict, errors: list[str], warnings: list[str], path: Path) -> None:
    if rec.get("label") not in FRAGMENT_LABELS:
        errors.append(f"{path.name} {rec.get('fragment_id')}: label 非法 {rec.get('label')}")
    if rec.get("label_confidence") == "disputed":
        warnings.append(f"{path.name} {rec.get('fragment_id')}: disputed 不入校准集（警告）")


def _validate_c(rec: dict, errors: list[str], warnings: list[str], path: Path) -> None:
    if rec.get("self_talk_only") is not True:
        errors.append(f"{path.name} {rec.get('clip_id')}: 隐私铁律 self_talk_only 必须 true")
    if rec.get("transcript_source") not in ("human", "asr"):
        errors.append(f"{path.name} {rec.get('clip_id')}: transcript_source 非法")
    dur = rec.get("duration_s")
    if not isinstance(dur, (int, float)) or dur <= 0 or dur > 210:
        errors.append(f"{path.name} {rec.get('clip_id')}: duration_s 非法（0<d≤210）")


def _validate_d(rec: dict, errors: list[str], warnings: list[str], path: Path) -> None:
    consensus = str(rec.get("consensus", ""))
    m = re.fullmatch(r"(\d+)/(\d+)", consensus)
    if not m:
        errors.append(f"{path.name} {rec.get('set_id')}: consensus 格式 n/m")
    else:
        agree, total = int(m.group(1)), int(m.group(2))
        if total < 1 or agree / total < 2 / 3:
            errors.append(f"{path.name} {rec.get('set_id')}: consensus 需 ≥2/3")
    for l1 in rec.get("expected_l1") or []:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(l1.get("date", ""))):
            errors.append(f"{path.name} {rec.get('set_id')}: expected_l1.date 格式 YYYY-MM-DD")
    ts = rec.get("time_span") or {}
    if not ts.get("from") or not ts.get("to"):
        errors.append(f"{path.name} {rec.get('set_id')}: time_span 必填 from/to")


def _validate_e(rec: dict, errors: list[str], warnings: list[str], path: Path) -> None:
    if rec.get("predicted") == rec.get("corrected"):
        errors.append(f"{path.name} {rec.get('correction_id')}: predicted 不能等于 corrected")
    if rec.get("predicted") not in FRAGMENT_LABELS or rec.get("corrected") not in FRAGMENT_LABELS:
        errors.append(f"{path.name} {rec.get('correction_id')}: 类别不在 5 类枚举内")
    if rec.get("context") not in ("user-manual", "arbitration"):
        errors.append(f"{path.name} {rec.get('correction_id')}: context 非法")


_VALIDATORS = {"a": _validate_a, "b": _validate_b, "c": _validate_c, "d": _validate_d, "e": _validate_e}


def validate_batch(batch: str) -> tuple[int, list[str], list[str]]:
    """校验单批，返回 (条数, errors, warnings)"""
    try:
        records, path = _load_batch(batch)
    except FileNotFoundError:
        return 0, [f"{batch}: 目录不存在 {DATA_DIR / BATCH_DIRS[batch]}"], []
    except json.JSONDecodeError as exc:
        return 0, [f"{batch}: JSON 解析失败 {exc}"], []
    if not path.name:
        return 0, [], [f"{batch}: 暂无 {batch}_v*.json（beta 采集后放入，模板见 templates/）"]

    errors: list[str] = []
    warnings: list[str] = []
    for rec in records:
        _check_required(rec, batch, errors, path)
        _VALIDATORS[batch](rec, errors, warnings, path)
    return len(records), errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="真值数据校验器（规格 v1）")
    parser.add_argument("--batch", choices=sorted(BATCH_DIRS), help="只校验单批")
    args = parser.parse_args()

    batches = [args.batch] if args.batch else sorted(BATCH_DIRS)
    total_errors = 0
    total_records = 0
    for batch in batches:
        count, errors, warnings = validate_batch(batch)
        total_records += count
        total_errors += len(errors)
        status = "❌" if errors else "✅"
        print(f"[{status}] 批 {batch}: {count} 条" + (f" | {len(warnings)} 警告" if warnings else ""))
        for w in warnings:
            print(f"  ⚠ {w}")
        for e in errors:
            print(f"  ✗ {e}")

    print(f"\n合计: {total_records} 条, {total_errors} 个阻断项")
    if total_errors:
        print("校验未通过，禁止入 manifest")
        return 1
    print("校验通过，可入 manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
