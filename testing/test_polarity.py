"""Signal-polarity classification and simulation regression tests.

The LLM-dependent cases install an in-memory label map, so this suite never
needs ``data/polarity_labels.json`` and never makes an API call.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import core.kernel as kernel
import core.polarity as polarity_module
from backtesting.optimize_cem import _diagnose_candidate_rejection, _make_disposition_row
from core.polarity import (
    POLARITY_OVERRIDES,
    _effective_prob_surge,
    _effective_probs,
    effective_prob_path,
    explain_polarity,
    question_polarity,
    resolve_polarity,
)


def _ts(day: str) -> pd.Timestamp:
    return pd.Timestamp(day, tz="UTC")


def _row(**overrides):
    row = {
        "market_id": "m1",
        "symbol": "USO",
        "question": "Military action against Iran ends by April 10, 2026?",
        "t_theta": _ts("2026-01-03"),
        "t_e": _ts("2026-01-10"),
        "feat_archetype": "",
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
    policy = {
        "atr_mult": 100.0,
        "lock_activate": 0.50,
        "theta_out": 0.55,
        "enter_strong": 0.75,
        "enter_floor": 0.70,
        "hold_days": 1,
        "max_prob_surge": 999.0,
        "max_price_runup": 999.0,
    }
    policy.update(overrides)
    return policy


def _bars() -> list[tuple]:
    return [
        (_ts("2026-01-01") + pd.Timedelta(days=i), 101.0, 99.0, 100.0)
        for i in range(10)
    ]


def _install_label(monkeypatch, question: str, symbol: str, value: int) -> None:
    monkeypatch.setattr(
        polarity_module,
        "_LLM_LABELS",
        {polarity_module._norm(question, symbol): value},
    )


@pytest.fixture(autouse=True)
def _isolate_label_and_kernel_caches(monkeypatch, tmp_path):
    """Keep tests independent of a real or concurrently generated label file."""
    monkeypatch.setattr(polarity_module, "_LABELS_PATH", tmp_path / "missing-labels.json")
    kernel.clear_kernel_caches()
    yield
    kernel.clear_kernel_caches()


# -- Regex classification ----------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Will the US not strike Iran by February 28, 2026?",
        "Will the US not strike Iran by March 31, 2026?",
        "No Israel strike Iran by Sunday Oct 20?",
        "No Israel strike on Iran by Sunday?",
        "Military action against Iran ends by April 10, 2026?",
        "Military action against Iran ends on April 9, 2026?",
        "East coast port strike ends in October?",
        "US x Iran ceasefire before Trump visits China?",
        "Will Trump agree to withdraw troops from the Iranian region by June 30?",
        "Strait of Hormuz traffic returns to normal by end of June?",
        "Annual inflation above 2.5% in August?",
        "Will OPEC crude oil production be above 18 million barrels per day in May?",
        "Will Acme miss quarterly earnings?",
        "Will the stock crash this week?",
        "Will the FDA reject the application?",
    ],
)
def test_bearish_yes_is_inverted(question):
    assert question_polarity(question) == -1, explain_polarity(question)


@pytest.mark.parametrize(
    "question",
    [
        "Will Cintas beat quarterly earnings?",
        "Will Five Below (FIVE) beat quarterly earnings?",
        "Will Tractor Supply (TSCO) beat quarterly earnings?",
        "Will Tractor Supply (TSCO) rise above $300?",
        "Robinhood Gold Subscribers above 4.2M in Q1?",
        "Will CrowdStrike Q1 net new ARR be above $225M?",
        "Will the S&P 500 be higher by end of June?",
        "Will the US strike Iran by February 28, 2026?",
    ],
)
def test_bullish_yes_is_untouched(question):
    assert question_polarity(question) == 1, explain_polarity(question)


def test_double_negative_resolves_to_bullish():
    polarity, fired = explain_polarity("Will inflation not be above 2.5%?")
    assert set(fired) == {"R1_negation", "R3_macro_level_up"}
    assert polarity == 1


def test_negated_cessation_resolves_to_bullish():
    assert question_polarity("Will military action against Iran not end by April 30?") == 1


# -- Probability-path semantics ---------------------------------------------


def test_effective_prob_path_is_identity_for_bullish():
    path = [(1, 0.2), (2, 0.9)]
    assert effective_prob_path(path, 1) is path


def test_effective_prob_path_inverts_pointwise():
    path = [(1, 0.20), (2, 0.775), (3, 0.986)]
    flipped = effective_prob_path(path, -1)
    assert [t for t, _ in flipped] == [1, 2, 3]
    assert [round(v, 6) for _, v in flipped] == [0.80, 0.225, 0.014]


def test_effective_prob_path_rejects_no_signal_state():
    with pytest.raises(ValueError, match="no tradable probability path"):
        effective_prob_path([(1, 0.5)], 0)


def test_flip_is_an_involution():
    path = [(1, 0.137), (2, 0.42), (3, 0.9)]
    twice = effective_prob_path(effective_prob_path(path, -1), -1)
    for (_, actual), (_, expected) in zip(twice, path):
        assert math.isclose(actual, expected, abs_tol=1e-12)


def test_flip_preserves_volatility_and_negates_slope():
    raw = np.array([0.10, 0.35, 0.60, 0.55])
    flipped = 1.0 - raw
    assert math.isclose(raw.std(), flipped.std(), abs_tol=1e-12)
    assert math.isclose(np.diff(raw).sum(), -np.diff(flipped).sum(), abs_tol=1e-12)


@pytest.mark.parametrize(
    "question, raw_entry_prob, expected_effective",
    [
        ("Will the US not strike Iran by February 28, 2026?", 0.775, 0.225),
        ("Military action against Iran ends by April 10, 2026?", 0.986, 0.014),
    ],
)
def test_iran_hero_trades_fall_below_enter_floor(
    question, raw_entry_prob, expected_effective
):
    polarity = question_polarity(question, "USO")
    assert polarity == -1
    (_, effective), = effective_prob_path([(0, raw_entry_prob)], polarity)
    assert math.isclose(effective, expected_effective, abs_tol=1e-9)
    assert effective < 0.763


def test_effective_prob_surge_is_negated_and_nan_safe():
    assert _effective_prob_surge({"feat_prob_surge_since_t0": 0.25}, -1) == -0.25
    assert _effective_prob_surge({"feat_prob_surge_since_t0": 0.25}, 1) == 0.25
    assert _effective_prob_surge({"feat_prob_surge_since_t0": None}, -1) is None
    result = _effective_prob_surge({"feat_prob_surge_since_t0": float("nan")}, -1)
    assert math.isnan(result)
    with pytest.raises(ValueError, match="no tradable probability surge"):
        _effective_prob_surge({"feat_prob_surge_since_t0": 0.25}, 0)


# -- Resolution precedence and three-state labels ---------------------------


def test_symbol_none_falls_back_to_regex():
    _, source = resolve_polarity("Will Cintas beat quarterly earnings?", None)
    assert source == "regex"


def test_llm_label_is_used_when_present(monkeypatch):
    question = "Will J.D. Vance have a diplomatic meeting with Iran by June 30?"
    assert explain_polarity(question)[1] == []
    _install_label(monkeypatch, question, "USO", -1)
    assert resolve_polarity(question, "USO") == (-1, "llm")


def test_regex_operator_gap_is_covered_by_llm(monkeypatch):
    question = "US inflation >0.1% from July to August 2024?"
    assert explain_polarity(question)[0] == 1
    _install_label(monkeypatch, question, "SHY", -1)
    assert resolve_polarity(question, "SHY") == (-1, "llm")


def test_override_beats_llm_on_carrier_economics(monkeypatch):
    question = "East coast port strike ends in October?"
    _install_label(monkeypatch, question, "ZIM", 1)
    assert resolve_polarity(question, "ZIM") == (-1, "override")
    assert resolve_polarity("Longshoremen east coast strike by Oct 1?", "ZIM") == (
        1,
        "override",
    )


def test_every_override_is_keyed_lowercase_and_upper_symbol():
    for question, symbol in POLARITY_OVERRIDES:
        assert question == question.lower()
        assert symbol == symbol.upper()


def test_label_zero_skips_both_simulation_paths(monkeypatch):
    question = "Will the Fed change its policy rate at the next meeting?"
    row = _row(question=question, symbol="SHY")
    prices = {"SHY": _bars()}
    probs = {"m1": [(_ts("2026-01-03"), 0.80), (_ts("2026-01-04"), 0.80)]}
    _install_label(monkeypatch, question, "SHY", 0)

    assert resolve_polarity(question, "SHY") == (0, "llm")
    assert kernel._simulate_one_py(row, prices, probs, _policy()) is None
    assert kernel.simulate_one(row, prices, probs, _policy()) is None


# -- Cache isolation ---------------------------------------------------------


def test_effective_probs_keyed_by_polarity_not_just_market():
    probs = {"mkt1": [(0, 0.8)]}
    bull = _effective_probs(probs, "mkt1", 1)
    bear = _effective_probs(probs, "mkt1", -1)
    assert bull is not bear
    assert bull["mkt1"] == [(0, 0.8)]
    assert bear["mkt1"] == [(0, pytest.approx(0.2))]


def test_effective_probs_is_stable_across_calls():
    probs = {"mkt1": [(0, 0.8)]}
    assert _effective_probs(probs, "mkt1", -1) is _effective_probs(probs, "mkt1", -1)


# -- Kernel and integration regressions -------------------------------------


@pytest.mark.skipif(not kernel.HAVE_NUMBA, reason="numba kernel is not available")
def test_bearish_candidate_is_byte_identical_in_python_and_numba(monkeypatch):
    row = _row(feat_prob_surge_since_t0=-0.20)
    prices = {"USO": _bars()}
    probs = {
        "m1": [
            (_ts("2026-01-03"), 0.20),
            (_ts("2026-01-04"), 0.20),
            (_ts("2026-01-05"), 0.60),
            (_ts("2026-01-06"), 0.60),
        ]
    }

    monkeypatch.setattr(kernel, "_USE_KERNEL", False)
    kernel.clear_kernel_caches()
    expected = kernel.simulate_one(row, prices, probs, _policy())

    monkeypatch.setattr(kernel, "_USE_KERNEL", True)
    kernel.clear_kernel_caches()
    actual = kernel.simulate_one(row, prices, probs, _policy())

    assert actual == expected
    assert actual is not None
    assert actual["polarity"] == -1
    assert actual["entry_prob"] == 0.8


def test_apply_polarity_false_preserves_raw_live_replay():
    row = _row(feat_prob_surge_since_t0=0.20)
    prices = {"USO": _bars()}
    probs = {
        "m1": [
            (_ts("2026-01-03"), 0.80),
            (_ts("2026-01-04"), 0.80),
            (_ts("2026-01-05"), 0.80),
            (_ts("2026-01-06"), 0.80),
            (_ts("2026-01-07"), 0.80),
            (_ts("2026-01-08"), 0.80),
        ]
    }

    assert kernel._simulate_one_py(row, prices, probs, _policy()) is None
    raw_trade = kernel._simulate_one_py(
        row,
        prices,
        probs,
        _policy(),
        apply_polarity=False,
    )
    assert raw_trade is not None
    assert raw_trade["entry_prob"] == 0.8
    assert raw_trade["exit_reason"] == "resolution-1d"


def test_neutral_diagnostic_and_disposition_preserve_polarity_metadata(monkeypatch):
    question = "Will the Fed change its policy rate?"
    _install_label(monkeypatch, question, "SHY", 0)
    diagnostic = _diagnose_candidate_rejection(
        pd.Series(_row(question=question, symbol="SHY")),
        _policy(),
        prices={},
        probs={},
        candidate_order=1,
    )
    assert diagnostic["polarity"] == 0
    assert diagnostic["polarity_source"] == "llm"
    assert diagnostic["disposition"] == "no_clean_signal_side"

    row = _make_disposition_row(
        diagnostic,
        _policy(),
        open_positions_count="",
        allocation_mode="chronological",
    )
    assert row["polarity"] == 0
    assert row["polarity_source"] == "llm"
    assert row["disposition"] == "no_clean_signal_side"
