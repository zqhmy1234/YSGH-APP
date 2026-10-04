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

import logging
import os
import re

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
FORBIDDEN_PATTERNS = [
    (r"content_format|内容形式[：:]", "内容形式(content_format)"),
    (r"记忆ID[：:]|收藏ID[：:]|memory_id|collection_id", "记忆/收藏ID"),
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
    from yishu.observability import log_event, timed

    if max_rounds is None:
        max_rounds = _env_int("AGENT_MAX_ROUNDS", 2)

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
        with timed("agent_invoke", log_ok=False, round=round_num) as t:
            result = current_agent.invoke(
                {"messages": [HumanMessage(content=input_content)]},
                config,
            )
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
