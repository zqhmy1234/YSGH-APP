"""Terminology guard: prefer neutral wording, never diagnose from a phrase."""

from __future__ import annotations

PREFERRED = {
    "老年痴呆": "认知障碍",
    "疯子": "情绪激动的人",
    "哑巴": "无法说话的人",
    "瞎子": "视障人士",
    "瘸子": "行动不便的人",
}
DIAGNOSTIC = ("抑郁症", "焦虑症", "自闭症", "精神病", "确诊")


def rewrite(text: str) -> str:
    out = text
    for bad, good in PREFERRED.items():
        out = out.replace(bad, good)
    return out


def violations(text: str) -> list[str]:
    found = [bad for bad in PREFERRED if bad in text]
    found += [term for term in DIAGNOSTIC if term in text]
    return found
