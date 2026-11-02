"""`model_text.strip_model_markup` 的不变量回归（2026-10-15 · AI 泄漏治理）。

**这条测试就是"鲁棒"的落点**：把"用户可见文本不得含 DSML/工具调用碎片"写成可回归断言，
而不是靠人眼盯屏幕。样本取自真机现象与仓内登记的 DSML 教训
（`docs/交接_流式输出与对话UI_20261009.md:41/68`）。

两半同等重要：
  · **正向**：标记/工具调用 JSON 必须被剥掉（含**流式半截标记**）；
  · **反向**：正常回答里的**合法 JSON 必须原样保留**（用户问"给段 JSON"时不能吞）。
"""
from app.services.model_text import strip_model_markup

MARKERS = ("DSML", "dsml", "tool_calls", "tool_call", "function_call")


def _has_marker(text: str) -> bool:
    return any(m in text for m in MARKERS)


# ── 正向：剥干净 ─────────────────────────────────────────────────────────────


def test_strips_paired_dsml_block():
    """真机形态：自然语言 + DSML 包裹的工具调用（标记与其中的 JSON 都要消失）"""
    raw = (
        "好的，我看看你的记忆。"
        "<｜DSML｜tool_calls｜DSML｜>"
        '{"name":"search_memory","arguments":{"q":"湖边"}}'
        "</｜DSML｜>"
    )
    out = strip_model_markup(raw)
    assert out == "好的，我看看你的记忆。", repr(out)
    assert not _has_marker(out)


def test_strips_unterminated_dsml_tail():
    """**流式半截**：标记开了头还没闭合 ⇒ 整段丢弃（下一帧重剥，幂等）"""
    raw = "我查一下<｜DSML｜tool_calls"
    out = strip_model_markup(raw)
    assert out == "我查一下", repr(out)


def test_strips_inline_tool_call_json():
    raw = '正在检索{"tool_calls":[{"id":"1","function":{"name":"search","arguments":"{}"}}]}完毕'
    out = strip_model_markup(raw)
    assert out == "正在检索完毕", repr(out)
    assert not _has_marker(out)


def test_strips_name_arguments_object():
    raw = '{"name":"search_memory","arguments":{"q":"湖边"}}'
    assert strip_model_markup(raw) == ""


def test_strips_fenced_tool_call():
    raw = '看这里：\n```json\n{"tool_calls":[{"function":{"name":"x","arguments":"{}"}}]}\n```\n就这样'
    out = strip_model_markup(raw)
    assert not _has_marker(out)
    assert "看这里" in out and "就这样" in out


def test_is_idempotent():
    """可对累计文本反复调用（流式每帧都会重剥）"""
    raw = "A<｜DSML｜tool_calls｜DSML｜>{}"
    once = strip_model_markup(raw)
    assert strip_model_markup(once) == once


# ── 反向：不许误伤 ───────────────────────────────────────────────────────────


def test_keeps_plain_text_untouched():
    s = "今天和妈妈在湖边喝茶，她说了句「买牛乃」，其实是买牛奶。"
    assert strip_model_markup(s) == s


def test_keeps_legitimate_json():
    """用户明确要 JSON 示例时必须原样保留（保守性的核心断言）"""
    s = '给你一段示例：{"name": "小明", "age": 18}'
    assert strip_model_markup(s) == s


def test_keeps_json_with_args_key_but_no_name():
    """`arguments` 单独出现（无 name）不构成工具调用 ⇒ 保留"""
    s = '{"arguments": "这是普通字段"}'
    assert strip_model_markup(s) == s


def test_keeps_code_block_of_python():
    s = "```python\nprint('hi')\n```"
    assert strip_model_markup(s) == s
