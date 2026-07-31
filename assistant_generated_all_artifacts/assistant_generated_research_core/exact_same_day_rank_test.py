from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('/mnt/data')
OUT=ROOT/'trade_opportunity_research'
OUT.mkdir(exist_ok=True)

# Load local corrected engine, patch only the in-memory same-day sort to accept rank_score.
src=(ROOT/'benchmark_exit_search.py').read_text(encoding='utf-8')
src=src.split('# ---------------------------------------------------------------------------\n# Experiments.')[0]
src=src.replace("OUT = ROOT / 'benchmark_exit_search'", "OUT = ROOT / 'trade_opportunity_research'")
src=src.replace("source = source.replace('@njit(cache=True)', '@njit(cache=False)')", "__KEEP_NESTED_NUMBA_REPLACE__")
src=src.replace('@njit(cache=True)', '@njit(cache=False)')
src=src.replace('__KEEP_NESTED_NUMBA_REPLACE__', "source = source.replace('@njit(cache=True)', '@njit(cache=False)')")
needle="source = source.replace('@njit(cache=True)', '@njit(cache=False)')"
insert=needle+"\nsource = source.replace(\"all_trades.sort(key=lambda t: t['_entry_ts'])\", \"all_trades.sort(key=lambda t: (t['_entry_ts'], -float(t.get('rank_score', 0.0))))\")"
if needle not in src:
    raise RuntimeError('patch insertion point missing')
src=src.replace(needle,insert,1)
exec(compile(src,'rank_test_prefix.py','exec'),globals())

policies=json.load(open(ROOT/'local_consensus_validation'/'consensus_policies.json'))
master=pd.read_csv(OUT/'trade_opportunity_master.csv') if (OUT/'trade_opportunity_master.csv').exists() else None

# Load precomputed train scores if available.
score_maps={}
if master is not None:
    for benchmark in ['SPY','QQQ']:
        tr=master[(master.benchmark==benchmark)&(master.analysis_split=='train')]
        gm=float(tr.hardcap_active_vs_benchmark_gross_pct.mean())
        grp=tr.groupby(['event_family','feat_sector']).hardcap_active_vs_benchmark_gross_pct.agg(['mean','count'])
        grp['score']=(grp['mean']*grp['count']+gm*20)/(grp['count']+20)
        score_maps[(benchmark,'family_sector')]=grp['score'].to_dict()
        score_maps[(benchmark,'family')]=tr.groupby('event_family').hardcap_active_vs_benchmark_gross_pct.mean().to_dict()

def family(row):
    text=(str(row.get('question',''))+' '+str(row.get('feat_archetype',''))).lower()
    if any(w in text for w in ['earnings','revenue','eps','ebitda','quarterly']): return 'earnings'
    if any(w in text for w in ['iran','israel','gaza','hezbollah','hamas','ukraine','russia','china','taiwan','strike','war','ceasefire','military','nato','invasion','gulf state','kuwait','saudi','oman','uae','geopolitical','diplomatic meeting']): return 'geo'
    return 'other'

orig_sim=simulate_one_v2
CURRENT_RANKER='current'
CURRENT_BENCH='SPY'

def ranked_sim(row, prices_arg, probs_arg, policy):
    t=orig_sim(row,prices_arg,probs_arg,policy)
    if t is None: return None
    fam=family(row)
    sector=str(row.get('feat_sector','Unknown'))
    latency=(pd.Timestamp(t['entry_date']).normalize()-pd.Timestamp(row['t_theta']).tz_convert(None).normalize()).days
    conn=float(row.get('feat_connection_strength',0.0) or 0.0)
    if CURRENT_RANKER=='current': score=0.0
    elif CURRENT_RANKER=='connection': score=conn
    elif CURRENT_RANKER=='latency': score=float(latency)
    elif CURRENT_RANKER=='geo_first': score={'geo':2.0,'other':1.0,'earnings':0.0}.get(fam,0.0)
    elif CURRENT_RANKER=='earnings_first': score={'earnings':2.0,'other':1.0,'geo':0.0}.get(fam,0.0)
    elif CURRENT_RANKER=='family_train': score=float(score_maps.get((CURRENT_BENCH,'family'),{}).get(fam,0.0))
    elif CURRENT_RANKER=='family_sector_train': score=float(score_maps.get((CURRENT_BENCH,'family_sector'),{}).get((fam,sector),0.0))
    elif CURRENT_RANKER=='connection_latency': score=conn+0.02*float(latency)
    else: score=0.0
    t['rank_score']=score
    t['event_family_ranked']=fam
    return t

simulate_one_proper=ranked_sim

rows=[]
rankers=['current','connection','latency','geo_first','earnings_first','family_train','family_sector_train','connection_latency']
for benchmark in ['SPY','QQQ']:
    CURRENT_BENCH=benchmark
    policy=policies[f'{benchmark}_hard_cap']
    for ranker in rankers:
        CURRENT_RANKER=ranker
        for split,df,start,end in [
            ('train',train_df,as_utc_day(train_df['t_theta'].min()),train_eval_end),
            ('test',oos_df,OOS_START,OOS_END),
        ]:
            tr,eq,stats=run_portfolio(df,policy,benchmark,start,end)
            score,active=objective_from_run(tr,eq,stats)
            rows.append({'benchmark':benchmark,'ranker':ranker,'split':split,'score':score,**stats,**{k:v for k,v in active.items() if k!='fold_irs'}})
            tr.to_csv(OUT/f'exact_{benchmark.lower()}_{ranker}_{split}_trades.csv',index=False)
            print(benchmark,ranker,split,stats['total_return'],stats['excess_return'],active.get('overall_ir'),len(tr),flush=True)

pd.DataFrame(rows).to_csv(OUT/'exact_same_day_ranker_results.csv',index=False)
