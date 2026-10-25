"""AI 页泄漏治理 · agent 侧回归（2026-10-25 · 真机反馈「中间的 json 格式的文本会混进来，非常丑」）。

三层治理里 agent 是**减少泄露源**的一层（后端 `model_text.strip_model_markup` 是最后一道闸）。
本文件守两条 agent 侧不变量：

  ① 三个高风险工具的返回值里**不再有 `_hint`** —— 它是模型复述进正文后
     用户看到的"英文提示词原文"的直接来源；
  ② `OutputValidator.FORBIDDEN_PATTERNS` 能识别「复述工具返回值」的两种形态，
     且**不误伤**用户主动要的 JSON 示例（与后端 `test_keeps_legitimate_json` 同一守则）。

为什么不测"模型一定不复述"：那是模型行为，不可单测。能守的是**我们可控的一半**——
不给它 `_hint`、并让校验器在它真复述时能拦住。
"""

import re

import pytest
from utils.output_validator import FORBIDDEN_PATTERNS, OutputValidator

# ── ① 工具返回值里不得再有 `_hint` ────────────────────────────────────────


def test_tool_result_shapes_have_no_internal_keys():
    """结构性扫尾：三个工具源码里不得再出现 `"_hint"` 这个字典键。

    为什么用**源码扫描**而不是逐个 mock 工具：
      · mock 整个 `save_memory` 需要伪造 6 个依赖（判重、镜像、关联查询…），
        副本越多越容易在下次重构后假绿——测试比被测代码还脆就没有价值；
      · 而"源码里没有这个键 ⇒ 返回值不可能带它"是**充分条件**，新增分支也自动覆盖；
      · 同时防止后人"顺手加回来"（这条会立刻红）。
    只查**代码行**（注释里允许出现历史说明，行首 `#` 跳过）。
    """
    from pathlib import Path

    src_path = Path(__file__).resolve().parents[1] / "src" / "tools" / "memory_tools.py"
    offenders = [
        line
        for line in src_path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#") and '"_hint"' in line
    ]
    assert offenders == [], f"源码里仍有 _hint 字典键: {offenders}"


# ── ② FORBIDDEN_PATTERNS 双向鉴别力 ───────────────────────────────────────


def _first_hit(text: str) -> str | None:
    for pattern, name in FORBIDDEN_PATTERNS:
        if re.search(pattern, text):
            return name
    return None


REAL_LEAK = (
    '好的已经收藏～ {"success": true, "collection_id": 59, "message": "知识收藏已成功保存", '
    '"content_type": "文章", "title": "AI 与记忆", "topics": ["学习"], "mood": "平静", '
    '"_hint": "回复用户时展示…"} 还有吗？'
)
TOOL_JSON_WITHOUT_HINT = (
    '{"success": true, "count": 2, "memories": [{"id": 1, "content": "x", "mood": "开心"}]}'
)


@pytest.mark.parametrize("leak", [REAL_LEAK, TOOL_JSON_WITHOUT_HINT])
def test_forbidden_patterns_catch_tool_result_echo(leak):
    """模型复述工具返回值时必须命中（这是真机那条漏到屏幕上的形态）。"""
    assert _first_hit(leak) is not None, "校验器漏掉了工具结果复述"


def test_forbidden_patterns_catch_bare_hint_word():
    """`_hint` 单独出现（JSON 没成形）也要命中。"""
    assert _first_hit("已保存好了。_hint 回复时展示分类。") is not None


@pytest.mark.parametrize(
    "legit",
    [
        '给你一段示例：\n{\n  "name": "小明",\n  "age": 18,\n  "city": "杭州"\n}',
        '{"name": "小明", "age": 18, "city": "杭州"}',
        "今天和妈妈在湖边喝茶，她说了句「买牛乃」，其实是买牛奶。",
    ],
)
def test_forbidden_patterns_do_not_flag_legitimate_content(legit):
    """**反向守护**：用户要的 JSON 示例/正常中文不得被误判（与后端同守则）。"""
    assert _first_hit(legit) is None, f"误伤了合法内容: {_first_hit(legit)}"


def test_forbidden_patterns_catch_hint_variant_that_old_blacklist_missed():
    """**这是本批最关键的用例**：`search_memories` 那个 `_hint` 变体不含
    `memory_id`/`collection_id` 字样 ⇒ 旧黑名单**完全命中不了**。

    真机那条漏出去的正是这种形态。旧判据下它是"干净文本"，一路到屏幕上。
    """
    hint_only = (
        '{"success": true, "count": 1, "memories": [{"id": 1, "content": "湖边喝茶"}],'
        ' "_hint": "若某条记忆的 source 是 app_contents，提及来源时说你在 App 里记录过。"}'
    )
    # 前提确认：这段文本里确实没有旧判据的触发词
    assert "memory_id" not in hint_only and "collection_id" not in hint_only
    hit = _first_hit(hint_only)
    assert hit is not None, "旧黑名单漏网形态未被新判据抓住"
    assert "_hint" in hit, hit


def test_validator_end_to_end_flags_tool_echo():
    """整条 `validate()` 走一遍：复述工具结果 ⇒ 不通过。

    ⚠️ 不断言"由哪一条规则命中"：同一段脏文本往往同时触发多条（ID + `_hint` + 英文），
    断言具体某条会让测试随规则顺序变化而假红。这里只断言**结论**（拦住了），
    具体判据的鉴别力由上面 `_first_hit` 系列逐条守。
    """
    ok, issues = OutputValidator().validate(REAL_LEAK)
    assert not ok, "复述工具结果的回复被判为通过"
    assert issues, "未通过却没有给出理由"


def test_validator_end_to_end_passes_clean_reply():
    """干净回复仍通过（确认新增模式没把正常路径弄红）。"""
    ok, issues = OutputValidator().validate("✅ 已保存\n📝 内容：今天在湖边喝茶")
    assert ok, issues
