from __future__ import annotations

import pytest
import pandas as pd

import pipeline.strategy as strategy
from pipeline.data_loader import compute_features


def _ts(day: str) -> pd.Timestamp:
    return pd.Timestamp(day, tz="UTC")


def _row(**overrides):
    row = {
        "market_id": "m1",
        "symbol": "XYZ",
        "question": "Will XYZ beat quarterly earnings?",
        "t_theta": _ts("2026-01-03"),
        "t_e": _ts("2026-01-10"),
        "feat_archetype": "earnings",
        "feat_connection_strength": 0.9,
        "confidence_score": 0.9,
        "feat_llm_confidence": 0.9,
        "feat_prob_surge_since_t0": 0.0,
        "feat_runup_since_t0": 0.0,
        "split": "test",
    }
    row.update(overrides)
    return row


def _policy(**overrides):
    policy = dict(strategy.DEFAULT_POLICY)
    policy.update(
        {
            "atr_mult": 1.0,
            "lock_activate": 0.50,
            "theta_out": 0.10,
            "enter_strong": 0.75,
            "enter_floor": 0.70,
            "hold_days": 1,
            "max_prob_surge": 999.0,
            "max_price_runup": 999.0,
        }
    )
    policy.update(overrides)
    return policy


def _bars(start: str, closes: list[float]) -> list[tuple]:
    start_ts = _ts(start)
    out = []
    for i, close in enumerate(closes):
        ts = start_ts + pd.Timedelta(days=i)
        out.append((ts, close + 1.0, close - 1.0, close))
    return out


def test_enter_strong_can_fire_after_first_point():
    probs = [
        (_ts("2026-01-03"), 0.60),
        (_ts("2026-01-04"), 0.80),
    ]
    assert strategy.entry_day(probs, _ts("2026-01-03"), _policy()) == (
        _ts("2026-01-04"),
        0.80,
    )


def test_hold_days_counts_exact_consecutive_floor_points():
    probs = [
        (_ts("2026-01-03"), 0.70),
        (_ts("2026-01-04"), 0.71),
    ]
    assert strategy.entry_day(probs, _ts("2026-01-03"), _policy(hold_days=2)) == (
        _ts("2026-01-04"),
        0.71,
    )


def test_resolution_exit_is_last_bar_strictly_before_cut():
    prices = {"XYZ": _bars("2026-01-01", [99, 100, 100, 100, 100, 100, 100, 100, 100, 100])}
    probs = {
        "m1": [
            (_ts("2026-01-03"), 0.80),
            (_ts("2026-01-04"), 0.80),
            (_ts("2026-01-05"), 0.80),
            (_ts("2026-01-06"), 0.80),
            (_ts("2026-01-07"), 0.80),
            (_ts("2026-01-08"), 0.80),
            (_ts("2026-01-09"), 0.80),
        ]
    }
    trade = strategy._simulate_one_py(_row(), prices, probs, _policy(atr_mult=100.0))
    assert trade is not None
    assert trade["exit_reason"] == "resolution-1d"
    assert pd.Timestamp(trade["exit_date"], tz="UTC") < _ts("2026-01-09")
    assert trade["exit_date"] == "2026-01-08"


def test_entry_too_near_resolution_cut_is_not_opened():
    prices = {"XYZ": _bars("2026-01-01", [99, 100, 100, 100, 100, 100, 100, 100, 100])}
    probs = {"m1": [(_ts("2026-01-08"), 0.80), (_ts("2026-01-09"), 0.80)]}
    trade = strategy._simulate_one_py(
        _row(t_theta=_ts("2026-01-08")),
        prices,
        probs,
        _policy(atr_mult=100.0),
    )
    assert trade is None


def test_theta_out_precedes_price_stops():
    prices = {
        "XYZ": [
            (_ts("2026-01-01"), 101.0, 99.0, 100.0),
            (_ts("2026-01-02"), 102.0, 100.0, 101.0),
            (_ts("2026-01-03"), 101.0, 99.0, 100.0),
            (_ts("2026-01-04"), 101.0, 80.0, 90.0),
        ]
    }
    probs = {"m1": [(_ts("2026-01-03"), 0.80), (_ts("2026-01-04"), 0.40)]}
    trade = strategy._simulate_one_py(_row(), prices, probs, _policy(theta_out=0.55))
    assert trade is not None
    assert trade["exit_reason"] == "poly<0.55"


def test_earnings_trades_receive_trailing_stops():
    prices = {
        "XYZ": [
            (_ts("2026-01-01"), 101.0, 99.0, 100.0),
            (_ts("2026-01-02"), 102.0, 100.0, 101.0),
            (_ts("2026-01-03"), 101.0, 99.0, 100.0),
            (_ts("2026-01-04"), 101.0, 80.0, 90.0),
        ]
    }
    probs = {"m1": [(_ts("2026-01-03"), 0.80), (_ts("2026-01-04"), 0.80)]}
    trade = strategy._simulate_one_py(_row(feat_archetype="earnings"), prices, probs, _policy())
    assert trade is not None
    assert trade["exit_reason"].startswith("trailing_")


def test_compute_features_uses_fixed_lookbacks_for_event_gates():
    prices = [
        (_ts("2026-01-01") + pd.Timedelta(days=i), float(100 + i))
        for i in range(25)
    ]
    probs = [
        (_ts("2026-01-01"), 0.60),
        (_ts("2026-01-18"), 0.70),
        (_ts("2026-01-25"), 0.80),
    ]
    rec = compute_features(
        market_id="m1",
        event_id="e1",
        symbol="XYZ",
        question="Will XYZ beat quarterly earnings?",
        archetype="earnings",
        relevance=0.9,
        world_size=1,
        t0=_ts("2026-01-01"),
        t_e=_ts("2026-02-10"),
        t_theta=_ts("2026-01-25"),
        prices=prices,
        probs=probs,
        spy_prices=prices,
        sector_etf_prices=prices,
        sector="Technology",
    )
    assert rec is not None
    assert rec["feat_has_pre_crossing_history"] is True
    assert rec["feat_prob_surge_since_t0"] == pytest.approx(0.10)
    assert rec["feat_runup_since_t0"] == pytest.approx(124.0 / 104.0 - 1.0)


@pytest.mark.skipif(not strategy.HAVE_NUMBA, reason="numba kernel is not available")
def test_kernel_matches_python_for_c0_exit_semantics():
    prices = {
        "XYZ": [
            (_ts("2026-01-01"), 101.0, 99.0, 100.0),
            (_ts("2026-01-02"), 102.0, 100.0, 101.0),
            (_ts("2026-01-03"), 101.0, 99.0, 100.0),
            (_ts("2026-01-04"), 101.0, 80.0, 90.0),
            (_ts("2026-01-05"), 91.0, 89.0, 90.0),
        ]
    }
    probs = {"m1": [(_ts("2026-01-03"), 0.80), (_ts("2026-01-04"), 0.40)]}
    policy = _policy(theta_out=0.55)
    row = _row()

    strategy.clear_kernel_caches()
    expected = strategy._simulate_one_py(row, prices, probs, policy)
    strategy.clear_kernel_caches()
    actual = strategy.simulate_one(row, prices, probs, policy)
    assert actual == expected
