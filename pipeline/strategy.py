"""Core trade simulation engine.

Extracted from general_testing/liran_strategy.py. ATR trailing stops,
profit locks, probability-based entry/exit, long-unfavorable filter.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from pipeline.sim_kernel import HAVE_NUMBA, clear_caches as clear_kernel_caches, scan_candidate

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
    p0 = pts[0][1]
    if p0 >= policy["enter_strong"]:
        return pts[0][0], p0
    held = 0
    for t, v in pts:
        if v >= policy["enter_floor"]:
            held += 1
            if held > policy["hold_days"]:
                return t, v
        else:
            held = 0
    return None


def long_unfavorable(question: str) -> bool:
    """Return True if the market's YES outcome is bearish for a long position."""
    q = " " + (question or "").lower() + " "
    if (" above " in q or " hike" in q or " raise" in q) and (
        "inflation" in q or "cpi" in q or "rate" in q
    ):
        return True
    return any(w in q for w in (
        " miss ", " misses ", " fall ", " decline", " crash", " fails to ", " reject"
    ))


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

    if long_unfavorable(str(row.get("question", ""))):
        return None

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

    p_surge = row.get("feat_prob_surge_since_t0")
    r_surge = row.get("feat_runup_since_t0")
    if p_surge is not None and p_surge > policy.get("max_prob_surge", 999.0):
        return None
    if r_surge is not None and r_surge > policy.get("max_price_runup", 999.0):
        return None

    entry_idx = next((i for i, b in enumerate(win) if b[0] >= entry_ts), -1)
    if entry_idx == -1:
        return None
    path = win[entry_idx:]
    if len(path) < 2:
        return None

    hist_bars = win[max(0, entry_idx - 15):entry_idx + 1]
    atr = calc_atr(hist_bars)

    entry_price = path[0][3]
    if atr == 0 or entry_price == 0:
        return None
    atr_pct = atr / entry_price

    prob_path = {t.normalize(): v for t, v in probs.get(mkt, [])}

    resolution_cut = t_e - pd.Timedelta(days=1)
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

            if not is_earnings:
                if ret_l <= peak - stop_dist:
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

            if reason is None:
                if prob_path.get(t.normalize(), 1.0) < theta_out:
                    reason = f"poly<{theta_out}"
                elif t >= resolution_cut:
                    reason = "resolution-1d"

        if reason:
            lo = min(ll / entry_price - 1.0 for _, _, ll, _ in path[:i + 1])
            mkt_probs = probs.get(mkt, [])
            converged = "YES" if mkt_probs and mkt_probs[-1][1] >= 0.5 else "NO" if mkt_probs else "UNKNOWN"
            return dict(
                market_id=mkt, symbol=sym,
                question=str(row.get("question", "")),
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
        question=str(row.get("question", "")),
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
    if long_unfavorable(str(row.get("question", ""))):
        return None

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
        row.get("feat_prob_surge_since_t0"),
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

    mkt_probs = probs.get(mkt, [])
    converged = "YES" if mkt_probs and mkt_probs[-1][1] >= 0.5 else "NO" if mkt_probs else "UNKNOWN"
    return dict(
        market_id=mkt, symbol=sym,
        question=str(row.get("question", "")),
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
