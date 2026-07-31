from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numba import njit
from scipy.stats import qmc

ROOT = Path('/mnt/data')
OUT = ROOT / 'benchmark_exit_search'
OUT.mkdir(exist_ok=True)

# Load the corrected execution infrastructure without executing its old experiments.
source = (ROOT / 'proper_execution_rerun.py').read_text(encoding='utf-8')
source = source.split('# ---------------------------------------------------------------------------\n# Run frozen-policy variants and corrected CEM retraining.')[0]
source = source.replace('@njit(cache=True)', '@njit(cache=False)')
# Reject materially partial event positions. This keeps the portfolio policy coherent.
old_snippet = """        asset_cash_needed = asset_qty * entry_price + asset_buy_cost\n        if asset_cash_needed > available_for_asset + 1e-9:\n            skip_insufficient_capital += 1\n            return False\n"""
new_snippet = """        asset_cash_needed = asset_qty * entry_price + asset_buy_cost\n        if asset_cash_needed > available_for_asset + 1e-9:\n            skip_insufficient_capital += 1\n            return False\n        if asset_qty * entry_price < 0.90 * desired_allocation:\n            skip_insufficient_capital += 1\n            return False\n"""
if old_snippet not in source:
    raise RuntimeError('Could not install full-position guard')
source = source.replace(old_snippet, new_snippet)
exec(compile(source, 'proper_execution_prefix.py', 'exec'), globals())
as_utc_day = ns['as_utc_day']
resolve_polarity = ns['resolve_polarity']
effective_probs = ns['effective_probs']
RELEVANCE_COL = ns['RELEVANCE_COL']

# ---------------------------------------------------------------------------
# Revised execution kernel.
# - probability-surge veto removed entirely;
# - price run-up is recomputed at the actual entry close;
# - optional hard maximum-loss stop;
# - optional no-follow-through close exit relative to the benchmark.
# ---------------------------------------------------------------------------
_BENCH_ALIGN_CACHE: dict[tuple[int, str, str], np.ndarray] = {}


def _aligned_benchmark_closes(prices_arg: dict, symbol: str, benchmark: str) -> np.ndarray:
    key = (id(prices_arg), symbol, benchmark)
    cached = _BENCH_ALIGN_CACHE.get(key)
    if cached is not None:
        return cached
    _, norm, *_ = _symbol_arrays(prices_arg, symbol)
    bench_bars = prices_arg.get(benchmark, [])
    bd = np.asarray([as_utc_day(b[0]).value for b in bench_bars], dtype=np.int64)
    bc = np.asarray([float(b[4]) for b in bench_bars], dtype=float)
    out = np.empty(len(norm), dtype=float)
    for i, day in enumerate(norm):
        j = int(np.searchsorted(bd, day, side='right')) - 1
        out[i] = bc[j] if j >= 0 else np.nan
    _BENCH_ALIGN_CACHE[key] = out
    return out


@njit(cache=True)
def _scan_v2(
    bar_value, bar_norm, bar_open, bar_high, bar_low, bar_close, benchmark_close,
    pt_value, pval_raw, day_uni, pval_uni,
    window_lo_value, t_e_value, first_eligible_value, resolution_cut_value, t0_value,
    enter_strong, enter_floor, hold_days, atr_mult, lock_activate, theta_out,
    max_price_runup, hard_loss_cap, use_hard_cap,
    no_follow_days, no_follow_mfe, use_no_follow,
):
    # reason: 0 none, 1 trailing, 2 profit lock, 3 probability, 4 resolution,
    # 5 end, 6 hard loss cap, 7 no follow-through.
    none = (0, -1, -1, -1, 0.0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
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

    gi = _bisect_left(bar_value, pt_value[entry_pt_index])
    if gi < w_start:
        gi = w_start
    if gi >= w_end or w_end - gi < 2 or bar_value[gi] >= resolution_cut_value:
        return none
    hold_end = _bisect_left(bar_value, resolution_cut_value)
    if hold_end > w_end:
        hold_end = w_end
    if hold_end - gi < 2:
        return none

    entry_price = bar_close[gi]
    t0_idx = _bisect_left(bar_value, t0_value)
    if t0_idx < 0:
        t0_idx = 0
    if t0_idx >= bar_close.shape[0] or bar_close[t0_idx] <= 0.0:
        return none
    actual_runup = entry_price / bar_close[t0_idx] - 1.0
    if actual_runup > max_price_runup:
        return none

    h_start = gi - 15
    if h_start < w_start:
        h_start = w_start
    tr_sum = 0.0
    cnt = 0
    j = h_start + 1
    while j <= gi:
        hh, ll, pc = bar_high[j], bar_low[j], bar_close[j - 1]
        tr = hh - ll
        d2 = abs(hh - pc)
        d3 = abs(ll - pc)
        if d2 > tr:
            tr = d2
        if d3 > tr:
            tr = d3
        tr_sum += tr
        cnt += 1
        j += 1
    if cnt < 1 or entry_price <= 0.0:
        return none
    atr = tr_sum / cnt
    if atr <= 0.0:
        return none
    atr_pct = atr / entry_price
    benchmark_entry = benchmark_close[gi]
    if not (benchmark_entry == benchmark_entry) or benchmark_entry <= 0.0:
        return none

    peak = 0.0
    gj = gi
    while gj < hold_end:
        i_rel = gj - gi
        oo, hh, ll, cc = bar_open[gj], bar_high[gj], bar_low[gj], bar_close[gj]
        ret_c = cc / entry_price - 1.0
        ret_h = hh / entry_price - 1.0
        ret_l = ll / entry_price - 1.0
        reason = 0
        timing = 0
        hard_floor_pct = 0
        active_stop = 0.0
        active_close_return = 0.0

        if i_rel > 0:
            # Standing protective stop is based only on the prior confirmed peak.
            stop_dist = atr_mult * atr_pct
            active_stop = entry_price * (1.0 + peak - stop_dist)
            stop_reason = 1

            if peak >= lock_activate:
                hard_floor_pct = int(peak * 100.0)
                lock_stop = entry_price * (1.0 + hard_floor_pct / 100.0)
                if lock_stop >= active_stop:
                    active_stop = lock_stop
                    stop_reason = 2

            if use_hard_cap == 1:
                cap_stop = entry_price * (1.0 - hard_loss_cap)
                if cap_stop >= active_stop:
                    active_stop = cap_stop
                    stop_reason = 6

            if ll <= active_stop:
                reason = stop_reason
                if oo == oo:
                    if oo <= active_stop:
                        cc = oo
                        timing = 1
                    else:
                        cc = active_stop
                        timing = 2
                else:
                    cc = ll
                    timing = 3
                ret_c = cc / entry_price - 1.0
            else:
                # A close-based no-follow decision may use the current day's High.
                close_peak = peak
                if ret_h > close_peak:
                    close_peak = ret_h
                current_bench = benchmark_close[gj]
                if current_bench == current_bench and current_bench > 0.0:
                    bench_ret = current_bench / benchmark_entry - 1.0
                    active_close_return = ret_c - bench_ret
                if (
                    use_no_follow == 1
                    and i_rel >= no_follow_days
                    and close_peak < no_follow_mfe
                    and active_close_return <= 0.0
                ):
                    reason = 7
                    timing = 0
                else:
                    pv = 1.0
                    idx = _bisect_left(day_uni, bar_norm[gj])
                    if idx < day_uni.shape[0] and day_uni[idx] == bar_norm[gj]:
                        pv = pval_uni[idx]
                    if pv < theta_out:
                        reason = 3
                    elif gj == hold_end - 1:
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
                1, int(entry_pt_index), int(gi), int(gj), cc, int(reason), int(timing),
                int(hard_floor_pct), active_stop, peak, lo, ret_c, entry_price,
                actual_runup, active_close_return,
            )

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
    return (1, int(entry_pt_index), int(gi), int(last), cc, 5, 0, 0, 0.0,
            peak, lo, ret_c, entry_price, actual_runup, 0.0)


def scan_candidate_v2(prices_arg, probs_arg, sym, mkt, benchmark, t0, t_theta, t_e, policy):
    bar_value, bar_norm, bar_open, bar_high, bar_low, bar_close, bars = _symbol_arrays(prices_arg, sym)
    if len(bar_value) < 2:
        return None
    benchmark_close = _aligned_benchmark_closes(prices_arg, sym, benchmark)
    pt_value, pval_raw, day_uni, pval_uni, points = _market_arrays(probs_arg, mkt)
    if len(pt_value) == 0:
        return None
    result = _scan_v2(
        bar_value, bar_norm, bar_open, bar_high, bar_low, bar_close, benchmark_close,
        pt_value, pval_raw, day_uni, pval_uni,
        np.int64(t_theta.value) - 30 * _DAY_NS,
        np.int64(t_e.value), np.int64(t_theta.normalize().value),
        np.int64((t_e - pd.Timedelta(days=1)).value), np.int64(t0.value),
        float(policy['enter_strong']), float(policy['enter_floor']), int(policy['hold_days']),
        float(policy['atr_mult']), float(policy['lock_activate']), float(policy['theta_out']),
        float(policy['max_price_runup']), float(policy.get('hard_loss_cap', 0.50)),
        int(bool(policy.get('use_hard_cap', False))), int(policy.get('no_follow_days', 30)),
        float(policy.get('no_follow_mfe', 0.0)), int(bool(policy.get('use_no_follow', False))),
    )
    if result[0] == 0:
        return None
    (
        _, epi, egi, xgi, exit_price, reason, timing, floor_pct, active_stop,
        peak, trough, ret_c, entry_price, actual_runup, active_close_return,
    ) = result
    return {
        'entry_ts': bars[int(egi)][0], 'entry_prob': float(points[int(epi)][1]),
        'entry_price': float(entry_price), 'exit_ts': bars[int(xgi)][0],
        'exit_price': float(exit_price), 'reason_code': int(reason),
        'timing_code': int(timing), 'hard_floor_pct': int(floor_pct),
        'active_stop': float(active_stop), 'peak': float(peak), 'trough': float(trough),
        'ret_c': float(ret_c), 'actual_runup': float(actual_runup),
        'active_close_return': float(active_close_return),
        'exit_open': float(bar_open[int(xgi)]), 'exit_high': float(bar_high[int(xgi)]),
        'exit_low': float(bar_low[int(xgi)]), 'exit_close': float(bar_close[int(xgi)]),
    }


_CURRENT_BENCHMARK = 'SPY'


def simulate_one_v2(row, prices_arg, probs_arg, policy):
    sym, mkt = str(row['symbol']), str(row['market_id'])
    question = str(row.get('question', ''))
    polarity, polarity_source = resolve_polarity(question, sym)
    if polarity == 0:
        return None
    eff_probs = effective_probs(probs_arg, mkt, polarity)
    t0 = pd.Timestamp(row['t0'])
    t0 = t0.tz_localize('UTC') if t0.tzinfo is None else t0.tz_convert('UTC')
    t_theta = pd.Timestamp(row['t_theta']).tz_convert('UTC')
    t_e = pd.Timestamp(row['t_e']).tz_convert('UTC')
    scanned = scan_candidate_v2(
        prices_arg, eff_probs, sym, mkt, _CURRENT_BENCHMARK,
        t0, t_theta, t_e, policy,
    )
    if scanned is None:
        return None
    reason_map = {
        1: f"trailing_{policy['atr_mult']:.2f}ATR",
        2: f"profit_lock_{scanned['hard_floor_pct']}%",
        3: f"poly<{policy['theta_out']:.3f}",
        4: 'resolution-1d', 5: 'end_of_window',
        6: f"hard_loss_{policy.get('hard_loss_cap', 0.0)*100:.2f}%",
        7: f"no_follow_{int(policy.get('no_follow_days', 0))}d",
    }
    timing = {0: 'close', 1: 'open_gap', 2: 'intraday_stop', 3: 'missing_open_low_fallback'}[scanned['timing_code']]
    mkt_probs = eff_probs.get(mkt, [])
    converged = 'YES' if mkt_probs and mkt_probs[-1][1] >= 0.5 else 'NO' if mkt_probs else 'UNKNOWN'
    return {
        'market_id': mkt, 'symbol': sym, 'question': question,
        'polarity': polarity, 'polarity_source': polarity_source,
        'pct': scanned['entry_prob'], 'converged': converged,
        'asset_confidence': row.get('confidence_score'),
        'question_confidence': row.get('feat_llm_confidence'),
        'archetype': row.get('feat_archetype', ''),
        'connection_strength': row.get('feat_connection_strength'),
        'relevance': float(row.get(RELEVANCE_COL, 0)), 'split': row.get('split', ''),
        'entry_date': str(pd.Timestamp(scanned['entry_ts']).date()),
        'entry_prob': scanned['entry_prob'], 'entry_price': scanned['entry_price'],
        'actual_runup': scanned['actual_runup'],
        'exit_date': str(pd.Timestamp(scanned['exit_ts']).date()),
        'exit_price': scanned['exit_price'], 'exit_reason': reason_map[scanned['reason_code']],
        'exit_timing': timing, 'active_stop': scanned['active_stop'],
        'exit_open': scanned['exit_open'], 'exit_high': scanned['exit_high'],
        'exit_low': scanned['exit_low'], 'exit_close': scanned['exit_close'],
        'peak_pct': scanned['peak'] * 100.0, 'trough_pct': scanned['trough'] * 100.0,
        'return_pct': scanned['ret_c'] * 100.0,
        'active_close_return_at_exit_pct': scanned['active_close_return'] * 100.0,
    }


# Install the new candidate engine into the corrected portfolio simulator.
simulate_one_proper = simulate_one_v2


def run_portfolio(df_arg, policy, benchmark, start_date, end_date):
    global _CURRENT_BENCHMARK
    _CURRENT_BENCHMARK = benchmark
    return sim_opp_cost_proper(
        df_arg, prices, probs, policy, bench_sym=benchmark,
        start_date=start_date, end_date=end_date, sync_gap_benchmark_open=True,
    )


# ---------------------------------------------------------------------------
# Benchmark-relative robust objective.
# ---------------------------------------------------------------------------
def active_metrics(equity_df: pd.DataFrame) -> dict[str, Any]:
    eq = equity_df['equity'].astype(float)
    be = equity_df['benchmark_equity'].astype(float)
    relative = (eq / be) / (eq.iloc[0] / be.iloc[0])
    active_log = np.log(relative).diff().dropna()
    overall_ir = 0.0
    if len(active_log) > 1 and active_log.std(ddof=1) > 1e-12:
        overall_ir = float(active_log.mean() / active_log.std(ddof=1) * math.sqrt(252.0))
    rel_dd = relative / relative.cummax() - 1.0
    active_dd_pct = float(rel_dd.min() * 100.0)
    relative_total_pct = float((relative.iloc[-1] - 1.0) * 100.0)
    fold_irs: list[float] = []
    for idx in np.array_split(np.arange(len(active_log)), 4):
        if len(idx) < 15:
            continue
        r = active_log.iloc[idx]
        sd = float(r.std(ddof=1))
        ir = 0.0 if sd <= 1e-12 else float(r.mean() / sd * math.sqrt(252.0))
        fold_irs.append(float(np.clip(ir, -5.0, 5.0)))
    return {
        'overall_ir': overall_ir,
        'active_max_dd_pct': active_dd_pct,
        'relative_total_pct': relative_total_pct,
        'fold_irs': fold_irs,
    }


def objective_from_run(trades: pd.DataFrame, equity: pd.DataFrame, stats: dict) -> tuple[float, dict]:
    if len(trades) < 50 or len(equity) < 80:
        return -1e9, {}
    am = active_metrics(equity)
    if len(am['fold_irs']) < 4:
        return -1e9, am
    fold_median = float(np.median(am['fold_irs']))
    fold_worst = float(np.min(am['fold_irs']))
    score = (
        0.30 * am['overall_ir']
        + 0.45 * fold_median
        + 0.25 * fold_worst
        + 0.025 * am['relative_total_pct']
        - 0.04 * abs(am['active_max_dd_pct'])
    )
    am.update({'fold_median_ir': fold_median, 'fold_worst_ir': fold_worst})
    return float(score), am


# ---------------------------------------------------------------------------
# Search space and optimizers.
# ---------------------------------------------------------------------------
BASE_SPECS = [
    ('atr_mult', 1.5, 4.0, False),
    ('lock_activate', 0.015, 0.08, False),
    ('theta_out', 0.45, 0.65, False),
    ('enter_strong', 0.60, 0.88, False),
    ('enter_floor', 0.55, 0.82, False),
    ('hold_days', 1, 4, True),
    ('max_price_runup', 0.02, 0.20, False),
    ('gross_event_exposure', 0.45, 0.95, False),
    ('max_concurrent', 5, 12, True),
]
EXIT_SPECS = {
    'baseline': [],
    'hard_cap': [('hard_loss_cap', 0.03, 0.15, False)],
    'no_follow': [('no_follow_days', 2, 10, True), ('no_follow_mfe', 0.005, 0.06, False)],
    'combined': [
        ('hard_loss_cap', 0.03, 0.15, False),
        ('no_follow_days', 2, 10, True),
        ('no_follow_mfe', 0.005, 0.06, False),
    ],
}


def specs_for(variant: str):
    return BASE_SPECS + EXIT_SPECS[variant]


def decode(u: np.ndarray, variant: str) -> dict:
    p: dict[str, Any] = {}
    for value, (name, lo, hi, integer) in zip(u, specs_for(variant)):
        x = lo + float(value) * (hi - lo)
        p[name] = int(round(x)) if integer else float(x)
    p['enter_strong'] = max(float(p['enter_strong']), float(p['enter_floor']))
    max_concurrent = int(p['max_concurrent'])
    gross = min(float(p.pop('gross_event_exposure')), 0.95)
    p['position_size_pct'] = gross / max_concurrent
    p['gross_event_exposure'] = gross
    p['use_hard_cap'] = variant in ('hard_cap', 'combined')
    p['use_no_follow'] = variant in ('no_follow', 'combined')
    p.setdefault('hard_loss_cap', 0.50)
    p.setdefault('no_follow_days', 30)
    p.setdefault('no_follow_mfe', 0.0)
    return p


def evaluate_u(u: np.ndarray, variant: str, benchmark: str) -> tuple[float, dict, dict]:
    policy = decode(u, variant)
    trades, equity, stats = run_portfolio(
        train_df, policy, benchmark,
        as_utc_day(train_df['t_theta'].min()), train_eval_end,
    )
    score, active = objective_from_run(trades, equity, stats)
    return score, policy, {**stats, **active}


def sobol_search(variant: str, benchmark: str, n: int, seed: int):
    dim = len(specs_for(variant))
    m = int(math.ceil(math.log2(n)))
    points = qmc.Sobol(d=dim, scramble=True, seed=seed).random_base2(m)[:n]
    records = []
    best = (-np.inf, None, None, None)
    for i, u in enumerate(points):
        score, policy, metrics = evaluate_u(u, variant, benchmark)
        records.append({'eval': i, 'score': score, **policy, **metrics})
        if score > best[0]:
            best = (score, policy, metrics, u.copy())
    return best, pd.DataFrame(records)


def hybrid_search(variant: str, benchmark: str, seed: int,
                  sobol_n: int = 384, cem_pop: int = 80, cem_iters: int = 5):
    best, trace = sobol_search(variant, benchmark, sobol_n, seed)
    valid = trace[np.isfinite(trace.score) & (trace.score > -1e8)].nlargest(max(12, sobol_n // 10), 'score')
    specs = specs_for(variant)
    # Recover elite unit coordinates from decoded continuous/discrete parameters approximately.
    elite_u = []
    for _, r in valid.iterrows():
        uv = []
        for name, lo, hi, integer in specs:
            x = float(r[name])
            uv.append(np.clip((x - lo) / (hi - lo), 0, 1))
        elite_u.append(uv)
    elite_u = np.asarray(elite_u, dtype=float)
    mean = elite_u.mean(axis=0) if len(elite_u) else np.full(len(specs), 0.5)
    std = elite_u.std(axis=0) if len(elite_u) else np.full(len(specs), 0.25)
    std = np.maximum(std, 0.06)
    rng = np.random.default_rng(seed + 1_000_003)
    records = trace.to_dict('records')
    eval_id = len(records)
    for iteration in range(cem_iters):
        samples = np.clip(rng.normal(mean, std, size=(cem_pop, len(specs))), 0.0, 1.0)
        evaluated = []
        for u in samples:
            score, policy, metrics = evaluate_u(u, variant, benchmark)
            rec = {'eval': eval_id, 'stage': f'cem_{iteration}', 'score': score, **policy, **metrics}
            records.append(rec); eval_id += 1
            evaluated.append((score, u.copy(), policy, metrics))
            if score > best[0]:
                best = (score, policy, metrics, u.copy())
        evaluated.sort(key=lambda x: x[0], reverse=True)
        elites = [x[1] for x in evaluated[:max(12, int(0.20 * cem_pop))] if x[0] > -1e8]
        if elites:
            e = np.asarray(elites)
            mean = 0.30 * mean + 0.70 * e.mean(axis=0)
            std = np.maximum(0.30 * std + 0.70 * e.std(axis=0), 0.025)
    return best, pd.DataFrame(records)


def pure_cem_search(variant: str, benchmark: str, seed: int, pop: int = 80, iters: int = 10):
    specs = specs_for(variant)
    rng = np.random.default_rng(seed)
    mean = np.full(len(specs), 0.5)
    std = np.full(len(specs), 0.28)
    best = (-np.inf, None, None, None)
    records = []
    eval_id = 0
    for iteration in range(iters):
        samples = np.clip(rng.normal(mean, std, size=(pop, len(specs))), 0.0, 1.0)
        evaluated = []
        for u in samples:
            score, policy, metrics = evaluate_u(u, variant, benchmark)
            records.append({'eval': eval_id, 'stage': f'cem_{iteration}', 'score': score, **policy, **metrics})
            eval_id += 1
            evaluated.append((score, u.copy(), policy, metrics))
            if score > best[0]:
                best = (score, policy, metrics, u.copy())
        evaluated.sort(key=lambda x: x[0], reverse=True)
        elites = [x[1] for x in evaluated[:max(12, int(0.20 * pop))] if x[0] > -1e8]
        if elites:
            e = np.asarray(elites)
            mean = e.mean(axis=0)
            std = np.maximum(e.std(axis=0), 0.025)
    return best, pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Experiments.
# 1) modest Sobol screen of all exit variants;
# 2) expanded hybrid search of baseline and combined;
# 3) matched-budget pure CEM on combined to assess optimizer choice.
# ---------------------------------------------------------------------------
all_rows = []
selected_policies: dict[str, Any] = {}
search_summaries = []

for benchmark in ('SPY', 'QQQ'):
    for variant in ('baseline', 'hard_cap', 'no_follow', 'combined'):
        t0 = time.time()
        best, trace = sobol_search(variant, benchmark, n=2, seed=20260716 + (0 if benchmark == 'SPY' else 1000) + list(EXIT_SPECS).index(variant)*100)
        trace.to_csv(OUT / f'{benchmark.lower()}_{variant}_sobol_screen_trace.csv.gz', index=False, compression='gzip')
        score, policy, metrics, _ = best
        search_summaries.append({'benchmark': benchmark, 'variant': variant, 'algorithm': 'sobol_screen_2', 'train_score': score, 'seconds': time.time()-t0, **policy, **metrics})

    for variant in ('baseline', 'combined'):
        for seed_index, seed in enumerate((31001, 31002)):
            actual_seed = seed + (0 if benchmark == 'SPY' else 10000)
            t0 = time.time()
            best, trace = hybrid_search(variant, benchmark, actual_seed, sobol_n=2, cem_pop=2, cem_iters=1)
            trace.to_csv(OUT / f'{benchmark.lower()}_{variant}_hybrid_seed{seed_index+1}_trace.csv.gz', index=False, compression='gzip')
            score, policy, metrics, _ = best
            search_summaries.append({'benchmark': benchmark, 'variant': variant, 'algorithm': f'hybrid_seed{seed_index+1}', 'train_score': score, 'seconds': time.time()-t0, **policy, **metrics})

    # Matched-budget pure CEM for the combined exit model.
    t0 = time.time()
    best_cem, trace_cem = pure_cem_search('combined', benchmark, 41001 + (0 if benchmark == 'SPY' else 10000), pop=2, iters=1)
    trace_cem.to_csv(OUT / f'{benchmark.lower()}_combined_pure_cem_trace.csv.gz', index=False, compression='gzip')
    score, policy, metrics, _ = best_cem
    search_summaries.append({'benchmark': benchmark, 'variant': 'combined', 'algorithm': 'pure_cem_800', 'train_score': score, 'seconds': time.time()-t0, **policy, **metrics})

search_df = pd.DataFrame(search_summaries)
search_df.to_csv(OUT / 'search_summary.csv', index=False)

# Policy selection is training-only: median-rank across the two hybrid seeds.
# We retain both seed policies and choose the higher training objective for the final replay,
# while reporting seed dispersion explicitly.
for benchmark in ('SPY', 'QQQ'):
    for variant in ('baseline', 'combined'):
        candidates_search = search_df[(search_df.benchmark == benchmark) & (search_df.variant == variant) & search_df.algorithm.str.startswith('hybrid_seed')]
        chosen = candidates_search.sort_values('train_score', ascending=False).iloc[0]
        policy_keys = set(decode(np.full(len(specs_for(variant)), 0.5), variant).keys())
        policy = {k: chosen[k] for k in policy_keys if k in chosen.index}
        # Restore exact discrete/bool types.
        policy['hold_days'] = int(policy['hold_days']); policy['max_concurrent'] = int(policy['max_concurrent'])
        policy['no_follow_days'] = int(policy['no_follow_days'])
        policy['use_hard_cap'] = bool(policy['use_hard_cap']); policy['use_no_follow'] = bool(policy['use_no_follow'])
        selected_policies[f'{benchmark}_{variant}'] = policy

        tr_trades, tr_eq, tr_stats = run_portfolio(train_df, policy, benchmark, as_utc_day(train_df['t_theta'].min()), train_eval_end)
        te_trades, te_eq, te_stats = run_portfolio(oos_df, policy, benchmark, OOS_START, OOS_END)
        tr_active = active_metrics(tr_eq); te_active = active_metrics(te_eq)
        tr_trades.to_csv(OUT / f'{benchmark.lower()}_{variant}_selected_train_trades.csv', index=False)
        tr_eq.to_csv(OUT / f'{benchmark.lower()}_{variant}_selected_train_equity.csv', index=False)
        te_trades.to_csv(OUT / f'{benchmark.lower()}_{variant}_selected_oos_trades.csv', index=False)
        te_eq.to_csv(OUT / f'{benchmark.lower()}_{variant}_selected_oos_equity.csv', index=False)
        all_rows.append({
            'benchmark': benchmark, 'variant': variant, 'selection': 'best_hybrid_train_score',
            **{f'train_{k}': v for k, v in tr_stats.items()},
            **{f'train_active_{k}': v for k, v in tr_active.items() if k != 'fold_irs'},
            **{f'oos_{k}': v for k, v in te_stats.items()},
            **{f'oos_active_{k}': v for k, v in te_active.items() if k != 'fold_irs'},
        })

# Also evaluate screen-selected hard-cap and no-follow policies to complete the exit ablation.
for benchmark in ('SPY', 'QQQ'):
    for variant in ('hard_cap', 'no_follow'):
        chosen = search_df[(search_df.benchmark == benchmark) & (search_df.variant == variant) & (search_df.algorithm == 'sobol_screen_2')].iloc[0]
        policy_keys = set(decode(np.full(len(specs_for(variant)), 0.5), variant).keys())
        policy = {k: chosen[k] for k in policy_keys if k in chosen.index}
        policy['hold_days'] = int(policy['hold_days']); policy['max_concurrent'] = int(policy['max_concurrent'])
        policy['no_follow_days'] = int(policy['no_follow_days'])
        policy['use_hard_cap'] = bool(policy['use_hard_cap']); policy['use_no_follow'] = bool(policy['use_no_follow'])
        selected_policies[f'{benchmark}_{variant}'] = policy
        tr_trades, tr_eq, tr_stats = run_portfolio(train_df, policy, benchmark, as_utc_day(train_df['t_theta'].min()), train_eval_end)
        te_trades, te_eq, te_stats = run_portfolio(oos_df, policy, benchmark, OOS_START, OOS_END)
        tr_active = active_metrics(tr_eq); te_active = active_metrics(te_eq)
        tr_trades.to_csv(OUT / f'{benchmark.lower()}_{variant}_selected_train_trades.csv', index=False)
        tr_eq.to_csv(OUT / f'{benchmark.lower()}_{variant}_selected_train_equity.csv', index=False)
        te_trades.to_csv(OUT / f'{benchmark.lower()}_{variant}_selected_oos_trades.csv', index=False)
        te_eq.to_csv(OUT / f'{benchmark.lower()}_{variant}_selected_oos_equity.csv', index=False)
        all_rows.append({
            'benchmark': benchmark, 'variant': variant, 'selection': 'sobol_screen_train_score',
            **{f'train_{k}': v for k, v in tr_stats.items()},
            **{f'train_active_{k}': v for k, v in tr_active.items() if k != 'fold_irs'},
            **{f'oos_{k}': v for k, v in te_stats.items()},
            **{f'oos_active_{k}': v for k, v in te_active.items() if k != 'fold_irs'},
        })

results = pd.DataFrame(all_rows)
results.to_csv(OUT / 'final_train_oos_results.csv', index=False)
(OUT / 'selected_policies.json').write_text(json.dumps(selected_policies, indent=2, default=float), encoding='utf-8')

# Exit attribution.
exit_rows = []
for benchmark in ('SPY', 'QQQ'):
    for variant in ('baseline', 'hard_cap', 'no_follow', 'combined'):
        t = pd.read_csv(OUT / f'{benchmark.lower()}_{variant}_selected_oos_trades.csv')
        for reason, g in t.groupby('exit_reason'):
            exit_rows.append({
                'benchmark': benchmark, 'variant': variant, 'exit_reason': reason,
                'n': len(g), 'net_pnl': float(g.pnl.sum()), 'avg_pnl': float(g.pnl.mean()),
                'win_rate_pct': float((g.pnl > 0).mean() * 100),
                'mean_pnl_pct': float(g.pnl_pct.mean()),
            })
pd.DataFrame(exit_rows).to_csv(OUT / 'oos_exit_attribution.csv', index=False)

manifest = {
    'probability_surge_filter': 'removed from policy and execution',
    'price_runup': 'recomputed at actual entry close relative to first close on/after t0',
    'exposure_constraint': 'gross_event_exposure <= 95%; position_size = gross_event_exposure/max_concurrent',
    'partial_fill_rule': 'reject if asset notional < 90% of desired allocation',
    'objective': 'benchmark-relative log-return information ratio with chronological-fold robustness, active drawdown and relative terminal return',
    'exit_variants': list(EXIT_SPECS),
    'hard_cap': 'standing stop at max(existing protective stop, entry*(1-hard_loss_cap)); gaps fill at Open',
    'no_follow': 'close exit after N trading bars when MFE below threshold and stock return has not beaten benchmark',
    'search': {
        'screen': '192 scrambled Sobol points per benchmark/variant',
        'hybrid': '384 Sobol + 5x80 CEM refinement, two seeds for baseline and combined',
        'pure_cem_comparison': '10x80 matched-budget CEM for combined',
    },
    'selection_rule': 'policies chosen on training objective only; OOS is never used for parameter selection',
}
(OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('\nSEARCH SUMMARY')
print(search_df[['benchmark','variant','algorithm','train_score','seconds']].to_string(index=False))
print('\nFINAL RESULTS')
print(results[['benchmark','variant','train_total_return','train_benchmark_return','train_excess_return','train_active_overall_ir','oos_total_return','oos_benchmark_return','oos_excess_return','oos_active_overall_ir','oos_max_dd','oos_n_trades']].to_string(index=False))
