from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from analysis.h1_expectation_protocol import (
    _stable_frame_hash,
    build_cluster_inference,
    build_secondary_benchmark_diagnostics,
    build_timing_audit,
    build_true_event_frame,
)


def _trades() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "candidate_id": "C1",
                "market_id": "M1",
                "event_id": "E1",
                "symbol": "AAA",
                "question": "event one",
                "event_family": "geo",
                "threshold_cross_time": "2026-01-05 00:00:00+00:00",
                "entry_date": "2026-01-05",
                "entry_price": 100.0,
                "exit_date_t_minus_1": "2026-01-08",
                "exit_price": 110.0,
                "gross_return": 0.10,
                "gross_pnl": 1000.0,
                "net_return": 0.099,
                "net_pnl": 990.0,
                "notional": 10_000.0,
                "estimated_transaction_cost": 10.0,
            },
            # Exact duplicate opportunity under another contract must not earn
            # an additional weight inside the economic event.
            {
                "candidate_id": "C2",
                "market_id": "M2",
                "event_id": "E1",
                "symbol": "AAA",
                "question": "event one duplicate",
                "event_family": "geo",
                "threshold_cross_time": "2026-01-05 00:00:00+00:00",
                "entry_date": "2026-01-05",
                "entry_price": 100.0,
                "exit_date_t_minus_1": "2026-01-08",
                "exit_price": 110.0,
                "gross_return": 0.10,
                "gross_pnl": 1000.0,
                "net_return": 0.099,
                "net_pnl": 990.0,
                "notional": 10_000.0,
                "estimated_transaction_cost": 10.0,
            },
            {
                "candidate_id": "C3",
                "market_id": "M3",
                "event_id": "E1",
                "symbol": "BBB",
                "question": "event one second asset",
                "event_family": "geo",
                "threshold_cross_time": "2026-01-05 00:00:00+00:00",
                "entry_date": "2026-01-05",
                "entry_price": 50.0,
                "exit_date_t_minus_1": "2026-01-08",
                "exit_price": 50.0,
                "gross_return": 0.0,
                "gross_pnl": 0.0,
                "net_return": -0.001,
                "net_pnl": -10.0,
                "notional": 10_000.0,
                "estimated_transaction_cost": 10.0,
            },
            {
                "candidate_id": "C4",
                "market_id": "M4",
                "event_id": "E2",
                "symbol": "CCC",
                "question": "event two",
                "event_family": "earnings",
                "threshold_cross_time": "2026-04-06 00:00:00+00:00",
                "entry_date": "2026-04-06",
                "entry_price": 100.0,
                "exit_date_t_minus_1": "2026-04-09",
                "exit_price": 90.0,
                "gross_return": -0.10,
                "gross_pnl": -1000.0,
                "net_return": -0.101,
                "net_pnl": -1010.0,
                "notional": 10_000.0,
                "estimated_transaction_cost": 10.0,
            },
        ]
    )


def _prices() -> dict[str, list[tuple]]:
    return {
        "AAA": [
            (pd.Timestamp("2026-01-05", tz="UTC"), 101.0, 99.0, 100.0),
            (pd.Timestamp("2026-01-08", tz="UTC"), 111.0, 109.0, 110.0),
        ],
        "XLK": [
            (pd.Timestamp("2026-01-05", tz="UTC"), 100.0, 99.0, 100.0),
            (pd.Timestamp("2026-01-08", tz="UTC"), 106.0, 104.0, 105.0),
        ],
        "SPY": [
            (pd.Timestamp("2026-01-05", tz="UTC"), 100.0, 99.0, 100.0),
            (pd.Timestamp("2026-01-08", tz="UTC"), 103.0, 101.0, 102.0),
        ],
    }


def test_true_event_collapse_equal_weights_unique_opportunities() -> None:
    events = build_true_event_frame(_trades())
    event_one = events.set_index("event_id").loc["E1"]

    assert event_one["n_candidate_rows"] == 3
    assert event_one["n_opportunities"] == 2
    assert event_one["n_symbols"] == 2
    assert event_one["mean_raw_net_return"] == 0.049


def test_cluster_inference_is_deterministic() -> None:
    trades = _trades()
    events = build_true_event_frame(trades)

    first = build_cluster_inference(trades, events, n_boot=200, seed=7)
    second = build_cluster_inference(trades, events, n_boot=200, seed=7)

    pd.testing.assert_frame_equal(first, second)
    assert {"economic_event_id", "symbol", "entry_week_block", "entry_month_block"}.issubset(
        set(first["cluster_type"])
    )


def test_timing_audit_uses_strictly_next_stored_close() -> None:
    trades = _trades().iloc[[0]].copy()
    prices = {
        "AAA": [
            (pd.Timestamp("2026-01-05", tz="UTC"), 101.0, 99.0, 100.0),
            (pd.Timestamp("2026-01-06", tz="UTC"), 106.0, 103.0, 105.0),
            (pd.Timestamp("2026-01-08", tz="UTC"), 111.0, 108.0, 110.0),
        ]
    }

    audit = build_timing_audit(trades, prices).iloc[0]

    assert not bool(audit["same_session_entry_verified"])
    assert audit["conservative_entry_date"] == "2026-01-06"
    assert audit["conservative_entry_price"] == 105.0
    assert audit["conservative_trading_session_latency"] == 1


def test_mapping_hash_is_order_invariant() -> None:
    frame = pd.DataFrame(
        [
            {"market_id": "M2", "symbol": "BBB", "question": "q2"},
            {"market_id": "M1", "symbol": "AAA", "question": "q1"},
        ]
    )
    reversed_frame = frame.iloc[::-1].reset_index(drop=True)

    assert _stable_frame_hash(frame, ("market_id", "symbol", "question")) == _stable_frame_hash(
        reversed_frame, ("market_id", "symbol", "question")
    )


def test_secondary_benchmark_diagnostics_uses_sector_etf_when_available() -> None:
    trades = _trades().iloc[[0]].copy()
    candidates = pd.DataFrame(
        [
            {
                "market_id": "M1",
                "symbol": "AAA",
                "feat_sector": "Technology",
                "sector_etf": "XLK",
            }
        ]
    )

    diagnostics = build_secondary_benchmark_diagnostics(trades, _prices(), candidates)
    sector_row = diagnostics[
        (diagnostics["level"] == "candidate_observation")
        & (diagnostics["benchmark"] == "sector_etf")
    ].iloc[0]

    assert sector_row["n"] == 1
    assert sector_row["n_stock_better"] == 1
    assert sector_row["n_benchmark_better"] == 0
    assert sector_row["share_stock_better"] == pytest.approx(1.0)
    assert sector_row["mean_excess_net_pnl"] == pytest.approx(
        sector_row["mean_stock_net_pnl"] - sector_row["mean_benchmark_net_pnl"]
    )


def test_secondary_benchmark_diagnostics_includes_spy_control() -> None:
    trades = _trades().iloc[[0]].copy()
    candidates = pd.DataFrame(
        [
            {
                "market_id": "M1",
                "symbol": "AAA",
                "feat_sector": "Technology",
                "sector_etf": "XLK",
            }
        ]
    )

    diagnostics = build_secondary_benchmark_diagnostics(trades, _prices(), candidates)
    spy_row = diagnostics[
        (diagnostics["level"] == "candidate_observation")
        & (diagnostics["benchmark"] == "SPY")
    ].iloc[0]

    assert spy_row["n"] == 1
    assert spy_row["n_stock_better"] == 1
    assert spy_row["mean_excess_net_pnl"] == pytest.approx(
        spy_row["mean_stock_net_pnl"] - spy_row["mean_benchmark_net_pnl"]
    )


def test_secondary_benchmark_diagnostics_skips_unknown_sector_for_sector_control() -> None:
    trades = _trades().iloc[[0]].copy()
    candidates = pd.DataFrame(
        [
            {
                "market_id": "M1",
                "symbol": "AAA",
                "feat_sector": "Unknown",
            }
        ]
    )

    diagnostics = build_secondary_benchmark_diagnostics(trades, _prices(), candidates)
    sector_rows = diagnostics[
        (diagnostics["level"] == "candidate_observation")
        & (diagnostics["benchmark"] == "sector_etf")
    ]

    assert int(sector_rows.iloc[0]["n"]) == 0