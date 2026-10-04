"""
忆光 - 记忆管理工具
提供记忆的存储、搜索、分类等功能
"""
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from langchain.tools import tool
from storage.database.supabase_client import get_supabase_client
from tools.tag_rules import coerce_str_list, validate_collection_tags, validate_memory_tags
from yishu.context import new_context, request_context
from yishu.db_errors import APIError

logger = logging.getLogger(__name__)

# ── 判重口径（与 utils/output_validator._check_duplicate_tool_calls 对齐）──
# LLM 反复调 save_memory 时，**每次的 content 往往不是逐字相同**：实测尾部常被
# 追加时间/语气词（如"（10月5日）""，我记下来了。"），于是
#   · 逐字相等去重 → 永不命中 → 每次真写库 → 模型以为没存成功 → 继续调
#     → LangGraph 递归上限耗尽（GraphRecursionError，实测 12s 后炸）
#   · 而 validator 按 `content[:50]` 判重 → 判为 terminal，但那是**事后**判定，
#     循环已经发生、垃圾记忆已经写入、延迟已经付出去了。
# 结论：判重必须做在**工具层**（唯一能事中终止循环的位置），且前缀长度须与
# validator 同口径，否则两层各判各的，永远对不上。
DEDUP_PREFIX_LEN = 50
# 前缀过短（如"今天"两个字）时做近似去重会误伤真实的不同记忆，低于此长度不启用。
DEDUP_MIN_PREFIX = 12
# 公共前缀阈值：尾部仅差日期/标点时（"…红叶特别美（10月5日）" vs "…红叶特别美（10月6日）"）
# 双向 startswith 都不成立，用公共前缀兜住。
NEAR_PREFIX_LEN = 20
# 窗口内最多回看多少条做比较（同一用户 10 分钟内不可能攒出上百条）
DEDUP_SCAN_LIMIT = 50


def _get_client():
    """获取Supabase客户端"""
    request_context.get() or new_context(method="memory_tool")
    return get_supabase_client()


def _get_user_id() -> str:
    """从请求上下文取当前 user_id（**fail-closed**，2026-10-04）。

    原实现：`return os.getenv("COZE_USER_ID", "default")` —— 上下文缺失时
    回落到一个**全局共享**的 user_id。后果不是「拿不到数据」，而是
    **所有用户的数据混进同一个身份**：
      · `save_memory` 写进公共桶；
      · `search_memories` / `get_recent_memories` /
        `export_knowledge_collections` 等 13 处 `.eq("user_id", ...)`
        全部作用在公共桶上 ⇒ **跨用户串档**。
      · 更隐蔽的是它**不报错**：没有用户维度的审计日志，静默就是常态。

    现在缺失即抛错拒绝执行。`yishu/context.current_user_id` 早已是同样口径
    （其 docstring 明确写了「不读环境变量冒充用户」），但本文件仍在用旧写法，
    同一仓库自相矛盾——统一到 fail-closed。
    """
    ctx = request_context.get()
    if ctx and ctx.user_id:
        return ctx.user_id
    from yishu.observability import log_audit

    log_audit("user_id_missing_reject", tool="memory_tools")
    raise PermissionError("缺少 user_id，拒绝执行记忆操作（防止跨用户串档）")


def _dedup_window_minutes() -> int:
    """前缀近似去重的时间窗（分钟）。设 0 可关闭第二级去重。"""
    try:
        return max(0, int(os.getenv("AGENT_DEDUP_WINDOW_MIN", "10")))
    except (TypeError, ValueError):
        return 10


def _norm(text: str) -> str:
    """归一化判重文本：去所有空白（LLM 重试的差异常是换行/空格这类排版噪声）。"""
    return re.sub(r"\s+", "", text or "")


def _common_prefix_len(a: str, b: str) -> int:
    """公共前缀长度（只看前 `DEDUP_PREFIX_LEN` 字：超出这个范围仍相同就不算同一条，
    否则两条开头雷同的长记忆会被误判为重复）。"""
    limit = min(len(a), len(b), DEDUP_PREFIX_LEN)
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return i


def _row_id(row: Any) -> Any:
    return row.get("id") if isinstance(row, dict) else None


def _find_existing_memory(client, user_id: str, content: str) -> tuple[Any, str]:
    """两级判重：先逐字相等，再「时间窗内 + 前缀近似」。

    返回 `(memory_id, match_kind)`；未命中时 `(None, "")`。
    match_kind 取值 `exact` / `prefix` / `near`，供日志区分是哪一级拦下的。

    ## 为什么第二级不能用 SQL `ilike` 前缀匹配（实测踩坑）

    最初实现是 `ilike("content", _pg_pattern(new_content) + "*")`，**永远匹配不到**：
    LLM 重试时是把内容**变长**（在尾部追加时间/语气词），DB 里存的是**变长前**那条，
    即旧内容是新内容的**真前缀**。用"长的新内容"去前缀匹配"短的旧内容"方向相反，
    必然落空——而且它**不报错**，只是静默返回空，表现为"判重像是没生效"。
    故改为：窗口内取最近若干条，在 Python 侧做**双向**前缀比较。

    判定三级（`min_len` 均为两者的较短长度）：
      · `prefix` —— 一方是另一方的前缀（覆盖"尾部追加"与"截短"两个方向）
      · `near`  —— 公共前缀 ≥ `NEAR_PREFIX_LEN`（覆盖"尾部只差一个日期/标点"）
      · 都不满足 ⇒ 视为不同内容，正常写入

    ⚠️ **近似去重的固有取舍**：窗口内提交两条开头相同的长记忆会被判为重复。
    用时间窗（默认 10 分钟）把误伤面压到"同一次交互内的重复提交"这一现实场景；
    真要关掉设 `AGENT_DEDUP_WINDOW_MIN=0`。取舍理由：漏写一条记忆用户还能再存一次，
    而循环不终止会让整轮对话撞递归上限后炸掉。
    """
    rows = getattr(
        client.table("memories").select("id").eq("user_id", user_id).eq("content", content).limit(1).execute(),
        "data",
        None,
    )
    if rows:
        return _row_id(rows[0]), "exact"

    window = _dedup_window_minutes()
    new_norm = _norm(content)
    if window <= 0 or len(new_norm) < DEDUP_MIN_PREFIX:
        return None, ""

    since = (datetime.now(timezone.utc) - timedelta(minutes=window)).isoformat()
    recent = getattr(
        client.table("memories")
        .select("id, content")
        .eq("user_id", user_id)
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(DEDUP_SCAN_LIMIT)
        .execute(),
        "data",
        None,
    )
    rows_in_window = len(recent or [])
    for row in recent or []:
        old_norm = _norm((row or {}).get("content") or "")
        min_len = min(len(old_norm), len(new_norm))
        if min_len < DEDUP_MIN_PREFIX:
            continue
        if old_norm.startswith(new_norm) or new_norm.startswith(old_norm):
            return _row_id(row), "prefix"
        if _common_prefix_len(old_norm, new_norm) >= NEAR_PREFIX_LEN:
            return _row_id(row), "near"

    # 未命中也要留痕：判重"看起来没生效"是本轮最难查的问题之一
    # （曾因 ilike 方向写反而长期静默失效，日志里什么都不留）。
    # 只记长度与候选数，**不记内容**（隐私纪律同 observability 模块）。
    from yishu.observability import log_event

    log_event(
        "save_memory_dedup_miss",
        level=logging.DEBUG,
        content_len=len(content),
        window_min=window,
        rows_in_window=rows_in_window,
    )
    return None, ""


def _parse_content(content: Any) -> str:
    """安全地将content转换为字符串"""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        if content and isinstance(content[0], str):
            return " ".join(content)
        else:
            return " ".join(item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text")
    return str(content)


def _find_related_memories(client, user_id: str, exclude_id: int,
                           category: str | None = None,
                           mood: str | None = None,
                           topics: list[str] | None = None,
                           limit: int = 3) -> list[dict[str, Any]]:
    """搜索与当前记忆相关的历史记忆（按分类、情绪、主题匹配）

    2026-10-04 修复（性能 P0，根因）：
      原实现 `select("..., memory_categories(name)")` 用的是 **Supabase/PostgREST 的
      外键嵌入语法**。去 Coze 迁移后数据层换成了原生 SQLAlchemy（`local_client`），
      它把这段**当成列名直拼**⇒ SQL 报错「列 memory_categories.name 不存在」。

      失败被 `except` 吞掉只打 warning ⇒ `save_memory` 每次都返回
      `related_memories: []`，而返回体本身是 `success: true`——
      LLM据此判断"信息不完整"，于是**反复调用同一个 save_memory**：
      实测单轮 16~18 次、耗时 32~64s、输入 token 涨到 13K、最后撞递归上限。
      **一个静默的 SQL 错误 ⇒ 32 秒延迟。**

      修法：`memories` 表本来就有 `category_id` 列，直接按它过滤，无需 join。
    """
    related: list[dict[str, Any]] = []
    try:
        query = client.table("memories").select(
            "id, content, mood, location, topics, created_at, category_id"
        ).eq("user_id", user_id).neq("id", exclude_id)

        if category:
            # 分类名 → id（一次极小的查询），再按 category_id 过滤
            cat_resp = (
                client.table("memory_categories")
                .select("id")
                .eq("name", category)
                .limit(1)
                .execute()
            )
            cat_rows = getattr(cat_resp, "data", None)
            cat_id = cat_rows[0].get("id") if cat_rows else None
            if cat_id is not None:
                query = query.eq("category_id", cat_id)

        response = query.order("created_at", desc=True).limit(limit * 3).execute()
        raw = response.data if isinstance(response.data, list) else []
        data: list[dict[str, Any]] = [d for d in raw if isinstance(d, dict)]

        # 优先匹配同主题的
        if topics:
            topic_set = set(topics)
            for d in data:
                if len(related) >= limit:
                    break
                d_topics = d.get("topics")
                if isinstance(d_topics, list) and (topic_set & set(d_topics)):
                    related.append(d)

        # 其次匹配同情绪的
        if mood and len(related) < limit:
            existing = {r["id"] for r in related}
            for d in data:
                if len(related) >= limit:
                    break
                if d.get("id") not in existing and d.get("mood") == mood:
                    related.append(d)

        # 不够则补充同分类的
        if len(related) < limit:
            existing = {r["id"] for r in related}
            for d in data:
                if len(related) >= limit:
                    break
                if d.get("id") not in existing:
                    related.append(d)

        # 格式化输出
        result = []
        for item in related[:limit]:
            cat_data = item.get("memory_categories")
            cat_name = cat_data.get("name") if isinstance(cat_data, dict) else None
            result.append({
                "id": item.get("id"),
                "content": item.get("content"),
                "mood": item.get("mood"),
                "topics": item.get("topics") or [],
                "category": cat_name,
                "location": item.get("location"),
                "created_at": item.get("created_at"),
            })
        return _jsonable(result)
    except Exception as e:
        # 2026-10-04：这个 except 曾经让「PostgREST 嵌入语法不被支持」这个硬错误
        # 静默了多久无从查证——只有一行 warning，且调用方拿到的是空列表而非错误。
        # 升级为结构化事件，让这类"能力性缺失"在日志里显形。
        from yishu.observability import log_event

        log_event(
            "related_memories_failed",
            level=logging.WARNING,
            user_id=user_id,
            exc_type=type(e).__name__,
            detail=str(e)[:150],
        )
        logger.warning("联想相关记忆失败: %s", e)
        return []


def _category_id(client, name: str) -> Any:
    """分类名 → category_id（2026-10-04）。

    `memories` 表存的是 `category_id`，分类名在 `memory_categories.name`。
    迁移前用 PostgREST 关联语法跨表过滤，迁移后失效 ⇒ 按 id 过滤才可靠。
    """
    try:
        resp = (
            client.table("memory_categories").select("id").eq("name", name).limit(1).execute()
        )
        rows = getattr(resp, "data", None)
        return rows[0].get("id") if rows else None
    except Exception as exc:  # noqa: BLE001 —— 取 id 失败不该阻断查询
        from yishu.observability import log_event

        log_event(
            "category_lookup_failed",
            level=logging.WARNING,
            exc_type=type(exc).__name__,
            detail=str(exc)[:100],
        )
        return None


def _jsonable(value: Any) -> Any:
    """把 DB 取出的非 JSON 原生类型（datetime/date/Decimal）转成字符串。

    2026-10-04 踩坑：`created_at` 从 SQLAlchemy 取出是 `datetime`，
    `save_memory` 末尾的 `json.dumps(...)` 直接抛
    `TypeError: Object of type datetime is not JSON serializable`
    ⇒ 工具失败 ⇒ LLM 反复重试同一个工具。

    这与上面那个 SQL 错误**互相掩盖**：关联查询坏时返回空列表、json 不崩；
    关联查询修好后 json 崩——修一个暴露另一个。

    统一在**数据源头**（联想函数返回值）归一化，
    避免在 24 处 `json.dumps` 逐个加 `default=str`。
    """
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _find_related_collections(client, user_id: str, exclude_id: int,
                               topics: list[str] | None = None,
                               content_type: str | None = None,
                               limit: int = 3) -> list[dict[str, Any]]:
    """搜索与当前收藏相关的历史收藏（按主题、类型匹配）"""
    related: list[dict[str, Any]] = []
    try:
        query = client.table("knowledge_collections").select(
            "id, title, content_type, topics, created_at"
        ).eq("user_id", user_id).neq("id", exclude_id)

        if content_type:
            query = query.eq("content_type", content_type)

        response = query.order("created_at", desc=True).limit(limit * 3).execute()
        raw = response.data if isinstance(response.data, list) else []
        data: list[dict[str, Any]] = [d for d in raw if isinstance(d, dict)]

        # 优先匹配同主题的
        if topics:
            topic_set = set(topics)
            for d in data:
                if len(related) >= limit:
                    break
                d_topics = d.get("topics")
                if isinstance(d_topics, list) and (topic_set & set(d_topics)):
                    related.append(d)

        # 不够则补充同类型的
        if len(related) < limit:
            existing = {r["id"] for r in related}
            for d in data:
                if len(related) >= limit:
                    break
                if d.get("id") not in existing:
                    related.append(d)

        # 格式化输出
        result = []
        for item in related[:limit]:
            result.append({
                "id": item.get("id"),
                "title": item.get("title") or "无标题",
                "content_type": item.get("content_type"),
                "topics": item.get("topics") or [],
                "created_at": item.get("created_at"),
            })
        return result
    except Exception as e:
        logger.warning(f"联想相关收藏失败: {e}")
        return []


@tool
def save_memory(
    content: str,
    category: str | None = None,
    mood: str | None = None,
    topics: str | list[str] | None = None,
    location: str | None = None,
    people: str | list[str] | None = None,
    content_format: str | None = None
) -> str:
    """
    保存一条新的记忆到数据库中。

    Args:
        content: 记忆的原始内容（必填）
        category: 记忆分类（七选一）：情绪、事件、想法、灵感、人物、地点、待分类
        mood: 情绪状态（八选一）：开心、平静、感恩、期待、悲伤、焦虑、愤怒、疲惫
        topics: 主题领域（1~2个，十三选一）：学习、工作、健康、美食、情感、旅行、家庭、社交、兴趣、目标、生活、认知、其他
        location: 相关地点（可选）
        people: 相关人物列表（可选）
        content_format: 内容形式：text、image、voice、link，默认text

    Returns:
        保存结果的JSON字符串，包含记忆ID、标签信息和相关历史记忆
    """
    client = _get_client()

    # 参数宽松化（2026-10-05）：模型经常把数组参数传成 JSON 字符串
    # （`'["旅行","兴趣"]'`）。归一化**必须放在函数体里**——放宽签名只是让它
    # 能进来，解析只能在这里做（pydantic 已在更早一层放过它了）。
    topics = coerce_str_list(topics)
    people = coerce_str_list(people)

    # 入口埋点（2026-10-05）：只记**长度与参数个数**，不记内容（隐私纪律）。
    # 加这条的直接原因：一次「判重看起来没生效」的排查耗时很久——因为
    # `tool_done` 记的是 ToolMessage 的 str() 长度，既分不清是工具返回还是
    # 异常兜底，也无法区分"判重未命中"与"压根没执行到判重"。
    from yishu.observability import log_event as _log_enter

    _log_enter(
        "save_memory_enter",
        content_len=len(content or ""),
        has_category=bool(category),
        has_mood=bool(mood),
        topics_n=len(topics or []),
    )

    # ---- 幂等：两级判重，同用户已有等价内容则直接返回，不重复写库 ----
    # 为什么必须在这里挡：LLM 收不到"已经存过了"的信号时，会**反复调用同一个
    # save_memory**——实测单轮 16~28 次、输入 token 涨到 15K、耗时 32~66s。
    # 工具层幂等是唯一能真正终止循环的位置（改 prompt 或降 max_rounds 都只是
    # 降低概率，LLM 仍可能在别处重试）。
    # 2026-10-05：逐字相等这一级**不够**——LLM 重试时 content 常带尾部差异
    # （时间/语气词），逐字去重永不命中，循环照旧撞 recursion_limit
    # （实测 GraphRecursionError@12.2s）。故增第二级前缀近似，见 _find_existing_memory。
    try:
        existing_id, match_kind = _find_existing_memory(client, _get_user_id(), content)
        if existing_id:
            from yishu.observability import log_event

            log_event(
                "save_memory_dedup",
                existing_id=existing_id,
                match=match_kind,
                content_len=len(content),
            )
            return json.dumps(
                {
                    "success": True,
                    "duplicate": True,
                    "memory_id": existing_id,
                    "message": (
                        f"这条内容已经保存过了（ID: {existing_id}），无需重复保存。请直接回复用户。"
                    ),
                },
                ensure_ascii=False,
            )
    except Exception as exc:  # noqa: BLE001 —— 去重是优化，不该因它阻断正常保存
        from yishu.observability import log_event

        # 隔离失败（缺 user_id）**必须重抛**：吞掉它会让审计日志显示成
        # 「去重失败」这种无害告警，而真实问题是 fail-closed 被绕过。
        if isinstance(exc, PermissionError):
            raise
        log_event(
            "save_memory_dedup_failed",
            level=logging.WARNING,
            exc_type=type(exc).__name__,
            detail=str(exc)[:100],
        )

    # V3标签校验与归一化
    validated = validate_memory_tags(
        category=category, mood=mood, content_format=content_format,
    )

    # 构建记忆数据
    memory_data: dict[str, Any] = {
        "content": content,
        "mood": validated["mood"],
        "content_format": validated["content_format"],
        "user_id": _get_user_id(),
    }
    if location:
        memory_data["location"] = location
    if people:
        memory_data["people"] = people
    if topics:
        memory_data["topics"] = topics

    # 处理分类（归一化后的值）
    cat_name = validated["category"]
    if cat_name:
        try:
            cat_response = client.table("memory_categories").select("id").eq("name", cat_name).maybe_single().execute()
            if cat_response and cat_response.data:
                cat_data: dict[str, Any] = cat_response.data
                memory_data["category_id"] = cat_data["id"]
            else:
                new_cat = client.table("memory_categories").insert({"name": cat_name}).execute()
                if new_cat.data:
                    new_cat_list: list[dict[str, Any]] = new_cat.data
                    memory_data["category_id"] = new_cat_list[0]["id"]
        except APIError as e:
            logger.warning(f"处理分类时出错: {e.message}")

    try:
        response = client.table("memories").insert(memory_data).execute()
        if response.data:
            response_list: list[dict[str, Any]] = response.data
            memory_id = response_list[0]["id"]
            user_id = _get_user_id()

            # 自动联想相关历史记忆
            related = _find_related_memories(
                client, user_id, memory_id,
                category=validated["category"],
                mood=validated["mood"],
                topics=topics,
                limit=3
            )

            return json.dumps({
                "success": True,
                "memory_id": memory_id,
                "message": f"记忆已成功保存，ID: {memory_id}",
                "category": validated["category"],
                "mood": validated["mood"],
                "topics": topics or [],
                "people": people,
                "location": location,
                "related_memories": related,
                "_hint": "回复用户时展示：✅已保存、📝内容、🏷️分类、💭情绪、📌主题、👫同行(如有)、📍地点(如有)。如果有related_memories，自然地提及相关历史记忆，如「这让我想起你X月X日也记录过类似的事情～」。严禁展示内容形式(content_format)、memory_id。",
            }, ensure_ascii=False)
        else:
            return json.dumps({"success": False, "message": "保存失败，未返回数据"}, ensure_ascii=False)
    except APIError as e:
        logger.error(f"保存记忆失败: {e.message}")
        raise Exception(f"保存记忆失败: {e.message}")


@tool
def search_memories(
    query: str | None = None,
    category: str | None = None,
    mood: str | None = None,
    topics: list[str] | None = None,
    limit: int = 10
) -> str:
    """
    搜索历史记忆。支持按关键词、分类、情绪、主题等条件筛选。
    用户可能用模糊语言描述，如「我之前心情不好的时候记录了什么」→ mood=悲伤/焦虑；
    「上个月关于旅行的事情」→ topics=旅行；「我和朋友相关的内容」→ category=人物。

    Args:
        query: 搜索关键词（在内容中模糊匹配）
        category: 按分类筛选（情绪、事件、想法、灵感、人物、地点、待分类）
        mood: 按情绪状态筛选（开心、平静、感恩、期待、悲伤、焦虑、愤怒、疲惫）
        topics: 按主题筛选（学习、工作、健康、美食、情感、旅行、家庭、社交、兴趣、目标、生活、认知、其他）
        limit: 返回结果数量限制，默认10条

    Returns:
        搜索结果的JSON字符串，包含匹配的记忆列表
    """
    client = _get_client()

    try:
        # 构建查询（按 user_id 隔离）
        db_query = client.table("memories").select("id, content, summary, mood, topics, location, people, created_at, memory_categories(name)").eq("user_id", _get_user_id())

        # 应用过滤条件
        if category:
            # 2026-10-04：同 `_find_related_memories` 的修复——原用 PostgREST 关联
            # 语法 `memory_categories(name)` + `.eq("memory_categories.name", ...)`，
            # 在原生 SQLAlchemy 后端下要么被当列名报错、要么过滤失效。
            cat_id = _category_id(client, category)
            if cat_id is not None:
                db_query = db_query.eq("category_id", cat_id)
        if mood:
            db_query = db_query.eq("mood", mood)
        if query:
            # 2026-10-04：统一为 **PostgREST 语义**——`ilike` 内部把 `*` 转成 SQL
            # 通配并转义用户输入里的 `%`/`_`。原代码传 `%{query}%` 会被当成
            # 「字面量百分号」而匹配不到任何东西（搜索功能实际一直是坏的）。
            db_query = db_query.ilike("content", f"*{query}*")

        # 按时间倒序，限制数量
        db_query = db_query.order("created_at", desc=True).limit(min(limit, 50))

        response = db_query.execute()

        memories: list[dict[str, Any]] = []
        response_data: list[dict[str, Any]] = response.data if response.data else []
        for mem in response_data:
            cat_data = mem.get("memory_categories")
            category_name = None
            if cat_data and isinstance(cat_data, dict):
                category_name = cat_data.get("name")

            memories.append({
                "id": mem.get("id"),
                "content": mem.get("content"),
                "summary": mem.get("summary"),
                "mood": mem.get("mood"),
                "topics": mem.get("topics") or [],
                "location": mem.get("location"),
                "people": mem.get("people") or [],
                "category": category_name,
                "created_at": mem.get("created_at")
            })

        # 如果指定了主题，在Python层过滤（JSON数组包含匹配）
        if topics:
            topic_set = set(topics)
            memories = [m for m in memories if isinstance(m.get("topics"), list) and topic_set & set(m["topics"])]

        return json.dumps({
            "success": True,
            "count": len(memories),
            "memories": memories
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"搜索记忆失败: {e.message}")
        raise Exception(f"搜索记忆失败: {e.message}")


@tool
def get_recent_memories(days: int = 7, limit: int = 20) -> str:
    """
    获取最近几天的记忆记录。

    Args:
        days: 获取最近多少天的记忆，默认7天
        limit: 返回结果数量限制，默认20条

    Returns:
        最近记忆的JSON字符串
    """
    client = _get_client()

    try:
        from datetime import timedelta
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()

        response = client.table("memories") \
            .select("id, content, summary, mood, topics, category_id, created_at, memory_categories(name)") \
            .eq("user_id", _get_user_id()) \
            .gte("created_at", cutoff_date) \
            .order("created_at", desc=True) \
            .limit(min(limit, 100)) \
            .execute()

        memories: list[dict[str, Any]] = []
        response_data: list[dict[str, Any]] = response.data if response.data else []
        for mem in response_data:
            cat_data = mem.get("memory_categories")
            cat_name = cat_data.get("name") if isinstance(cat_data, dict) else None
            memories.append({
                "id": mem.get("id"),
                "content": mem.get("content"),
                "summary": mem.get("summary"),
                "mood": mem.get("mood"),
                "topics": mem.get("topics") or [],
                "category": cat_name,
                "created_at": mem.get("created_at")
            })

        return json.dumps({
            "success": True,
            "period_days": days,
            "count": len(memories),
            "memories": memories
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"获取最近记忆失败: {e.message}")
        raise Exception(f"获取最近记忆失败: {e.message}")


@tool
def get_memory_stats() -> str:
    """
    获取记忆统计信息，包括总数、各分类数量、情绪分布等。

    Returns:
        统计信息的JSON字符串
    """
    client = _get_client()

    try:
        user_id = _get_user_id()
        # 获取总记忆数（按用户隔离）
        total_response = client.table("memories").select("id", count="exact").eq("user_id", user_id).execute()
        total_count = total_response.count if total_response.count else 0

        # 获取分类统计
        categories_response = client.table("memory_categories").select("id, name").execute()
        category_stats: list[dict[str, Any]] = []
        categories_data: list[dict[str, Any]] = categories_response.data if categories_response.data else []
        for cat in categories_data:
            cat_memories = client.table("memories").select("id", count="exact").eq("user_id", user_id).eq("category_id", cat.get("id")).execute()
            category_stats.append({
                "name": cat.get("name"),
                "count": cat_memories.count if cat_memories.count else 0
            })

        # 获取情绪分布（按用户隔离）
        mood_response = client.table("memories").select("mood").eq("user_id", user_id).execute()
        mood_counts: dict[str, int] = {}
        mood_data: list[dict[str, Any]] = mood_response.data if mood_response.data else []
        for mem in mood_data:
            mood = mem.get("mood") or "未标记"
            mood_counts[mood] = mood_counts.get(mood, 0) + 1

        # 获取主题分布
        topics_response = client.table("memories").select("topics").eq("user_id", user_id).execute()
        topic_counts: dict[str, int] = {}
        topics_data: list[dict[str, Any]] = topics_response.data if topics_response.data else []
        for mem in topics_data:
            mem_topics = mem.get("topics")
            if isinstance(mem_topics, list):
                for t in mem_topics:
                    topic_counts[t] = topic_counts.get(t, 0) + 1

        return json.dumps({
            "success": True,
            "total_memories": total_count,
            "categories": category_stats,
            "mood_distribution": mood_counts,
            "topic_distribution": topic_counts
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"获取统计信息失败: {e.message}")
        raise Exception(f"获取统计信息失败: {e.message}")


@tool
def update_memory_summary(memory_id: int, summary: str) -> str:
    """
    更新记忆的AI摘要。

    Args:
        memory_id: 记忆ID
        summary: AI生成的摘要内容

    Returns:
        更新结果的JSON字符串
    """
    client = _get_client()

    try:
        response = client.table("memories") \
            .update({"summary": summary}) \
            .eq("id", memory_id) \
            .eq("user_id", _get_user_id()) \
            .execute()

        if response.data:
            return json.dumps({
                "success": True,
                "message": f"记忆 {memory_id} 的摘要已更新"
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": f"未找到ID为 {memory_id} 的记忆"
            }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"更新记忆摘要失败: {e.message}")
        raise Exception(f"更新记忆摘要失败: {e.message}")


@tool
def delete_memory(memory_id: int) -> str:
    """
    删除指定的记忆。

    Args:
        memory_id: 要删除的记忆ID

    Returns:
        删除结果的JSON字符串
    """
    client = _get_client()

    try:
        response = client.table("memories").delete().eq("id", memory_id).eq("user_id", _get_user_id()).execute()

        if response.data:
            return json.dumps({
                "success": True,
                "message": f"记忆 {memory_id} 已删除"
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": f"未找到ID为 {memory_id} 的记忆"
            }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"删除记忆失败: {e.message}")
        raise Exception(f"删除记忆失败: {e.message}")


@tool
def save_knowledge_collection(
    content: str,
    content_type: str = "文章",
    title: str | None = None,
    author: str | None = None,
    source: str | None = None,
    topics: str | list[str] | None = None,
    image_url: str | None = None,
    mood: str | None = None,
    content_format: str | None = None
) -> str:
    """
    收藏一条外部信息到知识库。

    当用户分享外部信息（文章、帖子、观点、数据、图片等）时使用此工具。

    Args:
        content: 内容（必填）
        content_type: 收藏分类（六选一）：文章、帖子、观点、数据、图片、其他，默认文章
        title: 标题（可选）
        author: 作者（可选）
        source: 来源，如URL、平台名、出处（可选）
        topics: 主题领域（1~2个，十三选一）：学习、工作、健康、美食、情感、旅行、家庭、社交、兴趣、目标、生活、认知、其他
        image_url: 图片URL（可选）
        mood: 情绪状态（可选，八选一）：开心、平静、感恩、期待、悲伤、焦虑、愤怒、疲惫
        content_format: 内容形式：text、image、voice、link，默认text

    Returns:
        保存结果的JSON字符串
    """
    client = _get_client()

    # 参数宽松化（2026-10-05）：同 save_memory，模型会把数组传成 JSON 字符串
    topics = coerce_str_list(topics)

    # V3标签校验与归一化
    validated = validate_collection_tags(
        content_type=content_type, topics=topics,
        mood=mood, content_format=content_format,
    )

    try:
        data: dict[str, Any] = {
            "content": content,
            "content_type": validated["content_type"],
            "content_format": validated["content_format"],
            "user_id": _get_user_id(),
        }

        if title:
            data["title"] = title
        if author:
            data["author"] = author
        if source:
            data["source"] = source
        if validated["topics"]:
            data["topics"] = validated["topics"]
        if validated["mood"]:
            data["mood"] = validated["mood"]
        if image_url:
            data["image_url"] = image_url

        response = client.table("knowledge_collections").insert(data).execute()

        if response.data:
            item = response.data[0] if isinstance(response.data, list) else response.data
            item_id = item.get("id") if isinstance(item, dict) else None
            user_id = _get_user_id()

            # 自动联想相关历史收藏
            related_cols = _find_related_collections(
                client, user_id, item_id,
                topics=validated["topics"],
                content_type=validated["content_type"],
                limit=3
            )

            # 同时联想相关个人记忆（按主题匹配）
            related_mems: list[dict[str, Any]] = []
            if validated["topics"]:
                try:
                    mem_resp = client.table("memories").select(
                        "id, content, mood, topics, created_at, memory_categories(name)"
                    ).eq("user_id", user_id).order("created_at", desc=True).limit(20).execute()
                    raw_data = mem_resp.data if isinstance(mem_resp.data, list) else []
                    topic_set = set(validated["topics"])
                    related_mems: list[dict[str, Any]] = []
                    for m in raw_data:
                        if not isinstance(m, dict):
                            continue
                        m_topics = m.get("topics")
                        if not isinstance(m_topics, list):
                            continue
                        if not (topic_set & set(m_topics)):
                            continue
                        cat_data = m.get("memory_categories")
                        cat_name = cat_data.get("name") if isinstance(cat_data, dict) else None
                        related_mems.append({
                            "id": m.get("id"),
                            "content": m.get("content"),
                            "mood": m.get("mood"),
                            "topics": m_topics,
                            "category": cat_name,
                            "created_at": m.get("created_at"),
                        })
                        if len(related_mems) >= 3:
                            break
                except Exception as e:
                    logger.warning(f"联想相关记忆失败: {e}")

            return json.dumps({
                "success": True,
                "collection_id": item_id,
                "message": f"知识收藏已成功保存，ID: {item_id}",
                "content_type": validated["content_type"],
                "title": title,
                "topics": validated["topics"],
                "mood": validated["mood"],
                "related_collections": related_cols,
                "related_memories": related_mems,
                "_hint": "回复用户时展示：✅已收藏、📝标题、🏷️类型、📌主题、💭情绪(如有)。如果有related_memories或related_collections，自然地提及相关历史内容。严禁展示content_format、collection_id。",
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": "保存失败，未返回数据"
            }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"保存知识收藏失败: {e.message}")
        raise Exception(f"保存知识收藏失败: {e.message}")


@tool
def search_knowledge_collections(
    query: str | None = None,
    content_type: str | None = None,
    topics: list[str] | None = None,
    limit: int = 10
) -> str:
    """
    搜索知识库中的收藏内容。

    Args:
        query: 搜索关键词（可选，在内容中模糊匹配）
        content_type: 内容类型筛选（可选）
        topics: 主题筛选（可选）
        limit: 返回数量，默认10

    Returns:
        搜索结果的JSON字符串
    """
    client = _get_client()

    try:
        query_builder = client.table("knowledge_collections").select("*").eq("user_id", _get_user_id())

        if query:
            # PostgREST 语义：`*` 才是通配。原先传 `%{query}%` 会被 `_pg_pattern`
            # 转义成字面量 `\%...\%` ⇒ 永远匹配不到（收藏搜索实际一直是坏的）。
            query_builder = query_builder.ilike("content", f"*{query}*")
        if content_type:
            query_builder = query_builder.eq("content_type", content_type)

        # limit 必须夹紧：模型可以传 10^6 级别的值，无上限会把整表拉进内存
        # （与 search_memories 的 min(limit, 50) 保持同一口径）。
        response = query_builder.order("created_at", desc=True).limit(min(int(limit), 50)).execute()

        data: list[dict[str, Any]] = response.data if response.data else []

        # 过滤topics（客户端过滤）
        filtered_data = data
        if topics:
            filtered_data = [
                item for item in filtered_data
                if isinstance(item.get("topics"), list) and any(t in item.get("topics", []) for t in topics)
            ]

        return json.dumps({
            "success": True,
            "count": len(filtered_data),
            "collections": filtered_data
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"搜索知识收藏失败: {e.message}")
        raise Exception(f"搜索知识收藏失败: {e.message}")


@tool
def get_knowledge_summary(topics: list[str] | None = None, days: int = 7) -> str:
    """
    获取知识库的总结，包括收藏数量、主题分布等。

    Args:
        topics: 指定主题（可选，不指定则总结所有）
        days: 最近多少天，默认7天

    Returns:
        总结信息的JSON字符串
    """
    client = _get_client()

    try:
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)

        query_builder = client.table("knowledge_collections").select("*").eq("user_id", _get_user_id()).gte("created_at", cutoff_date.isoformat())

        if topics:
            response = query_builder.execute()
            data: list[dict[str, Any]] = response.data if response.data else []
            data = [
                item for item in data
                if isinstance(item.get("topics"), list) and any(t in item.get("topics", []) for t in topics)
            ]
        else:
            response = query_builder.execute()
            data: list[dict[str, Any]] = response.data if response.data else []

        # 统计
        total_count = len(data)
        type_counts: dict[str, int] = {}
        topic_counts: dict[str, int] = {}

        for item in data:
            # 类型统计
            ctype = item.get("content_type", "other")
            type_counts[ctype] = type_counts.get(ctype, 0) + 1

            # 主题统计
            item_topics = item.get("topics", [])
            if isinstance(item_topics, list):
                for t in item_topics:
                    topic_counts[t] = topic_counts.get(t, 0) + 1

        return json.dumps({
            "success": True,
            "period_days": days,
            "total_collections": total_count,
            "type_distribution": type_counts,
            "topic_distribution": topic_counts
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"获取知识总结失败: {e.message}")
        raise Exception(f"获取知识总结失败: {e.message}")
