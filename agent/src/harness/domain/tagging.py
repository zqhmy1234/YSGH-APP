"""V3 tagging rules, ported from ``tools/tag_rules.py`` and made deterministic.

The old SOP was a prompt: it told the model which enum to choose. Here the same
semantics are executable rules, so Ingestor/Sculptor can validate them without
asking an LLM. A short ``tag_hint`` may still be assembled into the prompt for
explicit user corrections, but the default path is code.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from harness.contracts import TagSet

MEMORY_CATEGORIES = ("情绪", "事件", "想法", "灵感", "人物", "地点", "待分类")
COLLECTION_TYPES = ("文章", "帖子", "观点", "数据", "图片", "其他")
MOODS = ("开心", "平静", "感恩", "期待", "悲伤", "焦虑", "愤怒", "疲惫")
TOPICS = ("学习", "工作", "健康", "美食", "情感", "旅行", "家庭", "社交", "兴趣", "目标", "生活", "认知", "其他")
CONTENT_FORMATS = ("text", "image", "voice", "link")

COLLECTION_TYPE_ALIASES = {
    "article": "文章",
    "post": "帖子",
    "opinion": "观点",
    "data": "数据",
    "visual": "图片",
    "other": "其他",
}

MOOD_ALIASES = {
    "高兴": "开心",
    "愉快": "开心",
    "兴奋": "开心",
    "喜悦": "开心",
    "快乐": "开心",
    "平和": "平静",
    "放松": "平静",
    "安心": "平静",
    "惬意": "平静",
    "舒适": "平静",
    "感激": "感恩",
    "珍惜": "感恩",
    "温暖": "感恩",
    "憧憬": "期待",
    "盼望": "期待",
    "跃跃欲试": "期待",
    "难过": "悲伤",
    "失落": "悲伤",
    "沮丧": "悲伤",
    "委屈": "悲伤",
    "低落": "悲伤",
    "不安": "焦虑",
    "担心": "焦虑",
    "紧张": "焦虑",
    "压力大": "焦虑",
    "压力": "焦虑",
    "生气": "愤怒",
    "不满": "愤怒",
    "烦躁": "愤怒",
    "憋屈": "愤怒",
    "气愤": "愤怒",
    "累": "疲惫",
    "倦怠": "疲惫",
    "心力交瘁": "疲惫",
    "想摆烂": "疲惫",
    "疲倦": "疲惫",
}

TOPIC_ALIASES = {
    "读书": "学习",
    "课程": "学习",
    "技能": "学习",
    "考试": "学习",
    "职场": "工作",
    "项目": "工作",
    "晋升": "工作",
    "运动": "健康",
    "睡眠": "健康",
    "体检": "健康",
    "养生": "健康",
    "饮食": "美食",
    "做饭": "美食",
    "餐厅": "美食",
    "探店": "美食",
    "食谱": "美食",
    "亲情": "情感",
    "友情": "情感",
    "爱情": "情感",
    "人际关系": "情感",
    "出游": "旅行",
    "见闻": "旅行",
    "攻略": "旅行",
    "风景": "旅行",
    "家人": "家庭",
    "家务": "家庭",
    "聚会": "社交",
    "朋友": "社交",
    "爱好": "兴趣",
    "艺术": "兴趣",
    "娱乐": "兴趣",
    "游戏": "兴趣",
    "计划": "目标",
    "梦想": "目标",
    "规划": "目标",
    "打卡": "目标",
    "日常": "生活",
    "消费": "生活",
    "起居": "生活",
    "观点": "认知",
    "思考": "认知",
    "方法论": "认知",
    "顿悟": "认知",
}

_MOOD_CUES = {
    "开心": ("开心", "高兴", "笑了", "太好了", "愉快"),
    "悲伤": ("难过", "想哭", "失落", "委屈", "心里堵"),
    "焦虑": ("焦虑", "担心", "睡不着", "慌", "紧张"),
    "愤怒": ("生气", "愤怒", "气死", "烦躁"),
    "疲惫": ("累", "困", "没力气", "撑不住"),
    "感恩": ("感谢", "感恩", "谢谢"),
    "期待": ("期待", "盼望", "等不及"),
}
_TOPIC_CUES = {
    "学习": ("学习", "读书", "课程", "考试", "技能"),
    "工作": ("工作", "上班", "项目", "同事", "晋升"),
    "健康": ("健康", "运动", "健身", "锻炼", "跑步", "睡眠", "体检", "生病"),
    "美食": ("美食", "吃饭", "做饭", "餐厅", "好吃"),
    "情感": ("感情", "爱情", "友情", "亲情", "朋友"),
    "旅行": ("旅行", "旅游", "出游", "风景", "酒店"),
    "家庭": ("家庭", "家人", "父母", "孩子", "家务"),
    "社交": ("聚会", "社交", "朋友", "饭局"),
    "兴趣": ("兴趣", "爱好", "游戏", "电影", "音乐"),
    "目标": ("目标", "计划", "梦想", "规划", "打卡"),
    "生活": ("生活", "日常", "消费", "搬家", "购物"),
    "认知": ("想法", "观点", "思考", "方法论", "顿悟"),
}
_EVENT_CUES = ("今天", "昨天", "上周", "去年", "那次", "当时", "去了", "见面", "发生", "旅行")
_IDEA_CUES = ("我觉得", "认为", "观点", "想法", "思考", "应该")
_INSPIRATION_CUES = ("灵感", "点子", "想到一个", "可以做", "不如")


@dataclass(frozen=True)
class TaggingInput:
    text: str
    source: str = "text"
    origin: str = ""  # personal / external / knowledge
    has_image: bool = False
    people: tuple[str, ...] = ()
    place: str = ""


@dataclass
class TaggingResult:
    tags: TagSet
    issues: list[str] = field(default_factory=list)


def coerce_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()] or None
        return [p.strip() for p in re.split(r"[,，、;；]", text) if p.strip()] or None
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()] or None
    return [str(value).strip()]


def normalize_category(value: str | None) -> str:
    if not value:
        return "待分类"
    text = value.strip()
    if text in MEMORY_CATEGORIES:
        return text
    for valid in MEMORY_CATEGORIES:
        if valid in text or text in valid:
            return valid
    return "待分类"


def normalize_collection_type(value: str | None) -> str:
    if not value:
        return "其他"
    text = value.strip()
    if text in COLLECTION_TYPES:
        return text
    if text.lower() in COLLECTION_TYPE_ALIASES:
        return COLLECTION_TYPE_ALIASES[text.lower()]
    for valid in COLLECTION_TYPES:
        if valid in text or text in valid:
            return valid
    return "其他"


def normalize_mood(value: str | None, *, required: bool = True) -> str:
    if not value:
        return "平静" if required else ""
    text = value.strip()
    if text in MOODS:
        return text
    if text in MOOD_ALIASES:
        return MOOD_ALIASES[text]
    for valid in MOODS:
        if valid in text or text in valid:
            return valid
    return "平静" if required else ""


def normalize_topics(values: Iterable[str] | None) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        text = str(raw).strip()
        if not text:
            continue
        mapped = text if text in TOPICS else TOPIC_ALIASES.get(text, "")
        if not mapped:
            mapped = next((valid for valid in TOPICS if valid in text or text in valid), "")
        if mapped and mapped not in seen:
            seen.add(mapped)
            out.append(mapped)
    return tuple(out[:2])


def normalize_content_format(value: str | None) -> str:
    text = (value or "text").strip().lower()
    return text if text in CONTENT_FORMATS else "text"


def route_image(*, has_image: bool, origin: str = "") -> str:
    """Return ``personal`` / ``knowledge`` / empty; never guess from pixels."""
    if not has_image:
        return ""
    if origin in {"external", "knowledge"}:
        return "knowledge"
    return "personal"


def detect_mood(text: str) -> str:
    for mood, cues in _MOOD_CUES.items():
        if any(cue in text for cue in cues):
            return mood
    return ""


def detect_topics(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for topic, cues in _TOPIC_CUES.items():
        if any(cue in text for cue in cues):
            found.append(topic)
    return normalize_topics(found)


def classify_category(text: str, *, people: tuple[str, ...], place: str, has_image: bool) -> str:
    if people and any(p and p in text for p in people):
        return "人物"
    if place and place in text:
        return "地点"
    if any(cue in text for cue in _INSPIRATION_CUES):
        return "灵感"
    if any(cue in text for cue in _IDEA_CUES):
        return "想法"
    if any(cue in text for cue in _EVENT_CUES):
        return "事件"
    if detect_mood(text):
        return "情绪"
    return "待分类"


def classify_tags(input: TaggingInput) -> TaggingResult:
    text = input.text.strip()
    issues: list[str] = []
    has_image = input.has_image or input.source in {"photo", "image"}
    image_route = route_image(has_image=has_image, origin=input.origin)

    if input.origin in {"external", "knowledge"} or input.source in {"link", "import"}:
        library = "knowledge"
    elif input.origin == "personal" or input.source in {"text", "photo", "voice", "wechat"}:
        library = "personal"
    else:
        library = "personal"
        issues.append("unknown_origin_default_personal")

    content_format = "text"
    if text.startswith("[语音输入]") or input.source == "voice":
        content_format = "voice"
    elif has_image:
        content_format = "image"
    elif input.source == "link" or text.startswith(("http://", "https://")):
        content_format = "link"

    if library == "knowledge":
        category = ""
        content_type = "图片" if has_image else "其他"
        if input.source == "link" or input.text.startswith(("http://", "https://")):
            content_type = "文章"
        mood = normalize_mood(detect_mood(text), required=False)
        topics = detect_topics(text)
    else:
        category = classify_category(text, people=input.people, place=input.place, has_image=has_image)
        content_type = ""
        mood = normalize_mood(detect_mood(text) or "平静", required=True)
        topics = detect_topics(text)

    tags = TagSet(
        library=library,
        category=category,
        content_type=content_type,
        mood=mood,
        topics=topics,
        content_format=content_format,  # type: ignore[arg-type]
        image_route=image_route,  # type: ignore[arg-type]
    )
    if not text:
        issues.append("empty_text")
    if library == "personal" and not category:
        issues.append("category_defaulted")
    if library == "knowledge" and not content_type:
        issues.append("collection_type_defaulted")
    return TaggingResult(tags=tags, issues=issues)


def mapping_layer(tags: TagSet) -> str:
    """Map V3 library labels onto ledger lifecycle; caller still validates."""
    if tags.library == "knowledge":
        return "asset"
    if tags.library == "personal":
        return "narrative"
    return ""
