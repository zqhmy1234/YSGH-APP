"""
忆光标签体系规范 V3 - 枚举常量、归一化与校验模块

标签分层：
  L0  库类型        个人记忆库 / 知识收藏库
  L1-A 记忆分类      七选一（仅个人记忆库）
  L1-B 收藏分类      六选一（仅知识收藏库）
  L2  情绪状态       八选一（记忆库必打，收藏库可选）
  L3  主题领域       十三选一~二（两库共用）
  L4  内容形式       四选一（两库共用）
"""

import re
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 枚举常量
# ──────────────────────────────────────────────

# L1-A 记忆分类（仅个人记忆库，七选一）
MEMORY_CATEGORIES = [
    "情绪", "事件", "想法", "灵感", "人物", "地点", "待分类",
]

# L1-B 收藏分类（仅知识收藏库，六选一，中文枚举）
COLLECTION_TYPES = [
    "文章", "帖子", "观点", "数据", "图片", "其他",
]

# 兼容旧值映射（英文 → 中文）
COLLECTION_TYPE_ALIASES = {
    "article": "文章",
    "post": "帖子",
    "opinion": "观点",
    "data": "数据",
    "visual": "图片",
    "other": "其他",
}

# L2 情绪状态（八选一）
MOODS = [
    "开心", "平静", "感恩", "期待",
    "悲伤", "焦虑", "愤怒", "疲惫",
]

# 情绪同义词映射（归一化用）
MOOD_ALIASES = {
    "高兴": "开心", "愉快": "开心", "兴奋": "开心", "喜悦": "开心", "快乐": "开心",
    "平和": "平静", "放松": "平静", "安心": "平静", "惬意": "平静", "舒适": "平静",
    "感激": "感恩", "珍惜": "感恩", "温暖": "感恩",
    "憧憬": "期待", "盼望": "期待", "跃跃欲试": "期待", "期待": "期待",
    "难过": "悲伤", "失落": "悲伤", "沮丧": "悲伤", "委屈": "悲伤", "低落": "悲伤",
    "不安": "焦虑", "担心": "焦虑", "紧张": "焦虑", "压力大": "焦虑", "压力": "焦虑",
    "生气": "愤怒", "不满": "愤怒", "烦躁": "愤怒", "憋屈": "愤怒", "气愤": "愤怒",
    "累": "疲惫", "倦怠": "疲惫", "心力交瘁": "疲惫", "想摆烂": "疲惫", "疲倦": "疲惫",
}

# L3 主题领域（十三类，两库共用）
TOPICS = [
    "学习", "工作", "健康", "美食", "情感",
    "旅行", "家庭", "社交", "兴趣", "目标",
    "生活", "认知", "其他",
]

# 主题同义词映射（归一化用）
TOPIC_ALIASES = {
    "读书": "学习", "课程": "学习", "技能": "学习", "考试": "学习",
    "职场": "工作", "项目": "工作", "晋升": "工作",
    "运动": "健康", "睡眠": "健康", "体检": "健康", "养生": "健康",
    "饮食": "美食", "做饭": "美食", "餐厅": "美食", "探店": "美食", "食谱": "美食",
    "亲情": "情感", "友情": "情感", "爱情": "情感", "人际关系": "情感",
    "出游": "旅行", "见闻": "旅行", "攻略": "旅行", "风景": "旅行",
    "家人": "家庭", "家务": "家庭",
    "聚会": "社交", "朋友": "社交",
    "爱好": "兴趣", "艺术": "兴趣", "娱乐": "兴趣", "游戏": "兴趣",
    "计划": "目标", "梦想": "目标", "规划": "目标", "打卡": "目标",
    "日常": "生活", "消费": "生活", "起居": "生活",
    "观点": "认知", "思考": "认知", "方法论": "认知", "顿悟": "认知",
}

# L4 内容形式（四选一）
CONTENT_FORMATS = ["text", "image", "voice", "link"]

# ──────────────────────────────────────────────
# 归一化函数
# ──────────────────────────────────────────────

def normalize_category(category: Optional[str]) -> str:
    """校验并归一化 L1-A 记忆分类，非法值兜底为 '待分类'"""
    if not category:
        return "待分类"
    c = category.strip()
    if c in MEMORY_CATEGORIES:
        return c
    # 尝试模糊匹配
    for valid in MEMORY_CATEGORIES:
        if valid in c or c in valid:
            return valid
    logger.warning(f"非法记忆分类 '{category}'，已归一化为 '待分类'")
    return "待分类"


def normalize_collection_type(content_type: Optional[str]) -> str:
    """校验并归一化 L1-B 收藏分类，非法值兜底为 '其他'"""
    if not content_type:
        return "其他"
    ct = content_type.strip()
    # 中文枚举直接匹配
    if ct in COLLECTION_TYPES:
        return ct
    # 英文旧值映射
    if ct.lower() in COLLECTION_TYPE_ALIASES:
        return COLLECTION_TYPE_ALIASES[ct.lower()]
    logger.warning(f"非法收藏分类 '{content_type}'，已归一化为 '其他'")
    return "其他"


def normalize_mood(mood: Optional[str]) -> Optional[str]:
    """
    校验并归一化 L2 情绪状态
    - 记忆库：必打，非法值兜底为 '平静'
    - 收藏库：可选，None 则返回 None
    """
    if not mood:
        return None
    m = mood.strip()
    if m in MOODS:
        return m
    # 同义词归一化
    if m in MOOD_ALIASES:
        return MOOD_ALIASES[m]
    # 尝试模糊匹配
    for valid in MOODS:
        if valid in m or m in valid:
            return valid
    logger.warning(f"非法情绪 '{mood}'，已归一化为 '平静'")
    return "平静"


def normalize_mood_required(mood: Optional[str]) -> str:
    """记忆库必打情绪，None 兜底为 '平静'"""
    result = normalize_mood(mood)
    return result if result else "平静"


def normalize_topics(topics: Optional[List[str]]) -> List[str]:
    """
    校验并归一化 L3 主题领域
    - 允许 1~2 个主题
    - 同义词归一化
    - 去重
    """
    if not topics:
        return []
    normalized = []
    seen = set()
    for topic in topics:
        if not topic:
            continue
        t = topic.strip()
        # 直接匹配
        if t in TOPICS:
            if t not in seen:
                seen.add(t)
                normalized.append(t)
            continue
        # 同义词归一化
        if t in TOPIC_ALIASES:
            mapped = TOPIC_ALIASES[t]
            if mapped not in seen:
                seen.add(mapped)
                normalized.append(mapped)
            continue
        # 尝试模糊匹配
        matched = False
        for valid in TOPICS:
            if valid in t or t in valid:
                if valid not in seen:
                    seen.add(valid)
                    normalized.append(valid)
                matched = True
                break
        if not matched:
            logger.warning(f"非法主题 '{topic}'，已跳过")
    # 限制最多 2 个主题
    return normalized[:2]


def normalize_content_format(content_format: Optional[str]) -> str:
    """校验 L4 内容形式，非法值兜底为 'text'"""
    if not content_format:
        return "text"
    cf = content_format.strip().lower()
    if cf in CONTENT_FORMATS:
        return cf
    logger.warning(f"非法内容形式 '{content_format}'，已归一化为 'text'")
    return "text"


def validate_memory_tags(
    category: Optional[str] = None,
    mood: Optional[str] = None,
    content_format: Optional[str] = None,
) -> dict:
    """
    一次性校验个人记忆库的全部标签
    返回归一化后的字典
    """
    return {
        "category": normalize_category(category),
        "mood": normalize_mood_required(mood),
        "content_format": normalize_content_format(content_format),
    }


def validate_collection_tags(
    content_type: Optional[str] = None,
    topics: Optional[List[str]] = None,
    mood: Optional[str] = None,
    content_format: Optional[str] = None,
) -> dict:
    """
    一次性校验知识收藏库的全部标签
    返回归一化后的字典
    """
    return {
        "content_type": normalize_collection_type(content_type),
        "topics": normalize_topics(topics),
        "mood": normalize_mood(mood),  # 收藏库情绪可选
        "content_format": normalize_content_format(content_format),
    }
