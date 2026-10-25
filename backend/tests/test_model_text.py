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


# ── 2026-10-25 补：工具结果 JSON 泄漏（真机反馈"中间 json 格式的文本混进来，非常丑"）──
#
# 下面两条是**同一现象的两半**，缺一条就会让修复退化成"过度清洗"或"清洗不到位"：
#   · 真实工具结果（含 `success` / `_hint`）⇒ 必须整段消失；
#   · 用户主动要的 JSON 示例 ⇒ 必须一字节不动（上面 `test_keeps_legitimate_json` 已守，
#     这里再补一条"形状接近但不含工具键"的更紧的边界）。


def test_strips_real_tool_result_leak():
    """真机样本：模型把 save_knowledge_collection 的 ToolMessage 整串复述进正文。

    这段 JSON 形状**完全正常**（不是工具调用），旧的判据只认 `tool_calls`/`name+arguments`
    ⇒ 一字节没动（实测复现）。现在靠"含 `success` 或含 `_` 开头内部键"识别。
    """
    leak = (
        '{"success": true, "collection_id": 59, "message": "知识收藏已成功保存，ID: 59", '
        '"content_type": "文章", "title": "AI 与记忆", "topics": ["学习"], "mood": "平静", '
        '"related_collections": [], "related_memories": [], '
        '"_hint": "回复用户时展示：✅已收藏、📝标题……严禁展示content_format、collection_id。"}'
    )
    raw = f"好的，已经帮你收藏啦～\n{leak}\n还有什么想记录的吗？"
    out = strip_model_markup(raw)
    assert "_hint" not in out, repr(out)
    assert "collection_id" not in out, repr(out)
    assert "success" not in out, repr(out)
    # 正文两端的自然语言必须保留（不是把整段回复吞掉）
    assert "已经帮你收藏啦" in out, repr(out)
    assert "还有什么想记录的吗" in out, repr(out)


def test_strips_hint_when_it_survives_as_bare_word():
    """`_hint` 任意位置出现都要剥——包括模型只复述了键名、JSON 没成形的情形。"""
    raw = "已保存好了。_hint 回复时展示分类和情绪。"
    out = strip_model_markup(raw)
    assert "_hint" not in out, repr(out)
    assert "已保存好了" in out, repr(out)


def test_strips_fenced_real_tool_result():
    """```json 围栏里的工具结果同样整段消失（围栏变空壳后连围栏一起收）。"""
    raw = (
        "帮你存好了\n```json\n"
        '{"success": true, "memory_id": 12, "_hint": "严禁展示 memory_id"}\n'
        "```\n早点休息"
    )
    out = strip_model_markup(raw)
    assert "_hint" not in out, repr(out)
    assert "memory_id" not in out, repr(out)
    assert "帮你存好了" in out and "早点休息" in out, repr(out)


def test_keeps_user_json_without_tool_keys():
    """紧邻边界：用户要的 JSON 示例**不含**工具键 ⇒ 必须原样保留。

    这是防止"为了不泄漏就一律剥 JSON"过度清洗的第二道守护
    （第一道是 `test_keeps_legitimate_json`）。
    """
    s = '给你一段示例：{"name": "小明", "age": 18, "city": "杭州"}'
    assert strip_model_markup(s) == s


def test_keeps_json_with_underscore_only_in_value():
    """`_` 只出现在**值**里（不在键上）不构成内部键 ⇒ 保留。"""
    s = '{"note": "这是_user_id字段的说明"}'
    assert strip_model_markup(s) == s

