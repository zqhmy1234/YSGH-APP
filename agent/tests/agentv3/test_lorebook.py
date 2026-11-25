from __future__ import annotations

from prompts.cards import CharacterBook, LorebookEntry, activate_lorebook, render_lorebook


def _book():
    return CharacterBook(
        entries=(
            LorebookEntry(keys=("西湖",), content="西湖在杭州", order=1, uid="a"),
            LorebookEntry(
                keys=("西湖", "断桥"), secondary_keys=("断桥",), selective=True, content="断桥残雪", order=2, uid="b"
            ),
            LorebookEntry(constant=True, content="你叫阿光", order=0, uid="c"),
            LorebookEntry(keys=("西湖",), probability=0, content="永远不该出现", order=3, uid="d"),
            LorebookEntry(keys=("工作",), content="工作话题", order=4, uid="e"),
        )
    )


def test_constant_always_and_keyword_match():
    active = activate_lorebook(_book(), "今天想去西湖")
    texts = render_lorebook(active)
    assert "你叫阿光" in texts
    assert "西湖在杭州" in texts
    assert "工作话题" not in texts


def test_selective_requires_secondary_key():
    assert "断桥残雪" not in render_lorebook(activate_lorebook(_book(), "今天想去西湖"))
    assert "断桥残雪" in render_lorebook(activate_lorebook(_book(), "西湖断桥"))


def test_probability_zero_never_injects_and_is_deterministic():
    active = activate_lorebook(_book(), "西湖断桥", seed="s1")
    assert "永远不该出现" not in render_lorebook(active)
    assert activate_lorebook(_book(), "西湖断桥", seed="s1") == active


def test_inclusion_group_keeps_one():
    book = CharacterBook(
        entries=(
            LorebookEntry(keys=("西湖",), content="A", inclusion_group="poi", order=1, uid="a"),
            LorebookEntry(keys=("西湖",), content="B", inclusion_group="poi", order=2, uid="b"),
        )
    )
    active = activate_lorebook(book, "西湖")
    assert len(active) == 1
    assert active[0].content == "A"
