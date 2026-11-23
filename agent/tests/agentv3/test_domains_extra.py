from datetime import date, datetime

from agentv3.contracts import Digest, DigestSection, Episode, MemoryEntry, MemoryLayer, SourceKind
from agentv3.domain.emotion import describe
from agentv3.domain.exclusion import ExclusionPolicy
from agentv3.domain.export import manifest
from agentv3.domain.terms import rewrite, violations


def _ep(text="今天去看桂花", people=("阿婆",), place="西湖", day=date(2025, 10, 1)):
    return Episode(episode_id="ep1", person_id="p1", source=SourceKind.TEXT, text=text, day=day, people=people, place=place, evidence_id="ev1")


def test_exclusion_blocks_person_word_and_range():
    ep = _ep()
    assert ExclusionPolicy(people=("阿婆",)).blocked_reason(ep) == "blocked_person"
    assert ExclusionPolicy(words=("桂花",)).blocked_reason(ep) == "blocked_word"
    assert ExclusionPolicy(ranges=[(date(2025, 9, 1), date(2025, 10, 2))]).blocked_reason(ep) == "blocked_range"
    assert ExclusionPolicy().filter([ep]) == [ep]


def test_terms_rewrite_and_flag_diagnosis():
    assert rewrite("老年痴呆的邻居") == "认知障碍的邻居"
    assert violations("她确诊抑郁症") == ["抑郁症", "确诊"]
    assert violations("她最近有点累") == []


def test_emotion_is_descriptive_no_confidence_or_diagnosis():
    result = describe("我睡不着，有点慌")
    assert result["cues"] == ["焦虑"]
    assert "confidence" not in result
    assert "诊断" not in str(result)


def test_export_manifest_is_deterministic_and_counts_real_fields():
    entry = MemoryEntry(entry_id="m1", person_id="p1", layer=MemoryLayer.FACT, text="喜欢桂花", valid_from=date(2025, 1, 1), recorded_at=datetime(2025, 1, 1), evidence=("e1",))
    digest = Digest(person_id="p1", sections=(DigestSection(name="facts", text="喜欢桂花"),), state="ok")
    first = manifest("p1", [entry], digest)
    second = manifest("p1", [entry], digest)
    assert first == second and first["entries"] == 1 and first["sha256"]
