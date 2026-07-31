from __future__ import annotations
import json
from pathlib import Path
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
exec(compile(src,'focused_selection_prefix.py','exec'),globals())
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

orig_sim=simulate_one_v2
CURRENT_VARIANT='connection'

def focused_sim(row,prices_arg,probs_arg,policy):
    fam=family(row); sym=str(row.get('symbol','')).upper()
    conn=float(row.get('feat_connection_strength',0.0) or 0.0)
    if CURRENT_VARIANT in {'conn1_earn','conn1_earn_no_xle','conn1_earn_no_xle_geo23'} and fam=='earnings' and conn < 0.999:
        return None
    if CURRENT_VARIANT in {'no_xle','conn1_earn_no_xle','conn1_earn_no_xle_geo23'} and fam=='geo' and sym=='XLE':
        return None
    base=orig_sim(row,prices_arg,probs_arg,policy)
    if base is None: return None
    latency=(pd.Timestamp(base['entry_date']).normalize()-pd.Timestamp(row['t_theta']).tz_convert(None).normalize()).days
    if CURRENT_VARIANT=='conn1_earn_no_xle_geo23' and fam=='geo' and 2 <= latency <= 3:
        base=orig_sim(row,prices_arg,probs_arg,te1_policy(policy))
        if base is None: return None
    base['rank_score']=conn
    base['event_family_focused']=fam
    base['entry_latency_days']=latency
    return base

simulate_one_proper=focused_sim
rows=[]
variants=['connection','conn1_earn','no_xle','conn1_earn_no_xle','conn1_earn_no_xle_geo23']
for benchmark in ['SPY','QQQ']:
    policy=policies[f'{benchmark}_hard_cap']
    for variant in variants:
        CURRENT_VARIANT=variant
        for split,df,start,end in [('train',train_df,as_utc_day(train_df['t_theta'].min()),train_eval_end),('test',oos_df,OOS_START,OOS_END)]:
            tr,eq,stats=run_portfolio(df,policy,benchmark,start,end)
            score,active=objective_from_run(tr,eq,stats)
            rows.append({'benchmark':benchmark,'variant':variant,'split':split,'score':score,**stats,**{k:v for k,v in active.items() if k!='fold_irs'}})
            tr.to_csv(OUT/f'focused_{benchmark.lower()}_{variant}_{split}_trades.csv',index=False)
            eq.to_csv(OUT/f'focused_{benchmark.lower()}_{variant}_{split}_equity.csv',index=False)
            print(benchmark,variant,split,stats['total_return'],stats['excess_return'],active.get('overall_ir'),active.get('active_max_dd_pct'),len(tr),flush=True)
pd.DataFrame(rows).to_csv(OUT/'focused_selection_rule_results.csv',index=False)
