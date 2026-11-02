"""剥掉模型输出里**不该给用户看**的结构化标记（2026-10-15 · 真机反馈「AI 对话冒出 JSON 中间消息」）。

## 现象与机制（查过，不是猜的）

真机实测：AI 对话过程中会闪现 JSON/标记碎片。根因**不在客户端**：
DeepSeek 在**流式**响应里会**同时**往 `delta.content` 写它原生的 **DSML**（DeepSeek Markup
Language）工具调用标记 —— 而标准规范下工具调用**只应**出现在 `delta.tool_calls` 里
（业界实测记录 + 本仓 `docs/交接_流式输出与对话UI_20261009.md:41/68` 早就登记过这条
「DSML 文本泄漏教训」）。`api/chat.py` 把 agent 的 `delta` 原样累积成 `preview` 帧推给
客户端 ⇒ 标记直接上屏。

## 口径（调研结论：不是「再加强清洗」，而是两条一起）

1. **单点过滤**：凡是"将要展示给用户的模型文本"都必须过本模块 —— 流式 `preview`、`replace`
   帧、以及**终稿**（终稿还会落库 ⇒ 库里也不能留标记）。只在一处实现，避免两端各写一份、
   日后再漂移；
2. **可验证不变量**：`tests/test_model_text.py` 断言"剥完之后**不含**任何 DSML/工具调用碎片"
   （含**流式半截标记**）。把"用户可见文本干净"变成可回归的约束，而不是靠人眼盯。

## 保守性（重要，也是"鲁棒"的关键）

**只剥工具调用/DSML 痕迹，绝不剥正常 JSON**：用户问"给我一段 JSON 示例"时，回答里的
合法 JSON **必须原样保留** ⇒ 本模块只认① DSML 标记语法、② **含 `tool_calls`（或
`name`+`arguments` 组合）的 JSON 对象**。其它花括号内容一律不动。
"""
from __future__ import annotations

import json
import re

# DSML 标记的**任意**承载形态：`<｜DSML｜>`、`</｜DSML｜>`、`<｜DSML｜tool_calls｜DSML｜>`、
# `<dsml>…</dsml>` —— 共同点是"一对尖括号里出现 DSML"且**括号内不含其它尖括号**
# （实测标记内部不会嵌 `<>`）⇒ 一个非贪婪匹配即可全覆盖，比逐种写法枚举稳。
_DSML_TAG = re.compile(r"<[^<>]{0,160}?DSML[^<>]{0,160}?>", re.IGNORECASE)
# 开了头没闭合（**流式半截标记**）⇒ 剥到串尾：下一帧会送来完整内容，重剥即可（幂等）
_DSML_OPEN_TAIL = re.compile(r"<[^<>]{0,160}?DSML[^<>]*$", re.IGNORECASE)

# 认定"这是工具调用"的键（大小写不敏感）
_TOOL_KEYS = ("tool_calls", "tool_call", "function_call")
_ARG_KEYS = ("arguments", "args", "parameters")


def _looks_like_tool_call(text: str) -> bool:
    """文本是否为工具调用结构（保守判定：只认 tool_calls 系列键，或 name+arguments 组合）"""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict):
        return False
    keys = {str(k).lower() for k in obj.keys()}
    if keys & set(_TOOL_KEYS):
        return True
    return "name" in keys and bool(keys & set(_ARG_KEYS))


def _strip_tool_call_objects(text: str) -> str:
    """删掉**顶层** JSON 对象里属于工具调用的那些（顺带处理 ```json 围栏）。

    用**字符串感知的括号配对**扫描，而不是正则 —— 工具调用参数常含嵌套对象与转义引号，
    正则配对必然出错（会把半句正常回答一起吞掉）。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "{":
            out.append(ch)
            i += 1
            continue
        # 从 { 开始做括号配对（跟踪引号与转义）
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            cj = text[j]
            if in_str:
                if esc:
                    esc = False
                elif cj == "\\":
                    esc = True
                elif cj == '"':
                    in_str = False
            elif cj == '"':
                in_str = True
            elif cj == "{":
                depth += 1
            elif cj == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        candidate = text[i:j]
        if _looks_like_tool_call(candidate):
            i = j  # 丢弃（不写入 out）
            continue
        out.append(candidate)
        i = j
    return "".join(out)


def strip_model_markup(text: str) -> str:
    """把模型输出剥成**用户可见文本**（幂等；可对累计文本反复调用）。"""
    if not text:
        return text
    cleaned = _DSML_TAG.sub("", text)
    cleaned = _DSML_OPEN_TAIL.sub("", cleaned)
    cleaned = _strip_tool_call_objects(cleaned)
    # 围栏里若只剩空壳（```json 块被剥空）⇒ 连围栏一起收掉
    cleaned = re.sub(r"```[a-zA-Z]*\s*```", "", cleaned)
    # 剥完常留下连续空行/尾随空白（标记整段消失）⇒ 收敛，避免气泡里一大片空白
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
