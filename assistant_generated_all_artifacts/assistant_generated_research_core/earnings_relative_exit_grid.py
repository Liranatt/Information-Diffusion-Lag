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
exec(compile(src,'earnrel_prefix.py','exec'),globals())
policies=json.load(open(ROOT/'local_consensus_validation'/'consensus_policies.json'))
SECTOR={'Technology':'XLK','Financial Services':'XLF','Healthcare':'XLV','Consumer Cyclical':'XLY','Consumer Defensive':'XLP','Communication Services':'XLC','Industrials':'XLI','Energy':'XLE','Basic Materials':'XLB','Utilities':'XLU'}

def is_earnings(row):
    text=(str(row.get('question',''))+' '+str(row.get('feat_archetype',''))).lower()
    return any(w in text for w in ['earnings','revenue','eps','ebitda','quarterly'])

def ref_symbol(row, ref, benchmark):
    if ref=='sector': return SECTOR.get(str(row.get('feat_sector','')))
    if ref=='benchmark': return benchmark
    return ref

def close_at_or_before(prices_arg,symbol,day):
    bars=prices_arg.get(symbol,[])
    if not bars: return None
    day=as_utc_day(day)
    out=None
    for b in bars:
        if as_utc_day(b[0])<=day: out=float(b[4])
        else: break
    return out

orig_sim=simulate_one_v2
CURRENT_BENCH='SPY'; CURRENT_RANKER='current'; RULE=None

def relative_exit_sim(row,prices_arg,probs_arg,policy):
    base=orig_sim(row,prices_arg,probs_arg,policy)
    if base is None: return None
    conn=float(row.get('feat_connection_strength',0.0) or 0.0)
    base['rank_score']=conn if CURRENT_RANKER=='connection' else 0.0
    if RULE is None or not is_earnings(row): return base
    ref,days,threshold=RULE
    rs=ref_symbol(row,ref,CURRENT_BENCH)
    if not rs or rs not in prices_arg: return base
    entry_day=pd.Timestamp(base['entry_date']); exit_day=pd.Timestamp(base['exit_date'])
    entry_ref=close_at_or_before(prices_arg,rs,entry_day)
    if entry_ref is None or entry_ref<=0: return base
    bars=prices_arg.get(str(row['symbol']),[])
    eligible=[]
    for b in bars:
        bd=pd.Timestamp(b[0]).tz_convert(None).normalize()
        if bd < entry_day.normalize(): continue
        if bd >= exit_day.normalize(): break  # current exit on same day has priority
        eligible.append(b)
    if len(eligible)<=days: return base
    entry_price=float(base['entry_price'])
    for i,b in enumerate(eligible):
        if i<days: continue
        bd=pd.Timestamp(b[0])
        ref_close=close_at_or_before(prices_arg,rs,bd)
        if ref_close is None or ref_close<=0: continue
        stock_close=float(b[4]); active=(stock_close/entry_price-1.0)-(ref_close/entry_ref-1.0)
        if active <= threshold:
            base=dict(base)
            base['exit_date']=str(pd.Timestamp(b[0]).date())
            base['exit_price']=stock_close
            base['exit_reason']=f'earn_rel_{ref}_{days}d_{threshold*100:.1f}pct'
            base['exit_timing']='close'
            base['exit_open']=float(b[1]); base['exit_high']=float(b[2]); base['exit_low']=float(b[3]); base['exit_close']=stock_close
            base['return_pct']=(stock_close/entry_price-1.0)*100.0
            base['active_close_return_at_exit_pct']=active*100.0
            return base
    return base

simulate_one_proper=relative_exit_sim
rows=[]; best={}
refs=['sector','benchmark','SPY','QQQ']; day_grid=[2,3,5]; thresholds=[0.0,-0.01,-0.02]
for benchmark in ['SPY','QQQ']:
    CURRENT_BENCH=benchmark; policy=policies[f'{benchmark}_hard_cap']
    for ranker in ['current','connection']:
        CURRENT_RANKER=ranker
        # baseline
        RULE=None
        tr,eq,stats=run_portfolio(train_df,policy,benchmark,as_utc_day(train_df['t_theta'].min()),train_eval_end)
        score,active=objective_from_run(tr,eq,stats)
        rows.append({'benchmark':benchmark,'ranker':ranker,'reference':'none','days':0,'threshold':np.nan,'split':'train','score':score,**stats,**{k:v for k,v in active.items() if k!='fold_irs'}})
        for ref in refs:
            for days in day_grid:
                for threshold in thresholds:
                    RULE=(ref,days,threshold)
                    tr,eq,stats=run_portfolio(train_df,policy,benchmark,as_utc_day(train_df['t_theta'].min()),train_eval_end)
                    score,active=objective_from_run(tr,eq,stats)
                    rec={'benchmark':benchmark,'ranker':ranker,'reference':ref,'days':days,'threshold':threshold,'split':'train','score':score,**stats,**{k:v for k,v in active.items() if k!='fold_irs'}}
                    rows.append(rec)
                    key=(benchmark,ranker,ref)
                    if key not in best or score>best[key][0]: best[key]=(score,(ref,days,threshold))
        # Evaluate best per ref and best overall on test
        candidates=[]
        for ref in refs:
            candidates.append((ref,*best[(benchmark,ranker,ref)]))
        best_overall=max(candidates,key=lambda x:x[1])
        eval_rules=[('best_'+ref,best[(benchmark,ranker,ref)][1]) for ref in refs]
        eval_rules.append(('best_overall',best_overall[2]))
        RULE=None
        tr,eq,stats=run_portfolio(oos_df,policy,benchmark,OOS_START,OOS_END); score,active=objective_from_run(tr,eq,stats)
        rows.append({'benchmark':benchmark,'ranker':ranker,'reference':'none','days':0,'threshold':np.nan,'split':'test','selection_label':'baseline','score':score,**stats,**{k:v for k,v in active.items() if k!='fold_irs'}})
        for label,rule in eval_rules:
            RULE=rule
            tr,eq,stats=run_portfolio(oos_df,policy,benchmark,OOS_START,OOS_END); score,active=objective_from_run(tr,eq,stats)
            rows.append({'benchmark':benchmark,'ranker':ranker,'reference':rule[0],'days':rule[1],'threshold':rule[2],'split':'test','selection_label':label,'score':score,**stats,**{k:v for k,v in active.items() if k!='fold_irs'}})
            tr.to_csv(OUT/f'earnrel_{benchmark.lower()}_{ranker}_{label}_test_trades.csv',index=False)
            eq.to_csv(OUT/f'earnrel_{benchmark.lower()}_{ranker}_{label}_test_equity.csv',index=False)
            print(benchmark,ranker,label,rule,stats['total_return'],stats['excess_return'],active.get('overall_ir'),flush=True)
pd.DataFrame(rows).to_csv(OUT/'earnings_relative_exit_grid_results.csv',index=False)
