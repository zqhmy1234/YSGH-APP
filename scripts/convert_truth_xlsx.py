#!/usr/bin/env python3
"""产品部 xlsx 标注表 → 真值 JSON（规格：docs/真值数据规格标准_v1.md）

【为什么需要这个脚本】
规格定义了真值 JSON 的字段契约（`research/truth-data/templates/` + `scripts/validate_truth_data.py`），
但 **xlsx → JSON 这一步在规格里是空缺的** —— 没规定执行者，仓库里也没有脚本
（全工作区 grep `openpyxl`/`load_workbook` 零命中）。本脚本补上这一环。

【设计】
  · 读层：**按表头关键字定位列**。各 sheet 的列索引不一致（A 命中类 8 列含 ref_batch、
    A 干扰类 7 列含 null_reason、H 有 note_type 无 user_id_hashed），按列位读必错。
  · 变换层：每批一个纯函数，无 IO，便于直接喂合成行做单测。
  · 宽容模式：能映射的字段全落盘；不能映射的条目**逐条跳过**并记入阻断清单
    （`_reports/blockers.{json,md}`，含文件/sheet/行号/记录 id/原因）。
  · 脱敏：`user_id_hashed = sha256(salt + 归一化代号)`。salt 必须显式提供 ——
    否则跨批哈希不稳定，同一用户在 B 批和 C 批会算出两个 id。

【每个 sheet 的固定版式】所有 sheet 都是四行头，数据从第 5 行起：
    r1 标题 ｜ r2 填表规则 ｜ r3 表头 ｜ r4 示例/说明行（**必须跳过**）
    ⚠️ r4 是陷阱：A 命中类说明行的 `query_id` 也叫 `q-0001`，不跳会产生重复记录。

【用法】
    export TRUTH_HASH_SALT='<本地随机串>'          # 或用 --init-salt 生成 .salt 文件
    python scripts/convert_truth_xlsx.py                        # 全批次
    python scripts/convert_truth_xlsx.py --batch b              # 只转单批
    python scripts/convert_truth_xlsx.py --init-salt            # 生成 research/truth-data/.salt
    python scripts/convert_truth_xlsx.py --strict-layer         # A 批 layer 不做推断（只留 time/person）
    python scripts/convert_truth_xlsx.py --dry-run              # 只解析与报告，不写盘

退出码：0 = 完成（可能含跳过项，看清单）；1 = 有硬失败；2 = 环境错误（缺 salt）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets as _secrets
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:  # 既支持直跑 python scripts/x.py，也支持被按包导入（backend/tests 会 import）
    from gate_io import force_utf8  # noqa: E402
except ModuleNotFoundError:  # 仅「按包导入」路径会走到这里（sys.path 里没有 scripts/）
    from scripts.gate_io import force_utf8  # noqa: E402

try:  # 复用仓库既有的密钥模式（scripts/secret_patterns.py 是唯一来源）
    from secret_patterns import SECRET_PATTERNS  # noqa: E402
except ModuleNotFoundError:
    from scripts.secret_patterns import SECRET_PATTERNS  # noqa: E402

force_utf8()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "truth-data"
# 产品部 xlsx 落在 <工作区>/真值数据/标注表/（仓库外，永不进 git）
# 2026-10-09：原先直接放在工作区根，太乱，整批挪进了 真值数据/ 目录。
# 目录约定见 <工作区>/真值数据/README.md；换了位置用 --xlsx-dir 指过去即可。
XLSX_DIR_DEFAULT = ROOT.parent / "真值数据" / "标注表"
SALT_FILE = DATA_DIR / ".salt"

# ---------------------------------------------------------------- 常量与枚举

# 2026-10-23：规格 2026-10-15 已扩到 7 类（+question/+fact），代码对齐。
FRAGMENT_LABELS = ("todo", "idea", "emotion", "quote", "mixed", "question", "fact")
# 规格 layer 枚举（docs/真值数据规格标准_v1.md §A）
QUERY_LAYERS = ("descriptive", "keyword", "typo", "person", "place", "time", "route")

# A 批 excel 的 query_type → 规格 layer。excel 用的是一套**不同的词**
# （time/topic/person/emotion/mixed），其中 topic、emotion 在规格里没有同名值，
# 这里是**推断**，需产品部复核（每条都会记 LAYER_INFERRED 告警）。
A_LAYER_MAP = {
    "time": "time",
    "person": "person",
    "place": "place",
    "topic": "keyword",
    "emotion": "descriptive",
    "mixed": "route",
    "keyword": "keyword",
    "descriptive": "descriptive",
    "typo": "typo",
    "route": "route",
}
# --strict-layer 时只保留**同名词**，其余跳过（不替产品部做判断）
A_LAYER_STRICT = {"time": "time", "person": "person", "place": "place"}

# A 干扰类 null_reason → negative_kind（规格只允许 real / synthetic）
NEGATIVE_KIND_MAP = {
    "not_stored": "real",
    "wrong_time": "real",
    "never_happened": "synthetic",
}

G_LAYER_MAP = {"sensitive": "profile-sensitive", "borderline_case": "boundary"}
G_VERDICT_MAP = {"safe": "safe", "borderline": "controversial", "controversial": "controversial", "unsafe": "unsafe"}
G_ACTION_MAP = {"pass": "allow", "allow": "allow", "review": "review", "block": "block"}

B_SOURCES = ("beta-user", "annotation")
C_NOISE_LEVELS = ("clean", "noisy", "bgm")

# 这些字段的值属敏感原文（真实聊天/转录/查询原话），**绝不进阻断清单的 detail**
SENSITIVE_FIELDS = frozenset({"text", "transcript", "query", "input_text", "expected_text"})

# ---------------------------------------------------------------- 数据类


@dataclass(frozen=True)
class Source:
    """一个 excel sheet 源。variant 决定用哪张 FIELD_MAP 和哪个转换函数。"""

    variant: str
    batch: str
    xlsx: str
    sheet: str
    anchor: str  # 表头锚点关键字（用于定位表头行）


SOURCES: tuple[Source, ...] = (
    Source("a_hit", "a", "A批_搜索查询标注表.xlsx", "A1批_命中类查询", "query_id"),
    Source("a_distract", "a", "A批_搜索查询标注表.xlsx", "A2批_干扰类查询", "query_id"),
    Source("b", "b", "B批_文字碎片标注表_v2.xlsx", "B批_文字碎片标注", "fragment_id"),
    Source("c", "c", "C批_语音片段标注表.xlsx", "C批_语音片段标注", "audio_id"),
    Source("d1", "d", "D批_照片事件标注表.xlsx", "D1批_连续照片事件分组", "photo_id"),
    Source("d2", "d", "D批_照片事件标注表.xlsx", "D2批_主题事件标注", "photo_id"),
    Source("e", "e", "EFG批_标注表.xlsx", "E批_分类纠错样本", "error_id"),
    Source("f", "f", "EFG批_标注表.xlsx", "F批_用户画像标注（待技术部）", "profile_case_id"),
    Source("g", "g", "EFG批_标注表.xlsx", "G批_安全护栏测试", "sample_id"),
    Source("h", "h", "H批_手写笔记OCR标注表.xlsx", "H批_手写笔记OCR", "ocr_id"),
)

# canonical 字段名 → 表头锚点。匹配规则见 find_column()：先整行精确匹配，
# 再退化为 startswith 前缀匹配（处理 `ground_truth（逐字）`、`transcript（逐字）` 这类）。
FIELD_MAPS: dict[str, dict[str, str]] = {
    "a_hit": {
        "query_id": "query_id",
        "query": "query_text",
        "query_type": "query_type",
        "expected_ids": "expected_ids",
        "ref_batch": "ref_batch",
        "user": "user_id_hashed",
        "collected_at": "collected_at",
        "note": "note",
    },
    "a_distract": {
        "query_id": "query_id",
        "query": "query_text",
        "query_type": "query_type",
        "null_reason": "null_reason",
        "user": "user_id_hashed",
        "collected_at": "collected_at",
        "note": "note",
    },
    "b": {
        "fragment_id": "fragment_id",
        "text": "text",
        "label": "label",
        "label_confidence": "label_confidence",
        "source": "source",
        "user": "user_id_hashed",
        "collected_at": "collected_at",
        "note": "note",
    },
    "c": {
        "clip_id": "audio_id",
        "file_name": "file_name",
        "transcript": "transcript",
        # 2026-10-27：可选列。AI/机器转写必须标 asr，不能冒充人工；缺列 → human（兼容旧表）
        "transcript_source": "transcript_source",
        "emotion": "emotion_label",
        "noise_level": "env",
        "duration_s": "duration_s",
        "user": "user_id_hashed",
        "collected_at": "collected_at",
        "note": "note",
    },
    "d1": {
        "photo_id": "photo_id",
        "timestamp": "timestamp",
        "location": "location",
        "event_id": "final_event_id",
        "event_label": "event_label",
        "note": "note",
    },
    "d2": {
        "photo_id": "photo_id",
        "timestamp": "timestamp",
        "location": "location",
        "event_id": "theme_event_id",
        "event_label": "theme_label",
        "note": "note",
    },
    "e": {
        "correction_id": "error_id",
        "ref_fragment_id": "ref_fragment_id",
        "text": "text",
        "predicted": "wrong_label",
        "corrected": "correct_label",
        "reason": "reason",
        "user": "user_id_hashed",
        "collected_at": "found_at",
        "note": "note",
    },
    "f": {"profile_case_id": "profile_case_id"},
    "g": {
        "guardrail_id": "sample_id",
        "input_text": "text",
        "layer": "category",
        "verdict": "safety_label",
        "action": "action",
        "reason": "reason",
        "user": "user_id_hashed",
        "collected_at": "collected_at",
        "note": "note",
    },
    "h": {
        "ocr_id": "ocr_id",
        "file_name": "file_name",
        "note_type": "note_type",
        "expected_text": "ground_truth",
        "handwriting_quality": "handwriting_quality",
        "source": "source",
        "collected_at": "collected_at",
        "note": "note",
    },
}


# 每批**必需**的列（canonical 名）。缺失 = 硬失败（该批整体读不动）。
# 不在此列的字段缺失只记 warn，值置空 —— 例：G 批 excel **根本没有 user_id_hashed 列**
# （规格却要求必填），F 批 sheet 只有说明文字没有表头。
REQUIRED_COLUMNS: dict[str, set[str]] = {
    "a_hit": {"query_id", "query", "query_type", "expected_ids", "collected_at"},
    "a_distract": {"query_id", "query", "query_type", "null_reason", "collected_at"},
    "b": {"fragment_id", "text", "label", "label_confidence", "source", "collected_at"},
    "c": {"clip_id", "transcript", "duration_s", "collected_at"},
    "d1": set(),
    "d2": set(),
    "e": {"correction_id", "text", "predicted", "corrected", "collected_at"},
    "f": set(),
    "g": {"guardrail_id", "input_text", "layer", "verdict", "action", "collected_at"},
    "h": {"ocr_id", "expected_text", "collected_at"},
}

# G 批缺 user_id_hashed 列时的兜底代号：该批文本是**产品部构造的测试语句**，
# 不绑定真实用户，故统一映射到同一个伪名（规格原文：「敏感文本不带真实用户」）。
_G_ANON_USER = "annotation"


@dataclass(frozen=True)
class RawRow:
    """一行原始数据。row_no 是 1-based 物理行号，用于把阻断项定位回 excel。"""

    row_no: int
    values: dict[str, Any]


@dataclass
class SheetRead:
    """一个 sheet 的读取结果。hard_errors 为空才算读成功。"""

    rows: list[RawRow] = field(default_factory=list)
    hard_errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # 非致命（如可选列缺失）


@dataclass
class Blocker:
    batch: str
    source_file: str
    sheet: str
    row: int
    record_id: str | None
    field_name: str
    reason: str  # 机器码
    detail: str
    severity: str  # "skip"（丢该条）| "warn"（保留该条）

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "source_file": self.source_file,
            "sheet": self.sheet,
            "row": self.row,
            "record_id": self.record_id,
            "field": self.field_name,
            "reason": self.reason,
            "detail": self.detail,
            "severity": self.severity,
        }


@dataclass
class BatchResult:
    batch: str
    records: list[dict] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    rows_read: int = 0
    converted: bool = True  # False → 只出骨架，**绝不写** {batch}/{batch}_vN.json
    skeleton: dict | None = None


@dataclass(frozen=True)
class Ctx:
    """转换上下文：salt 与已知 id 空间（A 批引用校验用）。"""

    salt: str
    known_ids: dict[str, set[str]]  # batch_text("b"/"c"/"d") -> 该批全部采集 id
    strict_layer: bool = False
    xlsx_dir: Path = XLSX_DIR_DEFAULT
    data_dir: Path = DATA_DIR


# ---------------------------------------------------------------- 小工具


def _s(value: Any) -> str:
    """单元格 → 去空白字符串（None/NaN 一律空串）"""
    if value is None:
        return ""
    return str(value).strip()


def _code(value: Any) -> str:
    """单元格 → 英文码。剥掉括号注释与空白，如 'time（时间描述）' → 'time'"""
    text = _s(value)
    for sep in ("（", "(", "\n", "\t", " "):
        if sep in text:
            text = text.split(sep, 1)[0]
    return text.strip().strip("/")


def _iso(value: Any) -> str | None:
    """单元格 → ISO8601 带时区。支持 datetime 与 'YYYY-MM-DD[ HH:MM[:SS]]' 字符串。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = _s(value)
        if not text:
            return None
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?", text)
        if not m:
            return None
        dt = datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0),
        )
    return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")


_STRIP_CHARS = "‘’'\"“”`´ 　\t\n\r"


def normalize_user(raw: Any) -> str:
    """用户代号归一：全角→半角 + 去首尾引号/空白。

    存在的理由：B 批 excel 里同一用户写成 `用户A‘`（U+2018）与 `用户A’`（U+2019）
    两种形态（不同人填的），不归一会让同一用户在跨批哈希出两个 id。
    注意 U+2018/U+2019 不在 NFKC 的映射范围内，必须显式剥离。
    """
    text = unicodedata.normalize("NFKC", _s(raw))
    return text.strip(_STRIP_CHARS)


def hash_user_id(raw: Any, salt: str) -> str:
    """代号 → `sha256:<hex>`。同一 salt + 同一归一化代号 ⇒ 同一哈希（跨批一致）。"""
    norm = normalize_user(raw)
    if not norm:
        return ""
    return "sha256:" + hashlib.sha256(f"{salt}:{norm}".encode()).hexdigest()


def _scan_secrets(text: str) -> list[str]:
    """复用仓库既有密钥模式扫内容，返回命中的模式（**不回显命中内容**）。"""
    found: list[str] = []
    for pattern in SECRET_PATTERNS:
        try:
            if re.search(pattern, text):
                found.append(pattern)
        except re.error:
            continue
    return found


def _safe_value(value: Any, field_name: str) -> str:
    """阻断清单里的值回显：敏感字段或长文本一律脱敏，只留长度。"""
    text = _s(value)
    if field_name in SENSITIVE_FIELDS or len(text) > 60:
        return f"<redacted {len(text)} chars>"
    return repr(text)


# ---------------------------------------------------------------- 读层


def resolve_xlsx(xlsx_dir: Path, name: str) -> Path:
    """解析逻辑表名到实际文件：同名 + 带日期后缀（如 `-20261026`）取最新。

    产品部更新表时不覆盖原文件，而是另存为 `<原名>-YYYYMMDD.xlsx`。
    取 mtime 最新的一份；`~$` 开头的 Excel 临时锁文件排除。
    """
    exact = xlsx_dir / name
    stem, suffix = exact.stem, exact.suffix
    candidates = [
        p for p in xlsx_dir.glob(f"{stem}*{suffix}")
        if not p.name.startswith("~$")
    ]
    if exact.exists() and exact not in candidates:
        candidates.append(exact)
    if not candidates:
        return exact
    return max(candidates, key=lambda p: (p.stat().st_mtime, p.name))


def _media_key(name: str) -> tuple[str, str]:
    """把文件名压成可比较的键 `(主干, 扩展名)`。

    主干去掉分隔符与数字前导零：`d001` 与 `d-0001` 同键、`a-0006` 与 `a0006` 同键。
    用来容忍产品部手写文件名的编号差异（表里写 `d001.mp3`，盘上是 `d-0001.mp3`）。
    """
    p = Path(name)
    stem = re.sub(r"[\s_\-]", "", p.stem).lower()
    stem = re.sub(r"\d+", lambda m: str(int(m.group(0))), stem)
    return stem, p.suffix.lower()


def resolve_media(media_dir: Path, raw_name: str) -> Path | None:
    """在 media_dir 里找出表里 `file_name` 对应的实际文件，找不到返回 None。

    三级匹配：① 文件名精确；② 主干+扩展名同键；③ 只主干同键。
    `a-0006.m4a`（表里）与 `a-0006.mp3`（盘上其实也是 AAC/M4A 容器）走 ③ 命中。
    同键多候选取 mtime 最新，保证同名不同版本时取更新的一份。
    """
    if not raw_name or raw_name == "/" or not media_dir.is_dir():
        return None
    exact = media_dir / Path(raw_name).name
    if exact.is_file():
        return exact
    key = _media_key(raw_name)
    files = [p for p in media_dir.iterdir() if p.is_file()]
    for pick in (lambda p: _media_key(p.name) == key,
                 lambda p: _media_key(p.name)[0] == key[0]):
        hits = [p for p in files if pick(p)]
        if hits:
            return max(hits, key=lambda p: (p.stat().st_mtime, p.name))
    return None


# 说话人标记：行首或标点后的「A：」「乙:」这类。只认显式标记——
# 这是作废级判据（见 convert_c），宁可漏判也不能把自述误杀。
_SPEAKER_LABEL = re.compile(r"(?:^|[\s，。？！、；])([A-Da-d甲乙丙丁])\s*[:：]")


def speaker_labels(text: str) -> set[str]:
    """返回转写里出现过的说话人标记集合（如 {'A', 'B'}）。"""
    return {m.group(1) for m in _SPEAKER_LABEL.finditer(text)}


def find_header_row(ws, anchor: str, scan: int = 6) -> int | None:
    """在 sheet 前 scan 行里找含 anchor 的表头行（1-based 行号）。"""
    for row_no in range(1, min(scan, ws.max_row) + 1):
        for col in range(1, ws.max_column + 1):
            if anchor in _s(ws.cell(row=row_no, column=col).value):
                return row_no
    return None


def find_column(ws, header_row: int, anchor: str) -> int | None:
    """按表头锚点定位列。

    先整行**精确**匹配（每行按换行拆开逐个比），再退化为 startswith 前缀匹配。
    精确优先是必须的：`label` 会是 `label_confidence` 的前缀，反过来则不会。
    """
    rows = [_s(ws.cell(row=header_row, column=c).value) for c in range(1, ws.max_column + 1)]
    for idx, text in enumerate(rows, start=1):
        if any(line.strip() == anchor for line in text.split("\n")):
            return idx
    hits = [
        idx for idx, text in enumerate(rows, start=1)
        if any(line.strip().startswith(anchor) for line in text.split("\n"))
    ]
    if len(hits) == 1:
        return hits[0]
    return None


def read_sheet_rows(xlsx: Path, source: Source) -> SheetRead:
    """读一个 sheet 的数据行。

    数据从表头行 + 2 起（跳掉表头下那行示例/说明 —— 见模块 docstring）。
    列缺失按 REQUIRED_COLUMNS 区分：必需列缺 = 硬失败，可选列缺 = 记 note 值置空。
    """
    read = SheetRead()
    try:
        import openpyxl  # 延迟导入：未装依赖时只有本脚本报错，不影响其他脚本
    except ModuleNotFoundError:
        read.hard_errors.append("缺少 openpyxl 依赖（pip install -r backend/requirements.txt）")
        return read

    try:
        # ⚠️ **不要用 read_only=True**：该模式下 `ws.cell(row, col)` 不是随机访问
        # （返回的是流式游标处的值，所有列会拿到同一个值）。产品部表格都很小
        # （最大 68 行），全量载入无压力。
        wb = openpyxl.load_workbook(xlsx, data_only=True)
    except Exception as exc:  # noqa: BLE001 —— 损坏/占用/格式错都归为硬失败
        read.hard_errors.append(f"无法打开 {xlsx.name}: {exc}")
        return read

    if source.sheet not in wb.sheetnames:
        read.hard_errors.append(f"{xlsx.name} 里没有 sheet {source.sheet!r}（实际: {wb.sheetnames}）")
        return read

    required = REQUIRED_COLUMNS[source.variant]
    ws = wb[source.sheet]
    header_row = find_header_row(ws, source.anchor)
    if header_row is None:
        if required:
            read.hard_errors.append(f"{xlsx.name}!{source.sheet}: 找不到含 {source.anchor!r} 的表头行")
        else:
            read.notes.append(f"{source.sheet}: 无表头行（该 sheet 无数据，符合预期）")
        wb.close()
        return read

    field_map = FIELD_MAPS[source.variant]
    col_of: dict[str, int] = {}
    for canonical, anchor in field_map.items():
        col = find_column(ws, header_row, anchor)
        if col is None:
            if canonical in required:
                read.hard_errors.append(f"{source.sheet}: 缺必需列 {anchor!r}（字段 {canonical}）")
            else:
                read.notes.append(f"{source.sheet}: 缺可选列 {anchor!r}（字段 {canonical}）→ 置空")
        else:
            col_of[canonical] = col
    if read.hard_errors:
        wb.close()
        return read

    for row_no in range(header_row + 2, ws.max_row + 1):
        values = {
            canonical: (ws.cell(row=row_no, column=col_of[canonical]).value
                        if canonical in col_of else None)
            for canonical in field_map
        }
        if not any(_s(v) for v in values.values()):
            continue  # 全空行
        read.rows.append(RawRow(row_no=row_no, values=values))
    wb.close()
    return read


def _notes_to_blockers(source: Source, notes: list[str]) -> list[Blocker]:
    return [
        _base_blocker(source, RawRow(0, {}), None, "-", "COLUMN_OPTIONAL_MISSING", note, "warn")
        for note in notes
    ]


def _handle_read(read: SheetRead, source: Source, result: BatchResult) -> bool:
    """把读取结果记进 result。返回 False = 硬失败，调用方应中止本批。"""
    result.blockers.extend(_notes_to_blockers(source, read.notes))
    if read.hard_errors:
        result.blockers.extend(
            _base_blocker(source, RawRow(0, {}), None, "-", "HARD_FAIL", e, "hard")
            for e in read.hard_errors
        )
        return False
    return True


# ---------------------------------------------------------------- 变换层：各批


def _base_blocker(source: Source, row: RawRow, record_id: str | None,
                  field_name: str, reason: str, detail: str, severity: str) -> Blocker:
    return Blocker(
        batch=source.batch,
        source_file=source.xlsx,
        sheet=source.sheet,
        row=row.row_no,
        record_id=record_id,
        field_name=field_name,
        reason=reason,
        detail=detail,
        severity=severity,
    )


def _source_for_user(raw_user: Any) -> str:
    """产品部自造的条目在 user 列填 'annotation'（见 A2 批表头说明）。"""
    return "annotation" if normalize_user(raw_user).lower() in ("annotation", "产品部", "产品部构造") else "beta-user"


def convert_a(sources: list[Source], ctx: Ctx) -> BatchResult:
    """A 批：两 sheet 合并为一个数组（命中类 + 干扰类）。"""
    result = BatchResult(batch="a")
    seen: dict[str, int] = {}
    for source in sources:
        read = read_sheet_rows(resolve_xlsx(ctx.xlsx_dir, source.xlsx), source)
        _handle_read(read, source, result)
        rows = read.rows
        if not read.hard_errors:
            result.rows_read += len(rows)
        is_hit = source.variant == "a_hit"
        for row in rows:
            rec = _convert_a_row(row, source, ctx, result, is_hit)
            if rec is None:
                continue
            qid = rec["query_id"]
            if qid in seen:
                result.blockers.append(_base_blocker(
                    source, row, qid, "query_id", "DUPLICATE_ID",
                    f"{qid} 前面已出现过（第 {seen[qid]} 行），本条跳过", "skip",
                ))
                continue
            seen[qid] = row.row_no
            result.records.append(rec)
    return result


def _convert_a_row(row: RawRow, source: Source, ctx: Ctx,
                   result: BatchResult, is_hit: bool) -> dict | None:
    v = row.values
    qid = _s(v["query_id"])
    query = _s(v["query"])
    if not qid or not query:
        result.blockers.append(_base_blocker(
            source, row, qid or None, "query_id/query", "MISSING_REQUIRED",
            "query_id 或 query 为空", "skip",
        ))
        return None

    raw_type = _code(v["query_type"])
    layer_map = A_LAYER_STRICT if ctx.strict_layer else A_LAYER_MAP
    layer = layer_map.get(raw_type)
    if layer is None:
        result.blockers.append(_base_blocker(
            source, row, qid, "layer", "ENUM_INVALID",
            f"query_type={_safe_value(raw_type, 'query_type')} 在 layer 枚举里没有对应值"
            f"（规格 7 层: {'/'.join(QUERY_LAYERS)}）", "skip",
        ))
        return None
    if raw_type != layer:
        result.blockers.append(_base_blocker(
            source, row, qid, "layer", "LAYER_INFERRED",
            f"query_type={_safe_value(raw_type, 'query_type')} → layer={layer}（推断值，需产品部复核）", "warn",
        ))

    rec: dict[str, Any] = {
        "query_id": qid,
        "query": query,
        "layer": layer,
        "expected": [],
        "expected_label": None,
        "expect_empty": not is_hit,
        "user_id_hashed": hash_user_id(v["user"], ctx.salt),
        "source": _source_for_user(v["user"]),
        "collected_at": _iso(v["collected_at"]),
    }

    note_parts = []
    if is_hit:
        raw_ids = _s(v["expected_ids"])
        ref_batch = _s(v["ref_batch"])
        if ref_batch:
            note_parts.append(f"来源批次：{ref_batch}")
            # 2026-10-27：ref_batch 进记录。B/C 两批存在同名 id（a-0001…a-0009 等），
            # 只有带上批次才能消歧；build_truth_corpus 用 (batch, id) 生成 uuid5。
            rec["ref_batch"] = ref_batch
        rec["expected"] = [p.strip() for p in re.split(r"[,，、\s]+", raw_ids) if p.strip()]
        if not rec["expected"]:
            result.blockers.append(_base_blocker(
                source, row, qid, "expected", "MISSING_REQUIRED",
                "命中类 expected 为空，且无 expected_label 可填 → 无法满足校验器的「二者不能同空」", "skip",
            ))
            return None
        # ref_batch（如 "B批, C批"）→ known_ids 的批键（"b"/"c"/"d"）。
        # 有批次声明时只在声明范围内查重，避免 B/C 同名 id 被误报 REF_AMBIGUOUS。
        declared = {b for b in ("b", "c", "d") if b.upper() in ref_batch.upper()} if ref_batch else set()
        for ref in rec["expected"]:
            owners = [b for b, ids in ctx.known_ids.items()
                      if (not declared or b in declared) and ref in ids]
            if not owners:
                result.blockers.append(_base_blocker(
                    source, row, qid, "expected", "REF_DANGLING",
                    f"引用 {_safe_value(ref, 'expected')} 在 B/C/D 批里找不到对应采集 id"
                    "（B 批 id 无 f- 前缀，D1 批无 IMG_*_masked）", "warn",
                ))
            elif len(owners) > 1:
                result.blockers.append(_base_blocker(
                    source, row, qid, "expected", "REF_AMBIGUOUS",
                    f"引用 {_safe_value(ref, 'expected')} 在 {'/'.join(owners)} 批**同时存在**，指向不明", "warn",
                ))
        if any("在 B/C/D 批里找不到" in b.detail or "同时存在" in b.detail
               for b in result.blockers if b.row == row.row_no):
            rec["expected_unresolved"] = True
    else:
        raw_reason = _code(v["null_reason"])
        kind = NEGATIVE_KIND_MAP.get(raw_reason)
        if kind is None:
            result.blockers.append(_base_blocker(
                source, row, qid, "negative_kind", "ENUM_INVALID",
                f"null_reason={_safe_value(raw_reason, 'null_reason')} 无法映射到 real/synthetic", "skip",
            ))
            return None
        rec["expected_label"] = "__none__"
        rec["negative_kind"] = kind
        if kind == "synthetic":
            result.blockers.append(_base_blocker(
                source, row, qid, "negative_kind", "NEGATIVE_KIND_INFERRED",
                f"null_reason={raw_reason} → synthetic（产品部构造，规格要求 ≤30%）", "warn",
            ))

    extra_note = _s(v.get("note"))
    if extra_note:
        note_parts.append(extra_note)
    rec["note"] = "；".join(note_parts)

    if rec["collected_at"] is None:
        result.blockers.append(_base_blocker(
            source, row, qid, "collected_at", "DATE_UNPARSEABLE",
            f"collected_at 无法解析: {_safe_value(v['collected_at'], 'collected_at')}", "skip",
        ))
        return None

    for pattern in _scan_secrets(query):
        result.blockers.append(_base_blocker(
            source, row, qid, "query", "SENSITIVE_CONTENT",
            f"query 命中密钥模式 {pattern!r}（内容已脱敏，建议产品部清除源表）", "warn",
        ))
    return rec


def convert_b(sources: list[Source], ctx: Ctx) -> BatchResult:
    result = BatchResult(batch="b")
    source = sources[0]
    xlsx = resolve_xlsx(ctx.xlsx_dir, source.xlsx)
    # 阻断清单要指回**真正读的那份**（产品部另存为 `...-20261026.xlsx` 时，
    # 逻辑名会指向一个没被读的旧文件），所以把实际文件名替换进 source。
    source = replace(source, xlsx=xlsx.name)
    read = read_sheet_rows(xlsx, source)
    if not _handle_read(read, source, result):
        return result
    rows = read.rows
    result.rows_read = len(rows)
    nonconformant: list[str] = []
    for row in rows:
        v = row.values
        fid = _s(v["fragment_id"])
        text = _s(v["text"])
        if not fid or not text:
            result.blockers.append(_base_blocker(
                source, row, fid or None, "fragment_id/text", "MISSING_REQUIRED", "id 或 text 为空", "skip",
            ))
            continue
        label = _code(v["label"])
        if label not in FRAGMENT_LABELS:
            result.blockers.append(_base_blocker(
                source, row, fid, "label", "ENUM_INVALID",
                f"label={_safe_value(label, 'label')} 不在 7 类枚举内"
                f"（{'/'.join(FRAGMENT_LABELS)}）", "skip",
            ))
            continue
        conf = _code(v["label_confidence"])
        if conf not in ("confirmed", "ai-assisted", "disputed"):
            result.blockers.append(_base_blocker(
                source, row, fid, "label_confidence", "ENUM_INVALID",
                f"label_confidence={_safe_value(conf, 'label_confidence')} 非法", "skip",
            ))
            continue
        src = _code(v["source"])
        if src not in B_SOURCES:
            result.blockers.append(_base_blocker(
                source, row, fid, "source", "ENUM_INVALID",
                f"source={_safe_value(src, 'source')} 非法 → 兜底 annotation", "warn",
            ))
            src = "annotation"
        collected = _iso(v["collected_at"])
        if collected is None:
            result.blockers.append(_base_blocker(
                source, row, fid, "collected_at", "DATE_UNPARSEABLE", "collected_at 无法解析", "skip",
            ))
            continue
        if not collected.startswith("2026"):
            result.blockers.append(_base_blocker(
                source, row, fid, "collected_at", "DATE_SUSPECT",
                f"collected_at={collected[:10]} 年份可疑（疑录入错误）", "warn",
            ))
        if not fid.startswith("f-"):
            nonconformant.append(fid)
        for pattern in _scan_secrets(text):
            result.blockers.append(_base_blocker(
                source, row, fid, "text", "SENSITIVE_CONTENT",
                f"text 命中密钥模式 {pattern!r}（内容已脱敏，建议产品部清除源表）", "warn",
            ))
        result.records.append({
            "fragment_id": fid,
            "text": text,
            "label": label,
            "label_confidence": conf,
            "source": src,
            "user_id_hashed": hash_user_id(v["user"], ctx.salt),
            "collected_at": collected,
            "note": _s(v["note"]),
        })
    if nonconformant:
        groups = "/".join(sorted({fid.split("-")[0] for fid in nonconformant}))
        result.blockers.append(Blocker(
            batch=source.batch, source_file=source.xlsx, sheet=source.sheet,
            row=0, record_id=None, field_name="fragment_id",
            reason="ID_PREFIX_NONCONFORMANT",
            detail=f"{len(nonconformant)}/{len(result.records)} 条不是 f- 前缀，"
                   f"实际按组号命名（{groups}）；**不改写**——A 批 expected 直接引用这些 id，"
                   "改了只会把引用断得更彻底（是否改规格见 docs/真值数据规格标准_v1.md §命名）",
            severity="warn",
        ))
    return result


def convert_c(sources: list[Source], ctx: Ctx) -> BatchResult:
    result = BatchResult(batch="c")
    source = sources[0]
    xlsx = resolve_xlsx(ctx.xlsx_dir, source.xlsx)
    source = replace(source, xlsx=xlsx.name)
    read = read_sheet_rows(xlsx, source)
    if not _handle_read(read, source, result):
        return result
    rows = read.rows
    result.rows_read = len(rows)
    nonconformant: list[str] = []
    for row in rows:
        v = row.values
        cid = _s(v["clip_id"])
        transcript = _s(v["transcript"])
        if not cid or not transcript:
            # 分开报：c 组是「有音频、有 id，但人工转写还空着」，和整行空是两回事
            if not cid and not transcript:
                what = "clip_id 与 transcript 都为空"
            elif not cid:
                what = "clip_id 为空"
            else:
                what = "人工转写 transcript 为空（音频已在，待逐字转写）"
            result.blockers.append(_base_blocker(
                source, row, cid or None, "clip_id/transcript", "MISSING_REQUIRED",
                what, "skip",
            ))
            continue
        dur = v["duration_s"]
        try:
            dur_f = float(dur)
        except (TypeError, ValueError):
            dur_f = -1
        if not (0 < dur_f <= 210):
            result.blockers.append(_base_blocker(
                source, row, cid, "duration_s", "ENUM_INVALID",
                f"duration_s={_safe_value(dur, 'duration_s')} 越界（校验器要求 0<d≤210）", "skip",
            ))
            continue
        collected = _iso(v["collected_at"])
        if collected is None:
            result.blockers.append(_base_blocker(
                source, row, cid, "collected_at", "DATE_UNPARSEABLE", "collected_at 无法解析", "skip",
            ))
            continue

        # 隐私铁律：录音只收自述片段（规格 L104/L159）。转写里出现两套以上
        # 说话人标记 = 多人对话，这条按规格「作废」，不能强行盖 self_talk_only=True。
        labels = speaker_labels(transcript)
        if len(labels) >= 2:
            result.blockers.append(_base_blocker(
                source, row, cid, "transcript", "SELF_TALK_VIOLATION",
                f"转写含 {len(labels)} 个说话人标记（{'/'.join(sorted(labels))}）——"
                "属多人对话，违反「录音只收自述片段」，该条作废", "skip",
            ))
            continue

        raw_name = _s(v["file_name"])
        audio_ref = ""
        if not raw_name or raw_name == "/":
            result.blockers.append(_base_blocker(
                source, row, cid, "audio_ref", "MISSING_MEDIA_FILE",
                "file_name 为空或 '/' —— 该条没有对应音频文件（转录文本仍可用）", "warn",
            ))
        else:
            found = resolve_media(ctx.data_dir / "c" / "audio", raw_name)
            if found is None:
                result.blockers.append(_base_blocker(
                    source, row, cid, "audio_ref", "MISSING_MEDIA_FILE",
                    f"音频文件 {Path(raw_name).name} 不在 research/truth-data/c/audio/ 下"
                    "（转录文本仍可用；RAG 评测只吃 transcript，音频仅 WER 校准需要）", "warn",
                ))
            else:
                audio_ref = f"research/truth-data/c/audio/{found.name}"
                if found.name != Path(raw_name).name:
                    result.blockers.append(_base_blocker(
                        source, row, cid, "audio_ref", "MEDIA_NAME_MISMATCH",
                        f"表里写 {Path(raw_name).name}，盘上是 {found.name}"
                        "（已按实际文件登记，建议产品部统一命名）", "warn",
                    ))

        noise = _code(v["noise_level"])
        if noise not in C_NOISE_LEVELS:
            result.blockers.append(_base_blocker(
                source, row, cid, "noise_level", "ENUM_INVALID",
                f"env={_safe_value(noise, 'noise_level')} 不在 clean/noisy/bgm 内", "warn",
            ))
        if not cid.startswith("v-"):
            nonconformant.append(cid)
        for pattern in _scan_secrets(transcript):
            result.blockers.append(_base_blocker(
                source, row, cid, "transcript", "SENSITIVE_CONTENT",
                f"transcript 命中密钥模式 {pattern!r}（已脱敏）", "warn",
            ))
        # 转写来源：表里显式填 asr 才记 asr；缺列/空值默认 human（兼容旧表）。
        # 2026-10-27：ASR 草稿不能冒充人工真值（校验器允许 asr，但下游要能区分）。
        tsrc = _code(v.get("transcript_source")) or "human"
        if tsrc not in ("human", "asr"):
            result.blockers.append(_base_blocker(
                source, row, cid, "transcript_source", "ENUM_INVALID",
                f"transcript_source={_safe_value(tsrc, 'transcript_source')} 非法（human/asr），按 human 处理", "warn",
            ))
            tsrc = "human"
        result.records.append({
            "clip_id": cid,
            "audio_ref": audio_ref,
            "duration_s": round(dur_f, 1),
            "transcript": transcript,
            "transcript_source": tsrc,
            "emotion": _code(v["emotion"]),
            "noise_level": noise,
            # 隐私铁律：录音只收自述片段。校验器硬要求 true，缺则整条作废
            "self_talk_only": True,
            "user_id_hashed": hash_user_id(v["user"], ctx.salt),
            "collected_at": collected,
            "note": _s(v["note"]),
        })
    if nonconformant:
        groups = "/".join(sorted({cid.split("-")[0] for cid in nonconformant}))
        result.blockers.append(Blocker(
            batch=source.batch, source_file=source.xlsx, sheet=source.sheet,
            row=0, record_id=None, field_name="clip_id",
            reason="ID_PREFIX_NONCONFORMANT",
            detail=f"{len(nonconformant)}/{len(result.records)} 条不是 v- 前缀，"
                   f"实际按组号命名（{groups}）；**不改写**（A 批 expected 直接引用这些 id）",
            severity="warn",
        ))
    return result


_D_SKELETON = {
    "reason": "D 批 excel 只有**照片明细**，缺少真值所需的全部结构性字段，无法产出合格真值",
    "missing_fields": ["set_id", "window", "time_span", "photo_refs", "annotator_count", "consensus",
                       "expected_l1（short）/ expected_l3（long）", "user_id_hashed"],
    "why_not_auto": (
        "consensus 是 2-3 人**独立标注后**的共识比例（规格要求 ≥2/3），"
        "自动编造共识即伪造真值。且 D1 的 final_event_id 只在示例区有 5 行、"
        "全挂在 photo_id 为空的行走上，真实 57 行照片**没有任何事件分组**。"
    ),
    "sample_template": {
        "set_id": "p-0001",
        "window": "short",
        "user_id_hashed": "sha256:...",
        "time_span": {"from": "2026-08-28T00:00:00+08:00", "to": "2026-08-30T23:59:59+08:00"},
        "photo_refs": ["research/truth-data/d/photos/p-0001/001.jpg"],
        "expected_l1": [{"date": "2026-08-28", "item_ids": ["001.jpg"], "title": "早上通勤"}],
        "annotator_count": 3,
        "consensus": "2/3",
        "collected_at": "2026-09-01T10:00:00+08:00",
    },
    "next_step": "请产品部按 set_id 分组，补 window / consensus / annotator_count，并完成 2-3 人独立标注",
}

_F_SKELETON = {
    "reason": "F 批 sheet 只有说明文字，**没有数据行**",
    "missing_fields": ["profile_case_id", "input_text", "expected_updates[]", "user_id_hashed", "collected_at"],
    "blocker_note": (
        "excel 里写「没有画像维度枚举集清单之前 F 批无法开始」——**该前置条件已解除**，"
        "清单已就绪：docs/画像维度枚举集清单_v1.md + docs/画像维度枚举集_l0.json"
    ),
    "sample_template": {
        "profile_case_id": "pf-0001",
        "input_text": "项目延期好累",
        "expected_updates": [{
            "dimension": "stress_level",
            "enum_value": "高",
            "confidence": 0.8,
            "should_update": True,
        }],
        "user_id_hashed": "sha256:...",
        "collected_at": "2026-09-01T10:00:00+08:00",
    },
    "next_step": "对照 docs/画像维度枚举集_l0.json 的维度 id（小写蛇形英文）与 values（中文枚举值）填写",
}


def convert_d(sources: list[Source], ctx: Ctx) -> BatchResult:
    """D 批只出骨架 —— 见 _D_SKELETON 的理由。"""
    result = BatchResult(batch="d", converted=False, skeleton=dict(_D_SKELETON))
    for source in sources:
        read = read_sheet_rows(resolve_xlsx(ctx.xlsx_dir, source.xlsx), source)
        _handle_read(read, source, result)
        rows = read.rows
        if not read.hard_errors:
            result.rows_read += len(rows)
        result.blockers.append(_base_blocker(
            source, RawRow(0, {}), None, "-", "BATCH_NOT_CONVERTIBLE",
            f"{source.sheet}: 读到 {len(rows)} 行照片明细，但缺真值结构字段 → 本批只出骨架，不写正式产物",
            "hard",
        ))
    return result


def convert_e(sources: list[Source], ctx: Ctx) -> BatchResult:
    result = BatchResult(batch="e")
    source = sources[0]
    read = read_sheet_rows(resolve_xlsx(ctx.xlsx_dir, source.xlsx), source)
    if not _handle_read(read, source, result):
        return result
    rows = read.rows
    result.rows_read = len(rows)
    for row in rows:
        v = row.values
        cid = _s(v["correction_id"])
        text = _s(v["text"])
        predicted = _code(v["predicted"])
        corrected = _code(v["corrected"])
        if not cid or not text:
            result.blockers.append(_base_blocker(
                source, row, cid or None, "correction_id/text", "MISSING_REQUIRED", "id 或 text 为空", "skip",
            ))
            continue
        if predicted not in FRAGMENT_LABELS or corrected not in FRAGMENT_LABELS:
            result.blockers.append(_base_blocker(
                source, row, cid, "predicted/corrected", "ENUM_INVALID",
                f"predicted={_safe_value(predicted, 'predicted')} / "
                f"corrected={_safe_value(corrected, 'corrected')} 不在 7 类枚举内"
                f"（{'/'.join(FRAGMENT_LABELS)}）", "skip",
            ))
            continue
        if predicted == corrected:
            result.blockers.append(_base_blocker(
                source, row, cid, "predicted", "PREDICTED_EQUALS_CORRECTED",
                "predicted 与 corrected 相同 → 不是纠错样本（校验器硬拒）", "skip",
            ))
            continue
        collected = _iso(v["collected_at"])
        if collected is None:
            result.blockers.append(_base_blocker(
                source, row, cid, "collected_at", "DATE_UNPARSEABLE", "found_at 无法解析", "skip",
            ))
            continue
        ref = _s(v["ref_fragment_id"])
        if ref and ref not in ctx.known_ids.get("b", set()):
            result.blockers.append(_base_blocker(
                source, row, cid, "ref_fragment_id", "REF_DANGLING",
                f"ref_fragment_id={_safe_value(ref, 'ref_fragment_id')} 在 B 批里不存在"
                "（校验器不校验此字段，不影响入库）", "warn",
            ))
        note = _s(v["reason"])
        extra = _s(v["note"])
        result.records.append({
            "correction_id": cid,
            "text": text,
            "predicted": predicted,
            "corrected": corrected,
            # 规格：现阶段只收 arbitration（user-manual 待 App 上线后补）
            "context": "arbitration",
            "user_id_hashed": hash_user_id(v["user"], ctx.salt),
            "collected_at": collected,
            "note": f"{note}；{extra}" if extra else note,
        })
    return result


def convert_f(sources: list[Source], ctx: Ctx) -> BatchResult:
    """F 批只出骨架 —— sheet 无数据行。"""
    result = BatchResult(batch="f", converted=False, skeleton=dict(_F_SKELETON))
    for source in sources:
        read = read_sheet_rows(resolve_xlsx(ctx.xlsx_dir, source.xlsx), source)
        _handle_read(read, source, result)
        if not read.hard_errors:
            result.rows_read += len(read.rows)
        result.blockers.append(_base_blocker(
            source, RawRow(0, {}), None, "-", "BATCH_NOT_CONVERTIBLE",
            f"{source.sheet}: 读到 {len(read.rows)} 行数据（sheet 只有说明文字）→ 本批只出骨架", "hard",
        ))
    return result


def convert_g(sources: list[Source], ctx: Ctx) -> BatchResult:
    result = BatchResult(batch="g")
    source = sources[0]
    read = read_sheet_rows(resolve_xlsx(ctx.xlsx_dir, source.xlsx), source)
    if not _handle_read(read, source, result):
        return result
    rows = read.rows
    result.rows_read = len(rows)
    for row in rows:
        v = row.values
        gid = _s(v["guardrail_id"])
        text = _s(v["input_text"])
        if not gid or not text:
            result.blockers.append(_base_blocker(
                source, row, gid or None, "guardrail_id/input_text", "MISSING_REQUIRED",
                "id 或文本为空", "skip",
            ))
            continue
        layer = G_LAYER_MAP.get(_code(v["layer"]))
        verdict = G_VERDICT_MAP.get(_code(v["verdict"]))
        action = G_ACTION_MAP.get(_code(v["action"]))
        for name, val in (("layer", layer), ("verdict", verdict), ("action", action)):
            if val is None:
                result.blockers.append(_base_blocker(
                    source, row, gid, name, "ENUM_INVALID",
                    f"{name} 取值 {_safe_value(v[name if name != 'layer' else 'layer'], name)} 无法映射", "skip",
                ))
        if layer is None or verdict is None or action is None:
            continue
        if verdict == "unsafe" and action == "allow":
            result.blockers.append(_base_blocker(
                source, row, gid, "action", "UNSAFE_ALLOWED",
                "unsafe 配 allow = 漏杀红线（校验器硬阻断）", "skip",
            ))
            continue
        collected = _iso(v["collected_at"])
        if collected is None:
            result.blockers.append(_base_blocker(
                source, row, gid, "collected_at", "DATE_UNPARSEABLE", "collected_at 无法解析", "skip",
            ))
            continue
        # G 批 excel **没有 user_id_hashed 列**（规格却必填）。该批是产品部构造的
        # 测试语句、不绑定真实用户（规格原文「敏感文本不带真实用户」），
        # 故统一映射到同一个伪名，而不是留空。
        raw_user = v.get("user")
        if _s(raw_user):
            user_hashed = hash_user_id(raw_user, ctx.salt)
        else:
            user_hashed = hash_user_id(_G_ANON_USER, ctx.salt)
            result.blockers.append(_base_blocker(
                source, row, gid, "user_id_hashed", "USER_SYNTHESIZED",
                f"excel 无 user_id_hashed 列 → 统一使用伪名 {_G_ANON_USER!r} 的哈希", "warn",
            ))
        result.records.append({
            "guardrail_id": gid,
            "input_text": text,
            "verdict": verdict,
            "action": action,
            "layer": layer,
            "user_id_hashed": user_hashed,
            "collected_at": collected,
            "note": _s(v["reason"]),
        })
    return result


def convert_h(sources: list[Source], ctx: Ctx) -> BatchResult:
    result = BatchResult(batch="h")
    source = sources[0]
    read = read_sheet_rows(resolve_xlsx(ctx.xlsx_dir, source.xlsx), source)
    if not _handle_read(read, source, result):
        return result
    rows = read.rows
    result.rows_read = len(rows)
    for row in rows:
        v = row.values
        oid = _s(v["ocr_id"])
        expected = _s(v["expected_text"])
        if not oid or not expected:
            result.blockers.append(_base_blocker(
                source, row, oid or None, "ocr_id/expected_text", "MISSING_REQUIRED",
                "id 或人工转录文字为空（校验器硬要求 expected_text 非空）", "skip",
            ))
            continue
        name = _s(v["file_name"])
        image_ref = f"research/truth-data/h/images/{Path(name).name}" if name else ""
        if not name or not (ctx.data_dir / "h" / "images" / Path(name).name).exists():
            result.blockers.append(_base_blocker(
                source, row, oid, "image_ref", "MISSING_MEDIA_FILE",
                f"图片 {name or '（空）'} 不在 research/truth-data/h/images/ 下"
                "（JSON 仍可用，但 OCR 评测要跑时需补图）", "warn",
            ))
        collected = _iso(v["collected_at"])
        if collected is None:
            result.blockers.append(_base_blocker(
                source, row, oid, "collected_at", "DATE_UNPARSEABLE", "collected_at 无法解析", "skip",
            ))
            continue
        # note_type（购物清单/日记/笔记/待办）**不是** image_type —— 规格的 image_type
        # 是出处分类（handwriting/screenshot/old_photo/complex_layout），excel 这份只会收手写。
        note_parts = []
        note_type = _s(v["note_type"])
        quality = _s(v["handwriting_quality"])
        if note_type:
            note_parts.append(f"笔记类型：{note_type}")
        if quality:
            note_parts.append(f"字迹：{quality}")
        if _s(v["note"]):
            note_parts.append(_s(v["note"]))
        result.records.append({
            "ocr_id": oid,
            "image_ref": image_ref,
            "expected_text": expected,
            "image_type": "handwriting",
            # 规格：公开数据集样本不落本目录，本目录只收 product-team
            "source": "product-team",
            "collected_at": collected,
            "note": "；".join(note_parts),
        })
    return result


_CONVERTERS: dict[str, Callable[[list[Source], Ctx], BatchResult]] = {
    "a": convert_a,
    "b": convert_b,
    "c": convert_c,
    "d": convert_d,
    "e": convert_e,
    "f": convert_f,
    "g": convert_g,
    "h": convert_h,
}


# ---------------------------------------------------------------- 写层


def _dump(records: list[dict]) -> str:
    return json.dumps(records, ensure_ascii=False, indent=2) + "\n"


def resolve_out_path(batch: str, payload: str, ctx: Ctx) -> Path:
    """挑一个版本号：优先复用**内容相同**的既有文件（幂等），否则取下一个空位。

    这样既不会覆盖 `a/a_v1.json`（仓库里那份示例），重跑也产出字节相同的文件。
    """
    out_dir = ctx.data_dir / batch
    version = 1
    while True:
        candidate = out_dir / f"{batch}_v{version}.json"
        if not candidate.exists():
            return candidate
        if candidate.read_text(encoding="utf-8") == payload:
            return candidate  # 内容一致 → 复用，保证幂等
        version += 1


def write_batch(result: BatchResult, ctx: Ctx, dry_run: bool) -> Path | None:
    """写 {batch}/{batch}_vN.json。converted=False 的批**不写**（空数组会让校验器假绿）。"""
    if not result.converted:
        return None
    payload = _dump(result.records)
    path = resolve_out_path(result.batch, payload, ctx)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    return path


def write_reports(results: list[BatchResult], ctx: Ctx, dry_run: bool) -> tuple[Path, Path]:
    """阻断清单（机读 json + 人读 md）与骨架文件。"""
    report_dir = ctx.data_dir / "_reports"
    machine: dict[str, Any] = {"version": 1, "batches": {}}
    for res in results:
        machine["batches"][res.batch] = {
            "rows_read": res.rows_read,
            "records_written": len(res.records),
            "skipped": sum(1 for b in res.blockers if b.severity == "skip"),
            "warns": sum(1 for b in res.blockers if b.severity == "warn"),
            "converted": res.converted,
            "items": [b.to_dict() for b in res.blockers],
        }

    lines = ["# 真值转换阻断清单", "",
             "来源：产品部 xlsx（<工作区>/真值数据/标注表/，仓库外）"
             "｜由 `scripts/convert_truth_xlsx.py` 生成", ""]
    for res in results:
        status = "✅ 已转出" if res.converted else "⚠️ 仅骨架"
        lines.append(f"## 批 {res.batch.upper()} — {status}"
                     f"（读 {res.rows_read} 行 → 写 {len(res.records)} 条）")
        lines.append("")
        if not res.converted and res.skeleton:
            lines.append(f"**不产出正式真值**：{res.skeleton.get('reason', '')}")
            lines.append("")
        for severity, mark in (("hard", "✗"), ("skip", "✗"), ("warn", "⚠")):
            items = [b for b in res.blockers if b.severity == severity]
            if not items:
                continue
            lines.append(f"### {mark} {severity}")
            lines.append("")
            for b in items:
                # row=0 是整批级阻断（如"109/109 条都不是 f- 前缀"），不指行号
                where = f"{b.source_file}!{b.sheet}" + (f" r{b.row}" if b.row else "")
                loc = f"{res.batch.upper()}批 {b.record_id or '-'} ({where})"
                lines.append(f"- {b.reason} ｜ {loc} ｜ 字段 `{b.field_name}`：{b.detail}")
            lines.append("")

    if not dry_run:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "blockers.json").write_text(
            json.dumps(machine, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (report_dir / "blockers.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for res in results:
            if res.skeleton is not None:
                (report_dir / f"skeleton_{res.batch}.json").write_text(
                    json.dumps(res.skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report_dir / "blockers.json", report_dir / "blockers.md"


# ---------------------------------------------------------------- salt


def load_salt(*, init: bool, salt_path: Path) -> str:
    """salt 来源：环境变量 → .salt 文件。都没有则拒绝运行（哈希必须可复现）。"""
    env_salt = _s(__import__("os").environ.get("TRUTH_HASH_SALT"))
    if env_salt:
        return env_salt
    if salt_path.exists():
        return _s(salt_path.read_text(encoding="utf-8"))
    if init:
        salt_path.parent.mkdir(parents=True, exist_ok=True)
        new_salt = _secrets.token_hex(16)
        salt_path.write_text(new_salt + "\n", encoding="utf-8")
        print(f"✅ 已生成脱敏盐 → {salt_path}（已在 .gitignore 内，勿提交）")
        return new_salt
    print("❌ 缺少脱敏盐：请设置环境变量 TRUTH_HASH_SALT，或先跑一次 --init-salt")
    print("   为什么必须显式提供：salt 变了哈希就变，同一用户在 B/C 批会算出两个 id，")
    print("   跨批一致性会被破坏。")
    raise SystemExit(2)


# ---------------------------------------------------------------- main


def collect_known_ids(by_batch: dict[str, list[Source]], ctx_xlsx_dir: Path) -> dict[str, set[str]]:
    """先扫 B/C/D 批，建立「采集 id → 批」索引，供 A 批引用校验。"""
    known: dict[str, set[str]] = {"b": set(), "c": set(), "d": set()}
    id_fields = {"b": "fragment_id", "c": "clip_id"}
    for batch in ("b", "c"):
        source = by_batch[batch][0]
        read = read_sheet_rows(resolve_xlsx(ctx_xlsx_dir, source.xlsx), source)
        if read.hard_errors:
            continue
        field_name = id_fields[batch]
        known[batch] = {_s(r.values[field_name]) for r in read.rows if _s(r.values[field_name])}
    for source in by_batch["d"]:
        read = read_sheet_rows(resolve_xlsx(ctx_xlsx_dir, source.xlsx), source)
        if read.hard_errors:
            continue
        known["d"] |= {_s(r.values["photo_id"]) for r in read.rows if _s(r.values["photo_id"])}
    return known


def main() -> int:
    parser = argparse.ArgumentParser(description="产品部 xlsx → 真值 JSON（规格 v1）")
    parser.add_argument("--batch", choices=sorted(_CONVERTERS), action="append",
                        help="只转指定批（可重复，如 --batch b --batch c）")
    parser.add_argument("--xlsx-dir", type=Path, default=XLSX_DIR_DEFAULT,
                        help=f"xlsx 所在目录（默认 {XLSX_DIR_DEFAULT}）")
    parser.add_argument("--truth-dir", type=Path, default=DATA_DIR,
                        help="真值 JSON 输出根目录")
    parser.add_argument("--init-salt", action="store_true", help="生成 research/truth-data/.salt")
    parser.add_argument("--strict-layer", action="store_true",
                        help="A 批 layer 不做推断（只保留 time/person/place 同名词）")
    parser.add_argument("--dry-run", action="store_true", help="只解析与报告，不写盘")
    args = parser.parse_args()

    data_dir = args.truth_dir
    salt = load_salt(init=args.init_salt, salt_path=data_dir / ".salt")

    by_batch: dict[str, list[Source]] = {}
    for source in SOURCES:
        by_batch.setdefault(source.batch, []).append(source)

    selected = args.batch or sorted(by_batch)
    missing = [s.xlsx for s in SOURCES if s.batch in selected
               and not resolve_xlsx(args.xlsx_dir, s.xlsx).exists()]
    if missing:
        print(f"❌ 以下 xlsx 不在 {args.xlsx_dir}：{sorted(set(missing))}")
        print("   用 --xlsx-dir 指定产品部表格所在目录。")
        return 2

    known_ids = collect_known_ids(by_batch, args.xlsx_dir)
    ctx = Ctx(salt=salt, known_ids=known_ids, strict_layer=args.strict_layer,
              xlsx_dir=args.xlsx_dir, data_dir=data_dir)

    print(f"xlsx 目录：{args.xlsx_dir}")
    print(f"已知采集 id：B {len(known_ids['b'])} / C {len(known_ids['c'])} / D {len(known_ids['d'])}\n")

    results: list[BatchResult] = []
    for batch in selected:
        result = _CONVERTERS[batch](by_batch[batch], ctx)
        results.append(result)
        path = write_batch(result, ctx, args.dry_run)
        skipped = sum(1 for b in result.blockers if b.severity == "skip")
        warns = sum(1 for b in result.blockers if b.severity == "warn")
        hard = sum(1 for b in result.blockers if b.severity == "hard")
        mark = "⚠️" if not result.converted else ("❌" if hard else "✅")
        tail = f" → {path.name}" if path else " → 仅骨架（不写正式产物）"
        print(f"[{mark}] 批 {batch}: 读 {result.rows_read} 行 → 写 {len(result.records)} 条"
              f"｜跳过 {skipped}｜告警 {warns}" + (f"｜硬失败 {hard}" if hard else "") + tail)

    json_path, md_path = write_reports(results, ctx, args.dry_run)
    total = sum(len(r.records) for r in results)
    print(f"\n合计写出 {total} 条真值")
    if args.dry_run:
        print("（--dry-run：未写盘）")
    else:
        print(f"阻断清单：{md_path}\n         {json_path}")
        print("\n下一步：python scripts/validate_truth_data.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
