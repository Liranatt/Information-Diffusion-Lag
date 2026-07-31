from __future__ import annotations

import pandas as pd

from selection.stage3a_execution_safe import (
    ENTRY_THRESHOLD,
    _entry_and_exit_plans,
    _next_open_after_signal,
)


def test_next_open_requires_signal_before_open() -> None:
    bars = [
        {"date": pd.Timestamp("2025-10-08", tz="UTC"), "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
        {"date": pd.Timestamp("2025-10-09", tz="UTC"), "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
    ]
    pre_open = pd.Timestamp("2025-10-08 12:00:00+00:00")  # 08:00 ET
    after_open = pd.Timestamp("2025-10-08 15:00:00+00:00")  # 11:00 ET
    assert _next_open_after_signal(bars, pre_open)["date"] == pd.Timestamp("2025-10-08", tz="UTC")
    assert _next_open_after_signal(bars, after_open)["date"] == pd.Timestamp("2025-10-09", tz="UTC")


def test_entry_audit_never_uses_post_entry_probability() -> None:
    oof = pd.DataFrame([
        {
            "stage2e_candidate_id": "candidate",
            "market_id": "market",
            "symbol": "TEST",
            "benchmark": "SPY",
            "outer_fold": 0,
            "t_theta": "2025-10-08T00:00:00Z",
            "t_e": "2025-10-13T00:00:00Z",
            "question": "Will TEST beat earnings?",
        }
    ])
    histories = {
        "market": pd.DataFrame({
            "source_ts_utc": pd.to_datetime(["2025-10-08T12:00:00Z", "2025-10-08T16:00:00Z"], utc=True),
            "probability_yes": [ENTRY_THRESHOLD, 0.2],
        })
    }
    bars = {
        "TEST": [
            {"date": pd.Timestamp("2025-10-07", tz="UTC"), "open": 99.0, "high": 100.0, "low": 98.0, "close": 99.0},
            {"date": pd.Timestamp("2025-10-08", tz="UTC"), "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0},
            {"date": pd.Timestamp("2025-10-09", tz="UTC"), "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0},
            {"date": pd.Timestamp("2025-10-10", tz="UTC"), "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.0},
        ]
    }
    audit, plans = _entry_and_exit_plans(oof, histories, bars)
    entry = audit.iloc[0]
    assert entry["status"] == "usable"
    assert entry["post_entry_observations_used_for_entry"] == 0
    assert pd.Timestamp(entry["signal_ts_utc"]) < pd.Timestamp(entry["entry_open_timestamp_utc"])
    assert len(plans) == 2
    assert plans["exit_strictly_before_legal_te"].all()
