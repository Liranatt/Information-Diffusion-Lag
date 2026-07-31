from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path('/mnt/data'); OUT=ROOT/'trade_opportunity_research'; OUT.mkdir(exist_ok=True)

src=(ROOT/'benchmark_exit_search.py').read_text(encoding='utf-8')
src=src.split('# ---------------------------------------------------------------------------\n# Experiments.')[0]
src=src.replace("OUT = ROOT / 'benchmark_exit_search'", "OUT = ROOT / 'trade_opportunity_research'")
src=src.replace("source = source.replace('@njit(cache=True)', '@njit(cache=False)')", "__KEEP_NESTED_NUMBA_REPLACE__")
src=src.replace('@njit(cache=True)', '@njit(cache=False)')
src=src.replace('__KEEP_NESTED_NUMBA_REPLACE__', "source = source.replace('@njit(cache=True)', '@njit(cache=False)')")
needle="source = source.replace('@njit(cache=True)', '@njit(cache=False)')"
insert=needle+"\nsource = source.replace(\"all_trades.sort(key=lambda t: t['_entry_ts'])\", \"all_trades.sort(key=lambda t: (t['_entry_ts'], -float(t.get('rank_score', 0.0))))\")"
src=src.replace(needle,insert,1)
exec(compile(src,'family_exit_prefix.py','exec'),globals())
policies=json.load(open(ROOT/'local_consensus_validation'/'consensus_policies.json'))

def family(row):
    text=(str(row.get('question',''))+' '+str(row.get('feat_archetype',''))).lower()
    if any(w in text for w in ['earnings','revenue','eps','ebitda','quarterly']): return 'earnings'
    if any(w in text for w in ['iran','israel','gaza','hezbollah','hamas','ukraine','russia','china','taiwan','strike','war','ceasefire','military','nato','invasion','gulf state','kuwait','saudi','oman','uae','geopolitical','diplomatic meeting']): return 'geo'
    return 'other'

def te1_policy(p):
    q=dict(p)
    q.update(atr_mult=1_000_000.0,lock_activate=1_000_000.0,theta_out=-1_000_000.0,
             use_hard_cap=False,use_no_follow=False,hard_loss_cap=0.99,no_follow_days=9999,no_follow_mfe=-999.0)
    return q

def relaxed_geo_policy(p):
    q=dict(p)
    q.update(atr_mult=6.0,lock_activate=0.10,theta_out=0.45,use_hard_cap=True,hard_loss_cap=0.15,use_no_follow=False)
    return q

orig_sim=simulate_one_v2
CURRENT_VARIANT='current'; CURRENT_RANKER='current'

def family_sim(row, prices_arg, probs_arg, policy):
    fam=family(row); sym=str(row.get('symbol',''))
    base=orig_sim(row,prices_arg,probs_arg,policy)
    if base is None: return None
    use_te1=False; use_relaxed=False
    latency=(pd.Timestamp(base['entry_date']).normalize()-pd.Timestamp(row['t_theta']).tz_convert(None).normalize()).days
    if CURRENT_VARIANT=='geo_te1' and fam=='geo': use_te1=True
    elif CURRENT_VARIANT=='oil_geo_te1' and fam=='geo' and sym in {'USO','BNO'}: use_te1=True
    elif CURRENT_VARIANT=='geo_latency_2_3_te1' and fam=='geo' and 2 <= latency <= 3: use_te1=True
    elif CURRENT_VARIANT=='geo_latency_le3_te1' and fam=='geo' and latency <= 3: use_te1=True
    elif CURRENT_VARIANT=='earnings_te1' and fam=='earnings': use_te1=True
    elif CURRENT_VARIANT=='geo_relaxed' and fam=='geo': use_relaxed=True
    if use_te1: base=orig_sim(row,prices_arg,probs_arg,te1_policy(policy))
    elif use_relaxed: base=orig_sim(row,prices_arg,probs_arg,relaxed_geo_policy(policy))
    if base is None: return None
    conn=float(row.get('feat_connection_strength',0.0) or 0.0)
    if CURRENT_RANKER=='connection': score=conn
    else: score=0.0
    base['rank_score']=score; base['event_family_variant']=fam; base['entry_latency_days']=latency
    return base

simulate_one_proper=family_sim
rows=[]
variants=['current','geo_te1','oil_geo_te1','geo_latency_2_3_te1','geo_latency_le3_te1','geo_relaxed','earnings_te1']
for benchmark in ['SPY','QQQ']:
    policy=policies[f'{benchmark}_hard_cap']
    for ranker in ['current','connection']:
        CURRENT_RANKER=ranker
        for variant in variants:
            CURRENT_VARIANT=variant
            for split,df,start,end in [('train',train_df,as_utc_day(train_df['t_theta'].min()),train_eval_end),('test',oos_df,OOS_START,OOS_END)]:
                tr,eq,stats=run_portfolio(df,policy,benchmark,start,end)
                score,active=objective_from_run(tr,eq,stats)
                rows.append({'benchmark':benchmark,'ranker':ranker,'variant':variant,'split':split,'score':score,**stats,**{k:v for k,v in active.items() if k!='fold_irs'}})
                tr.to_csv(OUT/f'familyexit_{benchmark.lower()}_{ranker}_{variant}_{split}_trades.csv',index=False)
                eq.to_csv(OUT/f'familyexit_{benchmark.lower()}_{ranker}_{variant}_{split}_equity.csv',index=False)
                print(benchmark,ranker,variant,split,stats['total_return'],stats['excess_return'],active.get('overall_ir'),len(tr),flush=True)
pd.DataFrame(rows).to_csv(OUT/'event_family_exit_results.csv',index=False)
