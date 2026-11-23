"""Descriptive emotion cues: describe what was said, never diagnose or score."""

from __future__ import annotations

CUES = {
    "疲惫": ("累", "困", "没力气", "撑不住"),
    "难过": ("想哭", "难受", "委屈", "心里堵"),
    "焦虑": ("睡不着", "担心", "慌", "怕"),
    "平静": ("还好", "安静", "舒服", "放松"),
    "开心": ("开心", "高兴", "笑了", "太好了"),
}


def describe(text: str) -> dict[str, object]:
    found = [name for name, words in CUES.items() if any(word in text for word in words)]
    return {"cues": found, "evidence": [word for words in CUES.values() for word in words if word in text]}
