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

import re
import logging
from typing import Tuple, List, Optional
from langchain_core.messages import AnyMessage, ToolMessage, AIMessage

logger = logging.getLogger(__name__)

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
        messages: Optional[List[AnyMessage]] = None,
    ) -> Tuple[bool, str]:
        """
        校验 Agent 输出质量

        :param ai_reply: AI 的最终文本回复
        :param messages: 完整消息列表（用于检查重复工具调用）
        :return: (passed, issues) 通过返回 (True, "")，不通过返回 (False, 具体问题描述)
        """
        issues: List[str] = []

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

    def _check_duplicate_tool_calls(self, messages: List[AnyMessage]) -> str:
        """检查是否重复调用了同一工具存同一条内容"""
        # 收集所有 save_memory 和 save_knowledge_collection 的调用
        save_calls: List[str] = []
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
    max_rounds: int = 3,
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
    :param max_rounds: 最大重试轮数（默认3）
    :return: Agent 最终执行结果
    """
    from langchain_core.messages import HumanMessage

    validator = OutputValidator()
    feedback = None

    for round_num in range(1, max_rounds + 1):
        logger.info(f"校验循环 第{round_num}/{max_rounds}轮")

        # 构造输入：第2轮起附带校验反馈
        if feedback:
            input_content = (
                f"{query}\n\n"
                f"[校验反馈] 上一次回复存在以下问题：{feedback}。"
                f"请修正这些问题后重新回复用户，不要提及校验过程。"
            )
        else:
            input_content = query

        # 调用 Agent
        result = agent.invoke(
            {"messages": [HumanMessage(content=input_content)]},
            config,
        )

        # 获取最后一条 AI 消息
        messages = result.get("messages", [])
        if not messages:
            logger.warning("Agent 返回空消息")
            return result

        ai_reply = ""
        last_msg = messages[-1]
        if hasattr(last_msg, "content"):
            ai_reply = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)

        # 校验
        passed, issues = validator.validate(ai_reply, messages)

        if passed:
            logger.info(f"第{round_num}轮校验通过，输出结果")
            return result

        feedback = issues
        logger.warning(f"第{round_num}轮校验未通过: {issues}")

    # 达到上限仍未通过，返回最后一次结果
    logger.warning(f"达到最大轮数({max_rounds})，输出最后一次结果（未通过校验）")
    return result
