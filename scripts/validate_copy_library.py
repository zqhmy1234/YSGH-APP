#!/usr/bin/env python3
"""文案库校验器（docs/copy_library · schema/care_copy/template_pool）——Agent C2 交付

校验项：
  1. JSON 可解析（schema.json / care_copy.json / template_pool.json 三文件）
  2. schema.json：draft-07 合法性（jsonschema 库可用时）+ 契约常量完整性
  3. 契约对齐：schema.json x-contract 与 notify.py 触发常量（C8）一致
     ——6 键 / 阈值 0.7 / 深夜 22-05 / 回看 3 天 / 数量区间 30-50 / 每场景候选 ≥3
  4. care_copy.json：恰好 6 场景键 / 每场景 ≥3 候选 / title+body 非空 / 无占位符直发 /
     基调策略齐备 / 敏感话题未主动提及（警告级）
  5. template_pool.json：数量 ∈[30,50] / 条目必填字段 / id 唯一且小写下划线 /
     占位符闭合（声明==使用）且均在词表内 / 花括号平衡
  6. 输出校验报告（stdout + 可选 --report 路径）

用法：
  python scripts/validate_copy_library.py
  python scripts/validate_copy_library.py --report .cowork-temp/copy_library_report.md
退出码：0=全绿（可提交）；1=有阻断项（warnings 仅提示不阻断）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "docs" / "copy_library"
SCHEMA_FILE = LIB_DIR / "schema.json"
CARE_COPY_FILE = LIB_DIR / "care_copy.json"
TEMPLATE_POOL_FILE = LIB_DIR / "template_pool.json"

# ---------------------------------------------------------------------------
# 契约真值（唯一数据源 = backend/app/services/notify.py，C8 契约；勿与 schema 冲突）
# 若 notify.py 调整触发常量，须同步本表与 schema.json x-contract，三者一致才放行。
# ---------------------------------------------------------------------------
NOTIFY_TRUTH = {
    "scenario_keys": ["sad_ask", "sad_respond", "angry", "late_night", "day2", "day3"],
    "emotion_action_threshold": 0.7,          # EMOTION_ACTION_THRESHOLD：confidence<0.7 不触发
    "late_night": {"start_hour": 22, "end_hour": 5},  # LATE_NIGHT_START_HOUR/END_HOUR
    "care_streak_lookback_days": 3,           # CARE_STREAK_LOOKBACK_DAYS
    "care_candidates_min": 3,
    "template_pool_count_min": 30,
    "template_pool_count_max": 50,
}

# 场景 → 触发语义关键词（适用条件 soft 检查；自然语言描述，仅警告不阻断）
TRIGGER_KEYWORDS: dict[str, list[str]] = {
    "sad_ask": ["未说明", "追问"],
    "sad_respond": ["已说明", "回应"],
    "angry": ["陪伴", "不追问"],
    "late_night": ["深夜", "22", "05", "22:00", "05:00"],
    "day2": ["第 2 天", "第2天", "streak==1", "好些"],
    "day3": ["第 3 天", "第3天", "streak>=2", "陪伴"],
}

PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]*)\}")


class Report:
    """校验报告收集器（errors=阻断 / warnings=提示 / info=计数）"""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_info(self, msg: str) -> None:
        self.info.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_json(path: Path, report: Report) -> object | None:
    """读取并解析 JSON；失败记阻断错误并返回 None。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.add_error(f"文件缺失: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        report.add_error(f"JSON 解析失败: {path.relative_to(ROOT)} @ {exc.lineno}:{exc.colno} {exc.msg}")
    return None


def check_contract_alignment(schema: dict, report: Report) -> None:
    """schema.json x-contract 与 NOTIFY_TRUTH 对齐（C8 契约铁律）。"""
    contract = schema.get("x-contract")
    if not isinstance(contract, dict):
        report.add_error("schema.json 缺 x-contract 契约块（B2 加载器与校验脚本共用常量）")
        return
    checks = {
        "scenario_keys": NOTIFY_TRUTH["scenario_keys"],
        "emotion_action_threshold": NOTIFY_TRUTH["emotion_action_threshold"],
        "care_streak_lookback_days": NOTIFY_TRUTH["care_streak_lookback_days"],
        "care_candidates_min": NOTIFY_TRUTH["care_candidates_min"],
        "template_pool_count_min": NOTIFY_TRUTH["template_pool_count_min"],
        "template_pool_count_max": NOTIFY_TRUTH["template_pool_count_max"],
    }
    for key, truth in checks.items():
        got = contract.get(key)
        if got != truth:
            report.add_error(
                f"契约对齐失败: x-contract.{key}={got!r}，notify.py 真值={truth!r}（需三方一致）"
            )
    late_night = contract.get("late_night")
    if not isinstance(late_night, dict) or (
        late_night.get("start_hour") != NOTIFY_TRUTH["late_night"]["start_hour"]
        or late_night.get("end_hour") != NOTIFY_TRUTH["late_night"]["end_hour"]
    ):
        report.add_error(
            f"契约对齐失败: x-contract.late_night={late_night!r}，notify.py 真值={NOTIFY_TRUTH['late_night']!r}"
        )
    if not contract.get("placeholders") or "vocabulary" not in contract.get("placeholders", {}):
        report.add_error("schema.json x-contract.placeholders 缺 vocabulary 词表")
    report.add_info("契约对齐: 6 键/阈值 0.7/深夜 22-05/回看 3 天 与 notify.py 一致")


def validate_care_copy(data: object, report: Report) -> None:
    """care_copy.json：6 场景键 / 每场景≥3 候选 / 必填字段 / 无占位符 / 基调 / 敏感词警告。"""
    if not isinstance(data, dict):
        report.add_error("care_copy.json 顶层必须是对象")
        return
    if data.get("schema_version") != "1.0.0":
        report.add_error(f"care_copy.json schema_version={data.get('schema_version')!r}，须为 1.0.0")

    tone = data.get("tone_policy")
    if not isinstance(tone, dict) or not all(k in tone for k in ("基调", "禁止主动提及", "仅陪伴出口")):
        report.add_error("care_copy.json 缺 tone_policy（须含 基调/禁止主动提及/仅陪伴出口）")
        banned = set(tone.get("禁止主动提及", [])) if isinstance(tone, dict) else set()
    else:
        banned = set(tone.get("禁止主动提及", []))
        if not isinstance(tone.get("仅陪伴出口"), bool):
            report.add_error("care_copy.json tone_policy.仅陪伴出口 须为布尔")
        if tone.get("仅陪伴出口") is False:
            report.add_warning("care_copy.json 仅陪伴出口=False，与拍板⑤（不提敏感话题仅陪伴）不一致")

    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict):
        report.add_error("care_copy.json 缺 scenarios 对象")
        return

    keys = list(scenarios.keys())
    if keys != NOTIFY_TRUTH["scenario_keys"]:
        report.add_error(
            f"场景键与 notify.py CARE_TEMPLATES 不一致: 实际={keys}，应有={NOTIFY_TRUTH['scenario_keys']}"
        )
    report.add_info(f"care_copy 场景键: {keys}（与 notify.py 对齐）")

    for key in NOTIFY_TRUTH["scenario_keys"]:
        sc = scenarios.get(key)
        if not isinstance(sc, dict):
            report.add_error(f"场景 {key}: 缺失/非对象")
            continue
        for field in ("emotion", "intent", "applicable_condition", "rotation", "candidates"):
            if field not in sc:
                report.add_error(f"场景 {key}: 缺必填字段 {field}")
        rotation = sc.get("rotation")
        if rotation not in ("round_robin", "random", "fixed"):
            report.add_error(f"场景 {key}: rotation={rotation!r}，须为 round_robin/random/fixed")
        candidates = sc.get("candidates")
        cand_len = len(candidates) if isinstance(candidates, list) else "非数组"
        if not isinstance(candidates, list) or len(candidates) < NOTIFY_TRUTH["care_candidates_min"]:
            report.add_error(
                f"场景 {key}: candidates 须 ≥{NOTIFY_TRUTH['care_candidates_min']} 条，实际={cand_len}"
            )
            continue
        for i, cand in enumerate(candidates):
            has_text = (
                isinstance(cand, dict)
                and str(cand.get("title", "")).strip()
                and str(cand.get("body", "")).strip()
            )
            if not has_text:
                report.add_error(f"场景 {key} 候选[{i}]: title/body 须非空字符串")
                continue
            for text in (str(cand["title"]), str(cand["body"])):
                if "{" in text or "}" in text:
                    report.add_error(
                        f"场景 {key} 候选[{i}]: 正文含占位符 {text}——care_copy 为直发原文，"
                        "禁止占位符（见 x-contract.placeholders.rule）"
                    )
                hit = [w for w in banned if w and w in text]
                if hit:
                    report.add_warning(f"场景 {key} 候选[{i}]: 命中『禁止主动提及』词 {hit}（请人工复核基调）")

    # 触发语义 soft 检查
    for key, kws in TRIGGER_KEYWORDS.items():
        sc = scenarios.get(key)
        if isinstance(sc, dict):
            cond = str(sc.get("applicable_condition", ""))
            if cond and not any(k in cond for k in kws):
                report.add_warning(
                    f"场景 {key}: 适用条件未含预期触发关键词 {kws}，请核对与 notify.py 语义一致: {cond}"
                )


def extract_placeholders(text: str) -> set[str]:
    """提取 {xxx} 占位符集合（不含空 {}）。"""
    return {m for m in PLACEHOLDER_PATTERN.findall(text) if m.strip()}


def brace_check(text: str, where: str, report: Report) -> None:
    """花括号平衡检查：左/右数量相等且无空 {}。"""
    if "{}" in text:
        report.add_error(f"{where}: 出现空占位符 {{}}")
    if text.count("{") != text.count("}"):
        report.add_error(f"{where}: 花括号不平衡（{text!r}）")


def validate_template_pool(data: object, report: Report, vocabulary: set[str]) -> None:
    """template_pool.json：数量区间 / 必填字段 / id 唯一 / 占位符闭合与词表。"""
    if not isinstance(data, dict):
        report.add_error("template_pool.json 顶层必须是对象")
        return
    if data.get("schema_version") != "1.0.0":
        report.add_error(f"template_pool.json schema_version={data.get('schema_version')!r}，须为 1.0.0")

    pool = data.get("pool")
    if not isinstance(pool, list):
        report.add_error("template_pool.json 缺 pool 数组")
        return
    lo, hi = NOTIFY_TRUTH["template_pool_count_min"], NOTIFY_TRUTH["template_pool_count_max"]
    if not (lo <= len(pool) <= hi):
        report.add_error(f"模板骨架池数量须在 [{lo},{hi}]，实际={len(pool)}")
    report.add_info(f"template_pool 条数: {len(pool)}（区间 [{lo},{hi}]）")

    seen_ids: set[str] = set()
    for idx, entry in enumerate(pool):
        where = f"pool[{idx}]"
        if not isinstance(entry, dict):
            report.add_error(f"{where}: 条目须为对象")
            continue
        for field in ("id", "domain", "scenario", "intent", "skeleton", "applicable_condition"):
            if field not in entry:
                report.add_error(f"{where}: 缺必填字段 {field}")
        eid = str(entry.get("id", ""))
        if not re.fullmatch(r"[a-z0-9_]+", eid):
            report.add_error(f"{where}: id={eid!r} 须匹配 ^[a-z0-9_]+$")
        if eid in seen_ids:
            report.add_error(f"{where}: id 重复 {eid}")
        seen_ids.add(eid)
        if entry.get("domain") not in ("echo", "followup"):
            report.add_error(f"{where}: domain={entry.get('domain')!r}，须为 echo/followup")

        skeleton = str(entry.get("skeleton", ""))
        variants = entry.get("variants") or []
        if not isinstance(variants, list):
            report.add_error(f"{where}: variants 须为数组")
            variants = []
        texts = [skeleton] + [str(v) for v in variants]
        for t in texts:
            brace_check(t, where, report)

        used = set()
        for t in texts:
            used |= extract_placeholders(t)
        declared = set(entry.get("placeholders") or []) if isinstance(entry.get("placeholders"), list) else set()
        if "placeholders" not in entry:
            report.add_warning(f"{where}: 建议显式声明 placeholders 字段（现按实际使用推导）")
        if used != declared:
            report.add_error(
                f"{where}: 占位符闭合失败——声明={sorted(declared)}，实际使用={sorted(used)}"
            )
        unknown = used - vocabulary
        if unknown:
            report.add_error(f"{where}: 使用词表外占位符 {sorted(unknown)}（词表={sorted(vocabulary)}）")


def validate_schema_json(schema: object, report: Report) -> None:
    """schema.json 基本结构与 draft-07 合法性（jsonschema 可用时）。"""
    if not isinstance(schema, dict):
        report.add_error("schema.json 顶层必须是对象")
        return
    for field in ("title", "version", "definitions", "x-contract"):
        if field not in schema:
            report.add_error(f"schema.json 缺顶层字段 {field}")
    if "definitions" in schema:
        for name in ("candidate", "care_scenario", "care_copy_file", "template_entry", "template_pool_file"):
            if name not in schema["definitions"]:
                report.add_error(f"schema.json definitions 缺 {name}")
    try:
        import jsonschema  # noqa: PLC0415

        jsonschema.Draft7Validator.check_schema(schema)
        report.add_info("schema.json: jsonschema Draft7 校验通过（check_schema）")
    except ImportError:
        report.add_info("jsonschema 库不可用，跳过 draft-07 语法校验（结构校验仍执行）")
    except Exception as exc:  # noqa: BLE001
        report.add_error(f"schema.json draft-07 语法不合法: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="文案库校验器（docs/copy_library）")
    parser.add_argument("--report", metavar="PATH", help="校验报告输出路径（可选）")
    args = parser.parse_args()

    report = Report()
    report.add_info(f"校验目录: {LIB_DIR.relative_to(ROOT)}")

    schema = load_json(SCHEMA_FILE, report)
    care = load_json(CARE_COPY_FILE, report)
    pool = load_json(TEMPLATE_POOL_FILE, report)

    if schema is not None:
        validate_schema_json(schema, report)
        check_contract_alignment(schema, report)

    if care is not None:
        validate_care_copy(care, report)

    if pool is not None and schema is not None:
        contract = schema.get("x-contract") if isinstance(schema, dict) else {}
        vocab = set((contract.get("placeholders") or {}).get("vocabulary", {}).keys())
        validate_template_pool(pool, report, vocab)
    elif pool is not None:
        report.add_warning("schema.json 缺失，无法做占位符词表校验（仅做结构/数量检查）")
        validate_template_pool(pool, report, vocabulary=set())

    # ---- 渲染报告 ----
    lines: list[str] = ["# 文案库校验报告（validate_copy_library）"]
    lines.append("")
    lines.append(f"- 时间: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}")
    lines.append(f"- 校验对象: {SCHEMA_FILE.name} / {CARE_COPY_FILE.name} / {TEMPLATE_POOL_FILE.name}")
    lines.append(f"- 结果: {'✅ 全绿（PASSED）' if report.ok else '❌ 存在阻断项（BLOCKED）'}")
    lines.append(f"- 阻断 errors: {len(report.errors)} / 警告 warnings: {len(report.warnings)}")
    lines.append("")
    lines.append("## 计数")
    for info in report.info:
        lines.append(f"- {info}")
    lines.append("")
    if report.warnings:
        lines.append("## 警告（不阻断，建议人工复核）")
        for w in report.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")
    if report.errors:
        lines.append("## 阻断项（须修复至全绿）")
        for e in report.errors:
            lines.append(f"- 🔴 {e}")
        lines.append("")

    text = "\n".join(lines)
    print(text)
    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"[report] 已写入 {out}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
