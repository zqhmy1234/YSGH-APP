"""B5-01 reconcile probes: exact sets pass, one edited text fails."""

from __future__ import annotations

from app.domain.memory.reconcile import reconcile_texts


def test_identical_text_sets_are_clean():
    texts = ["今天去了西湖。", "女儿来看我。"]
    report = reconcile_texts(texts, list(texts))
    assert report.diff_count == 0
    assert report.old_count == 2 and report.new_count == 2
    assert report.within_threshold is True


def test_one_edited_text_is_reported_with_sample():
    old = [f"记忆 {i}" for i in range(1000)]
    new = list(old)
    new[10] = "被偷偷改掉的一条"
    report = reconcile_texts(old, new, sample_limit=3)
    assert report.diff_count == 2  # one old-only, one new-only
    assert report.within_threshold is False
    assert any("old_only" in s for s in report.sample_diff)
    assert any("new_only" in s for s in report.sample_diff)


def test_threshold_is_strictly_greater_than():
    old = [f"m{i}" for i in range(1000)]
    new = list(old)
    new[0] = "one change"
    report = reconcile_texts(old, new, threshold=0.002)
    assert report.diff_ratio == 2 / 1000
    assert report.within_threshold is True


def test_both_empty_is_clean_not_unavailable():
    report = reconcile_texts([], [])
    assert report.diff_count == 0
    assert report.within_threshold is True
