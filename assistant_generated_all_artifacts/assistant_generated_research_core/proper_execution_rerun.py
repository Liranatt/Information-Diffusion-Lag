from __future__ import annotations

import json
import math
import pickle
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numba import njit

ROOT = Path('/mnt/data')
NB_PATH = ROOT / 'standard_cem_strategy(1).ipynb'
CAND_PATH = ROOT / 'candidates_orig.pkl'
OLD_PRICE_PATH = ROOT / 'prices(2).pkl'
MERGED_PRICE_PATH = ROOT / 'prices_open_merged.pkl'
PROB_PATH = ROOT / 'probs_h1.pkl'
POLARITY_PATH = ROOT / 'polarity_labels(1).json'
ORIGINAL_POLICY_PATH = ROOT / 'cem_fitted_parameters.json'
OUT = ROOT / 'proper_execution_rerun'
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Load the original notebook's polarity, portfolio and CEM implementation.
# The trade kernel and portfolio close execution are replaced below.
# ---------------------------------------------------------------------------
nb = json.loads(NB_PATH.read_text(encoding='utf-8'))
src4 = ''.join(nb['cells'][4]['source'])
src6 = ''.join(nb['cells'][6]['source'])
ns: dict[str, Any] = {
    '__name__': 'proper_execution_rerun',
    'np': np,
    'pd': pd,
    're': re,
    'math': math,
    'json': json,
    'Path': Path,
    'POLARITY_PATH': POLARITY_PATH,
}
exec(compile(src4, 'notebook_cell4.py', 'exec'), ns)
# Cell 6 defines functions that resolve simulate_one dynamically at call time.
# A temporary stub is sufficient during definition.
ns['simulate_one'] = lambda *args, **kwargs: None
exec(compile(src6, 'notebook_cell6.py', 'exec'), ns)
ns['_DAY_NS'] = 86_400_000_000_000

with OLD_PRICE_PATH.open('rb') as f:
    old_prices = pickle.load(f)
with MERGED_PRICE_PATH.open('rb') as f:
    prices = pickle.load(f)
with PROB_PATH.open('rb') as f:
    probs = pickle.load(f)
with CAND_PATH.open('rb') as f:
    candidates = pickle.load(f)
original_policies = json.loads(ORIGINAL_POLICY_PATH.read_text(encoding='utf-8'))

# Validate merged schema and frozen HLC identity.
unresolved_open_rows = 0
for sym, bars in prices.items():
    old_bars = old_prices[sym]
    if len(bars) != len(old_bars):
        raise ValueError(f'{sym}: merged bar count changed')
    for b5, b4 in zip(bars, old_bars):
        if len(b5) != 5:
            raise ValueError(f'{sym}: expected 5-field OHLC bar')
        if pd.Timestamp(b5[0]) != pd.Timestamp(b4[0]):
            raise ValueError(f'{sym}: date changed')
        if not np.allclose(np.asarray(b5[2:5], float), np.asarray(b4[1:4], float), rtol=0, atol=0):
            raise ValueError(f'{sym}: frozen HLC changed')
        if not np.isfinite(float(b5[1])):
            unresolved_open_rows += 1

# Candidate universe and split are identical to the notebook.
df = candidates.copy()
if 'cem_eligible' in df.columns:
    df = df.loc[df['cem_eligible'].fillna(False).astype(bool)].copy()
df = df[df[ns['RELEVANCE_COL']].astype(float) > 0.5].copy()
if 'split' in df.columns:
    df['split'] = df['split'].astype(str).str.lower().str.strip().replace({'val': 'test'})
df['t_theta'] = pd.to_datetime(df['t_theta'], utc=True)
df['t_e'] = pd.to_datetime(df['t_e'], utc=True)
OOS_START = pd.Timestamp('2026-01-01', tz='UTC')
OOS_END = ns['as_utc_day'](df['t_theta'].max())
train_df = ns['rows_completed_before'](df, OOS_START)
train_eval_end = OOS_START - pd.Timedelta(days=1)
oos_df = df[(df['t_theta'] >= OOS_START) & (df['t_theta'] <= OOS_END)].copy()

# ---------------------------------------------------------------------------
# Proper daily-bar kernel.
# ---------------------------------------------------------------------------
_DAY_NS = 86_400_000_000_000
_SYM_CACHE: dict[tuple[int, str], tuple] = {}
_MKT_CACHE: dict[tuple[int, str], tuple] = {}


def clear_proper_caches() -> None:
    _SYM_CACHE.clear()
    _MKT_CACHE.clear()
    ns['clear_effective_probs_cache']()
    ns['_CLOSE_CACHE'].clear()
    ns['_PATH_CUTOFF_CACHE'].clear()


def _symbol_arrays(prices_arg: dict, sym: str) -> tuple:
    key = (id(prices_arg), sym)
    if key in _SYM_CACHE:
        return _SYM_CACHE[key]
    bars = prices_arg.get(sym, [])
    n = len(bars)
    value = np.empty(n, dtype=np.int64)
    norm = np.empty(n, dtype=np.int64)
    opn = np.empty(n, dtype=np.float64)
    high = np.empty(n, dtype=np.float64)
    low = np.empty(n, dtype=np.float64)
    close = np.empty(n, dtype=np.float64)
    for i, bar in enumerate(bars):
        ts = bar[0] if isinstance(bar[0], pd.Timestamp) else pd.Timestamp(bar[0])
        value[i] = ts.value
        norm[i] = ts.normalize().value
        opn[i] = float(bar[1])
        high[i] = float(bar[2])
        low[i] = float(bar[3])
        close[i] = float(bar[4])
    out = (value, norm, opn, high, low, close, bars)
    _SYM_CACHE[key] = out
    return out


def _market_arrays(probs_arg: dict, mkt: str) -> tuple:
    key = (id(probs_arg), mkt)
    if key in _MKT_CACHE:
        return _MKT_CACHE[key]
    points = probs_arg.get(mkt, [])
    m = len(points)
    pt_value = np.empty(m, dtype=np.int64)
    pval_raw = np.empty(m, dtype=np.float64)
    day_to_val: dict[int, float] = {}
    for i, point in enumerate(points):
        ts = point[0] if isinstance(point[0], pd.Timestamp) else pd.Timestamp(point[0])
        pt_value[i] = ts.value
        pval_raw[i] = float(point[1])
        day_to_val[ts.normalize().value] = float(point[1])
    if day_to_val:
        day_uni = np.array(sorted(day_to_val), dtype=np.int64)
        pval_uni = np.array([day_to_val[d] for d in day_uni], dtype=np.float64)
    else:
        day_uni = np.empty(0, dtype=np.int64)
        pval_uni = np.empty(0, dtype=np.float64)
    out = (pt_value, pval_raw, day_uni, pval_uni, points)
    _MKT_CACHE[key] = out
    return out


@njit(cache=True)
def _bisect_left(a, x):
    lo, hi = 0, a.shape[0]
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(cache=True)
def _bisect_right(a, x):
    lo, hi = 0, a.shape[0]
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo


# reason: 0 none, 1 trailing, 2 profit lock, 3 probability, 4 resolution-1d, 5 end
# timing: 0 close/non-stop, 1 overnight gap at Open, 2 intraday stop, 3 missing-Open Low fallback
@njit(cache=True)
def _scan_proper(
    bar_value, bar_norm, bar_open, bar_high, bar_low, bar_close,
    pt_value, pval_raw, day_uni, pval_uni,
    window_lo_value, t_e_value, first_eligible_value, resolution_cut_value,
    enter_strong, enter_floor, hold_days, atr_mult, lock_activate, theta_out,
    p_surge, max_prob_surge, r_surge, max_price_runup,
):
    none = (0, -1, -1, -1, 0.0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    w_start = _bisect_left(bar_value, window_lo_value)
    w_end = _bisect_right(bar_value, t_e_value)
    if w_end - w_start < 2:
        return none

    e0 = _bisect_left(pt_value, first_eligible_value)
    if e0 >= pt_value.shape[0]:
        return none
    entry_pt_index = -1
    held = 0
    k = e0
    while k < pt_value.shape[0]:
        if pval_raw[k] >= enter_strong:
            entry_pt_index = k
            break
        if pval_raw[k] >= enter_floor:
            held += 1
            if held >= hold_days:
                entry_pt_index = k
                break
        else:
            held = 0
        k += 1
    if entry_pt_index < 0:
        return none

    if (p_surge == p_surge) and p_surge > max_prob_surge:
        return none
    if (r_surge == r_surge) and r_surge > max_price_runup:
        return none

    gi = _bisect_left(bar_value, pt_value[entry_pt_index])
    if gi < w_start:
        gi = w_start
    if gi >= w_end or w_end - gi < 2:
        return none
    if bar_value[gi] >= resolution_cut_value:
        return none
    hold_end = _bisect_left(bar_value, resolution_cut_value)
    if hold_end > w_end:
        hold_end = w_end
    if hold_end - gi < 2:
        return none

    entry_price = bar_close[gi]
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
        d2 = abs(hh - pc)
        if d2 > tr:
            tr = d2
        d3 = abs(ll - pc)
        if d3 > tr:
            tr = d3
        tr_sum += tr
        cnt += 1
        j += 1
    if cnt < 1 or entry_price == 0.0:
        return none
    atr = tr_sum / cnt
    if atr == 0.0:
        return none
    atr_pct = atr / entry_price

    peak = 0.0
    gj = gi
    while gj < hold_end:
        i_rel = gj - gi
        oo = bar_open[gj]
        hh = bar_high[gj]
        ll = bar_low[gj]
        cc = bar_close[gj]
        ret_c = cc / entry_price - 1.0
        ret_h = hh / entry_price - 1.0
        ret_l = ll / entry_price - 1.0
        reason = 0
        timing = 0
        hard_floor_pct = 0
        active_stop = 0.0

        if i_rel > 0:
            # Stops are standing orders established from prior bars, so they are
            # evaluated before a close-executed probability exit.
            stop_dist = atr_mult * atr_pct
            trailing_stop = entry_price * (1.0 + peak - stop_dist)
            active_stop = trailing_stop
            stop_reason = 1

            if peak >= lock_activate:
                hard_floor_pct = int(peak * 100.0)
                hard_floor = hard_floor_pct / 100.0
                lock_stop = entry_price * (1.0 + hard_floor)
                # If both protective rules are active, the tighter/higher long
                # stop is the one that would execute first.
                if lock_stop >= active_stop:
                    active_stop = lock_stop
                    stop_reason = 2

            if ll <= active_stop:
                reason = stop_reason
                if oo == oo:  # finite/NaN check; merged data has no inf values
                    if oo <= active_stop:
                        cc = oo
                        timing = 1
                    else:
                        cc = active_stop
                        timing = 2
                else:
                    # Fail-conservative fallback: use the observed daily Low, not
                    # an impossible stop-level fill, when Open is unavailable.
                    cc = ll
                    timing = 3
                ret_c = cc / entry_price - 1.0
            else:
                pv = 1.0
                idx = _bisect_left(day_uni, bar_norm[gj])
                if idx < day_uni.shape[0] and day_uni[idx] == bar_norm[gj]:
                    pv = pval_uni[idx]
                if pv < theta_out:
                    reason = 3
                    timing = 0
                elif gj == hold_end - 1:
                    reason = 4
                    timing = 0

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
                1, int(entry_pt_index), int(gi), int(gj), cc, int(reason), int(timing),
                int(hard_floor_pct), active_stop, peak, lo, ret_c, entry_price,
            )

        # Current-bar High becomes available only after this bar's stop checks.
        if i_rel == 0:
            peak = 0.0
        elif ret_h > peak:
            peak = ret_h
        gj += 1

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
    return (1, int(entry_pt_index), int(gi), int(last), cc, 5, 0, 0, 0.0, peak, lo, ret_c, entry_price)


def scan_candidate_proper(prices_arg, probs_arg, sym, mkt, t_theta, t_e, p_surge, r_surge, policy):
    bar_value, bar_norm, bar_open, bar_high, bar_low, bar_close, bars = _symbol_arrays(prices_arg, sym)
    if bar_value.shape[0] < 2:
        return None
    pt_value, pval_raw, day_uni, pval_uni, points = _market_arrays(probs_arg, mkt)
    if pt_value.shape[0] == 0:
        return None
    result = _scan_proper(
        bar_value, bar_norm, bar_open, bar_high, bar_low, bar_close,
        pt_value, pval_raw, day_uni, pval_uni,
        np.int64(t_theta.value) - 30 * _DAY_NS,
        np.int64(t_e.value),
        np.int64(t_theta.normalize().value),
        np.int64((t_e - pd.Timedelta(days=1)).value),
        float(policy['enter_strong']), float(policy['enter_floor']), int(policy['hold_days']),
        float(policy['atr_mult']), float(policy['lock_activate']), float(policy['theta_out']),
        float(p_surge) if p_surge is not None else float('nan'),
        float(policy.get('max_prob_surge', 999.0)),
        float(r_surge) if r_surge is not None else float('nan'),
        float(policy.get('max_price_runup', 999.0)),
    )
    if result[0] == 0:
        return None
    (
        _status, entry_pt_index, entry_idx, exit_idx, exit_price, reason, timing,
        hard_floor_pct, active_stop, peak, trough, ret_c, entry_price,
    ) = result
    return {
        'entry_ts': bars[int(entry_idx)][0],
        'entry_prob': float(points[int(entry_pt_index)][1]),
        'entry_price': float(entry_price),
        'exit_ts': bars[int(exit_idx)][0],
        'exit_price': float(exit_price),
        'reason_code': int(reason),
        'timing_code': int(timing),
        'hard_floor_pct': int(hard_floor_pct),
        'active_stop': float(active_stop),
        'peak': float(peak),
        'trough': float(trough),
        'ret_c': float(ret_c),
        'exit_open': float(bar_open[int(exit_idx)]),
        'exit_high': float(bar_high[int(exit_idx)]),
        'exit_low': float(bar_low[int(exit_idx)]),
        'exit_close': float(bar_close[int(exit_idx)]),
    }


def simulate_one_proper(row, prices_arg, probs_arg, policy):
    sym, mkt = row['symbol'], row['market_id']
    question = str(row.get('question', ''))
    polarity, polarity_source = ns['resolve_polarity'](question, sym)
    if polarity == 0:
        return None
    eff_probs = ns['effective_probs'](probs_arg, mkt, polarity)
    t_theta = pd.Timestamp(row['t_theta']).tz_convert('UTC')
    t_e = pd.Timestamp(row['t_e']).tz_convert('UTC')
    scanned = scan_candidate_proper(
        prices_arg, eff_probs, sym, mkt, t_theta, t_e,
        ns['effective_prob_surge'](row, polarity), row.get('feat_runup_since_t0'), policy,
    )
    if scanned is None:
        return None
    reason_code = scanned['reason_code']
    if reason_code == 1:
        reason = f"trailing_{policy['atr_mult']:.1f}ATR"
    elif reason_code == 2:
        reason = f"profit_lock_{scanned['hard_floor_pct']}%"
    elif reason_code == 3:
        reason = f"poly<{policy['theta_out']}"
    elif reason_code == 4:
        reason = 'resolution-1d'
    else:
        reason = 'end_of_window'
    timing_code = scanned['timing_code']
    timing = {0: 'close', 1: 'open_gap', 2: 'intraday_stop', 3: 'missing_open_low_fallback'}[timing_code]
    mkt_probs = eff_probs.get(mkt, [])
    converged = 'YES' if mkt_probs and mkt_probs[-1][1] >= 0.5 else 'NO' if mkt_probs else 'UNKNOWN'
    return {
        'market_id': mkt,
        'symbol': sym,
        'question': question,
        'polarity': polarity,
        'polarity_source': polarity_source,
        'pct': scanned['entry_prob'],
        'converged': converged,
        'asset_confidence': row.get('confidence_score'),
        'question_confidence': row.get('feat_llm_confidence'),
        'archetype': row.get('feat_archetype', ''),
        'relevance': float(row.get(ns['RELEVANCE_COL'], 0)),
        'split': row.get('split', ''),
        'entry_date': str(pd.Timestamp(scanned['entry_ts']).date()),
        'entry_prob': scanned['entry_prob'],
        # Keep exact prices internally. Round only when exporting/displaying.
        'entry_price': scanned['entry_price'],
        'exit_date': str(pd.Timestamp(scanned['exit_ts']).date()),
        'exit_price': scanned['exit_price'],
        'exit_reason': reason,
        'exit_timing': timing,
        'active_stop': scanned['active_stop'],
        'exit_open': scanned['exit_open'],
        'exit_high': scanned['exit_high'],
        'exit_low': scanned['exit_low'],
        'exit_close': scanned['exit_close'],
        'peak_pct': scanned['peak'] * 100.0,
        'trough_pct': scanned['trough'] * 100.0,
        'return_pct': scanned['ret_c'] * 100.0,
    }


# ---------------------------------------------------------------------------
# Portfolio simulator with coherent benchmark rotation timing.
# - asset and benchmark entry both at Close;
# - gap-through stop asset sale and benchmark rebuy both at Open;
# - intraday stop asset sale at stop, benchmark rebuy at Close proxy because
#   daily bars do not contain the synchronized intraday benchmark price;
# - probability/final exits and benchmark rebuy at Close.
# ---------------------------------------------------------------------------
_OPEN_CACHE: dict[tuple[int, str], tuple[np.ndarray, np.ndarray]] = {}


def _open_on(prices_arg: dict, symbol: str, date: Any) -> float | None:
    key = (id(prices_arg), symbol)
    cached = _OPEN_CACHE.get(key)
    if cached is None:
        bars = prices_arg.get(symbol, [])
        if not bars:
            return None
        days = np.array([ns['as_utc_day'](b[0]).value for b in bars], dtype=np.int64)
        values = np.asarray([float(b[1]) for b in bars], dtype=float)
        cached = (days, values)
        _OPEN_CACHE[key] = cached
    days, values = cached
    d = ns['as_utc_day'](date).value
    loc = int(np.searchsorted(days, d, side='left'))
    if loc >= len(days) or days[loc] != d:
        return None
    value = float(values[loc])
    return value if np.isfinite(value) else None


def sim_opp_cost_proper(df_arg, prices_arg, probs_arg, policy, *, bench_sym='SPY',
                        initial=None, start_date=None, end_date=None,
                        sync_gap_benchmark_open=True):
    if initial is None:
        initial = ns['INITIAL_CAPITAL']
    empty_stats = {
        'initial': initial, 'final': initial, 'total_return': 0.0, 'benchmark_return': 0.0,
        'excess_return': 0.0, 'max_dd': 0.0, 'sharpe': 0.0, 'sortino': 0.0,
        'benchmark_sharpe': 0.0, 'benchmark_sortino': 0.0, 'n_trades': 0, 'win_rate': 0.0,
        'avg_pnl': 0.0, 'avg_gross_pnl': 0.0, 'gross_trade_pnl': 0.0, 'net_trade_pnl': 0.0,
        'total_txn_cost': 0.0, 'trade_txn_cost': 0.0, 'avg_position_size': 0.0,
        'median_position_size': 0.0, 'min_position_size': 0.0, 'max_position_size': 0.0,
        'start_date': None, 'end_date': None, 'n_equity_days': 0,
        'skip_max_concurrent': 0, 'skip_duplicate_symbol': 0, 'skip_insufficient_capital': 0,
        'missing_open_fallback_exits': 0, 'gap_open_benchmark_rebuys': 0,
        'intraday_stop_close_proxy_rebuys': 0,
    }
    if df_arg.empty:
        return pd.DataFrame(), pd.DataFrame(), empty_stats

    sim_prices, sim_probs = ns['truncate_paths'](prices_arg, probs_arg, end_date)
    all_trades = []
    for _, row in df_arg.sort_values('t_theta').iterrows():
        candidate_theta = ns['as_utc_day'](row['t_theta'])
        trade = simulate_one_proper(row, sim_prices, sim_probs, policy)
        if trade is None:
            continue
        trade = dict(trade)
        trade['_entry_ts'] = ns['as_utc_day'](trade['entry_date'])
        trade['_exit_ts'] = ns['as_utc_day'](trade['exit_date'])
        if trade['_entry_ts'] < candidate_theta:
            raise ValueError(f"{trade['symbol']} entered before candidate t_theta")
        trade['candidate_t_theta'] = str(candidate_theta.date())
        trade['candidate_t_e'] = str(ns['as_utc_day'](row['t_e']).date())
        all_trades.append(trade)
    all_trades.sort(key=lambda t: t['_entry_ts'])

    candidate_start, candidate_end = ns['_frame_bounds'](df_arg)
    eval_start = ns['as_utc_day'](start_date) if start_date is not None else min(
        (t['_entry_ts'] for t in all_trades), default=candidate_start)
    eval_end = ns['as_utc_day'](end_date) if end_date is not None else max(
        (t['_exit_ts'] for t in all_trades), default=candidate_end)
    if end_date is not None and any(t['_exit_ts'] > eval_end for t in all_trades):
        raise ValueError('generated trades exit after evaluation end')

    calendar = ns['_calendar_dates'](sim_prices, bench_sym, eval_start, eval_end)
    if not calendar:
        raise ValueError('No benchmark bars overlap evaluation range')
    first_day, last_day = calendar[0], calendar[-1]
    first_bench_close = ns['_close_on'](sim_prices, bench_sym, first_day)
    last_bench_close = ns['_close_on'](sim_prices, bench_sym, last_day)
    if first_bench_close is None or last_bench_close is None:
        raise ValueError(f'Unable to price {bench_sym}')

    initial_bench_shares = ns['_bench_buy_qty'](initial, first_bench_close)
    initial_cost = ns['ib_cost'](initial_bench_shares, first_bench_close, False)
    initial_cash = initial - initial_bench_shares * first_bench_close - initial_cost
    bench_shares = initial_bench_shares
    cash = initial_cash
    total_txn_cost = initial_cost
    open_positions: list[dict] = []
    completed: list[dict] = []
    equity_rows: list[dict] = []
    trade_idx = 0
    skip_max_concurrent = skip_duplicate_symbol = skip_insufficient_capital = 0
    missing_open_fallback_exits = 0
    gap_open_benchmark_rebuys = 0
    intraday_stop_close_proxy_rebuys = 0

    def close_position(pos, close_day, exit_price, exit_reason):
        nonlocal cash, bench_shares, total_txn_cost
        nonlocal missing_open_fallback_exits, gap_open_benchmark_rebuys, intraday_stop_close_proxy_rebuys
        qty = int(pos['_qty'])
        entry_price = float(pos['entry_price'])
        asset_sell_cost = ns['ib_cost'](qty, exit_price, True)
        sale_proceeds = qty * exit_price - asset_sell_cost
        timing = str(pos.get('exit_timing', 'close'))
        if sync_gap_benchmark_open and timing == 'open_gap':
            bench_rebuy_price = _open_on(sim_prices, bench_sym, close_day)
            if bench_rebuy_price is None:
                bench_rebuy_price = ns['_close_on'](sim_prices, bench_sym, close_day)
                bench_rebuy_basis = 'close_fallback_missing_benchmark_open'
            else:
                bench_rebuy_basis = 'open'
                gap_open_benchmark_rebuys += 1
        else:
            bench_rebuy_price = ns['_close_on'](sim_prices, bench_sym, close_day)
            bench_rebuy_basis = 'close'
            if timing == 'intraday_stop':
                intraday_stop_close_proxy_rebuys += 1
        if timing == 'missing_open_low_fallback':
            missing_open_fallback_exits += 1
        if bench_rebuy_price is None or bench_rebuy_price <= 0:
            raise ValueError(f'Cannot price benchmark rebuy for {bench_sym} on {close_day}')
        bench_rebuy_price = float(bench_rebuy_price)
        rebuy_qty = ns['_bench_buy_qty'](sale_proceeds, bench_rebuy_price)
        rebuy_cost = ns['ib_cost'](rebuy_qty, bench_rebuy_price, False)
        cash += sale_proceeds - rebuy_qty * bench_rebuy_price - rebuy_cost
        bench_shares += rebuy_qty
        total_txn_cost += asset_sell_cost + rebuy_cost
        direct_cost = (
            float(pos['_benchmark_sell_cost']) + float(pos['_asset_buy_cost'])
            + asset_sell_cost + rebuy_cost
        )
        gross_pnl = qty * (exit_price - entry_price)
        net_pnl = gross_pnl - direct_cost
        exposure = max(float(pos['_asset_entry_notional']), 1e-12)
        pos['exit_price'] = float(exit_price)
        pos['exit_date'] = str(close_day.date())
        pos['realized_exit_reason'] = exit_reason
        pos['gross_pnl'] = round(gross_pnl, 2)
        pos['pnl'] = round(net_pnl, 2)
        pos['pnl_pct'] = round(net_pnl / exposure * 100.0, 4)
        pos['txn_cost'] = round(direct_cost, 2)
        pos['exit_value'] = round(qty * exit_price, 2)
        pos['benchmark_rebuy_qty'] = rebuy_qty
        pos['benchmark_rebuy_price'] = bench_rebuy_price
        pos['benchmark_rebuy_basis'] = bench_rebuy_basis
        completed.append(pos)

    def sweep_idle_cash(bench_close):
        nonlocal cash, bench_shares, total_txn_cost
        if not ns['FULLY_INVESTED_SWEEP'] or bench_close <= 0 or cash < ns['MIN_SWEEP_CASH']:
            return
        qty = ns['_bench_buy_qty'](cash, bench_close)
        if qty <= 0:
            return
        buy_cost = ns['ib_cost'](qty, bench_close, False)
        cash -= qty * bench_close + buy_cost
        bench_shares += qty
        total_txn_cost += buy_cost

    def try_open_trade(trade, day, bench_close, *, base_ps, max_concurrent):
        nonlocal cash, bench_shares, total_txn_cost
        nonlocal skip_max_concurrent, skip_duplicate_symbol, skip_insufficient_capital
        if len(open_positions) >= max_concurrent:
            skip_max_concurrent += 1
            return False
        if any(pos['symbol'] == trade['symbol'] for pos in open_positions):
            skip_duplicate_symbol += 1
            return False
        marked_open_value = sum(
            int(pos['_qty']) * float(ns['_close_on'](sim_prices, pos['symbol'], day) or pos['entry_price'])
            for pos in open_positions
        )
        current_equity = bench_shares * bench_close + marked_open_value + cash
        desired_allocation = current_equity * base_ps
        entry_price = float(trade['entry_price'])
        if entry_price <= 0 or desired_allocation < entry_price:
            skip_insufficient_capital += 1
            return False
        cash_contribution = min(max(cash, 0.0), desired_allocation)
        shortfall = desired_allocation - cash_contribution
        if shortfall > 0:
            desired_sell = shortfall / bench_close if ns['FRACTIONAL_BENCHMARK'] else int(shortfall / bench_close)
            benchmark_sell_qty = min(desired_sell, bench_shares)
        else:
            benchmark_sell_qty = 0.0
        if cash_contribution + benchmark_sell_qty * bench_close < entry_price:
            skip_insufficient_capital += 1
            return False
        benchmark_sell_cost = ns['ib_cost'](benchmark_sell_qty, bench_close, True) if benchmark_sell_qty > 0 else 0.0
        available_for_asset = cash_contribution + benchmark_sell_qty * bench_close - benchmark_sell_cost
        asset_qty = ns['_affordable_buy_qty'](available_for_asset, entry_price)
        if asset_qty < 1:
            skip_insufficient_capital += 1
            return False
        asset_buy_cost = ns['ib_cost'](asset_qty, entry_price, False)
        asset_cash_needed = asset_qty * entry_price + asset_buy_cost
        if asset_cash_needed > available_for_asset + 1e-9:
            skip_insufficient_capital += 1
            return False
        bench_shares -= benchmark_sell_qty
        cash += available_for_asset - asset_cash_needed - cash_contribution
        total_txn_cost += benchmark_sell_cost + asset_buy_cost
        open_positions.append({
            **trade,
            '_qty': asset_qty,
            '_position_size_pct': base_ps,
            '_asset_entry_notional': asset_qty * entry_price,
            '_equity_at_entry': current_equity,
            'invested_frac_pct': asset_qty * entry_price / max(current_equity, 1e-12) * 100.0,
            '_benchmark_sell_cost': benchmark_sell_cost,
            '_asset_buy_cost': asset_buy_cost,
            '_entry_ts': trade['_entry_ts'],
            '_exit_ts': trade['_exit_ts'],
        })
        return True

    for day in calendar:
        base_ps = float(policy.get('position_size_pct', 0.10))
        max_concurrent = int(policy.get('max_concurrent', 10))
        bench_close = ns['_close_on'](sim_prices, bench_sym, day)
        if bench_close is None:
            continue
        still_open = []
        for pos in open_positions:
            if pos['_exit_ts'] <= day:
                exit_reason = str(pos.get('exit_reason', 'strategy_exit'))
                if end_date is not None and exit_reason == 'end_of_window':
                    exit_reason = 'evaluation_end_liquidation'
                close_position(pos, day, float(pos['exit_price']), exit_reason)
            else:
                still_open.append(pos)
        open_positions = still_open

        while trade_idx < len(all_trades):
            trade = all_trades[trade_idx]
            if trade['_entry_ts'] > day:
                break
            trade_idx += 1
            if trade['_entry_ts'] < day:
                if (day - trade['_entry_ts']).days > 4:
                    continue
                trade['_entry_ts'] = day
                trade['entry_date'] = str(day.date())
            try_open_trade(trade, day, float(bench_close), base_ps=base_ps, max_concurrent=max_concurrent)

        sweep_idle_cash(float(bench_close))
        open_value = sum(
            int(pos['_qty']) * float(ns['_close_on'](sim_prices, pos['symbol'], day) or pos['entry_price'])
            for pos in open_positions
        )
        equity = bench_shares * bench_close + open_value + cash
        passive = initial_bench_shares * bench_close + initial_cash
        equity_rows.append({
            'date': str(day.date()), 'equity': round(equity, 2),
            'benchmark_equity': round(passive, 2), 'cash': round(cash, 2),
            'benchmark_shares': bench_shares, 'open_positions': len(open_positions),
        })

    if open_positions:
        for pos in list(open_positions):
            forced_price = ns['_close_on'](sim_prices, pos['symbol'], last_day)
            if forced_price is None:
                forced_price = float(pos['entry_price'])
            pos['exit_timing'] = 'close'
            close_position(pos, last_day, float(forced_price), 'evaluation_end_liquidation')
        open_positions = []

    final_equity = bench_shares * last_bench_close + cash
    final_passive = initial_bench_shares * last_bench_close + initial_cash
    if equity_rows:
        equity_rows[-1].update({
            'equity': round(final_equity, 2), 'benchmark_equity': round(final_passive, 2),
            'cash': round(cash, 2), 'benchmark_shares': bench_shares, 'open_positions': 0,
        })
    else:
        equity_rows.append({
            'date': str(last_day.date()), 'equity': round(final_equity, 2),
            'benchmark_equity': round(final_passive, 2), 'cash': round(cash, 2),
            'benchmark_shares': bench_shares, 'open_positions': 0,
        })

    equity_df = pd.DataFrame(equity_rows)
    trade_df = pd.DataFrame(completed)
    equity_values = equity_df['equity'].astype(float).to_numpy()
    peaks = np.maximum.accumulate(equity_values)
    drawdowns = np.where(peaks > 0, equity_values / peaks - 1.0, 0.0)
    max_dd = float(np.min(drawdowns) * 100.0) if len(drawdowns) else 0.0
    adv = ns['_calc_advanced_metrics'](equity_df['equity'])
    bench_adv = ns['_calc_advanced_metrics'](equity_df['benchmark_equity'])

    if trade_df.empty:
        gross_trade_pnl = net_trade_pnl = trade_txn_cost = 0.0
        win_rate = avg_pnl = avg_gross_pnl = 0.0
        position_sizes = np.asarray([], dtype=float)
    else:
        gross_trade_pnl = float(trade_df['gross_pnl'].sum())
        net_trade_pnl = float(trade_df['pnl'].sum())
        trade_txn_cost = float(trade_df['txn_cost'].sum())
        win_rate = float((trade_df['pnl'] > 0).mean() * 100.0)
        avg_pnl = float(trade_df['pnl'].mean())
        avg_gross_pnl = float(trade_df['gross_pnl'].mean())
        position_sizes = trade_df['_position_size_pct'].astype(float).to_numpy()

    stats = {
        'initial': round(initial, 2), 'final': round(final_equity, 2),
        'total_return': round((final_equity / initial - 1.0) * 100.0, 4),
        'benchmark_return': round((final_passive / initial - 1.0) * 100.0, 4),
        'excess_return': round((final_equity - final_passive) / initial * 100.0, 4),
        'max_dd': round(max_dd, 4),
        'sharpe': round(adv['sharpe'], 4), 'sortino': round(adv['sortino'], 4),
        'benchmark_sharpe': round(bench_adv['sharpe'], 4),
        'benchmark_sortino': round(bench_adv['sortino'], 4),
        'n_trades': int(len(trade_df)), 'win_rate': round(win_rate, 4),
        'avg_pnl': round(avg_pnl, 4), 'avg_gross_pnl': round(avg_gross_pnl, 4),
        'gross_trade_pnl': round(gross_trade_pnl, 2), 'net_trade_pnl': round(net_trade_pnl, 2),
        'total_txn_cost': round(total_txn_cost, 2), 'trade_txn_cost': round(trade_txn_cost, 2),
        'avg_position_size': round(float(position_sizes.mean() * 100.0), 4) if len(position_sizes) else 0.0,
        'median_position_size': round(float(np.median(position_sizes) * 100.0), 4) if len(position_sizes) else 0.0,
        'min_position_size': round(float(position_sizes.min() * 100.0), 4) if len(position_sizes) else 0.0,
        'max_position_size': round(float(position_sizes.max() * 100.0), 4) if len(position_sizes) else 0.0,
        'start_date': str(first_day.date()), 'end_date': str(last_day.date()),
        'n_equity_days': int(len(equity_df)),
        'skip_max_concurrent': skip_max_concurrent,
        'skip_duplicate_symbol': skip_duplicate_symbol,
        'skip_insufficient_capital': skip_insufficient_capital,
        'missing_open_fallback_exits': missing_open_fallback_exits,
        'gap_open_benchmark_rebuys': gap_open_benchmark_rebuys,
        'intraday_stop_close_proxy_rebuys': intraday_stop_close_proxy_rebuys,
    }
    return trade_df, equity_df, stats


# Install proper functions into the notebook namespace so CEM uses them.
ns['simulate_one'] = simulate_one_proper


def install_simulator(sync_gap_benchmark_open: bool):
    def _sim(*args, **kwargs):
        return sim_opp_cost_proper(*args, **kwargs, sync_gap_benchmark_open=sync_gap_benchmark_open)
    ns['sim_opp_cost'] = _sim


# ---------------------------------------------------------------------------
# Run frozen-policy variants and corrected CEM retraining.
# ---------------------------------------------------------------------------
rows = []
policies_out: dict[str, dict] = {'original_frozen': original_policies}

# Read prior minimal-gap results for transparent comparison.
minimal = pd.read_csv(ROOT / 'stop_execution_audit_retry' / 'frozen_policy_comparison.csv')
for _, r in minimal.iterrows():
    rows.append({
        'engine': 'gap_fix_only_frozen', 'benchmark': r['benchmark'],
        'return_pct': r['open_aware_return_pct'], 'benchmark_return_pct': r['benchmark_return_pct'],
        'excess_return_pct': r['open_aware_excess_return_pct'], 'sharpe': r['open_aware_sharpe'],
        'max_dd_pct': r['open_aware_max_dd_pct'], 'n_trades': int(r['open_aware_n_trades']),
        'win_rate_pct': np.nan, 'total_txn_cost': r['open_aware_total_txn_cost'],
        'policy_type': 'original_frozen',
    })

# Proper engine, original frozen policy; show benchmark-close and gap-open rotation.
for sync_name, sync in [('proper_frozen_close_rebuy', False), ('proper_frozen_gap_open_rebuy', True)]:
    install_simulator(sync)
    for bench in ('SPY', 'QQQ'):
        clear_proper_caches(); _OPEN_CACHE.clear()
        trades, equity, stats = ns['sim_opp_cost'](
            oos_df, prices, probs, original_policies[bench], bench_sym=bench,
            start_date=OOS_START, end_date=OOS_END,
        )
        trades.to_csv(OUT / f'{bench.lower()}_{sync_name}_trades.csv', index=False)
        equity.to_csv(OUT / f'{bench.lower()}_{sync_name}_equity.csv', index=False)
        rows.append({
            'engine': sync_name, 'benchmark': bench,
            'return_pct': stats['total_return'], 'benchmark_return_pct': stats['benchmark_return'],
            'excess_return_pct': stats['excess_return'], 'sharpe': stats['sharpe'],
            'max_dd_pct': stats['max_dd'], 'n_trades': stats['n_trades'],
            'win_rate_pct': stats['win_rate'], 'total_txn_cost': stats['total_txn_cost'],
            'policy_type': 'original_frozen',
            'missing_open_fallback_exits': stats['missing_open_fallback_exits'],
            'gap_open_benchmark_rebuys': stats['gap_open_benchmark_rebuys'],
            'intraday_stop_close_proxy_rebuys': stats['intraday_stop_close_proxy_rebuys'],
        })

# Main production-daily-bar specification: gap exits rotate to benchmark Open.
install_simulator(True)
retrained: dict[str, dict] = {}
train_diagnostics: dict[str, dict] = {}
for bench in ('SPY', 'QQQ'):
    seed = ns['CEM_BASE_SEED'] + ns['BENCHMARK_SEED_OFFSET'][bench]
    print(f'\n[Corrected CEM retrain {bench}] seed={seed}', flush=True)
    clear_proper_caches(); _OPEN_CACHE.clear()
    start = time.time()
    policy, train_score = ns['cem_search_baseline'](
        df, prices, probs, bench_sym=bench, train_fit_cutoff=OOS_START,
        n_iter=ns['CEM_ITERS'], pop=ns['CEM_POP'], seed=seed,
    )
    elapsed = time.time() - start
    retrained[bench] = policy

    clear_proper_caches(); _OPEN_CACHE.clear()
    tr_trades, tr_equity, tr_stats = ns['sim_opp_cost'](
        train_df, prices, probs, policy, bench_sym=bench,
        start_date=ns['as_utc_day'](train_df['t_theta'].min()), end_date=train_eval_end,
    )
    clear_proper_caches(); _OPEN_CACHE.clear()
    te_trades, te_equity, te_stats = ns['sim_opp_cost'](
        oos_df, prices, probs, policy, bench_sym=bench,
        start_date=OOS_START, end_date=OOS_END,
    )
    tr_trades.to_csv(OUT / f'{bench.lower()}_proper_retrained_train_trades.csv', index=False)
    tr_equity.to_csv(OUT / f'{bench.lower()}_proper_retrained_train_equity.csv', index=False)
    te_trades.to_csv(OUT / f'{bench.lower()}_proper_retrained_oos_trades.csv', index=False)
    te_equity.to_csv(OUT / f'{bench.lower()}_proper_retrained_oos_equity.csv', index=False)
    train_diagnostics[bench] = {'cem_score': train_score, 'fit_seconds': elapsed, **tr_stats}
    rows.append({
        'engine': 'proper_retrained_gap_open_rebuy', 'benchmark': bench,
        'return_pct': te_stats['total_return'], 'benchmark_return_pct': te_stats['benchmark_return'],
        'excess_return_pct': te_stats['excess_return'], 'sharpe': te_stats['sharpe'],
        'max_dd_pct': te_stats['max_dd'], 'n_trades': te_stats['n_trades'],
        'win_rate_pct': te_stats['win_rate'], 'total_txn_cost': te_stats['total_txn_cost'],
        'policy_type': 'retrained_corrected_engine',
        'missing_open_fallback_exits': te_stats['missing_open_fallback_exits'],
        'gap_open_benchmark_rebuys': te_stats['gap_open_benchmark_rebuys'],
        'intraday_stop_close_proxy_rebuys': te_stats['intraday_stop_close_proxy_rebuys'],
        'train_return_pct': tr_stats['total_return'], 'train_sharpe': tr_stats['sharpe'],
        'train_max_dd_pct': tr_stats['max_dd'], 'train_n_trades': tr_stats['n_trades'],
        'cem_train_score': train_score, 'fit_seconds': elapsed,
    })

policies_out['proper_retrained'] = retrained
summary = pd.DataFrame(rows)
summary.to_csv(OUT / 'proper_execution_summary.csv', index=False)
(OUT / 'proper_retrained_policies.json').write_text(json.dumps(retrained, indent=2), encoding='utf-8')
(OUT / 'all_policy_sets.json').write_text(json.dumps(policies_out, indent=2), encoding='utf-8')
(OUT / 'train_diagnostics.json').write_text(json.dumps(train_diagnostics, indent=2), encoding='utf-8')

# Exit-reason/timing diagnostics for main proper frozen and retrained OOS runs.
diag_rows = []
for engine in ('proper_frozen_gap_open_rebuy', 'proper_retrained_oos'):
    for bench in ('SPY', 'QQQ'):
        if engine == 'proper_retrained_oos':
            path = OUT / f'{bench.lower()}_proper_retrained_oos_trades.csv'
        else:
            path = OUT / f'{bench.lower()}_{engine}_trades.csv'
        t = pd.read_csv(path)
        for (reason, timing), g in t.groupby(['exit_reason', 'exit_timing'], dropna=False):
            diag_rows.append({
                'engine': engine, 'benchmark': bench, 'exit_reason': reason,
                'exit_timing': timing, 'n_trades': len(g), 'net_pnl': g['pnl'].sum(),
                'avg_pnl': g['pnl'].mean(), 'win_rate_pct': (g['pnl'] > 0).mean() * 100,
            })
pd.DataFrame(diag_rows).to_csv(OUT / 'exit_reason_timing_summary.csv', index=False)

manifest = {
    'unresolved_open_rows_in_full_frozen_price_file': unresolved_open_rows,
    'candidate_rows_total': len(df), 'train_rows': len(train_df), 'oos_rows': len(oos_df),
    'oos_start': str(OOS_START), 'oos_end': str(OOS_END),
    'proper_logic': {
        'entry': 'same-day Close after stored daily probability eligibility',
        'entry_day_stops': 'disabled because entry occurs at Close',
        'peak': 'updated after exit checks; current High affects next bar only',
        'protective_stop': 'max(previous-peak ATR stop, armed integer-percent profit floor)',
        'stop_priority': 'standing stop before Close probability exit',
        'gap_fill': 'Open when Open <= active stop; otherwise stop level',
        'missing_open': 'daily Low conservative fallback',
        'price_precision': 'exact float internally; rounding only in exported reporting',
        'benchmark_rotation': 'benchmark Open for gap exits; Close for close exits and intraday-stop proxy',
    },
}
(OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('\nFINAL SUMMARY')
print(summary.to_string(index=False))
print('\nRETRAINED POLICIES')
print(json.dumps(retrained, indent=2))
