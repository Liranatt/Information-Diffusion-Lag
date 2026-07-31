from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('/mnt/data')
OUT=ROOT/'trade_opportunity_research'
OUT.mkdir(exist_ok=True)

# Load only the local corrected-engine definitions; do not run its experiment block.
src=(ROOT/'benchmark_exit_search.py').read_text(encoding='utf-8')
src=src.split('# ---------------------------------------------------------------------------\n# Experiments.')[0]
src=src.replace("OUT = ROOT / 'benchmark_exit_search'", "OUT = ROOT / 'trade_opportunity_research'")
src=src.replace("source = source.replace('@njit(cache=True)', '@njit(cache=False)')", "__KEEP_NESTED_NUMBA_REPLACE__")
src=src.replace('@njit(cache=True)', '@njit(cache=False)')
src=src.replace('__KEEP_NESTED_NUMBA_REPLACE__', "source = source.replace('@njit(cache=True)', '@njit(cache=False)')")
exec(compile(src,'trade_opportunity_prefix.py','exec'),globals())

policies=json.load(open(ROOT/'local_consensus_validation'/'consensus_policies.json'))

FEATURE_COLS=[
    'event_id','market_id','symbol','question','t0','t_theta','t_e','feat_archetype','feat_sector',
    'feat_prob_at_trigger','feat_prob_slope_24h','feat_prob_volatility','feat_prob_surge_since_t0',
    'feat_time_to_resolution_days','feat_crossing_latency_days','feat_pre_entry_volume_log',
    'feat_runup_since_t0','feat_asset_2w_trend','feat_sector_1m_trend','feat_spy_2w_trend',
    'feat_ytd_change','feat_debt_to_equity','feat_cash_to_marketcap','feat_beta','feat_profit_margin',
    'feat_log_market_cap','feat_connection_strength','feat_world_size','feat_runup_rank','feat_size_rank',
    'expected_return_pct','confidence_score','feat_llm_expected_return','feat_llm_confidence',
    'economic_event_id','economic_event_group_clean','split'
]

def candidate_outcomes(df, policy, benchmark, forced_te1=False):
    global _CURRENT_BENCHMARK
    _CURRENT_BENCHMARK=benchmark
    p=dict(policy)
    if forced_te1:
        p.update(
            atr_mult=1_000_000.0,
            lock_activate=1_000_000.0,
            theta_out=-1_000_000.0,
            use_hard_cap=False,
            use_no_follow=False,
            hard_loss_cap=0.99,
            no_follow_days=9999,
            no_follow_mfe=-999.0,
        )
    records=[]
    for source_order,(_,row) in enumerate(df.sort_values(['t_theta','market_id','symbol'],kind='mergesort').iterrows(),start=1):
        trade=simulate_one_v2(row,prices,probs,p)
        if trade is None:
            continue
        rec={c:row.get(c,np.nan) for c in FEATURE_COLS if c in row.index}
        rec.update(trade)
        rec['source_order']=source_order
        rec['forced_te1']=forced_te1
        records.append(rec)
    return pd.DataFrame(records)

def key_cols(df):
    return ['market_id','symbol','entry_date']

manifest={'source':'uploaded local files only','policy':'consensus hard-cap selected on train','universes':{}}
for benchmark in ['SPY','QQQ']:
    policy=policies[f'{benchmark}_hard_cap']
    # Portfolio-selected trades under the final consensus hard-cap policy.
    for split,df,start,end in [
        ('train',train_df,as_utc_day(train_df['t_theta'].min()),train_eval_end),
        ('test',oos_df,OOS_START,OOS_END),
    ]:
        selected,equity,stats=run_portfolio(df,policy,benchmark,start,end)
        selected.to_csv(OUT/f'{benchmark.lower()}_{split}_selected_trades.csv',index=False)
        equity.to_csv(OUT/f'{benchmark.lower()}_{split}_selected_equity.csv',index=False)
        hard=candidate_outcomes(df,policy,benchmark,forced_te1=False)
        te1=candidate_outcomes(df,policy,benchmark,forced_te1=True)
        hard.to_csv(OUT/f'{benchmark.lower()}_{split}_all_eligible_hardcap_outcomes.csv',index=False)
        te1.to_csv(OUT/f'{benchmark.lower()}_{split}_all_eligible_te1_outcomes.csv',index=False)
        sk=set(map(tuple,selected[key_cols(selected)].astype(str).to_numpy()))
        for frame,name in [(hard,'hardcap'),(te1,'te1')]:
            frame['selected_by_portfolio']=frame[key_cols(frame)].astype(str).apply(tuple,axis=1).isin(sk)
            frame.to_csv(OUT/f'{benchmark.lower()}_{split}_all_eligible_{name}_outcomes.csv',index=False)
        manifest['universes'][f'{benchmark}_{split}']={
            'input_candidates':int(len(df)),
            'eligible_hardcap':int(len(hard)),
            'eligible_te1':int(len(te1)),
            'selected':int(len(selected)),
            'portfolio_stats':stats,
            'policy':policy,
        }
        print(benchmark,split,'input',len(df),'eligible',len(te1),'selected',len(selected),flush=True)

(OUT/'universe_manifest.json').write_text(json.dumps(manifest,indent=2,default=str),encoding='utf-8')
