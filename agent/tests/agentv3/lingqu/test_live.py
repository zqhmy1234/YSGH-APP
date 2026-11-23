"""Live Lingqu tests. Never run in the normal suite.

    LINGQU_LIVE=1 COMPANY_LLM_API_KEY=... pytest -m live tests/agentv3/lingqu/test_live.py -q

Without both the live flag and a key, every test skips. A missing key is never
turned into a pass.
"""

from __future__ import annotations

import os

import pytest

from agentv3.contracts import LLMRequest
from agentv3.lingqu.contracts import HealthState
from agentv3.lingqu.gateway import LingquGateway

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LINGQU_LIVE") != "1", reason="LINGQU_LIVE!=1"),
    pytest.mark.skipif(not os.environ.get("COMPANY_LLM_API_KEY"), reason="COMPANY_LLM_API_KEY not set"),
]


def test_live_deepseek_chat():
    gateway = LingquGateway()
    reply = gateway.complete(LLMRequest(system="只回复 OK。", user="回复 OK", max_chars=64, temperature=0.0))
    assert reply.text.strip()
    assert len(reply.text) < 2000


def test_live_all_providers_probe():
    gateway = LingquGateway()
    states = {}
    for profile in gateway.profiles():
        if profile.provider.value in states:
            continue
        states[profile.provider.value] = gateway.probe(profile.id).state
    assert len(states) == 5
    assert states["deepseek"] is HealthState.OK
