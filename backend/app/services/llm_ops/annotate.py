"""annotate.py —— B1 域：画像枚举标注（LLM 映射，只映射不生成）

任务归属：Wave 3 Agent I（B1 画像域）独占本文件。
把自然语言文本映射为 [{dimension, enum_value, confidence}]（B1 §2.3"标注是映射不是生成"）：
- 真实通道：qwen-flash（经 base.chat_text，禁止直接 import dashscope），
  prompt 内置标注池维度 + 种子值，约束只映射不生成、同义归一、输出 JSON。
- Mock 通道（无 key / MOCK_EXTERNAL_AI=true / LLM 失败降级）：确定性种子值 + 别名子串匹配，
  与真实输出同构（同字段），切真实 key 无代码改动。
- 输出已按 schema 校验：dimension 必须存在、confidence 收敛 0-1；enum_value 命中种子值
  或为开放枚举新值（是否"新增"由 profile_annotator 归一后裁决）。
"""
from __future__ import annotations

import json
import logging
import re

from app.services.llm_ops.base import chat_text, llm_available
from app.services.profile_schema import DimensionSpec, EnumSchema, get_schema

logger = logging.getLogger("yishu.annotate")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

# 单次标注最多命中维度数（防 LLM 满屏幻觉）
_MAX_HITS = 5

_ANNOTATE_SYSTEM = (
    "你是用户画像标注器。把用户的话「映射」到画像维度枚举值，不要生成新概念、不要编造。\n"
    "规则：\n"
    "1. 从维度清单中选确有把握命中的维度（可多选，最多 5 个）；没把握就不要输出。\n"
    "2. 每个命中输出一个 JSON 对象：{\"dimension\": 维度id, \"enum_value\": 枚举值, \"confidence\": 0到1}。\n"
    "3. enum_value 必须从该维度的枚举值中选最贴切的一个；同义表述归一到已存在值（如「母亲」→「妈妈」）。\n"
    "4. 只有确无等价值时才给一个最接近的简短新值（4-8 个汉字，沿用已有命名风格）。\n"
    "5. 低置信（confidence < 0.6）不要输出。\n"
    "6. 只输出 JSON：{\"dimension_hits\": [...]}，不要任何解释。"
)


def annotate(
    text: str,
    *,
    schema: EnumSchema | None = None,
    dimension_hint: list[str] | None = None,
    confidence: float | None = None,
) -> list[dict]:
    """文本 → [{dimension, enum_value, confidence}]（已校验，可含开放枚举新值）

    dimension_hint：只在该维度子集内匹配（冷启动兴趣稀疏激活用）；None = 默认标注池。
    confidence：仅供 mock 通道覆盖（测试 / 冷启动用），真实 LLM 路径忽略。
    """
    text = (text or "").strip()
    if not text:
        return []
    schema = schema or get_schema()
    pool = _pool_for(schema, dimension_hint)
    if not pool:
        return []
    if llm_available():
        try:
            return _llm_annotate(text, pool)
        except Exception as exc:  # noqa: BLE001 —— LLM 失败降级 mock（不阻断标注管线）
            logger.warning("画像标注 LLM 调用失败，降级 mock: %s", exc)
    return _mock_annotate(text, pool, confidence=confidence)


# ---------------------------------------------------------------- 标注池
def _pool_for(schema: EnumSchema, dimension_hint: list[str] | None) -> list[DimensionSpec]:
    if dimension_hint:
        return [schema.get(i) for i in dimension_hint if schema.get(i) is not None]
    return schema.annotate_dims()


# ---------------------------------------------------------------- 真实 LLM 通道
def _llm_annotate(text: str, pool: list[DimensionSpec]) -> list[dict]:
    raw = chat_text(_ANNOTATE_SYSTEM, _build_user_prompt(text, pool)).strip()
    data = _parse_json(raw)
    hits = data.get("dimension_hits") if isinstance(data, dict) else None
    return _normalize_hits(hits, pool)


def _build_user_prompt(text: str, pool: list[DimensionSpec]) -> str:
    lines = []
    for spec in pool:
        values = "、".join(spec.values[:12])
        lines.append(f"- {spec.id}（{spec.label}）：{values}")
    return f"用户的话：\n{text}\n\n可标注维度清单：\n" + "\n".join(lines)


def _parse_json(raw: str) -> dict:
    """容错解析 LLM 输出为 dict（围栏/前后噪声剥离）"""
    if not raw:
        return {}
    m = _JSON_FENCE.search(raw)
    body = m.group(1) if m else raw
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        obj = re.search(r"\{.*\}", body, re.S)
        if obj:
            try:
                data = json.loads(obj.group(0))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
    return data if isinstance(data, dict) else {}


def _normalize_hits(hits, pool: list[DimensionSpec]) -> list[dict]:
    pool_ids = {s.id for s in pool}
    out: list[dict] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        dim = str(hit.get("dimension") or "")
        value = str(hit.get("enum_value") or "").strip()
        if dim not in pool_ids or not value:
            continue
        conf = _to_confidence(hit.get("confidence"))
        if conf < 0.6:
            continue
        out.append({"dimension": dim, "enum_value": value, "confidence": conf})
        if len(out) >= _MAX_HITS:
            break
    return out


# ---------------------------------------------------------------- Mock 通道（确定性）
def _mock_annotate(text: str, pool: list[DimensionSpec], *, confidence: float | None = None) -> list[dict]:
    """种子值 + 别名子串匹配（schema 驱动，零硬编码规则）

    长词优先（更具体）；单值维度每维取 1 条、集合型取最多 3 条；
    单字值（男/女/不/是/否）跳过防误命中。与真实输出同构。
    """
    conf = confidence if confidence is not None else 0.85
    hits: list[dict] = []
    for spec in pool:
        matched: dict[str, int] = {}  # value → 命中词长度
        for value in spec.values:
            if len(value) >= 2 and value in text:
                matched[value] = max(matched.get(value, 0), len(value))
        for alias, canonical in spec.aliases.items():
            if len(alias) >= 2 and alias in text:
                matched[canonical] = max(matched.get(canonical, 0), len(alias))
        if not matched:
            continue
        ranked = sorted(matched.items(), key=lambda kv: (-kv[1], kv[0]))
        cap = 3 if spec.multi_value else 1
        for value, _ln in ranked[:cap]:
            hits.append({"dimension": spec.id, "enum_value": value, "confidence": conf})
    return hits


# ---------------------------------------------------------------- 工具
def _to_confidence(raw) -> float:
    try:
        conf = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, conf))
