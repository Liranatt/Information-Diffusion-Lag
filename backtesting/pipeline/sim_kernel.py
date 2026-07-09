"""
Numba-accelerated core for ``pipeline.strategy.simulate_one``.

Why this module exists
----------------------
``simulate_one`` is the innermost hot loop of the experiment runners. Each CEM
fit evaluates ~``CEM_POP`` policies for ~``CEM_ITERS`` iterations (≈120 portfolio
simulations), every simulation calls ``simulate_one`` once per candidate, and a
single run issues thousands of fits (8 experiments × 2 benchmarks × folds ×
seeds). The per-call work — filtering a symbol's price window, building a
probability-by-day dict, scanning the holding path bar-by-bar for ATR-trailing /
profit-lock / probability / resolution exits — is pure-Python pandas/list code,
so it dominates wall-clock time.

This module reproduces that numeric core as a JIT-compiled kernel.

Design and safety
-----------------
* **Bit-identical output.** The kernel only ever sees ``int64`` / ``float64``
  numpy arrays and scalars and performs exactly the same arithmetic, in the same
  order, as the reference Python. All timestamp normalization and every
  date-string / reason-string is produced in Python from the original tz-aware
  ``pandas.Timestamp`` objects, so the result is tz-correct and matches the
  pure-Python path field for field. ``tests``/parity scripts assert this.
* **No new parallelism.** This changes *how fast* one simulation runs, never
  *what* it computes and never the order in which the runners drive it, so it
  cannot introduce look-ahead/leakage. The CEM population loop stays serial.
* **Reuse across the population.** The per-(price/prob object, symbol/market)
  numpy views are independent of the policy, so they are built once and reused
  for every policy evaluation inside a CEM fit. ``truncate_paths`` already
  memoizes one dict object per horizon, so the cache key (object id, symbol)
  stays stable across the 120 evaluations of that horizon.
* **Graceful fallback.** If numba is unavailable the caller keeps using the
  original pure-Python implementation; nothing here is required for correctness.

The kernel assumes chronologically sorted daily bars and probability points,
which every loader in this project already guarantees.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:  # numba is optional; callers fall back to pure Python when it is absent.
    from numba import njit

    HAVE_NUMBA = True
except Exception:  # pragma: no cover - exercised only when numba is missing
    HAVE_NUMBA = False

    def njit(*args, **kwargs):  # type: ignore[misc]
        """No-op decorator so the module imports without numba installed."""
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]

        def _wrap(fn):
            return fn

        return _wrap


# Caches keyed by (id(container), key). The container ids stay valid because the
# runners (and ``truncate_paths``) keep the price/probability dicts alive for the
# whole run, so repeated horizons reuse their arrays.
_SYM_CACHE: dict[tuple[int, str], tuple] = {}
_MKT_CACHE: dict[tuple[int, str], tuple] = {}


def clear_caches() -> None:
    """Drop cached numpy views (call when fresh price/probability dicts load)."""
    _SYM_CACHE.clear()
    _MKT_CACHE.clear()


def _symbol_arrays(prices: dict, sym: str) -> tuple:
    """Return cached ``(value, norm, high, low, close, bars)`` arrays for sym.

    ``value`` is each bar's epoch-ns (``Timestamp.value`` is tz-independent, so
    chronological comparisons match the original Timestamp comparisons). ``norm``
    is each bar's ``.normalize().value`` — computed with real Timestamps so the
    day key matches ``Timestamp.normalize()`` in any timezone. ``bars`` is the
    original list, kept so the caller can format exit dates from real Timestamps.
    """
    key = (id(prices), sym)
    cached = _SYM_CACHE.get(key)
    if cached is not None:
        return cached

    bars = prices.get(sym, [])
    n = len(bars)
    value = np.empty(n, dtype=np.int64)
    norm = np.empty(n, dtype=np.int64)
    high = np.empty(n, dtype=np.float64)
    low = np.empty(n, dtype=np.float64)
    close = np.empty(n, dtype=np.float64)
    for i in range(n):
        bar = bars[i]
        ts = bar[0]
        if not isinstance(ts, pd.Timestamp):
            ts = pd.Timestamp(ts)
        value[i] = ts.value
        norm[i] = ts.normalize().value
        high[i] = bar[1]
        low[i] = bar[2]
        close[i] = bar[3]

    cached = (value, norm, high, low, close, bars)
    _SYM_CACHE[key] = cached
    return cached


def _market_arrays(probs: dict, mkt: str) -> tuple:
    """Return cached arrays for a market's probability path.

    ``pt_value`` / ``pval_raw`` preserve the raw point order used by the entry
    rule. ``day_uni`` / ``pval_uni`` collapse to one value per normalized day
    (last point wins, matching the ``{t.normalize(): v}`` dict in the reference)
    for the O(log n) exit-day lookup. ``points`` is kept for entry-date strings.
    """
    key = (id(probs), mkt)
    cached = _MKT_CACHE.get(key)
    if cached is not None:
        return cached

    points = probs.get(mkt, [])
    m = len(points)
    pt_value = np.empty(m, dtype=np.int64)
    pval_raw = np.empty(m, dtype=np.float64)
    day_to_val: dict[int, float] = {}
    for i in range(m):
        point = points[i]
        ts = point[0]
        if not isinstance(ts, pd.Timestamp):
            ts = pd.Timestamp(ts)
        pt_value[i] = ts.value
        value = float(point[1])
        pval_raw[i] = value
        day_to_val[ts.normalize().value] = value  # last point of a day wins

    if day_to_val:
        day_uni = np.array(sorted(day_to_val.keys()), dtype=np.int64)
        pval_uni = np.array([day_to_val[d] for d in day_uni], dtype=np.float64)
    else:
        day_uni = np.empty(0, dtype=np.int64)
        pval_uni = np.empty(0, dtype=np.float64)

    cached = (pt_value, pval_raw, day_uni, pval_uni, points)
    _MKT_CACHE[key] = cached
    return cached


@njit(cache=True)
def _bisect_left(a, x):
    """First index ``i`` with ``a[i] >= x`` (``a`` ascending)."""
    lo = 0
    hi = a.shape[0]
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(cache=True)
def _bisect_right(a, x):
    """First index ``i`` with ``a[i] > x`` (``a`` ascending)."""
    lo = 0
    hi = a.shape[0]
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo


# Reason codes returned by the kernel (mapped to strings by the caller):
#   0 none, 1 trailing-ATR, 2 profit-lock, 3 probability-out, 4 resolution-1d,
#   5 end-of-window.
@njit(cache=True)
def _scan(
    bar_value,
    bar_norm,
    bar_high,
    bar_low,
    bar_close,
    pt_value,
    pval_raw,
    day_uni,
    pval_uni,
    window_lo_value,
    t_e_value,
    first_eligible_value,
    resolution_cut_value,
    is_earnings,
    enter_strong,
    enter_floor,
    hold_days,
    atr_mult,
    lock_activate,
    theta_out,
    p_surge,
    max_prob_surge,
    r_surge,
    max_price_runup,
):
    """Reproduce simulate_one's numeric core; see module docstring for parity.

    Returns an 11-tuple
    ``(status, entry_pt_index, entry_gindex, exit_gindex, exit_price,
    reason_code, hard_floor_pct, peak, trough, ret_c, entry_price)``. ``status``
    is 0 when no trade is produced.
    """
    none = (0, -1, -1, -1, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0)

    # Price window: bars with window_lo_value <= t <= t_e_value (both inclusive).
    w_start = _bisect_left(bar_value, window_lo_value)
    w_end = _bisect_right(bar_value, t_e_value)  # exclusive
    if w_end - w_start < 2:
        return none

    # Entry rule (entry_day): scan raw points whose timestamp >= the first
    # eligible day. Strong entry can fire on any point; otherwise enter_floor
    # must hold for hold_days consecutive points.
    m = pt_value.shape[0]
    e0 = _bisect_left(pt_value, first_eligible_value)
    if e0 >= m:
        return none

    entry_pt_index = -1
    held = 0
    k = e0
    while k < m:
        if pval_raw[k] >= enter_strong:
            entry_pt_index = k
            break
        elif pval_raw[k] >= enter_floor:
            held += 1
            if held >= hold_days:
                entry_pt_index = k
                break
        else:
            held = 0
        k += 1
    if entry_pt_index < 0:
        return none
    entry_ts_value = pt_value[entry_pt_index]

    # Prob-surge / price-runup gates (NaN means "field absent" -> skip).
    if (p_surge == p_surge) and (p_surge > max_prob_surge):
        return none
    if (r_surge == r_surge) and (r_surge > max_price_runup):
        return none

    # First window bar at/after the entry timestamp.
    gi = _bisect_left(bar_value, entry_ts_value)
    if gi < w_start:
        gi = w_start
    if gi >= w_end:
        return none
    if w_end - gi < 2:  # path needs at least two bars
        return none
    if bar_value[gi] >= resolution_cut_value:
        return none
    hold_end = _bisect_left(bar_value, resolution_cut_value)
    if hold_end > w_end:
        hold_end = w_end
    if hold_end - gi < 2:
        return none
    entry_price = bar_close[gi]

    # ATR over the <=16 bars ending at the entry bar (calc_atr): start index is
    # max(window_start, entry - 15), matching win[max(0, entry_idx - 15):...].
    h_start = gi - 15
    if h_start < w_start:
        h_start = w_start
    tr_sum = 0.0
    cnt = 0
    j = h_start + 1
    while j <= gi:
        hh = bar_high[j]
        ll = bar_low[j]
        pc = bar_close[j - 1]
        tr = hh - ll
        d2 = hh - pc
        if d2 < 0.0:
            d2 = -d2
        if d2 > tr:
            tr = d2
        d3 = ll - pc
        if d3 < 0.0:
            d3 = -d3
        if d3 > tr:
            tr = d3
        tr_sum += tr
        cnt += 1
        j += 1
    if cnt < 1:
        return none
    atr = tr_sum / cnt
    if atr == 0.0 or entry_price == 0.0:
        return none
    atr_pct = atr / entry_price

    # Exit scan across the holding path.
    peak = 0.0
    gj = gi
    while gj < hold_end:
        i_rel = gj - gi
        hh = bar_high[gj]
        ll = bar_low[gj]
        cc = bar_close[gj]
        ret_c = cc / entry_price - 1.0
        ret_h = hh / entry_price - 1.0
        ret_l = ll / entry_price - 1.0

        reason = 0
        hard_floor_pct = 0
        if i_rel > 0:
            stop_dist = atr_mult * atr_pct

            pv = 1.0
            idx = _bisect_left(day_uni, bar_norm[gj])
            if idx < day_uni.shape[0] and day_uni[idx] == bar_norm[gj]:
                pv = pval_uni[idx]
            if pv < theta_out:
                reason = 3
            elif ret_l <= peak - stop_dist:
                reason = 1
                cand = entry_price * (1.0 + peak - stop_dist)
                cc = ll if ll > cand else cand
                ret_c = cc / entry_price - 1.0
            elif peak >= lock_activate:
                hard_floor_pct = int(peak * 100.0)
                hard_floor = hard_floor_pct / 100.0
                if ret_l < hard_floor:
                    reason = 2
                    cand = entry_price * (1.0 + hard_floor)
                    cc = ll if ll > cand else cand
                    ret_c = cc / entry_price - 1.0
            if reason == 0:
                if gj == hold_end - 1:
                    reason = 4

        if reason != 0:
            lo = 0.0
            first = 1
            k = gi
            while k <= gj:
                rl = bar_low[k] / entry_price - 1.0
                if first == 1 or rl < lo:
                    lo = rl
                    first = 0
                k += 1
            return (
                1,
                int(entry_pt_index),
                int(gi),
                int(gj),
                cc,
                int(reason),
                int(hard_floor_pct),
                peak,
                lo,
                ret_c,
                entry_price,
            )

        if i_rel == 0:
            peak = 0.0
        elif ret_h > peak:
            peak = ret_h
        gj += 1

    # No exit triggered: liquidate on the last eligible pre-resolution bar.
    last = hold_end - 1
    cc = bar_close[last]
    ret_c = cc / entry_price - 1.0
    lo = 0.0
    first = 1
    k = gi
    while k < hold_end:
        rl = bar_low[k] / entry_price - 1.0
        if first == 1 or rl < lo:
            lo = rl
            first = 0
        k += 1
    return (
        1,
        int(entry_pt_index),
        int(gi),
        int(last),
        cc,
        5,
        0,
        peak,
        lo,
        ret_c,
        entry_price,
    )


_DAY_NS = 86_400_000_000_000


def scan_candidate(
    prices: dict,
    probs: dict,
    sym: str,
    mkt: str,
    t_theta: pd.Timestamp,
    t_e: pd.Timestamp,
    is_earnings: bool,
    p_surge,
    r_surge,
    policy: dict,
):
    """Run the JIT kernel for one candidate.

    Returns ``None`` when no trade is produced, otherwise a tuple
    ``(entry_ts, entry_prob, entry_price, exit_ts, exit_price, reason_code,
    hard_floor_pct, peak, trough, ret_c)`` where ``entry_ts``/``exit_ts`` are the
    original tz-aware Timestamps (so the caller formats dates identically).
    """
    bar_value, bar_norm, bar_high, bar_low, bar_close, bars = _symbol_arrays(prices, sym)
    if bar_value.shape[0] < 2:
        return None
    pt_value, pval_raw, day_uni, pval_uni, points = _market_arrays(probs, mkt)
    if pt_value.shape[0] == 0:
        return None

    window_lo_value = np.int64(t_theta.value) - 30 * _DAY_NS
    t_e_value = np.int64(t_e.value)
    first_eligible_value = np.int64(t_theta.normalize().value)
    resolution_cut_value = np.int64((t_e - pd.Timedelta(days=1)).value)

    surge = float(p_surge) if p_surge is not None else float("nan")
    runup = float(r_surge) if r_surge is not None else float("nan")

    result = _scan(
        bar_value,
        bar_norm,
        bar_high,
        bar_low,
        bar_close,
        pt_value,
        pval_raw,
        day_uni,
        pval_uni,
        window_lo_value,
        t_e_value,
        first_eligible_value,
        resolution_cut_value,
        1 if is_earnings else 0,
        float(policy["enter_strong"]),
        float(policy["enter_floor"]),
        int(policy["hold_days"]),
        float(policy["atr_mult"]),
        float(policy["lock_activate"]),
        float(policy["theta_out"]),
        surge,
        float(policy.get("max_prob_surge", 999.0)),
        runup,
        float(policy.get("max_price_runup", 999.0)),
    )

    if result[0] == 0:
        return None

    (
        _status,
        entry_pt_index,
        _entry_gindex,
        exit_gindex,
        exit_price,
        reason_code,
        hard_floor_pct,
        peak,
        trough,
        ret_c,
        entry_price,
    ) = result

    entry_point = points[int(entry_pt_index)]
    exit_bar = bars[int(exit_gindex)]
    return (
        bars[int(_entry_gindex)][0], # entry_ts (rolled to asset's valid trading day)
        float(entry_point[1]),   # entry_prob
        float(entry_price),
        exit_bar[0],             # exit_ts (original Timestamp)
        float(exit_price),
        int(reason_code),
        int(hard_floor_pct),
        float(peak),
        float(trough),
        float(ret_c),
    )
