"""Polarity classification and probability-flip semantics.

The cases below are not hypothetical: every -1 example is a question that the
old `long_unfavorable` boolean let through, and that was traded long. See the
`explain_polarity` docstring block in pipeline/strategy.py.
"""
from __future__ import annotations

import math

import pytest

from pipeline.strategy import (
    POLARITY_OVERRIDES,
    _effective_probs,
    effective_prob_path,
    explain_polarity,
    question_polarity,
    resolve_polarity,
)


# ── Bearish YES: the flip cases the old filter missed ────────────────────────

@pytest.mark.parametrize(
    "question",
    [
        # R1 negation -- these two were traded long USO at entry_prob 0.775
        "Will the US not strike Iran by February 28, 2026?",
        "Will the US not strike Iran by March 31, 2026?",
        "No Israel strike Iran by Sunday Oct 20?",
        "No Israel strike on Iran by Sunday?",
        # R2 cessation -- traded long USO at entry_prob 0.986
        "Military action against Iran ends by April 10, 2026?",
        "Military action against Iran ends on April 9, 2026?",
        "East coast port strike ends in October?",
        "US x Iran ceasefire before Trump visits China?",
        "Will Trump agree to withdraw troops from the Iranian region by June 30?",
        "Strait of Hormuz traffic returns to normal by end of June?",
        # R3 macro level up
        "Annual inflation above 2.5% in August?",
        # R4 commodity supply up
        "Will OPEC crude oil production be above 18 million barrels per day in May?",
        # R5 adverse corporate event
        "Will Acme miss quarterly earnings?",
        "Will the stock crash this week?",
        "Will the FDA reject the application?",
    ],
)
def test_bearish_yes_is_inverted(question):
    assert question_polarity(question) == -1, explain_polarity(question)


# ── Bullish YES: must not be flipped ─────────────────────────────────────────

@pytest.mark.parametrize(
    "question",
    [
        "Will Cintas beat quarterly earnings?",
        # "below" is part of the company name -- the old filter's blocklist had
        # no "below", but a naive fix would add it and break this.
        "Will Five Below (FIVE) beat quarterly earnings?",
        # "supply" is part of the company name; R4 needs a commodity context.
        "Will Tractor Supply (TSCO) beat quarterly earnings?",
        "Will Tractor Supply (TSCO) rise above $300?",
        # "above" is subject-dependent: more subscribers/ARR is bullish.
        "Robinhood Gold Subscribers above 4.2M in Q1?",
        "Will CrowdStrike Q1 net new ARR be above $225M?",
        # A date, not a cessation. "\bends?\b(?!\s+of\b)" must not fire.
        "Will the S&P 500 be higher by end of June?",
        "Will the US strike Iran by February 28, 2026?",
    ],
)
def test_bullish_yes_is_untouched(question):
    assert question_polarity(question) == 1, explain_polarity(question)


# ── Sign composition ─────────────────────────────────────────────────────────

def test_double_negative_resolves_to_bullish():
    """R1 x R3. YES = tame inflation = bullish. The old boolean returned True
    (drop) for this, which was simply wrong."""
    polarity, fired = explain_polarity("Will inflation not be above 2.5%?")
    assert set(fired) == {"R1_negation", "R3_macro_level_up"}
    assert polarity == 1


def test_negated_cessation_resolves_to_bullish():
    """R1 x R2. YES = the war does NOT end = bullish for crude."""
    assert question_polarity("Will military action against Iran not end by April 30?") == 1


# ── Probability flip ─────────────────────────────────────────────────────────

def test_effective_prob_path_is_identity_for_bullish():
    path = [(1, 0.2), (2, 0.9)]
    assert effective_prob_path(path, 1) is path


def test_effective_prob_path_inverts_pointwise():
    path = [(1, 0.20), (2, 0.775), (3, 0.986)]
    flipped = effective_prob_path(path, -1)
    assert [t for t, _ in flipped] == [1, 2, 3]
    assert [round(v, 6) for _, v in flipped] == [0.80, 0.225, 0.014]


def test_flip_is_an_involution():
    path = [(1, 0.137), (2, 0.42), (3, 0.9)]
    twice = effective_prob_path(effective_prob_path(path, -1), -1)
    for (_, a), (_, b) in zip(path, twice):
        assert math.isclose(a, b, abs_tol=1e-12)


def test_flip_preserves_volatility_and_negates_slope():
    """Justifies leaving feat_prob_volatility alone while negating the surge."""
    import numpy as np

    raw = np.array([0.10, 0.35, 0.60, 0.55])
    flipped = 1.0 - raw
    assert math.isclose(raw.std(), flipped.std(), abs_tol=1e-12)
    assert math.isclose(np.diff(raw).sum(), -np.diff(flipped).sum(), abs_tol=1e-12)


# ── The regression that motivated all of this ────────────────────────────────

@pytest.mark.parametrize(
    "question, raw_entry_prob, expected_effective",
    [
        ("Will the US not strike Iran by February 28, 2026?", 0.775, 0.225),
        ("Military action against Iran ends by April 10, 2026?", 0.986, 0.014),
    ],
)
def test_iran_hero_trades_fall_below_enter_floor(question, raw_entry_prob, expected_effective):
    """Both were entered long USO. T4 QQQ's fitted enter_floor was 0.763.
    Under the flip neither can trigger an entry."""
    polarity = question_polarity(question, "USO")
    assert polarity == -1
    (_, effective), = effective_prob_path([(0, raw_entry_prob)], polarity)
    assert math.isclose(effective, expected_effective, abs_tol=1e-9)
    assert effective < 0.763


# ── Resolution precedence: override > llm > regex ────────────────────────────

def test_symbol_none_falls_back_to_regex():
    """Polarity belongs to the (question, symbol) pair; without a symbol we can
    only use the keyword fallback."""
    _, source = resolve_polarity("Will Cintas beat quarterly earnings?", None)
    assert source == "regex"


def test_llm_label_is_used_when_present():
    """The LLM catches inversions with no lexical marker at all -- no regex will
    ever classify a "diplomatic meeting" as bearish crude."""
    q = "Will J.D. Vance have a diplomatic meeting with Iran by June 30?"
    assert explain_polarity(q)[1] == [], "regex must fire no rule here"
    polarity, source = resolve_polarity(q, "USO")
    assert (polarity, source) == (-1, "llm")


def test_regex_operator_gap_is_covered_by_llm():
    """R3 matches the word "above", not the ">" operator."""
    q = "US inflation >0.1% from July to August 2024?"
    assert explain_polarity(q)[0] == 1, "regex misses the > operator"
    assert resolve_polarity(q, "SHY") == (-1, "llm")


def test_override_beats_the_llm_on_carrier_economics():
    """Gemini says (conf 1.00) "strike ends -> shipping normalizes -> ZIM up".
    ZIM fell 17.7% when the Oct 2024 ILA strike ended. Port congestion spikes
    freight rates; carriers rally on disruption and sell off on resolution."""
    assert resolve_polarity("East coast port strike ends in October?", "ZIM") == (-1, "override")
    assert resolve_polarity("Longshoremen east coast strike by Oct 1?", "ZIM") == (+1, "override")


def test_every_override_is_keyed_lowercase_and_upper_symbol():
    """resolve_polarity normalizes before lookup; a mis-cased key is dead code."""
    for question, symbol in POLARITY_OVERRIDES:
        assert question == question.lower()
        assert symbol == symbol.upper()


# ── The cache must not serve one symbol another symbol's path ────────────────

def test_effective_probs_keyed_by_polarity_not_just_market():
    """87 markets carry more than one symbol. If two ever disagree on polarity,
    a dict-keyed-only-by-market would hand one of them the wrong path."""
    probs = {"mkt1": [(0, 0.8)]}
    bull = _effective_probs(probs, "mkt1", 1)
    bear = _effective_probs(probs, "mkt1", -1)
    assert bull is not bear
    assert bull["mkt1"] == [(0, 0.8)]
    assert bear["mkt1"] == [(0, pytest.approx(0.2))]


def test_effective_probs_is_stable_across_calls():
    """The numba kernel caches on id(probs_dict); a fresh dict per call would
    defeat the cache and risk id-reuse collisions after GC."""
    probs = {"mkt1": [(0, 0.8)]}
    assert _effective_probs(probs, "mkt1", -1) is _effective_probs(probs, "mkt1", -1)
