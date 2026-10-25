"""
忆光 Agent 输出质量校验器

检查 Agent 回复是否符合输出标准，不通过则反馈问题供重试。
校验项：
1. 格式合规性 - 禁展示内容形式/记忆ID
2. 分类合法性 - 在枚举范围内
3. 重复工具调用 - 同一工具不重复调用存同一条记忆
4. 能力边界 - 禁止输出代码块/SQL语句/编程内容
5. 语言限制 - 必须使用中文回复，禁止使用英文
"""

import json
import logging
import os
import re
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage

logger = logging.getLogger(__name__)

# 重试所需的最小剩余预算（秒）：低于它就不值得再跑一轮——
# 一轮最少也要几秒，用户宁可拿到不完美但及时的回复。
_MIN_RETRY_BUDGET_S = 5.0

# 不可重试的判定关键字（命中即判定为「副作用已发生，重跑无意义」）。
# 2026-10-04：原来「重复调用 save_memory」会触发**整条 agent 重跑**（含工具链、
# 写库、数十秒），而记忆**第一次就已经写进去了** —— 重试纯属浪费并放大副作用。
_TERMINAL_MARKERS = ("重复调用", "保存相同内容", "请勿重复保存")


def _classify_issues(issues: str) -> tuple[bool, str]:
    """把校验失败原因分级：可重试（纯输出形态）/ 不可重试（副作用已发生）。

    返回 `(retryable, reason)`；`reason` 为短代码，用于日志与 `_diag.status`。
    """
    text = issues or ""
    for marker in _TERMINAL_MARKERS:
        if marker in text:
            return False, "duplicate_tool_call"
    return True, "output_shape"

# 禁展示字段正则模式（情绪和主题现在允许展示，仅禁止内容形式和ID）
#
# 2026-10-25 补三条（真机反馈「中间的 json 格式的文本会混进来，非常丑」）：
#   ① `_hint` —— 工具结果里的**内部提示词键**。模型复述工具返回时会连它一起复述，
#      用户屏幕上就出现英文提示词原文。它**永远**不该出现在回复里（不是"尽量别"）。
#      ⚠️ 这条与 ② 的区别：`memory_id`/`collection_id` 原来只在**恰好写出字段名**时命中，
#      而真机那段的 `_hint` 变体（search_memories 的）**压根不含这两个词** ⇒ 连黑名单
#      都命中不了，这正是它一路漏到屏幕上的原因之一。
#   ② 整段工具结果 JSON —— 连续的 `{`/`"`/`:` 结构出现在正文里就是复述了工具返回值。
#      判据取「同一行内 ≥4 组 `关键":"` 」而不是"含花括号"：用户主动要的 JSON 示例
#      通常是**整齐多行/短小**的，而工具结果是**一整行压平的长 JSON**（几十个键值对）。
FORBIDDEN_PATTERNS = [
    (r"content_format|内容形式[：:]", "内容形式(content_format)"),
    (r"记忆ID[：:]|收藏ID[：:]|memory_id|collection_id", "记忆/收藏ID"),
    (r"_hint", "内部提示词键(_hint)"),
    # 一行里 ≥4 个 `"key":` —— 工具结果复述的典型形态（正常回复里不会出现）。
    # 用 `\"` 转义写法而不是裸引号，避免与相邻的字面量引号混淆。
    (r'"[A-Za-z_][A-Za-z0-9_]*"\s*:\s*(?:"[^"\n]*"|\[|\{|true|false|null|\d)'
     r'(?:[^\n]*?"[A-Za-z_][A-Za-z0-9_]*"\s*:\s*){3,}', "工具返回值 JSON（大段键值对）"),
]

# 合法分类枚举（L1-A 记忆分类 + L1-B 收藏分类）
VALID_CATEGORIES = {
    "情绪", "事件", "想法", "灵感", "人物", "地点", "待分类",
    "文章", "帖子", "观点", "数据", "图片", "其他",
}

# 能力边界：检测代码块和SQL语句
CODE_BLOCK_PATTERN = re.compile(r"```\s*\w*", re.IGNORECASE)
SQL_PATTERN = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE|CREATE\s+DATABASE)\b",
    re.IGNORECASE,
)

# 语言限制：检测连续英文单词（3个及以上英文字母组成的序列）
ENGLISH_WORD_PATTERN = re.compile(r"[a-zA-Z]{3,}")
# 允许的英文白名单（工具枚举值、常见缩写等）
ENGLISH_WHITELIST = {
    "text", "image", "voice", "link",
    "app", "bot", "api", "url", "pdf", "doc", "csv",
    "http", "https", "www", "com", "org", "net",
}


class OutputValidator:
    """输出质量校验器"""

    def validate(
        self,
        ai_reply: str,
        messages: list[AnyMessage] | None = None,
    ) -> tuple[bool, str]:
        """
        校验 Agent 输出质量

        :param ai_reply: AI 的最终文本回复
        :param messages: 完整消息列表（用于检查重复工具调用）
        :return: (passed, issues) 通过返回 (True, "")，不通过返回 (False, 具体问题描述)
        """
        issues: list[str] = []

        # 1. 格式合规性检查
        fmt_issue = self._check_format_compliance(ai_reply)
        if fmt_issue:
            issues.append(fmt_issue)

        # 2. 分类合法性检查
        cat_issue = self._check_category(ai_reply)
        if cat_issue:
            issues.append(cat_issue)

        # 3. 重复工具调用检查
        if messages:
            dup_issue = self._check_duplicate_tool_calls(messages)
            if dup_issue:
                issues.append(dup_issue)

        # 4. 能力边界检查（禁止输出代码/SQL）
        boundary_issue = self._check_capability_boundary(ai_reply)
        if boundary_issue:
            issues.append(boundary_issue)

        # 5. 语言限制检查（禁止使用英文回复）
        lang_issue = self._check_language(ai_reply)
        if lang_issue:
            issues.append(lang_issue)

        if issues:
            issue_text = "；".join(issues)
            logger.warning(f"输出校验未通过: {issue_text}")
            return False, issue_text

        logger.info("输出校验通过")
        return True, ""

    def _check_format_compliance(self, text: str) -> str:
        """检查回复中是否包含禁展示字段"""
        for pattern, field_name in FORBIDDEN_PATTERNS:
            if re.search(pattern, text):
                return f"回复中不应展示{field_name}，请移除该字段"
        return ""

    def _check_category(self, text: str) -> str:
        """检查分类是否在合法枚举范围内"""
        # 匹配 "分类：xxx" 或 "分类: xxx"
        match = re.search(r"分类[：:]\s*(\S+)", text)
        if not match:
            return ""  # 没有分类不强制报错（可能是搜索/闲聊场景）

        category = match.group(1).strip()
        # 去掉可能的 | 分隔符
        category = category.split("|")[0].strip()

        if category not in VALID_CATEGORIES:
            return f"分类「{category}」不在合法枚举范围内"
        return ""

    def _check_duplicate_tool_calls(self, messages: list[AnyMessage]) -> str:
        """检查是否重复调用了同一工具存同一条内容"""
        # 收集所有 save_memory 和 save_knowledge_collection 的调用
        save_calls: list[str] = []
        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "")
                    if name in ("save_memory", "save_knowledge_collection"):
                        # 提取 content 参数作为唯一标识
                        args = tc.get("args", {})
                        content = args.get("content", "")
                        if content:
                            call_key = f"{name}:{content[:50]}"
                            if call_key in save_calls:
                                return f"重复调用了 {name} 保存相同内容，请勿重复保存"
                            save_calls.append(call_key)
        return ""

    def _check_capability_boundary(self, text: str) -> str:
        """检查是否输出了代码块/SQL/编程内容，超出能力边界"""
        # 检测代码块（```python, ```sql 等）
        if CODE_BLOCK_PATTERN.search(text):
            return (
                "回复中包含代码块，超出能力边界。"
                "你是记忆助手，不是编程工具。"
                "请拒绝此类请求，引导用户回到记忆/收藏功能。"
            )
        # 检测SQL语句
        if SQL_PATTERN.search(text):
            return (
                "回复中包含SQL语句，超出能力边界。"
                "你是记忆助手，不是数据库工具。"
                "请拒绝此类请求，引导用户回到记忆/收藏功能。"
            )
        return ""

    def _check_language(self, text: str) -> str:
        """检查回复中是否使用了英文（违反中文限制）"""
        # 移除 URL 中的英文（避免误报）
        text_without_urls = re.sub(r"https?://\S+", "", text)
        # 移除白名单中的英文单词
        words = ENGLISH_WORD_PATTERN.findall(text_without_urls.lower())
        non_whitelisted = [w for w in words if w not in ENGLISH_WHITELIST]
        if non_whitelisted:
            return (
                "回复中包含英文内容，违反语言限制。"
                "必须使用中文回复，禁止使用英文。"
                "请用中文重新组织回复。"
            )
        return ""


def _salvage_partial_reply(agent, config) -> tuple[str, list] | tuple[None, None]:
    """从 checkpoint 里捞回「最后一条有文本的 AI 回复」。

    撞 `recursion_limit` 时对话通常已经产生了可用回复（工具链跑了几轮），
    直接把异常抛出去等于把这段回复与已完成的副作用一起丢掉。用它兜底，
    取不到就返回 `(None, None)` 由调用方决定重抛。
    """
    try:
        state = agent.get_state(config)
    except Exception:  # noqa: BLE001 —— 取不到就交还调用方重抛
        return None, None
    messages = list((state.values or {}).get("messages") or [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = msg.content if isinstance(msg.content, str) else ""
            if text.strip():
                return text, messages
    return None, None


def run_agent_with_validation(
    agent,
    query: str,
    config: dict,
    max_rounds: int | None = None,
) -> dict:
    """
    带输出校验的 Agent 运行器

    流程：
    1. 调用 Agent 生成回复
    2. 校验器检查输出质量
    3. 不通过 → 带着反馈重新生成（最多 max_rounds 轮）
    4. 通过或达到上限 → 返回结果

    :param agent: LangChain Agent 实例
    :param query: 用户输入
    :param config: Agent 配置（含 thread_id 等）
    :param max_rounds: 最大重试轮数；None = 读 `AGENT_MAX_ROUNDS`（默认 2，
        环境变量可覆盖回旧行为设 3）
    :return: Agent 最终执行结果（附带 `_diag` 诊断字段，供上层埋点与耗时归因）
    """
    import time

    from langchain_core.messages import HumanMessage
    from langgraph.errors import GraphRecursionError
    from yishu.observability import log_event, timed

    if max_rounds is None:
        max_rounds = _env_int("AGENT_MAX_ROUNDS", 2)
    # 夹紧到 >= 1（2026-10-05 修复）：`for round_num in range(1, max_rounds + 1)`
    # 在 max_rounds<=0 时**一次都不执行**，循环尾的 `return _finish(result, ...)`
    # 于是引用从未绑定的 `result` ⇒ `UnboundLocalError` ⇒ 整个请求 500。
    # 实测复现：`AGENT_MAX_ROUNDS=0 python -c "run_agent_with_validation(...)"`
    # → UnboundLocalError: cannot access local variable 'result'。
    # 配错一个环境变量就让对话全挂，且日志里只有一行看不懂的 UnboundLocalError，
    # 比"少校验一轮"糟糕得多；夹紧后最坏也只是回到单轮（既有默认行为）。
    try:
        max_rounds = int(max_rounds)
    except (TypeError, ValueError):
        max_rounds = 2
    if max_rounds < 1:
        log_event(
            "validator_rounds_clamped",
            level=logging.WARNING,
            requested_rounds=max_rounds,
            applied_rounds=1,
        )
        max_rounds = 1

    validator = OutputValidator()
    feedback = None
    started = time.perf_counter()
    # 延迟预算（2026-10-04）：宁可返回不完美文本，也不让用户干等。
    # 原实现无预算，一轮不理想就可能再等 30~60 秒。
    try:
        budget_s = float(os.getenv("AGENT_VALIDATOR_BUDGET_S", "20"))
    except (TypeError, ValueError):
        budget_s = 20.0
    # 诊断计数：用于回答"时间花在哪"（LLM 调了几次 / 工具调了几次 / 校验重跑几轮）
    llm_calls = 0
    tool_calls = 0
    rounds_used = 0
    tool_dist: dict[str, int] = {}
    # 已见消息数：用于只统计本轮新增（checkpointer 会把历史消息一并带回）
    usage_total = {"tokens_in": 0, "tokens_out": 0, "tokens_total": 0}
    exhausted_reason = ""

    def _finish(result: dict, status: str, issues: str | None = None) -> dict:
        result["_diag"] = {
            "rounds": rounds_used,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "tool_dist": dict(tool_dist),
            "validator_rounds": max(rounds_used - 1, 0),
            "agent_ms": int((time.perf_counter() - started) * 1000),
            "usage": dict(usage_total),
            "status": status,
        }
        # 结构化附加（cards/chips）：与流式版**同一实现**（build_reply_meta），两端形态必须一致
        result["_meta"] = build_reply_meta(result.get("messages") or [])
        return result

    for round_num in range(1, max_rounds + 1):
        rounds_used = round_num
        # 重试轮换用**无工具 agent**：目的只是让 LLM 按反馈重写回复文本。
        # 带工具重试 = 再跑一遍完整工具链（实测又调 16~28 次工具、写库副作用、
        # 数十秒）。无工具后成本降到「一次纯文本生成」，副作用归零。
        use_tools = round_num == 1
        if use_tools:
            current_agent = agent
        else:
            try:
                from agents.agent import build_agent_no_tools

                current_agent = build_agent_no_tools()
            except Exception as exc:  # noqa: BLE001 —— 拿不到就退回带工具 agent
                log_event(
                    "no_tools_agent_unavailable",
                    level=logging.WARNING,
                    exc_type=type(exc).__name__,
                )
                current_agent = agent

        log_event(
            "validator_round_start",
            round=round_num,
            max_rounds=max_rounds,
            has_feedback=feedback is not None,
            tools_enabled=use_tools,
        )

        # 构造输入：第2轮起附带校验反馈
        if feedback:
            input_content = (
                f"{query}\n\n"
                f"[校验反馈] 上一次回复存在以下问题：{feedback}。"
                f"请修正这些问题后重新回复用户，不要提及校验过程。"
            )
        else:
            input_content = query

        # 调用 Agent（耗时/token/工具数在此归因）
        try:
            with timed("agent_invoke", log_ok=False, round=round_num) as t:
                result = current_agent.invoke(
                    {"messages": [HumanMessage(content=input_content)]},
                    config,
                )
        except GraphRecursionError as exc:
            # 兜底而不是让它冒成 500（2026-10-05）：撞 recursion_limit 时对话里
            # **通常已经有可用的 AI 回复**（工具链跑了几轮，回复在最后一条
            # AIMessage 里）。直接抛异常等于把这段回复和已完成的副作用一起丢掉，
            # 用户侧表现为「一句话反复重发都是同一个错」。
            # 这里取回最后一条有文本的 AIMessage 作为回复，并把「本轮未正常收尾」
            # 显式记进结构化日志——宁可返回不完整的结果，也不让整轮对话报废。
            log_event(
                "agent_recursion_exhausted",
                level=logging.WARNING,
                round=round_num,
                exc_detail=str(exc)[:120],
            )
            salvaged = _salvage_partial_reply(current_agent, config)
            if salvaged and salvaged[0]:
                reply_text, messages = salvaged
                log_event("agent_partial_reply_salvaged", round=round_num, reply_len=len(reply_text))
                return _finish({"messages": messages}, "recursion_salvaged")
            raise
        llm_calls += 1
        _accumulate_usage(usage_total, result)
        round_messages = result.get("messages", [])
        # ⚠️ 只统计**本轮新增**的消息：`result["messages"]` 是累积的
        # （checkpointer 会把历史一起带回）。2026-10-04 连踩两次：
        #   ① 跨轮累加 ⇒ 只存 1 条记忆却报 tool_calls=40；
        #   ② 用 prev_msg_count（从 0 起）⇒ 同一 thread 第二次请求时
        #      把 checkpointer 里的历史全算进来（报 24 次，大部分是历史）。
        # 正确切分点 = **本轮最后一条 human 消息**之后的所有消息。
        new_messages = _messages_since_last_human(round_messages)
        round_tool_calls = _count_tool_messages(new_messages)
        tool_calls += round_tool_calls
        for tool_name, n in tool_call_distribution(new_messages).items():
            tool_dist[tool_name] = tool_dist.get(tool_name, 0) + n
        log_event(
            "agent_invoke_done",
            **{k: v for k, v in t.fields.items() if k != "stage"},
            round_tool_calls=round_tool_calls,
            tool_calls=tool_calls,
            tool_dist=tool_dist,
            msg_total=len(round_messages),
        )

        # 获取最后一条 AI 消息
        messages = round_messages
        if not messages:
            log_event("validator_empty_messages", level=logging.WARNING, round=round_num)
            logger.warning("Agent 返回空消息")
            return _finish(result, "empty_messages")

        ai_reply = ""
        last_msg = messages[-1]
        if hasattr(last_msg, "content"):
            ai_reply = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)

        # 校验
        passed, issues = validator.validate(ai_reply, messages)

        if passed:
            log_event("validator_pass", round=round_num, reply_len=len(ai_reply))
            return _finish(result, "passed")

        feedback = issues
        # 只记长度与首条原因片段，不记回复正文
        elapsed_s = time.perf_counter() - started
        budget_left = budget_s - elapsed_s
        # 分级：只有「纯输出形态问题」才值得重试；**重复工具调用不可重试**
        # （记忆已写入，重跑只会再写一次并再次失败——实测这正是 30~60s 的来源）。
        retryable, reason = _classify_issues(issues)
        will_retry = (
            round_num < max_rounds and retryable and budget_left > _MIN_RETRY_BUDGET_S
        )
        log_event(
            "validator_fail",
            level=logging.WARNING,
            round=round_num,
            issues=issues[:120],
            issues_len=len(issues),
            reply_len=len(ai_reply),
            retryable=retryable,
            reason=reason,
            elapsed_s=int(elapsed_s),
            budget_left_s=int(budget_left),
            will_retry=will_retry,
        )
        logger.warning(f"第{round_num}轮校验未通过: {issues}")
        if not will_retry:
            if not retryable:
                exhausted_reason = f"terminal:{reason}"
            elif budget_left <= _MIN_RETRY_BUDGET_S:
                exhausted_reason = "budget_exhausted"
            else:
                exhausted_reason = "max_rounds"
            break

    # 达到上限/预算/不可重试，返回最后一次结果
    log_event("validator_exhausted", level=logging.WARNING, max_rounds=max_rounds,
              reason=exhausted_reason or "max_rounds")
    logger.warning(
        "校验未通过但停止重试（%s），输出最后一次结果", exhausted_reason or "max_rounds"
    )
    return _finish(result, "rounds_exhausted" if not exhausted_reason else exhausted_reason)


def _env_int(key: str, default: int) -> int:
    import os

    try:
        return int(os.getenv(key, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _messages_since_last_human(messages: list) -> list:
    """返回「本轮」的消息切片：从**最后一条 human 消息**之后到末尾。

    为什么不用长度差：checkpointer 会把历史一起带回，而一次 invoke 之前
    我们拿不到历史的准确长度（也不该去猜）。最后一条 human 消息一定是本轮
    入口（`agent.invoke({"messages": [HumanMessage(...)]})`），
    它之后的全部消息都属本轮——这是唯一可靠的切分点。
    """
    if not messages:
        return []
    last_human = -1
    for idx, m in enumerate(messages):
        if getattr(m, "type", "") == "human":
            last_human = idx
    return messages[last_human + 1:] if last_human >= 0 else list(messages)


def _count_tool_messages(messages: list) -> int:
    """数一下本轮真的发生了多少次工具调用（用于耗时归因）。"""
    count = 0
    for m in messages or []:
        if getattr(m, "type", "") == "tool":
            count += 1
        elif getattr(m, "tool_calls", None):
            count += len(m.tool_calls)
    return count


def tool_call_distribution(messages: list) -> dict[str, int]:
    """按工具名统计调用次数。

    2026-10-04 实测发现单轮对话有 **40 次**工具调用（远超 13 个工具的预期），
    是 27s 延迟的主因。光看总数不知道是哪个工具在循环，必须按名字拆开。
    """
    dist: dict[str, int] = {}
    for m in messages or []:
        name = getattr(m, "name", None)
        if getattr(m, "type", "") == "tool" and isinstance(name, str):
            dist[name] = dist.get(name, 0) + 1
        for tc in getattr(m, "tool_calls", None) or []:
            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if isinstance(tc_name, str):
                dist[tc_name] = dist.get(tc_name, 0) + 1
    return dist


def _accumulate_usage(total: dict, result: dict) -> None:
    """把本轮 token 用量累加进总量（只累加数量）。"""
    from yishu.observability import extract_usage

    for m in (result.get("messages") or [])[::-1]:
        if getattr(m, "type", "") == "ai":
            for k, v in extract_usage(m).items():
                total[k] = total.get(k, 0) + v
            return


# ═══════════════════════════════════════════════════════════════════════════════
# 流式运行器（AG6 余项 · 2026-10-09）
#
# 与同步版 `run_agent_with_validation` 的关系：**同一套校验口径**（同一 OutputValidator、
# 同一 `_classify_issues` 分级、同一预算/轮数夹紧），差别只在「第 1 轮用 `.stream()` 边出边推」。
#
# 为什么**必须**复用同一套口径而不是另写一份：同步版的两条不可退让行为
#   ① 重复工具调用**不可重试**（记忆已写入，重跑只会再写一次）；
#   ② 预算耗尽不再重试（宁可返回不完美文本，也不让用户干等）
# 是踩过坑得来的（见本文件上方注释）。流式版若自建口径，两条都会静默失效。
#
# ⚠️ 与同步版的一个**刻意差异**：校验发生在**流之后**。也就是说预览可能先于校验推送
# 出去（被拦文本的"泄漏窗口"）。三道处置（缺一不可）：
#   · 上游（backend）对预览串做**规则层**门控（screen_generated(deep=False)），
#     规则层命中即中断流——已推送的前缀必然是过了规则层的；
#   · 客户端预览**带"生成中"水印**，终稿事件到达后**整体替换**（不是追加）；
#   · 终稿仍走**完整链路**（规则层 + LLM 级）护栏，未过审 ⇒ 不落库、不返回（CHAT_004）。
# ═══════════════════════════════════════════════════════════════════════════════


def _chunk_text(chunk: Any) -> str:
    """取流式分片的**正文**文本（跳过 tool_call 空分片与思维链）。

    为什么不取 `additional_kwargs`：百炼/DeepSeek 的思维链落在
    `additional_kwargs["reasoning_content"]`（部分版本叫 `thinking`）——它是模型的内部
    推理过程，**不得**进用户预览：既是隐私面（可能复述记忆正文），也是体验噪音
    （用户看到大段自我盘算）。只推 `content`。
    """
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 多模态内容块：只拼 text 块
        parts = [b.get("text", "") for b in content if isinstance(b, dict)]
        return "".join(p for p in parts if p)
    return ""


def _tool_events_from_update(payload: Any) -> list[dict[str, str]]:
    """从 `stream_mode="updates"` 的更新块里抽工具状态事件（started/done）。

    形态：`{node_name: {"messages": [AIMessage(tool_calls=[...]) | ToolMessage]}}`。
    **不假设节点名**（"model"/"tools"/"agent" 随 langchain 版本变），只认消息本身——
    节点名是内部实现，靠它做判断等于把版本升级变成故障。
    """
    events: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return events
    for _node, update in payload.items():
        if not isinstance(update, dict):
            continue
        msgs = update.get("messages")
        if not isinstance(msgs, (list, tuple)):
            continue
        for msg in msgs:
            for tc in getattr(msg, "tool_calls", None) or []:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                events.append({"phase": "started", "name": str(name or "")})
            if getattr(msg, "type", "") == "tool":
                events.append({"phase": "done", "name": str(getattr(msg, "name", "") or "")})
    return events


def _last_ai_text(messages: list) -> str:
    """取最后一条**有正文**的 AI 消息（同 `_salvage_partial_reply` 的取值口径）。"""
    for msg in (messages or [])[::-1]:
        if isinstance(msg, AIMessage):
            text = msg.content if isinstance(msg.content, str) else ""
            if text.strip():
                return text
    return ""


# 卡片标题最大字数（超出截断加省略号）。20~24 字是手机卡片一行半的容量，
# 再长会把卡片撑成两行、横滑组看起来像列表；完整正文仍在 chat_messages.content 里（不丢）。
_MAX_CARD_TITLE = 22


def _clip(text: str, limit: int = _MAX_CARD_TITLE) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


def _tool_json(content: object) -> dict:
    """ToolMessage 内容是 JSON 字符串时解析为 dict（否则 `{}`）。

    `save_memory` 成功分支返回 `json.dumps({success, memory_id, category, mood, topics, …})`
    （见 tools/memory_tools.py）；异常兜底路径返回的是中文提示句 ⇒ 解析失败即"不是结构化结果"。
    """
    if not isinstance(content, str):
        return {}
    text = content.strip()
    if not text.startswith("{"):
        return {}
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _collect_sources(round_messages: list) -> list[dict]:
    """从 `save_memory` / `search_memories` 的 ToolMessage **结构化结果**收集引用来源。

    2026-10-17（引用回溯 · "来自 N 条记忆"尾注）：只认工具结果 JSON 的 id 字段，
    **不解析回复文本**——文本是 LLM 的自由措辞，抽它随模型换句而静默失效
    （与 cards 同一哲学，backend `_reply_payload` 同口径）。条目形状对齐
    backend 透传契约：`{"content_id", "kind", "origin"}`——client 只取
    `content_id` 数数量，id 预留给"跳回原文"。

    id 空间（必须区分，否则"跳回原文"会跳到别人的空间）：
      · `origin="app"`   —— App contents 的 UUID（save 镜像回传 / search 的
        `source=app_contents` 命中），App 侧可解析；
      · `origin="agent"` —— agent `memories` 表的自增 id（镜像降级时的退回值），
        只在 agent 库内可解析。

    已知边界（诚实标注）：同一记忆"先 save（拿到 app UUID）再被 search 本地命中
    （agent id）"会产出两条不同 id 的条目——两套 id 之间没有映射字段，宁可
    多报也不猜。
    """
    sources: list[dict] = []
    seen: set[str] = set()

    def _push(content_id: Any, *, origin: str) -> None:
        if content_id is None or not str(content_id).strip():
            return
        key = f"{origin}:{content_id}"
        if key in seen:
            return
        seen.add(key)
        # 只有 id 与空间标记：绝不带 content 正文（隐私纪律——工具结果里的
        # 记忆原文不许进给客户端的结构化数据）。
        sources.append({"content_id": str(content_id), "kind": "memory", "origin": origin})

    for msg in round_messages or []:
        if getattr(msg, "type", "") != "tool":
            continue
        data = _tool_json(getattr(msg, "content", ""))
        if not data:
            continue  # 非 JSON（异常兜底文案）⇒ 没有结构化真相，跳过
        name = getattr(msg, "name", "")
        if name == "save_memory":
            if not data.get("success"):
                continue  # 保存失败 ⇒ 该行不存在，不能当来源（宁缺勿假）
            # duplicate=True 也计入：回复确实引用了那条既有记忆。
            if data.get("content_id"):
                _push(data.get("content_id"), origin="app")
            else:
                _push(data.get("memory_id"), origin="agent")
        elif name == "search_memories":
            for m in data.get("memories") or []:
                if not isinstance(m, dict):
                    continue
                if m.get("source") == "app_contents":
                    _push(m.get("id"), origin="app")
                else:
                    _push(m.get("id"), origin="agent")
    return sources


def build_reply_meta(messages: list) -> dict:
    """从**本轮消息**里抽结构化附加（`cards` / `chips` / `sources`）——不猜提示词协议、不解析自然语言。

    数据来源是 `save_memory` 的 ToolMessage（机器可读的成功结果）。为什么从**工具结果**取、
    而不是从回复文本里正则抽：
      · 文本是给 LLM 复述用的自由格式，抽取必然脆弱（模型换一句措辞就崩）；
      · 工具结果才是"到底存进去了没"的真相——文本写着"已保存"而工具其实是
        `duplicate`/失败，正是本项目最忌讳的假状态（宁可不出卡，也不出一张假卡）。
    故：`success != True` 或 `duplicate == True` 的调用**一律不产卡**。

    ⚠️ 已知边界：校验重试轮用的是**无工具** agent，若第 1 轮已保存、第 2 轮只重写文本，
    则切片里看不到那次 `save_memory` ⇒ 该轮不出卡（宁缺勿假；卡片的正文仍在 content 里）。
    """
    round_messages = _messages_since_last_human(messages)
    args_by_call: dict[str, dict] = {}
    for msg in round_messages or []:
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", None) or []:
                if isinstance(tc, dict) and tc.get("name") == "save_memory":
                    call_args = tc.get("args")
                    args_by_call[str(tc.get("id") or "")] = call_args if isinstance(call_args, dict) else {}

    cards: list[dict] = []
    seen: set[str] = set()
    for msg in round_messages or []:
        if getattr(msg, "type", "") != "tool":
            continue
        if getattr(msg, "name", "") != "save_memory":
            continue
        data = _tool_json(getattr(msg, "content", ""))
        if not data or not data.get("success") or data.get("duplicate"):
            continue
        args = args_by_call.get(str(getattr(msg, "tool_call_id", "") or "")) or {}
        content = str(args.get("content") or data.get("content") or "")
        category = str(data.get("category") or args.get("category") or "")
        mood = str(data.get("mood") or args.get("mood") or "")
        raw_topics = data.get("topics") or args.get("topics") or []
        if isinstance(raw_topics, str):
            raw_topics = [raw_topics]
        topics = [str(t) for t in raw_topics if str(t).strip()] if isinstance(raw_topics, list) else []
        title = _clip(content)
        meta_parts = [p for p in (category, mood, topics[0] if topics else "") if p]
        # 去重键用**展示字段**（不带 memory_id：id 属内部标识，不该出现在给客户端的结构化数据里）
        key = f"{title}|{' · '.join(meta_parts)}"
        if key in seen:
            continue
        seen.add(key)
        cards.append(
            {
                "title": title,
                "meta": " · ".join(meta_parts),
                "img": "",  # 文字记忆没有缩略图：留空串，客户端按空串跳过图片（不猜占位图）
                "category": category,
                "mood": mood,
                "topics": topics,
            }
        )

    chips: list[str] = []
    if cards:
        # 追问建议**由卡片本身派生**（而不是写死两句客套话）：用户刚存了「旅行」主题，
        # 最可能的下一步就是"看看这个主题还有什么"。
        first_topics = cards[0].get("topics") or []
        chips.append(f"看看「{first_topics[0]}」相关的记忆" if first_topics else "看看相关的记忆")
        chips.append("再帮我记一条")
    return {"cards": cards, "chips": chips, "sources": _collect_sources(round_messages)}


def _resolve_max_rounds(max_rounds: int | None) -> int:
    """轮数夹紧（口径同同步版：`AGENT_MAX_ROUNDS` 默认 2，且夹到 >= 1）。"""
    if max_rounds is None:
        max_rounds = _env_int("AGENT_MAX_ROUNDS", 2)
    try:
        max_rounds = int(max_rounds)
    except (TypeError, ValueError):
        max_rounds = 2
    return max(max_rounds, 1)


def _resolve_budget_s() -> float:
    try:
        return float(os.getenv("AGENT_VALIDATOR_BUDGET_S", "20"))
    except (TypeError, ValueError):
        return 20.0


def stream_agent_with_validation(
    agent,
    query: str,
    config: dict,
    max_rounds: int | None = None,
):
    """带输出校验的**流式** Agent 运行器（生成器）。

    产出的事件（`(kind, payload)`），上游 `main.py` 逐条翻成 SSE 帧：
      · `("delta", str)`  —— 第 1 轮的 token 增量（预览用，**不是**终稿）
      · `("tool", {"phase": "started"|"done", "name": str})` —— 工具状态（供 UI 显示"在翻记忆"）
      · `("replace", str)` —— 校验重试后的**整段替换**文本（预览作废，客户端须整体覆盖）
      · `("final", {...})` —— 终稿结果（含 `_diag`，形状与同步版一致，可直接喂 `_normalize_reply`）

    ⚠️ 事件顺序契约：`final` **最后且仅一条**；`replace` 只可能出现在 `final` 之前。
    """
    import time

    from langchain_core.messages import HumanMessage
    from langgraph.errors import GraphRecursionError
    from yishu.observability import log_event

    max_rounds = _resolve_max_rounds(max_rounds)
    budget_s = _resolve_budget_s()
    validator = OutputValidator()
    started = time.perf_counter()
    stats: dict[str, Any] = {
        "llm_calls": 0,
        "tool_calls": 0,
        "tool_dist": {},
        "usage": {"tokens_in": 0, "tokens_out": 0, "tokens_total": 0},
    }
    rounds_used = 1

    def _accumulate_round(messages: list) -> None:
        """按**本轮**切片累加诊断（切分口径同同步版：最后一条 human 消息之后）。"""
        new_messages = _messages_since_last_human(messages)
        stats["llm_calls"] += 1
        stats["tool_calls"] += _count_tool_messages(new_messages)
        for name, n in tool_call_distribution(new_messages).items():
            stats["tool_dist"][name] = stats["tool_dist"].get(name, 0) + n
        _accumulate_usage(stats["usage"], {"messages": new_messages})

    def _finish(messages: list, status: str) -> dict:
        return {
            "messages": messages,
            # 结构化附加（cards/chips）：与非流式版同一实现，两端形态必须一致
            "_meta": build_reply_meta(messages),
            "_diag": {
                "rounds": rounds_used,
                "llm_calls": stats["llm_calls"],
                "tool_calls": stats["tool_calls"],
                "tool_dist": dict(stats["tool_dist"]),
                "validator_rounds": max(rounds_used - 1, 0),
                "agent_ms": int((time.perf_counter() - started) * 1000),
                "usage": dict(stats["usage"]),
                "status": status,
                "streamed": True,
            },
        }

    log_event("stream_round_start", round=1, max_rounds=max_rounds)
    streamed_len = 0
    try:
        # `stream_mode` 传列表 ⇒ 逐条产出 `(mode, payload)`：
        #   messages → (message_chunk, metadata)：token 增量
        #   updates  → {node: state_update}：工具起止（messages 模式看不到 ToolMessage）
        for mode, payload in agent.stream(
            {"messages": [HumanMessage(content=query)]},
            config,
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                chunk, _meta = payload
                text = _chunk_text(chunk)
                if text:
                    streamed_len += len(text)
                    yield ("delta", text)
                continue
            for event in _tool_events_from_update(payload):
                yield ("tool", event)
    except GraphRecursionError as exc:
        # 兜底口径同同步版：撞递归上限时对话里通常已有可用回复，取回它而不是抛 500
        log_event(
            "stream_recursion_exhausted",
            level=logging.WARNING,
            round=1,
            exc_detail=str(exc)[:120],
        )
        salvaged = _salvage_partial_reply(agent, config)
        if salvaged and salvaged[0]:
            reply_text, messages = salvaged
            log_event("stream_partial_reply_salvaged", round=1, reply_len=len(reply_text))
            _accumulate_round(messages)
            yield ("replace", reply_text)
            yield ("final", _finish(messages, "recursion_salvaged"))
            return
        raise

    state = agent.get_state(config)
    messages = list((state.values or {}).get("messages") or [])
    _accumulate_round(messages)
    ai_reply = _last_ai_text(messages)
    log_event(
        "stream_round_done",
        round=1,
        reply_len=len(ai_reply),
        streamed_len=streamed_len,
        tool_calls=stats["tool_calls"],
    )
    if not ai_reply:
        log_event("stream_empty_reply", level=logging.WARNING)
        yield ("final", _finish(messages, "empty_messages"))
        return

    passed, issues = validator.validate(ai_reply, messages)
    if passed:
        log_event("validator_pass", round=1, reply_len=len(ai_reply), streamed=True)
        yield ("final", _finish(messages, "passed"))
        return

    retryable, reason = _classify_issues(issues)
    elapsed_s = time.perf_counter() - started
    budget_left = budget_s - elapsed_s
    will_retry = max_rounds > rounds_used and retryable and budget_left > _MIN_RETRY_BUDGET_S
    log_event(
        "validator_fail",
        level=logging.WARNING,
        round=1,
        issues=issues[:120],
        reply_len=len(ai_reply),
        retryable=retryable,
        reason=reason,
        budget_left_s=int(budget_left),
        will_retry=will_retry,
        streamed=True,
    )
    if not will_retry:
        if not retryable:
            status = f"terminal:{reason}"
        elif budget_left <= _MIN_RETRY_BUDGET_S:
            status = "budget_exhausted"
        else:
            status = "max_rounds"
        yield ("final", _finish(messages, status))
        return

    # ── 重试轮：无工具 agent + **非流式** ──
    # 同同步版理由：带工具重试 = 再跑一遍完整工具链（写库副作用 + 数十秒）。
    # 非流式：重试轮的文本要**整段替换**预览，本来也没有"逐字"的意义。
    retry_agent = agent
    try:
        from agents.agent import build_agent_no_tools

        retry_agent = build_agent_no_tools()
    except Exception as exc:  # noqa: BLE001 —— 拿不到就退回带工具 agent
        log_event("no_tools_agent_unavailable", level=logging.WARNING, exc_type=type(exc).__name__)

    rounds_used = 2
    input_content = (
        f"{query}\n\n"
        f"[校验反馈] 上一次回复存在以下问题：{issues}。"
        f"请修正这些问题后重新回复用户，不要提及校验过程。"
    )
    log_event("stream_retry_round_start", round=2, has_feedback=True)
    result2 = retry_agent.invoke({"messages": [HumanMessage(content=input_content)]}, config)
    messages2 = list(result2.get("messages") or [])
    _accumulate_round(messages2)
    text2 = _last_ai_text(messages2)
    if text2:
        yield ("replace", text2)
    passed2, issues2 = validator.validate(text2, messages2)
    log_event(
        "stream_retry_round_done",
        round=2,
        reply_len=len(text2),
        passed=passed2,
        issues=issues2[:120],
    )
    yield ("final", _finish(messages2, "passed" if passed2 else "rounds_exhausted"))
