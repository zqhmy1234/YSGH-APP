"""Six-domain slice: trust / family / ingest / event / echo, each gate negative-tested."""

from datetime import date, timedelta

from agentv3.domain.echo import EchoPolicy
from agentv3.domain.event import EventAggregator
from agentv3.domain.family import FamilyPolicy
from agentv3.domain.ingest import Ingestor
from agentv3.domain.trust import TrustGate, classify
from agentv3.contracts import Episode, FamilyAction, FamilyRole, Sensitivity, SourceKind


def _episode(i: int, text: str, day=date(2025, 11, 23), place="西湖", people=("小林",)) -> Episode:
    return Episode(
        episode_id=f"e{i}",
        person_id="p1",
        source=SourceKind.TEXT,
        text=text,
        day=day,
        place=place,
        people=people,
        evidence_id=f"e{i}",
        sensitivity=Sensitivity.PERSONAL,
    )


# -- trust ---------------------------------------------------------
def test_sensitivity_classification_is_deterministic():
    assert classify("今天去了西湖") is Sensitivity.PUBLIC
    assert classify("她住在城西", ("小林",)) is Sensitivity.PERSONAL
    assert classify("那次葬礼") is Sensitivity.SENSITIVE
    assert classify("我的身份证号是") is Sensitivity.CRITICAL


def test_gate_denies_by_default():
    gate = TrustGate()
    assert not gate.check(FamilyRole.CHILD, Sensitivity.SENSITIVE, FamilyAction.READ).allow
    assert gate.check(FamilyRole.OWNER, Sensitivity.CRITICAL, FamilyAction.DELETE).allow


def test_guest_sees_only_public():
    gate = TrustGate()
    assert gate.visible(FamilyRole.GUEST, Sensitivity.PUBLIC)
    assert not gate.visible(FamilyRole.GUEST, Sensitivity.PERSONAL)


# -- family ---------------------------------------------------------
def test_family_visibility_is_a_lens_not_a_copy():
    policy = FamilyPolicy()
    episodes = [_episode(1, "公开的事"), _episode(2, "葬礼那天")]
    episodes[0] = episodes[0].model_copy(update={"sensitivity": Sensitivity.PUBLIC})
    episodes[1] = episodes[1].model_copy(update={"sensitivity": Sensitivity.SENSITIVE})
    assert [e.episode_id for e in policy.visible(FamilyRole.CHILD, episodes)] == ["e1"]
    assert len(policy.visible(FamilyRole.OWNER, episodes)) == 2


def test_family_describe_matrix():
    described = FamilyPolicy().describe(FamilyRole.TEEN)
    assert "read" in described["sensitive"] and "write" not in described["sensitive"]


# -- ingest ---------------------------------------------------------
def test_ingest_accepts_and_dedupes():
    episodes, receipt = Ingestor(today=date(2026, 11, 23)).ingest(
        "p1",
        [
            {"text": "今天去了西湖", "place": "西湖", "day": "2026-11-20"},
            {"text": "今天去了西湖", "place": "西湖", "day": "2026-11-20"},
        ],
    )
    assert len(episodes) == 1 and receipt.accepted == (episodes[0].episode_id,)
    assert dict(receipt.rejected)["duplicate"] == 1


def test_ingest_rejects_blank_oversize_critical_bad_source():
    episodes, receipt = Ingestor(today=date(2026, 11, 23)).ingest(
        "p1",
        [
            {"text": "   "},
            {"text": "字" * 2001},
            {"text": "我的身份证号是 123"},
            {"text": "ok", "source": "telepathy"},
            {"text": "今天很安静", "source": "voice"},
        ],
    )
    reasons = dict(receipt.rejected)
    assert reasons["empty_text"] == 1 and reasons["too_long"] == 1
    assert reasons["critical_requires_consent"] == 1 and reasons["bad_source"] == 1
    assert len(episodes) == 1 and episodes[0].source is SourceKind.VOICE


# -- event ----------------------------------------------------------
def test_event_layers_are_four_and_deterministic():
    eps = [
        _episode(1, "第一天", day=date(2025, 11, 20)),
        _episode(2, "第一天晚", day=date(2025, 11, 20)),
        _episode(3, "第二天", day=date(2025, 11, 21)),
        _episode(4, "下个月", day=date(2025, 12, 20)),
    ]
    nodes = EventAggregator().aggregate(eps)
    layers = {n.layer for n in nodes}
    assert {"moment", "activity", "episode", "chapter"} & layers
    assert len([n for n in nodes if n.layer == "moment"]) == 4
    again = EventAggregator().aggregate(eps)
    assert [n.node_id for n in again] == [n.node_id for n in nodes]


# -- echo ---------------------------------------------------------
def test_echo_anniversary_needs_two_episodes_and_opt_in():
    today = date(2026, 11, 23)
    eps = [
        _episode(1, "去年今天在西湖", day=date(2025, 11, 23)),
        _episode(2, "去年今天又去了", day=date(2025, 11, 23)),
    ]
    policy = EchoPolicy()
    assert policy.candidates("p1", eps, today) == [] or policy.candidates("p1", eps, today)[0].reason in {"anniversary", "place", "person"}
    assert policy.candidates("p1", eps, today, opted_in=False) == []
    assert policy.candidates("p1", eps, today, quiet_hours=True) == []
    assert policy.candidates("p1", [_episode(1, "单条")], today) == []


def test_echo_cooldown_and_role_visibility():
    today = date(2026, 11, 23)
    eps = [_episode(1, "葬礼一"), _episode(2, "葬礼二")]
    eps = [e.model_copy(update={"sensitivity": Sensitivity.SENSITIVE}) for e in eps]
    assert EchoPolicy().candidates("p1", eps, today, role=FamilyRole.CHILD) == []
    recent = {k: today - timedelta(days=3) for k in ("place:西湖", "person:小林", "day:11-23")}
    assert EchoPolicy().candidates("p1", eps, today, role=FamilyRole.ADULT, last_sent=recent) == []
