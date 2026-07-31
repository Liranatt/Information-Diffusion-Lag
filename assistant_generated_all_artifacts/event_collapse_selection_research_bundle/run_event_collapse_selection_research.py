from __future__ import annotations

import json
import math
import random
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import lil_matrix, csr_matrix

ROOT = Path('/mnt/data')
SRC_OUT = ROOT / 'trade_opportunity_research'
OUT = ROOT / 'event_collapse_selection_research'
OUT.mkdir(exist_ok=True)

# Load only the local corrected execution definitions. No repository access.
src = (ROOT / 'benchmark_exit_search.py').read_text(encoding='utf-8')
src = src.split('# ---------------------------------------------------------------------------\n# Experiments.')[0]
src = src.replace("OUT = ROOT / 'benchmark_exit_search'", "OUT = ROOT / 'event_collapse_selection_research'")
src = src.replace("source = source.replace('@njit(cache=True)', '@njit(cache=False)')", "__KEEP_NESTED_NUMBA_REPLACE__")
src = src.replace('@njit(cache=True)', '@njit(cache=False)')
src = src.replace('__KEEP_NESTED_NUMBA_REPLACE__', "source = source.replace('@njit(cache=True)', '@njit(cache=False)')")
needle = "source = source.replace('@njit(cache=True)', '@njit(cache=False)')"
insert = needle + "\nsource = source.replace(\"all_trades.sort(key=lambda t: t['_entry_ts'])\", \"all_trades.sort(key=lambda t: (t['_entry_ts'], -float(t.get('rank_score', 0.0)), int(t.get('source_order_rank', 0))))\")"
if needle not in src:
    raise RuntimeError('Could not install rank-aware local simulator')
src = src.replace(needle, insert, 1)
exec(compile(src, 'event_collapse_local_prefix.py', 'exec'), globals())

policies = json.load(open(ROOT / 'local_consensus_validation' / 'consensus_policies.json', encoding='utf-8'))
master = pd.read_csv(SRC_OUT / 'trade_opportunity_master.csv')


def norm_ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True).dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def add_candidate_key(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.copy()
    x['_t_theta_key'] = norm_ts(x['t_theta'])
    x['_candidate_key'] = (
        x['market_id'].astype(str) + '|' + x['symbol'].astype(str).str.upper() + '|' + x['_t_theta_key']
    )
    return x

train_df = add_candidate_key(train_df)
oos_df = add_candidate_key(oos_df)
master = add_candidate_key(master)

# Attach rank and event metadata to generated trades.
_orig_sim = simulate_one_v2
CURRENT_RANKER = 'current'


def ranked_sim(row, prices_arg, probs_arg, policy):
    t = _orig_sim(row, prices_arg, probs_arg, policy)
    if t is None:
        return None
    conn = float(row.get('feat_connection_strength', 0.0) or 0.0)
    source_order = int(row.get('_source_order_local', 0) or 0)
    if CURRENT_RANKER == 'connection':
        score = conn
    else:
        score = 0.0
    t['rank_score'] = score
    t['source_order_rank'] = source_order
    t['economic_event_id'] = str(row.get('economic_event_id', ''))
    t['economic_event_group_clean'] = str(row.get('economic_event_group_clean', ''))
    t['_candidate_key'] = str(row.get('_candidate_key', ''))
    t['feat_connection_strength'] = conn
    return t

simulate_one_proper = ranked_sim


def prepare_raw_order(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.sort_values(['t_theta', 'market_id', 'symbol'], kind='mergesort').copy()
    x['_source_order_local'] = np.arange(1, len(x) + 1)
    return x

train_df = prepare_raw_order(train_df)
oos_df = prepare_raw_order(oos_df)


def load_eligible(benchmark: str, split: str) -> pd.DataFrame:
    p = SRC_OUT / f'{benchmark.lower()}_{split}_all_eligible_te1_outcomes.csv'
    d = pd.read_csv(p)
    d = add_candidate_key(d)
    d['entry_date'] = pd.to_datetime(d['entry_date']).dt.normalize()
    d['exit_date'] = pd.to_datetime(d['exit_date']).dt.normalize()
    d['symbol'] = d['symbol'].astype(str).str.upper()
    d['economic_event_id'] = d['economic_event_id'].astype(str)
    d['feat_connection_strength'] = pd.to_numeric(d['feat_connection_strength'], errors='coerce').fillna(0.0)
    d['source_order'] = pd.to_numeric(d['source_order'], errors='coerce').fillna(10**9).astype(int)

    # Use the integrated master for net and benchmark-relative fixed-horizon labels.
    mm = master[(master['benchmark'] == benchmark) & (master['analysis_split'] == split)].copy()
    label_cols = [
        '_candidate_key', 'entry_date', 'stock_te1_net_return_pct',
        'te1_active_vs_spy_net_twin_pct', 'te1_active_vs_qqq_net_twin_pct',
        'event_family', 'feat_sector', 'hardcap_return_pct',
        'hardcap_active_vs_benchmark_gross_pct'
    ]
    mm['entry_date'] = pd.to_datetime(mm['entry_date']).dt.normalize()
    mm = mm[label_cols].drop_duplicates(['_candidate_key', 'entry_date'])
    d = d.merge(mm, on=['_candidate_key', 'entry_date'], how='left', suffixes=('', '_m'))
    active_col = 'te1_active_vs_spy_net_twin_pct' if benchmark == 'SPY' else 'te1_active_vs_qqq_net_twin_pct'
    d['label_net_pct'] = pd.to_numeric(d['stock_te1_net_return_pct'], errors='coerce')
    d['label_active_pct'] = pd.to_numeric(d[active_col], errors='coerce')
    # Fallback to gross fixed-horizon return only if the net twin field is absent.
    d['label_net_pct'] = d['label_net_pct'].fillna(pd.to_numeric(d['return_pct'], errors='coerce'))
    d['label_active_pct'] = d['label_active_pct'].fillna(d['label_net_pct'])
    return d


def choose_rep(group: pd.DataFrame, representative: str) -> pd.Series:
    if representative == 'connection':
        return group.sort_values(['feat_connection_strength', 'source_order'], ascending=[False, True], kind='mergesort').iloc[0]
    return group.sort_values(['source_order'], ascending=[True], kind='mergesort').iloc[0]


def collapse_opportunities(d: pd.DataFrame, mode: str, representative: str = 'first') -> pd.DataFrame:
    x = d.copy().sort_values(['entry_date', 'source_order'], kind='mergesort')
    if mode == 'raw':
        return x.reset_index(drop=True)
    if mode == 'event_symbol_day':
        keys = ['economic_event_id', 'symbol', 'entry_date']
        rows = [choose_rep(g, representative) for _, g in x.groupby(keys, sort=False, dropna=False)]
        return pd.DataFrame(rows).sort_values(['entry_date', 'source_order'], kind='mergesort').reset_index(drop=True)
    if mode == 'symbol_day':
        keys = ['symbol', 'entry_date']
        rows = [choose_rep(g, representative) for _, g in x.groupby(keys, sort=False, dropna=False)]
        return pd.DataFrame(rows).sort_values(['entry_date', 'source_order'], kind='mergesort').reset_index(drop=True)
    if mode == 'event_symbol_first':
        keys = ['economic_event_id', 'symbol']
        rows = [choose_rep(g, representative) for _, g in x.groupby(keys, sort=False, dropna=False)]
        return pd.DataFrame(rows).sort_values(['entry_date', 'source_order'], kind='mergesort').reset_index(drop=True)
    if mode == 'event_symbol_episode':
        kept = []
        for _, g in x.groupby(['economic_event_id', 'symbol'], sort=False, dropna=False):
            g = g.sort_values(['entry_date', 'source_order'], kind='mergesort')
            active_until = pd.Timestamp.min
            for _, row in g.iterrows():
                if row['entry_date'] >= active_until:
                    kept.append(row)
                    active_until = row['exit_date']
                elif representative == 'connection':
                    # For overlapping entries, retain the stronger candidate only if it entered the same day.
                    # Cross-day replacement would change timing and is intentionally not performed.
                    pass
        return pd.DataFrame(kept).sort_values(['entry_date', 'source_order'], kind='mergesort').reset_index(drop=True)
    raise ValueError(mode)


def subset_candidate_frame(raw_df: pd.DataFrame, collapsed: pd.DataFrame) -> pd.DataFrame:
    keys = set(collapsed['_candidate_key'].astype(str))
    return raw_df[raw_df['_candidate_key'].astype(str).isin(keys)].copy()


# Fixed-horizon selection simulator. This isolates allocator quality from execution/exit logic.
def select_fixed_horizon(
    opportunities: pd.DataFrame,
    max_concurrent: int,
    selector: str,
    rng: np.random.Generator | None = None,
    max_event_positions: int | None = None,
) -> pd.DataFrame:
    x = opportunities.copy().sort_values(['entry_date', 'source_order'], kind='mergesort')
    by_day = {d: g.copy() for d, g in x.groupby('entry_date', sort=True)}
    active: list[pd.Series] = []
    selected: list[pd.Series] = []
    for day in sorted(by_day):
        active = [r for r in active if r['exit_date'] > day]
        candidates = by_day[day].copy()
        if selector == 'connection':
            candidates = candidates.sort_values(['feat_connection_strength', 'source_order'], ascending=[False, True], kind='mergesort')
        elif selector == 'random':
            if rng is None:
                raise ValueError('rng required for random selector')
            candidates = candidates.iloc[rng.permutation(len(candidates))]
        else:
            candidates = candidates.sort_values(['source_order'], kind='mergesort')

        open_symbols = {str(r['symbol']) for r in active}
        event_counts = defaultdict(int)
        for r in active:
            event_counts[str(r['economic_event_id'])] += 1
        slots = max(0, max_concurrent - len(active))
        if slots == 0:
            continue
        for _, row in candidates.iterrows():
            if slots <= 0:
                break
            sym = str(row['symbol'])
            event = str(row['economic_event_id'])
            if sym in open_symbols:
                continue
            if max_event_positions is not None and event_counts[event] >= max_event_positions:
                continue
            selected.append(row)
            active.append(row)
            open_symbols.add(sym)
            event_counts[event] += 1
            slots -= 1
    if not selected:
        return x.iloc[0:0].copy()
    return pd.DataFrame(selected).reset_index(drop=True)


def daily_selection_metrics(opps: pd.DataFrame, selected: pd.DataFrame, label: str) -> dict[str, float]:
    sel_keys = set(selected['_candidate_key'].astype(str))
    daily = []
    for day, g in opps.groupby('entry_date', sort=True):
        s = g[g['_candidate_key'].astype(str).isin(sel_keys)]
        k = len(s)
        if k == 0:
            continue
        # Same-day diagnostic: enforce at most one candidate per symbol.
        oracle_candidates = (
            g.sort_values(label, ascending=False, kind='mergesort')
             .drop_duplicates('symbol', keep='first')
             .head(k)
        )
        sv = float(s[label].sum())
        ov = float(oracle_candidates[label].sum())
        spos = float(s[label].clip(lower=0).sum())
        opos = float(oracle_candidates[label].clip(lower=0).sum())
        sneg = float((-s[label].clip(upper=0)).sum())
        oneg = float((-oracle_candidates[label].clip(upper=0)).sum())
        daily.append({
            'selected': sv, 'oracle': ov, 'regret': ov - sv,
            'selected_positive': spos, 'oracle_positive': opos,
            'selected_downside': sneg, 'oracle_downside': oneg,
            'avoidable_downside': sneg - oneg,
        })
    if not daily:
        return {}
    dd = pd.DataFrame(daily)
    return {
        'same_day_selected_sum_pct': dd['selected'].sum(),
        'same_day_oracle_sum_pct': dd['oracle'].sum(),
        'same_day_upside_regret_pct': dd['regret'].sum(),
        'same_day_avoidable_downside_pct': dd['avoidable_downside'].sum(),
        'same_day_winner_capture': dd['selected_positive'].sum() / max(dd['oracle_positive'].sum(), 1e-12),
        'same_day_regret_p95_pct': float(dd['regret'].quantile(0.95)),
        'same_day_regret_max_pct': float(dd['regret'].max()),
        'same_day_days_evaluated': int(len(dd)),
    }


def build_milp_constraints(opps: pd.DataFrame, max_concurrent: int, entry_counts: dict[pd.Timestamp, int] | None = None,
                           max_event_positions: int | None = None):
    n = len(opps)
    dates = sorted(set(opps['entry_date']).union(set(opps['exit_date'])))
    rows = []
    lbs = []
    ubs = []

    # Portfolio capacity on every state-change date. Exit day is free before new entries.
    for day in dates:
        idx = np.where((opps['entry_date'].to_numpy() <= day) & (opps['exit_date'].to_numpy() > day))[0]
        if len(idx):
            rows.append(idx)
            lbs.append(-np.inf)
            ubs.append(max_concurrent)

    # One active position per symbol.
    for sym, sg in opps.groupby('symbol', sort=False):
        sidx_all = sg.index.to_numpy()
        for day in dates:
            mask = (opps.loc[sidx_all, 'entry_date'].to_numpy() <= day) & (opps.loc[sidx_all, 'exit_date'].to_numpy() > day)
            idx = sidx_all[mask]
            if len(idx) > 1:
                rows.append(idx)
                lbs.append(-np.inf)
                ubs.append(1)

    if max_event_positions is not None:
        for event, eg in opps.groupby('economic_event_id', sort=False):
            eidx_all = eg.index.to_numpy()
            for day in dates:
                mask = (opps.loc[eidx_all, 'entry_date'].to_numpy() <= day) & (opps.loc[eidx_all, 'exit_date'].to_numpy() > day)
                idx = eidx_all[mask]
                if len(idx) > max_event_positions:
                    rows.append(idx)
                    lbs.append(-np.inf)
                    ubs.append(max_event_positions)

    if entry_counts is not None:
        for day, count in entry_counts.items():
            idx = np.where(opps['entry_date'].to_numpy() == day)[0]
            if len(idx):
                rows.append(idx)
                lbs.append(count)
                ubs.append(count)

    A = lil_matrix((len(rows), n), dtype=float)
    for r, idx in enumerate(rows):
        A[r, idx] = 1.0
    return LinearConstraint(csr_matrix(A), np.asarray(lbs), np.asarray(ubs))


def oracle_select(opps: pd.DataFrame, label: str, max_concurrent: int, entry_counts: dict[pd.Timestamp, int] | None = None,
                  max_event_positions: int | None = None) -> pd.DataFrame:
    x = opps.reset_index(drop=True).copy()
    c = -pd.to_numeric(x[label], errors='coerce').fillna(-1e6).to_numpy(dtype=float)
    constraints = build_milp_constraints(x, max_concurrent, entry_counts, max_event_positions)
    res = milp(c=c, integrality=np.ones(len(x)), bounds=Bounds(np.zeros(len(x)), np.ones(len(x))),
               constraints=constraints, options={'time_limit': 90.0, 'mip_rel_gap': 1e-8})
    if res.x is None:
        return x.iloc[0:0]
    return x[np.asarray(res.x) > 0.5].copy()


def aggregate_selection_metrics(opps: pd.DataFrame, selected: pd.DataFrame, label: str, max_concurrent: int,
                                random_n: int, seed: int, max_event_positions: int | None = None) -> tuple[dict[str, Any], pd.DataFrame]:
    selected = selected.copy()
    counts = selected.groupby('entry_date').size().to_dict()
    oracle_same_count = oracle_select(opps, label, max_concurrent, counts, max_event_positions)
    oracle_free = oracle_select(opps, label, max_concurrent, None, max_event_positions)
    selected_value = float(selected[label].sum())
    oracle_value = float(oracle_same_count[label].sum())
    free_value = float(oracle_free[label].sum())

    rng = np.random.default_rng(seed)
    random_values = np.empty(random_n, dtype=float)
    random_ntrades = np.empty(random_n, dtype=int)
    for j in range(random_n):
        rs = select_fixed_horizon(opps, max_concurrent, 'random', rng=rng, max_event_positions=max_event_positions)
        random_values[j] = float(rs[label].sum())
        random_ntrades[j] = len(rs)
    random_median = float(np.median(random_values))
    denom = oracle_value - random_median
    nse = (selected_value - random_median) / denom if abs(denom) > 1e-12 else np.nan
    percentile = float((random_values <= selected_value).mean() * 100.0)

    selected_pos = float(selected[label].clip(lower=0).sum())
    oracle_pos = float(oracle_same_count[label].clip(lower=0).sum())
    selected_down = float((-selected[label].clip(upper=0)).sum())
    oracle_down = float((-oracle_same_count[label].clip(upper=0)).sum())
    metrics = {
        'n_opportunities': int(len(opps)),
        'n_selected': int(len(selected)),
        'selected_sum_pct': selected_value,
        'same_count_oracle_sum_pct': oracle_value,
        'free_skip_oracle_sum_pct': free_value,
        'full_horizon_upside_regret_pct': oracle_value - selected_value,
        'full_horizon_avoidable_downside_pct': selected_down - oracle_down,
        'full_horizon_winner_capture': selected_pos / max(oracle_pos, 1e-12),
        'random_median_sum_pct': random_median,
        'random_mean_sum_pct': float(np.mean(random_values)),
        'random_p05_sum_pct': float(np.quantile(random_values, 0.05)),
        'random_p95_sum_pct': float(np.quantile(random_values, 0.95)),
        'random_percentile': percentile,
        'normalized_selection_efficiency': nse,
        'random_median_n_trades': float(np.median(random_ntrades)),
    }
    metrics.update(daily_selection_metrics(opps, selected, label))
    random_df = pd.DataFrame({'random_sum_pct': random_values, 'n_trades': random_ntrades})
    return metrics, random_df


collapse_modes = ['raw', 'event_symbol_day', 'event_symbol_episode', 'event_symbol_first', 'symbol_day']
representatives = ['first', 'connection']
portfolio_rows = []
selection_rows = []
collapse_diag_rows = []
selected_detail_frames = []
random_frames = []

for benchmark in ['SPY', 'QQQ']:
    policy = policies[f'{benchmark}_hard_cap']
    max_concurrent = int(policy['max_concurrent'])
    for split, raw_df, start, end in [
        ('train', train_df, as_utc_day(train_df['t_theta'].min()), train_eval_end),
        ('test', oos_df, OOS_START, OOS_END),
    ]:
        eligible = load_eligible(benchmark, split)
        for mode in collapse_modes:
            reps = representatives if mode != 'raw' else ['first']
            for representative in reps:
                collapsed = collapse_opportunities(eligible, mode, representative)
                collapse_diag_rows.append({
                    'benchmark': benchmark, 'split': split, 'collapse_mode': mode,
                    'representative': representative, 'raw_rows': len(eligible),
                    'collapsed_rows': len(collapsed), 'rows_removed': len(eligible) - len(collapsed),
                    'pct_removed': (len(eligible) - len(collapsed)) / max(len(eligible), 1) * 100.0,
                    'unique_events': collapsed['economic_event_id'].nunique(),
                    'unique_symbols': collapsed['symbol'].nunique(),
                    'unique_entry_days': collapsed['entry_date'].nunique(),
                })

                # Exact corrected-engine replay under fixed policy; only candidate representation/ranking changes.
                subset = subset_candidate_frame(raw_df, collapsed)
                for ranker in ['current', 'connection']:
                    CURRENT_RANKER = ranker
                    clear_proper_caches(); _OPEN_CACHE.clear()
                    trades, equity, stats = run_portfolio(subset, policy, benchmark, start, end)
                    score, active = objective_from_run(trades, equity, stats)
                    row = {
                        'benchmark': benchmark, 'split': split, 'collapse_mode': mode,
                        'representative': representative, 'ranker': ranker,
                        'candidate_rows': len(subset), 'eligible_opportunities': len(collapsed),
                        'objective_score': score, **stats,
                        **{k: v for k, v in active.items() if k != 'fold_irs'},
                    }
                    portfolio_rows.append(row)
                    stem = f'{benchmark.lower()}_{split}_{mode}_{representative}_{ranker}'
                    trades.to_csv(OUT / f'{stem}_portfolio_trades.csv', index=False)
                    equity.to_csv(OUT / f'{stem}_portfolio_equity.csv', index=False)

                # Selection-efficiency research uses the existing fixed T_e-1 labels.
                for selector in ['current', 'connection']:
                    selected = select_fixed_horizon(collapsed, max_concurrent, selector)
                    selected = selected.copy()
                    selected['benchmark'] = benchmark
                    selected['analysis_split'] = split
                    selected['collapse_mode'] = mode
                    selected['representative'] = representative
                    selected['selector'] = selector
                    selected_detail_frames.append(selected)
                    for label in ['label_net_pct', 'label_active_pct']:
                        metrics, rnd = aggregate_selection_metrics(
                            collapsed, selected, label, max_concurrent,
                            random_n=1000, seed=42 + (0 if benchmark == 'SPY' else 10000) + (0 if split == 'train' else 1000)
                            + collapse_modes.index(mode) * 100 + (0 if selector == 'current' else 10),
                        )
                        selection_rows.append({
                            'benchmark': benchmark, 'split': split, 'collapse_mode': mode,
                            'representative': representative, 'selector': selector,
                            'label': label, **metrics,
                        })
                        rnd['benchmark'] = benchmark
                        rnd['split'] = split
                        rnd['collapse_mode'] = mode
                        rnd['representative'] = representative
                        rnd['selector'] = selector
                        rnd['label'] = label
                        random_frames.append(rnd)
                print(benchmark, split, mode, representative, 'done', flush=True)

pd.DataFrame(collapse_diag_rows).to_csv(OUT / 'collapse_diagnostics.csv', index=False)
pd.DataFrame(portfolio_rows).to_csv(OUT / 'collapse_portfolio_replay_results.csv', index=False)
pd.DataFrame(selection_rows).to_csv(OUT / 'selection_efficiency_results.csv', index=False)
pd.concat(selected_detail_frames, ignore_index=True).to_csv(OUT / 'selection_selected_opportunities.csv', index=False)
pd.concat(random_frames, ignore_index=True).to_csv(OUT / 'random_allocator_distributions.csv.gz', index=False, compression='gzip')

# Compact best-comparison table: primary representative=first.
sel = pd.DataFrame(selection_rows)
port = pd.DataFrame(portfolio_rows)
primary_sel = sel[(sel['representative'] == 'first') & (sel['label'] == 'label_active_pct')].copy()
primary_port = port[port['representative'] == 'first'].copy()
primary_sel.to_csv(OUT / 'primary_selection_efficiency_active.csv', index=False)
primary_port.to_csv(OUT / 'primary_collapse_portfolio_results.csv', index=False)

# Event clusters most affected by collapse.
cluster_rows = []
for benchmark in ['SPY', 'QQQ']:
    for split in ['train', 'test']:
        e = load_eligible(benchmark, split)
        g = e.groupby(['economic_event_id', 'economic_event_group_clean'], dropna=False).agg(
            candidate_rows=('market_id', 'size'), markets=('market_id', 'nunique'), symbols=('symbol', 'nunique'),
            entry_days=('entry_date', 'nunique'), mean_te1_net_pct=('label_net_pct', 'mean'),
            mean_te1_active_pct=('label_active_pct', 'mean'), min_te1_net_pct=('label_net_pct', 'min'),
            max_te1_net_pct=('label_net_pct', 'max')
        ).reset_index()
        g['benchmark'] = benchmark; g['split'] = split
        cluster_rows.append(g)
pd.concat(cluster_rows, ignore_index=True).sort_values('candidate_rows', ascending=False).to_csv(
    OUT / 'event_cluster_diagnostics.csv', index=False
)

# Build a concise machine-readable summary.
summary = {
    'scope': 'Uploaded/local files only; no repository access or modification.',
    'fixed_components': ['eligibility policy', 'hard-cap exit policy for exact replay', 'position sizing', 'max concurrent', 'benchmark'],
    'collapse_modes': collapse_modes,
    'selection_labels': {
        'label_net_pct': 'existing net stock return through T_e-1',
        'label_active_pct': 'existing net stock return minus benchmark twin through T_e-1',
    },
    'random_allocators_per_cell': 1000,
}
(OUT / 'method_manifest.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')

# Zip outputs.
shutil.make_archive(str(ROOT / 'event_collapse_selection_research_bundle'), 'zip', OUT)
print('COMPLETE', OUT, flush=True)
