"""RAG 查询改写（RET-007 / B2-2 NER 实体抽取 / S1-03 LLM 改写）

拆包自 services/rag.py（F6，2026-08-27）：
  query → (rewritten, filters, ner_filters)
  时间表达解析（句首才算时间过滤意图）+ 显式参数 + NER 实体抽取 + LLM 改写
  （配置 key 后启用；未配置/失败 → 规则结果兜底）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.schemas.search import SearchQuery
from app.services.external import rewrite_query
from app.services.ner import extract_entities

# 时间表达规则（"去年夏天" → 时间范围；MVP 简化）
# 2026-08-26（audit #17）扩充：去年夏天/上上周/三年前/前年/上周/前天。
# 顺序 = 优先级：长模式在前（"去年夏天" 必须先于 "去年" 匹配，break 语义下
# 首个 pos==0 命中生效）；重叠模式按最长优先排列。
_TIME_PATTERNS = [
    (re.compile(r"去年夏天"), "last_summer"),
    (re.compile(r"上上周"), "two_weeks_ago"),
    (re.compile(r"三年前"), "three_years_ago"),
    (re.compile(r"前年"), "year_before_last"),
    (re.compile(r"去年"), "last_year"),
    (re.compile(r"今年"), "this_year"),
    (re.compile(r"上个月"), "last_month"),
    (re.compile(r"上周"), "last_week"),
    (re.compile(r"前天"), "day_before_yesterday"),
    (re.compile(r"昨天"), "yesterday"),
]


def _rewrite_query(q: SearchQuery) -> tuple[str, dict, dict]:
    """Query 改写（RET-007）：时间表达解析 → 过滤条件；无 LLM 时规则兜底

    返回 (rewritten, filters, ner_filters)：ner_filters 为 NER 派生过滤子集
    （place/tag），供搜索层"空结果回退"用——避免语料缺元数据时过过滤。
    """
    filters: dict = {}
    rewritten = q.q
    now = datetime.now(timezone.utc).astimezone()

    # 修复（2026-08-25 RAG 审查）：时间表达只在【句首】才算时间过滤意图。
    # 此前 pattern.search 任意位置命中即加 time 过滤并删词——"记得明天下午之前把
    # 上个月的工作总结报告交给领导" 中"上个月"是名词修饰语，不是查询意图，
    # 误加过滤 + 语料无 taken_at → 检索空结果（length 层 hit_rate 0.5 根因）。
    # 规则：时间词在句首（"去年去的地方"/"上个月的照片"）→ 时间意图，过滤+删词；
    # 句中/句尾（"我们去年去了苏州"/"把上个月的总结交了"）→ 描述性提及，不过滤。
    for pattern, kind in _TIME_PATTERNS:
        m = pattern.search(q.q)
        if m and m.start() == 0:
            if kind == "last_summer":
                # 去年夏天：去年 6/1 - 8/31（自然季窗口）
                filters["time_from"] = now.replace(year=now.year - 1, month=6, day=1)
                filters["time_to"] = now.replace(year=now.year - 1, month=8, day=31, hour=23, minute=59, second=59)
            elif kind == "two_weeks_ago":
                # 上上周：前 14 天 00:00 → 前 7 天 23:59:59（简化窗口）
                lo = (now - timedelta(days=14)).replace(hour=0, minute=0, second=0)
                hi = (now - timedelta(days=7)).replace(hour=23, minute=59, second=59)
                filters["time_from"] = lo
                filters["time_to"] = hi
            elif kind == "three_years_ago":
                filters["time_from"] = now.replace(year=now.year - 3, month=1, day=1)
                filters["time_to"] = now.replace(year=now.year - 3, month=12, day=31, hour=23, minute=59, second=59)
            elif kind == "year_before_last":
                filters["time_from"] = now.replace(year=now.year - 2, month=1, day=1)
                filters["time_to"] = now.replace(year=now.year - 2, month=12, day=31, hour=23, minute=59, second=59)
            elif kind == "last_week":
                lo = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0)
                hi = (now - timedelta(days=1)).replace(hour=23, minute=59, second=59)
                filters["time_from"] = lo
                filters["time_to"] = hi
            elif kind == "day_before_yesterday":
                d = now - timedelta(days=2)
                filters["time_from"] = d.replace(hour=0, minute=0, second=0)
                filters["time_to"] = d.replace(hour=23, minute=59, second=59)
            elif kind == "last_year":
                filters["time_from"] = now.replace(year=now.year - 1, month=1, day=1)
                filters["time_to"] = now.replace(year=now.year - 1, month=12, day=31)
            elif kind == "this_year":
                filters["time_from"] = now.replace(month=1, day=1)
            elif kind == "last_month":
                first = now.replace(day=1)
                if first.month > 1:
                    prev = first.replace(month=first.month - 1)
                else:
                    prev = first.replace(year=first.year - 1, month=12)
                filters["time_from"] = prev
                filters["time_to"] = first
            elif kind == "yesterday":
                y = now - timedelta(days=1)
                filters["time_from"] = y.replace(hour=0, minute=0, second=0)
                filters["time_to"] = y.replace(hour=23, minute=59, second=59)
            rewritten = pattern.sub("", q.q).strip()
            break

    # LLM 改写（S1-03 百炼接入：配置 key 后启用；未配置/失败 → 规则结果兜底）
    # 修复（审查 MINOR）：LLM 输入用规则改写后的文本（rewritten），避免
    # LLM 把已删的时间词带回原文（filter 已生效，文本却含"去年"造成语义噪音）
    # 2026-08-25 实测：llm_rewrite_enabled 默认关（短关键词改写伤害，见 config 注释）；
    # 开启时仍走双路召回（rag.py 3.3），改写路只增不减。
    if settings.llm_rewrite_enabled:
        try:
            llm_q = rewrite_query(rewritten)
            if llm_q:
                rewritten = llm_q
        except RuntimeError:
            pass

    if q.content_types:
        filters["content_types"] = q.content_types
    if q.time_from:
        filters["time_from"] = q.time_from
    if q.time_to:
        filters["time_to"] = q.time_to
    if q.place:
        filters["place"] = q.place
    if q.tag:
        filters["tag"] = q.tag

    # B2-2 NER 实体抽取（2026-08-19）：查询"苏州"/"小张" → place/tag 过滤
    # 规则词表起步（零延迟零成本，P95<3s 门禁不增 LLM 往返）；LLM 兜底默认关，
    # 待延迟预算评估后开（enable_llm=True）。显式参数优先于 NER 抽取结果。
    entities = extract_entities(rewritten)
    ner_filters: dict = {}
    if entities.get("place") and not filters.get("place"):
        filters["place"] = entities["place"]
        ner_filters["place"] = entities["place"]
    if entities.get("person") and not filters.get("tag"):
        filters["tag"] = entities["person"]
        ner_filters["tag"] = entities["person"]

    return rewritten, filters, ner_filters
