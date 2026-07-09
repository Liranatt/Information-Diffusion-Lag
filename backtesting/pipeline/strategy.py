"""Core trade simulation engine.

Extracted from general_testing/liran_strategy.py. ATR trailing stops,
profit locks, probability-based entry/exit, signal polarity.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.sim_kernel import HAVE_NUMBA, clear_caches as _clear_sim_kernel_caches, scan_candidate

# The numba kernel is used automatically when numba is importable. It produces
# output identical to the pure-Python reference (_simulate_one_py); set
# SIM_KERNEL=0 to force the reference path (used by the parity test).
_USE_KERNEL = HAVE_NUMBA and os.environ.get("SIM_KERNEL", "1") != "0"

DEFAULT_POLICY = dict(
    atr_mult=3.65,
    lock_activate=0.03,
    theta_out=0.55,
    enter_strong=0.75,
    enter_floor=0.70,
    hold_days=1,
    max_prob_surge=0.40,
    max_price_runup=0.10,
)

CEM_BOUNDS = dict(
    atr_mult=(1.5, 4.0),
    lock_activate=(0.02, 0.10),
    theta_out=(0.45, 0.60),
    enter_strong=(0.60, 0.85),
    enter_floor=(0.55, 0.80),
    hold_days=(1, 5),
    max_prob_surge=(0.20, 0.80),
    max_price_runup=(0.02, 0.20),
)

RELEVANCE_COL = "feat_connection_strength"


def calc_atr(bars: list[tuple]) -> float:
    """Average True Range from (t, h, l, c) bars."""
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, c = bars[i][1], bars[i][2], bars[i][3]
        pc = bars[i - 1][3]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / len(trs)


def entry_day(
    prob_path: list[tuple],
    t_theta: pd.Timestamp,
    policy: dict,
) -> tuple[pd.Timestamp, float] | None:
    """Apply the entry rule; return (entry_ts, entry_prob) or None."""
    first_eligible_day = t_theta.normalize()
    pts = [(t, v) for t, v in prob_path if t >= first_eligible_day]
    if not pts:
        return None
    held = 0
    for t, v in pts:
        if v >= policy["enter_strong"]:
            return t, v
        elif v >= policy["enter_floor"]:
            held += 1
            if held >= policy["hold_days"]:
                return t, v
        else:
            held = 0
    return None


# ── Signal polarity ──────────────────────────────────────────────────────────
#
# The world-builder (LLM/build_world.py) picks symbols that benefit from the
# *event occurring*, and its prompt explicitly forbids reasoning about which way
# an asset's price would move. Nothing downstream ever recorded a per-candidate
# direction. Meanwhile `entry_day` fires on HIGH P(YES) and the book is
# structurally long-only.
#
# So a question that asks whether the event will NOT occur, or will CEASE, is
# inverted: "Military action against Iran ends by April 10?" at P(YES)=0.986
# bought USO -- i.e. went long crude at 98.6% confidence the war was over.
#
# The fix is not to drop these candidates. P(no strike) = 0.30 means
# P(strike) = 0.70, which is a perfectly good long-oil signal. We flip the
# probability path (`p_eff = 1 - p`) and let the unchanged entry/exit rules run
# on it. The book stays long-only; only the signal is re-polarized.
#
# Each rule below toggles the sign, so composition works: "Will inflation not be
# above 2.5%?" fires R1 and R3 and lands back on +1 (YES = tame inflation =
# bullish), which the old boolean filter got wrong.
#
# This is a keyword heuristic, and keyword heuristics are exactly what caused the
# original bug. Run `python -m pipeline.polarity_audit` to eyeball every
# classification before trusting a run. Known unresolved gap: `above` is
# subject-dependent ("Robinhood subscribers above 4.2M" is bullish, "annual
# inflation above 2.5%" is bearish, "OPEC production above 18M bpd" is bearish
# for the USO it maps to). R3/R4 encode the two bearish subjects present in the
# current universe; a new subject will need a new rule, or the LLM polarity pass.

# R1 -- explicit negation of the event.
_NEG_RE = re.compile(r"^\s*no\b|\bnot\b|\bnever\b|\bwithout\b|\brefrains?\s+from\b", re.I)

# R2 -- the event ceases / reverses. `\bends?\b` must not fire on "by end of May",
# which is a date, not a cessation.
_CESSATION_RE = re.compile(
    r"\bends?\b(?!\s+of\b)|\bending\b(?!\s+of\b)"
    r"|\bceasefire\b|\bde-?escalat\w*|\bwithdraw\w*|\breturns?\s+to\s+normal\b",
    re.I,
)

# R3 -- a macro level rising is bearish for equities.
_MACRO_UP_RE = re.compile(r"\babove\b|\bexceeds?\b|\bhikes?\b|\braises?\b", re.I)
_MACRO_SUBJ_RE = re.compile(r"\binflation\b|\bcpi\b|\brates?\b", re.I)

# R4 -- more commodity supply is bearish for the commodity's long proxy. The
# commodity context is required: "Tractor Supply (TSCO)" is a retailer, and
# without the guard "Will Tractor Supply rise above $300?" would flip to -1.
_SUPPLY_UP_RE = re.compile(r"\babove\b|\bexceeds?\b|\brises?\b|\bincreases?\b", re.I)
_SUPPLY_SUBJ_RE = re.compile(r"\bproduction\b|\boutput\b|\bsupply\b", re.I)
_COMMODITY_RE = re.compile(r"\bcrude\b|\boil\b|\bopec\b|\bbarrels?\b|\bnatural\s+gas\b|\bgas\b", re.I)

# R5 -- an adverse corporate/market event. Note "below" is deliberately absent:
# it would match the company "Five Below (FIVE)".
_BEARISH_EVENT_RE = re.compile(
    r"\bmiss(?:es|ed)?\b|\bfalls?\b|\bfallen\b|\bdeclin\w*|\bcrash\w*"
    r"|\bfails?\s+to\b|\brejects?\b|\bdowngrade\w*|\bbankrupt\w*"
    r"|\bdefaults?\b|\blayoffs?\b",
    re.I,
)

_POLARITY_RULES = (
    ("R1_negation", lambda q: bool(_NEG_RE.search(q))),
    ("R2_cessation", lambda q: bool(_CESSATION_RE.search(q))),
    ("R3_macro_level_up", lambda q: bool(_MACRO_UP_RE.search(q) and _MACRO_SUBJ_RE.search(q))),
    (
        "R4_commodity_supply_up",
        lambda q: bool(
            _SUPPLY_UP_RE.search(q) and _SUPPLY_SUBJ_RE.search(q) and _COMMODITY_RE.search(q)
        ),
    ),
    ("R5_bearish_event", lambda q: bool(_BEARISH_EVENT_RE.search(q))),
)


def explain_polarity(question: str) -> tuple[int, list[str]]:
    """Regex-only polarity: (sign, names of rules that fired).

    This is the *fallback*, used for pairs with no LLM label -- notably new
    markets appearing in live trading. Prefer `resolve_polarity`.
    """
    q = (question or "").strip()
    fired = [name for name, test in _POLARITY_RULES if test(q)]
    return (-1 if len(fired) % 2 else 1), fired


# ── LLM labels, and the domain facts the LLM gets wrong ──────────────────────
#
# `LLM/label_polarity.py` asks Gemini, per (question, symbol) pair: "if this
# market resolves YES, is that bullish or bearish for a LONG position in this
# symbol?" That is a causal question about the asset, which no regex can answer.
# It caught 30 rows the keyword rules missed, in three families the rules cannot
# express:
#   - "...blockade of the Strait of Hormuz has been LIFTED by June 21"  (no rule)
#   - "US inflation >0.1% from July to August"   (R3 matches "above", not ">")
#   - "Will J.D. Vance have a diplomatic meeting with Iran by June 30?"
#       de-escalation -> bearish crude, with zero lexical markers.
#
# But the LLM is not an oracle. It is confidently (conf=1.00) and *consistently*
# wrong about container-carrier economics: it reasons "port strike -> shipping
# disruption -> ZIM down". The opposite is true -- port congestion spikes freight
# rates and carriers rally. ZIM around the Oct 2024 ILA east-coast strike, all
# TRAIN-period data (< 2026-01-01, so no OOS leakage):
#
#       2024-09-20  $20.06     run-up into the strike
#       2024-09-30  $25.66     +28%
#       2024-10-03  $21.67     tentative deal announced
#       2024-10-04  $18.95     -12.6% in one session
#
# So a port strike is BULLISH ZIM and its ending is BEARISH. These four
# overrides encode that fact. Every entry must cite its evidence.
POLARITY_OVERRIDES: dict[tuple[str, str], int] = {
    # Container carriers gain from port congestion; see the ZIM tape above.
    ("east coast port strike ends by friday?", "ZIM"): -1,
    ("east coast port strike ends by next friday?", "ZIM"): -1,
    ("east coast port strike ends in october?", "ZIM"): -1,
    ("longshoremen east coast strike by oct 1?", "ZIM"): +1,
}

_LABELS_PATH = Path(__file__).resolve().parent.parent / "data" / "polarity_labels.json"
_LLM_LABELS: dict[tuple[str, str], int] | None = None


def _norm(question: str, symbol: str) -> tuple[str, str]:
    return (question or "").strip().lower(), (symbol or "").strip().upper()


def _llm_labels() -> dict[tuple[str, str], int]:
    global _LLM_LABELS
    if _LLM_LABELS is None:
        if _LABELS_PATH.exists():
            raw = json.loads(_LABELS_PATH.read_text(encoding="utf-8"))
            _LLM_LABELS = {
                _norm(rec["question"], rec["symbol"]): int(rec["polarity"])
                for rec in raw.values()
            }
        else:
            _LLM_LABELS = {}
    return _LLM_LABELS


def resolve_polarity(question: str, symbol: str | None = None) -> tuple[int, str]:
    """Return (polarity, source) where source is override | llm | regex.

    Precedence is deliberate: a hand-verified domain fact beats the LLM, and the
    LLM beats the keyword fallback. `symbol=None` can only use the fallback,
    because polarity is a property of the (question, symbol) pair -- "OPEC
    production above 18M bpd" is bearish for USO and would be bullish for a
    producer's own stock.
    """
    if symbol is not None:
        key = _norm(question, symbol)
        if key in POLARITY_OVERRIDES:
            return POLARITY_OVERRIDES[key], "override"
        label = _llm_labels().get(key)
        if label is not None:
            return label, "llm"
    return explain_polarity(question)[0], "regex"


def question_polarity(question: str, symbol: str | None = None) -> int:
    """+1 if a YES resolution is bullish for the linked symbol, -1 if bearish."""
    return resolve_polarity(question, symbol)[0]


def effective_prob_path(prob_path: list[tuple], polarity: int) -> list[tuple]:
    """Re-polarize a probability path so that "high" always means "bullish"."""
    if polarity == 1:
        return prob_path
    return [(t, 1.0 - v) for t, v in prob_path]


# `_market_arrays` in the numba kernel caches on `(id(probs), mkt)`, so handing
# it a freshly-built dict on every call would both defeat the cache and risk
# id-reuse collisions after GC. Memoize the effective-probs dicts per source
# `probs` identity, holding a strong reference to the source so its id cannot be
# recycled while we still key on it.
#
# Keyed by polarity as well as source, because polarity belongs to the
# (question, symbol) pair while a probability path belongs to the market: 87
# markets carry more than one symbol, so two symbols could in principle disagree
# and a single dict-per-market would silently serve one of them the wrong path.
# Two dicts at most, so the kernel cache still hits.
_EFF_PROBS_CACHE: dict[tuple[int, int], tuple[dict, dict]] = {}


def _effective_probs(probs: dict, mkt: str, polarity: int) -> dict:
    """A dict view of `probs` whose `mkt` entry is corrected for `polarity`."""
    key = (id(probs), polarity)
    cached = _EFF_PROBS_CACHE.get(key)
    if cached is None or cached[0] is not probs:
        cached = (probs, {})
        _EFF_PROBS_CACHE[key] = cached
    _source, eff = cached
    if mkt not in eff:
        eff[mkt] = effective_prob_path(probs.get(mkt, []), polarity)
    return eff


def _effective_prob_surge(row, polarity: int):
    """`feat_prob_surge_since_t0` is a delta on the raw path, so it flips too."""
    surge = row.get("feat_prob_surge_since_t0")
    if polarity == 1 or surge is None:
        return surge
    try:
        value = float(surge)
    except (TypeError, ValueError):
        return surge
    return surge if value != value else -value  # NaN passes through unchanged


def clear_kernel_caches() -> None:
    """Drop cached numpy views AND the polarity-corrected probability paths."""
    _clear_sim_kernel_caches()
    _EFF_PROBS_CACHE.clear()


def _simulate_one_py(
    row: dict | pd.Series,
    prices: dict[str, list[tuple]],
    probs: dict[str, list[tuple]],
    policy: dict,
) -> dict | None:
    """Reference pure-Python trade simulation. Returns trade dict or None.

    This is the authoritative definition of the trade semantics. The numba
    kernel in ``pipeline.sim_kernel`` reproduces it exactly; ``simulate_one``
    dispatches to whichever is active.
    """
    sym, mkt = row["symbol"], row["market_id"]
    t_theta = pd.Timestamp(row["t_theta"]).tz_convert("UTC")
    t_e = pd.Timestamp(row["t_e"]).tz_convert("UTC")

    # Re-polarize before anything reads the path: entry_day, the theta_out exit
    # and `converged` must all see "high == bullish".
    question = str(row.get("question", ""))
    polarity, polarity_source = resolve_polarity(question, sym)
    probs = _effective_probs(probs, mkt, polarity)

    is_earnings = "earnings" in str(row.get("feat_archetype", "")).lower()
    closes = prices.get(sym, [])

    win = [(t, h, l, c) for t, h, l, c in closes
           if t_theta - pd.Timedelta(days=30) <= t <= t_e]
    if len(win) < 2:
        return None

    ent = entry_day(probs.get(mkt, []), t_theta, policy)
    if ent is None:
        return None
    entry_ts = ent[0]

    p_surge = _effective_prob_surge(row, polarity)
    r_surge = row.get("feat_runup_since_t0")
    if p_surge is not None and p_surge > policy.get("max_prob_surge", 999.0):
        return None
    if r_surge is not None and r_surge > policy.get("max_price_runup", 999.0):
        return None

    entry_idx = next((i for i, b in enumerate(win) if b[0] >= entry_ts), -1)
    if entry_idx == -1:
        return None
    resolution_cut = t_e - pd.Timedelta(days=1)
    if win[entry_idx][0] >= resolution_cut:
        return None

    path = [bar for bar in win[entry_idx:] if bar[0] < resolution_cut]
    if len(path) < 2:
        return None

    hist_bars = win[max(0, entry_idx - 15):entry_idx + 1]
    atr = calc_atr(hist_bars)

    entry_price = path[0][3]
    if atr == 0 or entry_price == 0:
        return None
    atr_pct = atr / entry_price

    prob_path = {t.normalize(): v for t, v in probs.get(mkt, [])}

    atr_mult = policy["atr_mult"]
    lock_activate = policy["lock_activate"]
    theta_out = policy["theta_out"]
    peak = 0.0

    for i, (t, h, l, c) in enumerate(path):
        ret_c = c / entry_price - 1.0
        ret_h = h / entry_price - 1.0
        ret_l = l / entry_price - 1.0

        reason = None
        if i > 0:
            stop_dist = atr_mult * atr_pct

            if prob_path.get(t.normalize(), 1.0) < theta_out:
                reason = f"poly<{theta_out}"
            elif ret_l <= peak - stop_dist:
                reason = f"trailing_{atr_mult:.1f}ATR"
                c = max(l, entry_price * (1.0 + peak - stop_dist))
                ret_c = c / entry_price - 1.0
            elif peak >= lock_activate:
                hard_floor_pct = int(peak * 100)
                hard_floor = hard_floor_pct / 100.0
                if ret_l < hard_floor:
                    reason = f"profit_lock_{hard_floor_pct}%"
                    c = max(l, entry_price * (1.0 + hard_floor))
                    ret_c = c / entry_price - 1.0

            if reason is None and i == len(path) - 1:
                reason = "resolution-1d"

        if reason:
            lo = min(ll / entry_price - 1.0 for _, _, ll, _ in path[:i + 1])
            mkt_probs = probs.get(mkt, [])
            converged = "YES" if mkt_probs and mkt_probs[-1][1] >= 0.5 else "NO" if mkt_probs else "UNKNOWN"
            return dict(
                market_id=mkt, symbol=sym,
                question=question,
                polarity=polarity,
                polarity_source=polarity_source,
                pct=round(ent[1], 3),
                converged=converged,
                asset_confidence=row.get("confidence_score"),
                question_confidence=row.get("feat_llm_confidence"),
                archetype=row.get("feat_archetype", ""),
                relevance=round(float(row.get(RELEVANCE_COL, 0)), 3),
                split=row.get("split", ""),
                entry_date=str(path[0][0].date()), entry_prob=round(ent[1], 3),
                entry_price=round(entry_price, 2),
                exit_date=str(t.date()), exit_price=round(c, 2),
                exit_reason=reason,
                peak_pct=round(peak * 100, 2), trough_pct=round(lo * 100, 2),
                return_pct=round(ret_c * 100, 2),
            )

        if i == 0:
            peak = 0.0
        else:
            peak = max(peak, ret_h)

    t, h, l, c = path[-1]
    ret_c = c / entry_price - 1.0
    lo = min(ll / entry_price - 1.0 for _, _, ll, _ in path)
    mkt_probs = probs.get(mkt, [])
    converged = "YES" if mkt_probs and mkt_probs[-1][1] >= 0.5 else "NO" if mkt_probs else "UNKNOWN"
    return dict(
        market_id=mkt, symbol=sym,
        question=question,
        polarity=polarity,
        polarity_source=polarity_source,
        pct=round(ent[1], 3),
        converged=converged,
        asset_confidence=row.get("confidence_score"),
        question_confidence=row.get("feat_llm_confidence"),
        archetype=row.get("feat_archetype", ""),
        relevance=round(float(row.get(RELEVANCE_COL, 0)), 3),
        split=row.get("split", ""),
        entry_date=str(path[0][0].date()), entry_prob=round(ent[1], 3),
        entry_price=round(entry_price, 2),
        exit_date=str(t.date()), exit_price=round(c, 2),
        exit_reason="end_of_window",
        peak_pct=round(peak * 100, 2), trough_pct=round(lo * 100, 2),
        return_pct=round(ret_c * 100, 2),
    )


def simulate_one(
    row: dict | pd.Series,
    prices: dict[str, list[tuple]],
    probs: dict[str, list[tuple]],
    policy: dict,
) -> dict | None:
    """Simulate a single trade. Returns trade result dict or None.

    Dispatches to the numba kernel in ``pipeline.sim_kernel`` when available
    (``_USE_KERNEL``), otherwise to the pure-Python reference
    ``_simulate_one_py``. Both return identical dicts; the kernel only removes
    the per-call pandas/list overhead so the CEM search runs faster. The
    timestamp and string formatting below stays in Python so the fast path is
    timezone-correct and byte-for-byte compatible with the reference.
    """
    if not _USE_KERNEL:
        return _simulate_one_py(row, prices, probs, policy)

    sym, mkt = row["symbol"], row["market_id"]

    # The kernel never sees `question`, so polarity must be resolved here. See
    # the block above `explain_polarity` for why this is a flip, not a filter.
    question = str(row.get("question", ""))
    polarity, polarity_source = resolve_polarity(question, sym)
    probs = _effective_probs(probs, mkt, polarity)

    t_theta = pd.Timestamp(row["t_theta"]).tz_convert("UTC")
    t_e = pd.Timestamp(row["t_e"]).tz_convert("UTC")
    is_earnings = "earnings" in str(row.get("feat_archetype", "")).lower()

    scanned = scan_candidate(
        prices,
        probs,
        sym,
        mkt,
        t_theta,
        t_e,
        is_earnings,
        _effective_prob_surge(row, polarity),
        row.get("feat_runup_since_t0"),
        policy,
    )
    if scanned is None:
        return None

    (
        entry_ts,
        entry_prob,
        entry_price,
        exit_ts,
        exit_price,
        reason_code,
        hard_floor_pct,
        peak,
        trough,
        ret_c,
    ) = scanned

    if reason_code == 1:
        reason = f"trailing_{policy['atr_mult']:.1f}ATR"
    elif reason_code == 2:
        reason = f"profit_lock_{hard_floor_pct}%"
    elif reason_code == 3:
        reason = f"poly<{policy['theta_out']}"
    elif reason_code == 4:
        reason = "resolution-1d"
    else:
        reason = "end_of_window"

    # `probs` is the polarity-corrected view, so `converged` reports whether the
    # bullish thesis resolved true -- not whether the raw market resolved YES.
    mkt_probs = probs.get(mkt, [])
    converged = "YES" if mkt_probs and mkt_probs[-1][1] >= 0.5 else "NO" if mkt_probs else "UNKNOWN"
    return dict(
        market_id=mkt, symbol=sym,
        question=question,
        polarity=polarity,
        polarity_source=polarity_source,
        pct=round(entry_prob, 3),
        converged=converged,
        asset_confidence=row.get("confidence_score"),
        question_confidence=row.get("feat_llm_confidence"),
        archetype=row.get("feat_archetype", ""),
        relevance=round(float(row.get(RELEVANCE_COL, 0)), 3),
        split=row.get("split", ""),
        entry_date=str(entry_ts.date()), entry_prob=round(entry_prob, 3),
        entry_price=round(entry_price, 2),
        exit_date=str(exit_ts.date()), exit_price=round(exit_price, 2),
        exit_reason=reason,
        peak_pct=round(peak * 100, 2), trough_pct=round(trough * 100, 2),
        return_pct=round(ret_c * 100, 2),
    )


def run_backtest(
    df: pd.DataFrame,
    prices: dict[str, list[tuple]],
    probs: dict[str, list[tuple]],
    policy: dict,
    split_filter: str | None = None,
) -> pd.DataFrame:
    """Run the full backtest for a given policy."""
    subset = df if split_filter is None else df[df["split"] == split_filter]
    trades = [
        t for t in (simulate_one(r, prices, probs, policy) for _, r in subset.iterrows())
        if t is not None
    ]
    return pd.DataFrame(trades) if trades else pd.DataFrame()


def policy_from_vector(vec: np.ndarray) -> dict:
    """Convert CEM sample vector to clipped policy dict."""
    names = list(CEM_BOUNDS.keys())
    p = {}
    for i, name in enumerate(names):
        lo, hi = CEM_BOUNDS[name]
        p[name] = float(np.clip(vec[i], lo, hi))
    p["hold_days"] = int(round(p["hold_days"]))
    if p["enter_strong"] < p["enter_floor"]:
        p["enter_strong"] = p["enter_floor"]
    return p


def score_sharpe_per_day(tdf: pd.DataFrame) -> float:
    """Annualised Sharpe from per-trade daily returns."""
    if len(tdf) < 3:
        return -999.0
    entry = pd.to_datetime(tdf["entry_date"])
    exit_ = pd.to_datetime(tdf["exit_date"])
    days = (exit_ - entry).dt.days.clip(lower=1)
    daily_ret = tdf["return_pct"].values / days.values
    mu = daily_ret.mean()
    sigma = daily_ret.std()
    if sigma < 1e-9:
        return -999.0
    return float(mu / sigma * np.sqrt(252))


def score_mean_return(tdf: pd.DataFrame) -> float:
    if tdf.empty:
        return -999.0
    return float(tdf["return_pct"].mean())
