"""profile_schema.py —— B1 域：画像枚举集 JSON 加载器（零硬编码）

任务归属：Wave 3 Agent I（B1 画像域）独占本文件。
从 docs/ 加载《画像维度枚举集_l0.json》+《画像维度枚举集_l1_骨架.json》→ 内存枚举集：
维度（id/label/category/values/phrase/threshold/disclosure/open_enum/multi_value/aliases）、
默认标注池（annotate_default_dims）、披露层与置信度双门槛全部数据驱动、代码零硬编码。

路径：默认相对仓库根 docs/；可用环境变量 PROFILE_ENUM_DIR 覆盖（部署时 JSON 迁移）。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("yishu.profile_schema")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ENUM_DIR = _REPO_ROOT / "docs"
L0_FILENAME = "画像维度枚举集_l0.json"
L1_FILENAME = "画像维度枚举集_l1_骨架.json"

# 披露层合法取值（B1 §2.4：L1 常驻索引 / L2 场景扩展 / L3 全量）
_DISCLOSURE_LEVELS = ("L1", "L2", "L3")

# 缺省置信度（普通维度门槛；超细性格 0.8 由 JSON confidence_threshold 覆盖）
_DEFAULT_THRESHOLD = 0.7


@dataclass(frozen=True)
class DimensionSpec:
    """单个画像维度的枚举定义（全部来自 JSON，无代码硬编码）"""

    id: str
    label: str
    category: str
    values: tuple[str, ...]                    # 种子值（开放枚举起点，非封闭集合）
    phrase: str                                # 顶层 phrase 模板（含 {value}，披露路由用）
    confidence_threshold: float                # 普通 0.7 / 超细性格 0.8（双门槛）
    disclosure: str                            # L1 常驻 / L2 场景 / L3 全量
    open_enum: bool                            # 是否允许直接新增 value
    multi_value: bool                          # 集合型（同值累加、异值追加）；否则单值替换+历史
    aliases: dict[str, str] = field(default_factory=dict)   # 同义归一别名表（alias → 规范值）
    level: str = "L0"                          # L0 核心 / L1 扩展
    description: str = ""
    values_detail: tuple[dict, ...] = ()

    def threshold(self) -> float:
        return self.confidence_threshold or _DEFAULT_THRESHOLD

    def is_superfine(self) -> bool:
        """超细性格维度（置信度门槛 ≥0.8 → 双门槛第二档，需多证据累积）"""
        return (self.confidence_threshold or 0) >= 0.8

    def canonicalize(self, value: str) -> str | None:
        """同义归一（B1 §2.3 开放枚举第一步）：命中种子值或别名 → 规范值；否则 None（可新增）"""
        if value in self.values:
            return value
        return self.aliases.get(value)


@dataclass(frozen=True)
class EnumSchema:
    """内存枚举集（L0 + L1 合并）"""

    dimensions: dict[str, DimensionSpec]
    annotate_default_dims: tuple[str, ...]     # 默认标注池（LLM prompt / mock 匹配维度清单）
    l0_count: int
    l1_count: int

    def get(self, dim_id: str) -> DimensionSpec | None:
        return self.dimensions.get(dim_id)

    def annotate_dims(self) -> list[DimensionSpec]:
        """默认标注池（缺清单则回退全部维度）"""
        if self.annotate_default_dims:
            return [self.dimensions[i] for i in self.annotate_default_dims if i in self.dimensions]
        return list(self.dimensions.values())

    def dims_in_categories(self, categories: tuple[str, ...]) -> list[DimensionSpec]:
        """按类别取维度（冷启动兴趣稀疏激活用）"""
        return [d for d in self.dimensions.values() if d.category in categories]

    def confidence_threshold(self, dim_id: str) -> float:
        spec = self.dimensions.get(dim_id)
        return spec.threshold() if spec else _DEFAULT_THRESHOLD

    def validate(self) -> list[str]:
        """完整性校验（DoD：维度数/枚举值/引用完整性），返回问题列表（空 = 通过）"""
        issues: list[str] = []
        for spec in self.dimensions.values():
            if not spec.values:
                issues.append(f"{spec.id}: 无枚举值")
            if not spec.phrase:
                issues.append(f"{spec.id}: 缺顶层 phrase")
            if spec.disclosure not in _DISCLOSURE_LEVELS:
                issues.append(f"{spec.id}: disclosure={spec.disclosure} 非法")
            for vd in spec.values_detail:
                if vd.get("value") not in spec.values:
                    issues.append(f"{spec.id}: values_detail「{vd.get('value')}」不在 values")
        for i in self.annotate_default_dims:
            if i not in self.dimensions:
                issues.append(f"annotate_default_dims 引用缺失: {i}")
        if self.l0_count != 51:
            issues.append(f"L0 维度数异常: {self.l0_count}（期望 51）")
        if self.l1_count != 193:
            issues.append(f"L1 维度数异常: {self.l1_count}（期望 193）")
        return issues


def _enum_dir() -> Path:
    return Path(os.environ.get("PROFILE_ENUM_DIR") or _DEFAULT_ENUM_DIR)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_level(js: dict, level: str) -> dict[str, DimensionSpec]:
    dims: dict[str, DimensionSpec] = {}
    for raw in js.get("dimensions", []):
        values = raw.get("values")
        if not values:
            # 模板型/结构型维度只有 values_detail（无顶层 values）
            values = [vd["value"] for vd in raw.get("values_detail") or []]
        if not values:
            logger.warning("维度 %s 无枚举值，跳过", raw.get("id"))
            continue
        dims[raw["id"]] = DimensionSpec(
            id=raw["id"],
            label=raw.get("label") or raw["id"],
            category=raw.get("category") or "",
            values=tuple(values),
            phrase=raw.get("phrase") or "",
            confidence_threshold=float(raw.get("confidence_threshold") or _DEFAULT_THRESHOLD),
            disclosure=raw.get("disclosure") or "L2",
            open_enum=bool(raw.get("open_enum", True)),
            multi_value=bool(raw.get("multi_value", False)),
            aliases=dict(raw.get("aliases") or {}),
            level=level,
            description=raw.get("description") or "",
            values_detail=tuple(raw.get("values_detail") or []),
        )
    return dims


def _build_schema() -> EnumSchema:
    enum_dir = _enum_dir()
    l0 = _load_json(enum_dir / L0_FILENAME)
    l1 = _load_json(enum_dir / L1_FILENAME)
    dimensions: dict[str, DimensionSpec] = {}
    dimensions.update(_load_level(l0, "L0"))
    dimensions.update(_load_level(l1, "L1"))
    annotate_default = tuple(l0.get("annotate_default_dims") or [])
    return EnumSchema(
        dimensions=dimensions,
        annotate_default_dims=annotate_default,
        l0_count=len(l0.get("dimensions", [])),
        l1_count=len(l1.get("dimensions", [])),
    )


@lru_cache(maxsize=1)
def get_schema() -> EnumSchema:
    """枚举集单例（进程内缓存；JSON 变更后重启生效）"""
    return _build_schema()
