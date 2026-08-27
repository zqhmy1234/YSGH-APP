#!/usr/bin/env python3
"""文案库校验器（docs/copy_library · schema/care_copy/template_pool）——Agent C2 交付 v2

校验规则矩阵（v2，2026-08-28）：
  ├─ JSON 可解析    三文件均可 json.load  → 失败=报错退出码 1
  ├─ schema 合规    条目必填字段齐全、类型正确 → 失败=报错+字段定位
  ├─ 键对齐         care_copy scenes ⊆ notify 六键（== 六键）→ 失败=报错
  ├─ 数量区间        care 每场景 variants≥3；pool 总量 30–50 → 失败=报错
  │                   pool 三层分布 echo12-18/ask10-16/companion8-16 → 越界=警告（建议值）
  ├─ 占位符闭合      所有 { 有配对 }（正则扫描）+ 词表内 → 失败=报错
  ├─ 长度/枚举       title≤20 / body≤100 / tone∈{gentle,warm,light} → 失败=报错
  ├─ 敏感词预检      黑名单（前任/初恋等）扫描 body/template → 警告（标注人工审阅）
  ├─ 唯一性          variant.id 全局唯一 → 失败=报错
  └─ 输出            打印校验报告（通过项数/警告/失败）+ 退出码（0=全绿）

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
    "pool_distribution": {"echo": (12, 18), "ask": (10, 16), "companion": (8, 16)},
    "title_max_chars": 20,
    "body_max_chars": 100,
}

VALID_TONES = {"gentle", "warm", "light"}
VALID_POOL_SCENES = {"echo", "ask", "companion"}
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
        report.add_error(
            f"JSON 解析失败: {path.relative_to(ROOT)} @ {exc.lineno}:{exc.colno} {exc.msg}"
        )
    return None


def extract_placeholders(text: str) -> set[str]:
    """提取 {xxx} 占位符集合（不含空 {}）。"""
    return {m for m in PLACEHOLDER_PATTERN.findall(text) if m.strip()}


def brace_check(text: str, where: str, report: Report) -> None:
    """占位符闭合：无空 {} 且 { 与 } 数量相等。"""
    if "{}" in text:
        report.add_error(f"{where}: 出现空占位符 {{}}")
    if text.count("{") != text.count("}"):
        report.add_error(f"{where}: 花括号不平衡（{text!r}）")


def vocab_check(used: set[str], vocabulary: set[str], where: str, report: Report) -> None:
    """词表检查：占位符必须在 x-contract.placeholders.vocabulary 内。"""
    unknown = used - vocabulary
    if unknown:
        report.add_error(
            f"{where}: 使用词表外占位符 {sorted(unknown)}（词表={sorted(vocabulary)}）"
        )


def sensitive_scan(texts: list[str], banned: set[str], where: str, report: Report) -> None:
    """敏感词预检（警告级，标注人工审阅）。"""
    for text in texts:
        hit = [w for w in banned if w and w in text]
        if hit:
            report.add_warning(f"{where}: 命中『禁止主动提及』词 {hit}（请人工复核基调）")


# ---------------------------------------------------------------------------
# schema.json
# ---------------------------------------------------------------------------
def validate_schema_json(schema: object, report: Report) -> None:
    """schema.json 顶层结构 + draft-07 合法性（jsonschema 可用时）。"""
    if not isinstance(schema, dict):
        report.add_error("schema.json 顶层必须是对象")
        return
    for field in ("schema_version", "updated_at", "scenes", "definitions", "x-contract"):
        if field not in schema:
            report.add_error(f"schema.json 缺顶层字段 {field}")
    if schema.get("schema_version") != "1.0":
        report.add_error(f"schema.json schema_version={schema.get('schema_version')!r}，须为 1.0")
    if "definitions" in schema:
        for name in (
            "variant", "care_scene", "care_copy_file", "template_entry", "template_pool_file",
        ):
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


def check_contract_alignment(schema: dict, report: Report) -> None:
    """schema.json 顶层 scenes + x-contract 与 NOTIFY_TRUTH 对齐（C8 契约铁律）。"""
    top_scenes = schema.get("scenes")
    if top_scenes != NOTIFY_TRUTH["scenario_keys"]:
        report.add_error(
            f"schema.json 顶层 scenes={top_scenes!r}，notify.py 真值={NOTIFY_TRUTH['scenario_keys']!r}"
        )

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
        "title_max_chars": NOTIFY_TRUTH["title_max_chars"],
        "body_max_chars": NOTIFY_TRUTH["body_max_chars"],
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
            f"契约对齐失败: x-contract.late_night={late_night!r}，"
            f"notify.py 真值={NOTIFY_TRUTH['late_night']!r}"
        )
    if not contract.get("placeholders") or "vocabulary" not in contract.get("placeholders", {}):
        report.add_error("schema.json x-contract.placeholders 缺 vocabulary 词表")
    report.add_info("契约对齐: 6 键/阈值 0.7/深夜 22-05/回看 3 天 与 notify.py 一致")


# ---------------------------------------------------------------------------
# care_copy.json
# ---------------------------------------------------------------------------
def validate_care_copy(data: object, report: Report, vocabulary: set[str]) -> None:
    """care_copy.json：scenes[] 6 键全覆盖 / 每场景 variants≥3 / 长度/枚举/唯一性/占位符闭合/敏感词。"""
    if not isinstance(data, dict):
        report.add_error("care_copy.json 顶层必须是对象")
        return
    if data.get("schema_version") != "1.0":
        report.add_error(f"care_copy.json schema_version={data.get('schema_version')!r}，须为 1.0")
    if not str(data.get("updated_at", "")).strip():
        report.add_error("care_copy.json 缺 updated_at（YYYY-MM-DD）")

    tone = data.get("tone_policy")
    if not isinstance(tone, dict) or not all(k in tone for k in ("基调", "禁止主动提及", "仅陪伴出口")):
        report.add_error("care_copy.json 缺 tone_policy（须含 基调/禁止主动提及/仅陪伴出口）")
        banned = set(tone.get("禁止主动提及", [])) if isinstance(tone, dict) else set()
    else:
        banned = set(tone.get("禁止主动提及", []))
        if tone.get("仅陪伴出口") is False:
            report.add_warning(
                "care_copy.json 仅陪伴出口=False，与拍板⑤（不提敏感话题仅陪伴）不一致"
            )

    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        report.add_error("care_copy.json 缺 scenes 数组")
        return

    scene_set: set[str] = set()
    all_variant_ids: set[str] = set()
    for entry in scenes:
        if not isinstance(entry, dict):
            report.add_error("care_copy.json scenes[] 条目须为对象")
            continue
        scene = entry.get("scene")
        if not isinstance(scene, str) or scene not in NOTIFY_TRUTH["scenario_keys"]:
            report.add_error(
                f"care_copy 条目 scene={scene!r} 不在 notify 六键 {NOTIFY_TRUTH['scenario_keys']} 内"
            )
            continue
        scene_set.add(scene)
        variants = entry.get("variants")
        if not isinstance(variants, list) or len(variants) < NOTIFY_TRUTH["care_candidates_min"]:
            report.add_error(
                f"场景 {scene}: variants 须 ≥{NOTIFY_TRUTH['care_candidates_min']} 条，"
                f"实际={len(variants) if isinstance(variants, list) else '非数组'}"
            )
            if not isinstance(variants, list):
                continue  # 非数组无法细检，其余仍逐条细检给出完整诊断
        report.add_info(f"场景 {scene}: {len(variants)} 条候选")
        for i, v in enumerate(variants):
            where = f"场景 {scene} variants[{i}]"
            if not isinstance(v, dict):
                report.add_error(f"{where}: 条目须为对象")
                continue
            vid = str(v.get("id", ""))
            if not re.fullmatch(r"[a-z0-9_]+", vid):
                report.add_error(f"{where}: id={vid!r} 须匹配 ^[a-z0-9_]+$")
            if vid in all_variant_ids:
                report.add_error(f"{where}: variant.id 重复 {vid}")
            all_variant_ids.add(vid)

            title = str(v.get("title", ""))
            body = str(v.get("body", ""))
            if not title or not body:
                report.add_error(f"{where}: title/body 须非空字符串")
            if len(title) > NOTIFY_TRUTH["title_max_chars"]:
                report.add_error(
                    f"{where}: title 超 {NOTIFY_TRUTH['title_max_chars']} 字（实际 {len(title)}）: {title}"
                )
            if len(body) > NOTIFY_TRUTH["body_max_chars"]:
                report.add_error(
                    f"{where}: body 超 {NOTIFY_TRUTH['body_max_chars']} 字（实际 {len(body)}）: {body}"
                )
            if v.get("tone") not in VALID_TONES:
                report.add_error(f"{where}: tone={v.get('tone')!r}，须 ∈ {sorted(VALID_TONES)}")

            declared = set()
            ph = v.get("placeholders")
            if ph is not None:
                if not isinstance(ph, list):
                    report.add_error(f"{where}: placeholders 须为数组")
                else:
                    declared = {str(p.get("name", "")) for p in ph if isinstance(p, dict)}
            for text in (title, body):
                brace_check(text, where, report)
                used = extract_placeholders(text)
                vocab_check(used, vocabulary, where, report)
                undeclared = used - declared
                if undeclared:
                    report.add_error(
                        f"{where}: 使用了未声明占位符 {sorted(undeclared)}（须在 placeholders 声明）"
                    )
            sensitive_scan([title, body], banned, where, report)

    if scene_set != set(NOTIFY_TRUTH["scenario_keys"]):
        report.add_error(
            f"care_copy 场景键集合须 == notify 六键: 实际={sorted(scene_set)}，"
            f"应有={NOTIFY_TRUTH['scenario_keys']}"
        )
    report.add_info(f"care_copy 场景键: {sorted(scene_set)}（与 notify.py 对齐）")


# ---------------------------------------------------------------------------
# template_pool.json
# ---------------------------------------------------------------------------
def validate_template_pool(
    data: object, report: Report, vocabulary: set[str], banned: set[str]
) -> None:
    """template_pool.json：总量 30–50 / 三层分布建议 / 必填字段 / variants≥2 / 占位符闭合 / 敏感词。"""
    if not isinstance(data, dict):
        report.add_error("template_pool.json 顶层必须是对象")
        return
    if data.get("schema_version") != "1.0":
        report.add_error(f"template_pool.json schema_version={data.get('schema_version')!r}，须为 1.0")
    if not str(data.get("updated_at", "")).strip():
        report.add_error("template_pool.json 缺 updated_at（YYYY-MM-DD）")

    pool = data.get("pool")
    if not isinstance(pool, list):
        report.add_error("template_pool.json 缺 pool 数组")
        return
    lo, hi = NOTIFY_TRUTH["template_pool_count_min"], NOTIFY_TRUTH["template_pool_count_max"]
    if not (lo <= len(pool) <= hi):
        report.add_error(f"模板骨架池总量须在 [{lo},{hi}]，实际={len(pool)}")
    report.add_info(f"template_pool 总量: {len(pool)}（区间 [{lo},{hi}]）")

    dist: dict[str, int] = {"echo": 0, "ask": 0, "companion": 0}
    seen_ids: set[str] = set()
    for idx, entry in enumerate(pool):
        where = f"pool[{idx}]"
        if not isinstance(entry, dict):
            report.add_error(f"{where}: 条目须为对象")
            continue
        scene = entry.get("scene")
        if scene not in VALID_POOL_SCENES:
            report.add_error(f"{where}: scene={scene!r}，须 ∈ {sorted(VALID_POOL_SCENES)}")
        else:
            dist[scene] += 1
        for field in ("intent", "template", "variants"):
            if field not in entry:
                report.add_error(f"{where}: 缺必填字段 {field}")
        if not str(entry.get("intent", "")).strip():
            report.add_error(f"{where}: intent 须非空字符串")
        if not str(entry.get("template", "")).strip():
            report.add_error(f"{where}: template 须非空字符串")
        variants = entry.get("variants")
        if not isinstance(variants, list) or len(variants) < 2:
            report.add_error(f"{where}: variants 须 ≥2 条")
            variants = []
        for text in [str(entry.get("template", ""))] + [str(x) for x in variants]:
            brace_check(text, where, report)
        used = extract_placeholders(str(entry.get("template", "")))
        for x in variants if isinstance(variants, list) else []:
            used |= extract_placeholders(str(x))
        vocab_check(used, vocabulary, where, report)
        pool_texts = [str(entry.get("template", ""))]
        if isinstance(variants, list):
            pool_texts += [str(x) for x in variants]
        sensitive_scan(pool_texts, banned, where, report)
        eid = str(entry.get("id", ""))
        if eid and (not re.fullmatch(r"[a-z0-9_]+", eid) or eid in seen_ids):
            report.add_error(f"{where}: id={eid!r} 非法或重复")
        seen_ids.add(eid)

    for scene, (dlo, dhi) in NOTIFY_TRUTH["pool_distribution"].items():
        n = dist.get(scene, 0)
        if not (dlo <= n <= dhi):
            report.add_warning(
                f"template_pool 分层分布: {scene}={n}，建议区间 [{dlo},{dhi}] 外（建议值，不阻断）"
            )
    report.add_info(
        f"template_pool 分层分布: echo={dist['echo']} / ask={dist['ask']} / "
        f"companion={dist['companion']}（建议 echo12-18/ask10-16/companion8-16）"
    )


def _banned_from_care(data: object) -> set[str]:
    """从 care_copy.json 读取『禁止主动提及』词表（供 template_pool 敏感词预检复用）。"""
    if isinstance(data, dict):
        tone = data.get("tone_policy")
        if isinstance(tone, dict):
            return set(tone.get("禁止主动提及", []))
    return set()


def main() -> int:
    parser = argparse.ArgumentParser(description="文案库校验器（docs/copy_library）")
    parser.add_argument("--report", metavar="PATH", help="校验报告输出路径（可选）")
    args = parser.parse_args()

    report = Report()
    report.add_info(f"校验目录: {LIB_DIR.relative_to(ROOT)}")

    schema = load_json(SCHEMA_FILE, report)
    care = load_json(CARE_COPY_FILE, report)
    pool = load_json(TEMPLATE_POOL_FILE, report)

    vocabulary: set[str] = set()
    if schema is not None:
        validate_schema_json(schema, report)
        check_contract_alignment(schema, report)
        contract = schema.get("x-contract") if isinstance(schema, dict) else {}
        vocabulary = set((contract.get("placeholders") or {}).get("vocabulary", {}).keys())
    else:
        report.add_warning("schema.json 缺失，占位符词表校验降级为空词表")

    if care is not None:
        validate_care_copy(care, report, vocabulary)

    if pool is not None:
        banned = _banned_from_care(care) if care is not None else set()
        validate_template_pool(pool, report, vocabulary, banned)

    # ---- 渲染报告 ----
    lines: list[str] = ["# 文案库校验报告（validate_copy_library）"]
    lines.append("")
    lines.append(f"- 时间: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}")
    lines.append(
        f"- 校验对象: {SCHEMA_FILE.name} / {CARE_COPY_FILE.name} / {TEMPLATE_POOL_FILE.name}"
    )
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
