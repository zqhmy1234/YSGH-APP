from __future__ import annotations

from agentv3.domain.tagging import (
    TaggingInput,
    classify_tags,
    normalize_category,
    normalize_collection_type,
    normalize_mood,
    normalize_topics,
    route_image,
)


def test_personal_text_event_and_mood():
    result = classify_tags(TaggingInput(text="今天和朋友去西湖，特别开心", people=("朋友",), place="西湖"))
    assert result.tags.library == "personal"
    assert result.tags.category == "人物"
    assert result.tags.mood == "开心"
    assert result.tags.content_format == "text"


def test_unknown_mood_defaults_to_calm_and_invalid_category_to_pending():
    result = classify_tags(TaggingInput(text="今天有点说不上来", origin="personal"))
    assert result.tags.mood == "平静"
    assert result.tags.category in {"事件", "待分类"}
    assert normalize_category("被删除的分类") == "待分类"
    assert normalize_collection_type("podcast") == "其他"
    assert normalize_mood("说不上来") == "平静"


def test_topic_alias_and_limit_two():
    assert normalize_topics(["读书", "职场", "运动", "吃饭"]) == ("学习", "工作")
    result = classify_tags(TaggingInput(text="最近在读书和健身", origin="personal"))
    assert result.tags.topics == ("学习", "健康")


def test_knowledge_and_image_routing():
    personal_image = classify_tags(TaggingInput(text="拍的西湖", source="photo", origin="personal", has_image=True))
    assert personal_image.tags.image_route == "personal"
    assert personal_image.tags.content_format == "image"
    external_image = classify_tags(TaggingInput(text="一张外链图", source="photo", origin="external", has_image=True))
    assert external_image.tags.library == "knowledge"
    assert external_image.tags.image_route == "knowledge"
    assert external_image.tags.content_type == "图片"
    assert route_image(has_image=True) == "personal"
    assert route_image(has_image=False) == ""


def test_voice_prefix_is_voice():
    result = classify_tags(TaggingInput(text="[语音输入] 今天好累", source="voice", origin="personal"))
    assert result.tags.content_format == "voice"
    assert result.tags.mood == "疲惫"
