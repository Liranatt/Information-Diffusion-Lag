from __future__ import annotations

import asyncio
import json

from live.config import CONFIG
from live.dashboard import healthz
from live.policy import REQUIRED_KEYS, load_live_policy


def test_configured_live_policy_artifacts_are_present_and_loadable():
    policy = load_live_policy(CONFIG)

    assert CONFIG.experiment == "T1+T2+T3+T4"
    assert CONFIG.benchmark == "SPY"
    assert not [key for key in REQUIRED_KEYS if key not in policy]
    assert int(policy["max_concurrent"]) > 0
    assert 0 < float(policy["position_size_pct"]) <= 1


def test_dashboard_health_requires_a_loadable_policy():
    response = asyncio.run(healthz())
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["policy_ok"] is True
    assert payload["experiment"] == CONFIG.experiment
    assert payload["benchmark"] == CONFIG.benchmark
