"""parsing.py —— LLM JSON 输出容错解析统一入口（TD-P2B · S1-H1 收口）

此前 4 个模块各写一份容错解析（annotate/event_merge/rerank/ner），同一正则常量
`_JSON_FENCE` 复制、兜底逻辑不一（annotate 有 `{...}` 兜底、event_merge 无内层
try、ner 连围栏剥离都没有）——LLM 输出格式兼容层是外部契约的"单点"，散落多份后
改一处漏一处。现统一本模块，语义对齐 + 收敛为容错降级（解析失败返回空容器，不抛错）：
- extract_json_object(raw)：fence 剥离 + 花括号对象切片兜底 + dict 类型校验
- extract_json_array(raw)：fence 剥离 + 方括号数组切片兜底 + list 类型校验

调用方各自保留字段级清洗（rerank 的 i/ans/reason、annotate 的 dimension_hits、
ner 的字符串值剥离等）。
"""
from __future__ import annotations

import json
import re

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json_object(raw: str) -> dict:
    """容错解析 LLM 输出为 dict（围栏剥离 + `{...}` 兜底 + 类型校验）

    空输入 / 解析失败 / 非对象（list/str/数字）→ {}，调用方按空处理降级。
    """
    if not raw:
        return {}
    body = _strip_fence(raw)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        obj = re.search(r"\{.*\}", body, re.S)
        if obj is None:
            return {}
        try:
            data = json.loads(obj.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def extract_json_array(raw: str) -> list:
    """容错解析 LLM 输出为 list（围栏剥离 + `[...]` 切片兜底 + 类型校验）

    空输入 / 无数组 / 解析失败 / 非数组 → []，调用方按空处理降级。
    """
    if not raw:
        return []
    body = _strip_fence(raw)
    start, end = body.find("["), body.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(body[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _strip_fence(raw: str) -> str:
    """剥 ```json ... ``` 代码围栏（无围栏原样返回）"""
    m = _JSON_FENCE.search(raw)
    return m.group(1) if m else raw
