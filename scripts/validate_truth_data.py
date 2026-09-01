#!/usr/bin/env python3
"""真值数据校验器（规格：docs/真值数据规格标准_v1.md · 评测门禁见 docs/RAG评测体系与门禁标准.md）

扫描 research/truth-data/{a,b,c,d,e,f,g,h}/*_v{n}.json，逐批校验：
  - 必填字段 100% / 枚举合法 / 类型正确
  - A：命中类 expected/expected_label 二选一非空；干扰类 expected=[]+expect_empty=true+expected_label=__none__
       （expected 与 run_eval.py 对齐；expect_empty 负样本，每层都要有）
  - B：label 英文码（todo/idea/emotion/quote/mixed）；label_confidence=disputed 不阻断但警告
  - C：self_talk_only 必须 true（隐私铁律）；transcript_source=human 才算真值
  - D：consensus ≥2/3 才入真值；expected_l1.date 格式
  - E：predicted ≠ corrected；类别英文码
  - F：expected_updates 维度/枚举值合法；should_update 对齐置信度 0.7
  - G：verdict 三级 + action 对齐 + layer 三层枚举
  - H：expected_text 非空；image_type 四类枚举；source 两枚举

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

FRAGMENT_LABELS = {"todo", "idea", "emotion", "quote", "mixed"}
QUERY_LAYERS = {"descriptive", "keyword", "typo", "person", "place", "time", "route"}
VERDICTS = {"safe", "controversial", "unsafe"}
GUARD_ACTIONS = {"allow", "review", "block"}
GUARD_LAYERS = {"profile-sensitive", "boundary"}  # preset 层借用 SafetyBench/CValues 公开基准，不采集
OCR_TYPES = {"screenshot", "old_photo", "handwriting", "complex_layout"}
OCR_SOURCES = {"product-team"}  # 公开数据集样本由评测程序直读公开源，不落本目录
BATCH_DIRS = {"a": "a", "b": "b", "c": "c", "d": "d", "e": "e", "f": "f", "g": "g", "h": "h"}

REQUIRED: dict[str, set[str]] = {
    "a": {"query_id", "query", "layer", "expected", "expected_label", "expect_empty",
          "user_id_hashed", "source", "collected_at"},
    "b": {"fragment_id", "text", "label", "label_confidence", "source",
          "user_id_hashed", "collected_at"},
    "c": {"clip_id", "audio_ref", "duration_s", "transcript",
          "transcript_source", "noise_level", "self_talk_only",
          "user_id_hashed", "collected_at"},
    "d": {"set_id", "user_id_hashed", "time_span", "photo_refs",
          "window", "annotator_count", "consensus", "collected_at"},
    "e": {"correction_id", "text", "predicted", "corrected", "context",
          "user_id_hashed", "collected_at"},
    "f": {"profile_case_id", "input_text", "expected_updates",
          "user_id_hashed", "collected_at"},
    "g": {"guardrail_id", "input_text", "verdict", "action", "layer",
          "user_id_hashed", "collected_at"},
    "h": {"ocr_id", "image_ref", "expected_text", "image_type", "source",
          "collected_at"},
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
    if "expected_ids" in rec:
        errors.append(f"{path.name} {rec.get('query_id')}: 字段应为 expected（非 expected_ids，与 harness 对齐）")
    ids = rec.get("expected") or []
    label = rec.get("expected_label")
    expect_empty = rec.get("expect_empty")
    if expect_empty is True:
        # 干扰类（负样本）：期望无结果
        if ids:
            errors.append(f"{path.name} {rec.get('query_id')}: expect_empty=true 时 expected 必须为空 []")
        if label != "__none__":
            errors.append(f"{path.name} {rec.get('query_id')}: expect_empty=true 时 expected_label 必须为 __none__")
        nk = rec.get("negative_kind")
        if nk not in ("real", "synthetic"):
            errors.append(f"{path.name} {rec.get('query_id')}: 干扰类必须填 negative_kind（real/synthetic）")
    else:
        # 命中类（正样本）：expected 或 expected_label 至少其一非空
        if not ids and not label:
            errors.append(f"{path.name} {rec.get('query_id')}: 命中类 expected 与 expected_label 不能同空")
        if label is not None and label != "__none__" and label not in FRAGMENT_LABELS:
            errors.append(f"{path.name} {rec.get('query_id')}: expected_label 非法（类别英文码或 __none__）")
        if "negative_kind" in rec:
            errors.append(f"{path.name} {rec.get('query_id')}: 命中类不应填 negative_kind（仅干扰类）")
    if not isinstance(ids, list):
        errors.append(f"{path.name} {rec.get('query_id')}: expected 必须是数组")


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
    window = rec.get("window")
    if window not in ("short", "long"):
        errors.append(f"{path.name} {rec.get('set_id')}: window 必须 short/long")
    if window == "long":
        # 长期窗口：expected_l3 必填，L1/L2 可为空
        if not rec.get("expected_l3"):
            errors.append(f"{path.name} {rec.get('set_id')}: long 窗口 expected_l3 必填")
        for l3 in rec.get("expected_l3") or []:
            if not l3.get("theme"):
                errors.append(f"{path.name} {rec.get('set_id')}: expected_l3.theme 必填")
            if not isinstance(l3.get("item_ids"), list) or not l3["item_ids"]:
                errors.append(f"{path.name} {rec.get('set_id')}: expected_l3.item_ids 必须为非空数组")
    else:
        # 短期窗口：expected_l1 必填，L2 可选
        if not rec.get("expected_l1"):
            errors.append(f"{path.name} {rec.get('set_id')}: short 窗口 expected_l1 必填")
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
    elif rec.get("context") == "user-manual":
        warnings.append(f"{path.name} {rec.get('correction_id')}: MVP 只收 arbitration（user-manual 待 beta 上线后补）")


def _validate_f(rec: dict, errors: list[str], warnings: list[str], path: Path) -> None:
    updates = rec.get("expected_updates")
    if not isinstance(updates, list):
        errors.append(f"{path.name} {rec.get('profile_case_id')}: expected_updates 必须是数组")
        return
    for up in updates:
        if not isinstance(up, dict):
            errors.append(f"{path.name} {rec.get('profile_case_id')}: expected_updates 元素必须是对象")
            continue
        if not up.get("dimension") or not up.get("enum_value"):
            errors.append(f"{path.name} {rec.get('profile_case_id')}: 维度/枚举值不能为空")
        conf = up.get("confidence")
        if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
            errors.append(f"{path.name} {rec.get('profile_case_id')}: confidence 须 0-1")
        if "should_update" not in up:
            errors.append(f"{path.name} {rec.get('profile_case_id')}: should_update 必填")
        elif isinstance(conf, (int, float)) and conf >= 0.7 and up.get("should_update") is False:
            warnings.append(
                f"{path.name} {rec.get('profile_case_id')}: 置信度≥0.7 却 should_update=false"
                "（对齐 B1 语义，警告）"
            )
        elif isinstance(conf, (int, float)) and conf < 0.7 and up.get("should_update") is True:
            warnings.append(
                f"{path.name} {rec.get('profile_case_id')}: 置信度<0.7 却 should_update=true"
                "（对齐 B1 语义，警告）"
            )
        if up.get("dimension") == "sensitive_topic":
            disposal = up.get("disposal")
            if disposal not in ("allow", "mention", "caution", "review", "forbid"):
                errors.append(
                    f"{path.name} {rec.get('profile_case_id')}: sensitive_topic 必须带 disposal"
                    "（allow/mention/caution/review/forbid）"
                )


def _validate_g(rec: dict, errors: list[str], warnings: list[str], path: Path) -> None:
    if rec.get("verdict") not in VERDICTS:
        errors.append(f"{path.name} {rec.get('guardrail_id')}: verdict 非法 {rec.get('verdict')}")
    if rec.get("action") not in GUARD_ACTIONS:
        errors.append(f"{path.name} {rec.get('guardrail_id')}: action 非法 {rec.get('action')}")
    if rec.get("layer") not in GUARD_LAYERS:
        errors.append(f"{path.name} {rec.get('guardrail_id')}: layer 非法 {rec.get('layer')}")
    verdict, action = rec.get("verdict"), rec.get("action")
    if verdict == "safe" and action not in ("allow",):
        warnings.append(f"{path.name} {rec.get('guardrail_id')}: safe 通常应 allow（警告）")
    if verdict == "unsafe" and action == "allow":
        errors.append(f"{path.name} {rec.get('guardrail_id')}: unsafe 不能 allow（漏杀红线）")


def _validate_h(rec: dict, errors: list[str], warnings: list[str], path: Path) -> None:
    if not rec.get("expected_text"):
        errors.append(f"{path.name} {rec.get('ocr_id')}: expected_text 不能为空（人工转录）")
    if rec.get("image_type") not in OCR_TYPES:
        errors.append(f"{path.name} {rec.get('ocr_id')}: image_type 非法 {rec.get('image_type')}")
    if rec.get("source") not in OCR_SOURCES:
        errors.append(f"{path.name} {rec.get('ocr_id')}: source 非法 {rec.get('source')}")


_VALIDATORS = {"a": _validate_a, "b": _validate_b, "c": _validate_c, "d": _validate_d,
               "e": _validate_e, "f": _validate_f, "g": _validate_g, "h": _validate_h}


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
