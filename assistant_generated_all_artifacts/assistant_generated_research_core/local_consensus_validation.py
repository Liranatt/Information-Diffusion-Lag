from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('/mnt/data')
OUT=ROOT/'local_consensus_validation'
OUT.mkdir(exist_ok=True)

src=(ROOT/'benchmark_exit_search.py').read_text(encoding='utf-8')
src=src.split('# ---------------------------------------------------------------------------\n# Experiments.')[0]
src=src.replace("OUT = ROOT / 'benchmark_exit_search'", "OUT = ROOT / 'local_consensus_validation'")
src=src.replace("source = source.replace('@njit(cache=True)', '@njit(cache=False)')", "__KEEP_NESTED_NUMBA_REPLACE__")
src=src.replace('@njit(cache=True)', '@njit(cache=False)')
src=src.replace('__KEEP_NESTED_NUMBA_REPLACE__', "source = source.replace('@njit(cache=True)', '@njit(cache=False)')")
exec(compile(src,'local_consensus_prefix.py','exec'),globals())

def eval_policy(policy, benchmark, split):
    if split=='train':
        tr,eq,stats=run_portfolio(train_df,policy,benchmark,as_utc_day(train_df['t_theta'].min()),train_eval_end)
    else:
        tr,eq,stats=run_portfolio(oos_df,policy,benchmark,OOS_START,OOS_END)
    score,active=objective_from_run(tr,eq,stats)
    return score,{**stats,**active},tr,eq

def consensus_policy(benchmark):
    policies=[]
    for i in range(1,5):
        p=json.load(open(ROOT/f'local_focused_quant_seed{i}'/'selected_policies.json'))[f'{benchmark}_optimized_baseline']
        p={k:v for k,v in p.items() if k!='search_method'}
        policies.append(p)
    out={}
    continuous=['atr_mult','lock_activate','theta_out','enter_strong','enter_floor','max_price_runup','gross_event_exposure']
    for k in continuous:
        out[k]=float(np.median([float(p[k]) for p in policies]))
    out['hold_days']=int(round(np.median([int(p['hold_days']) for p in policies])))
    out['max_concurrent']=int(round(np.median([int(p['max_concurrent']) for p in policies])))
    out['gross_event_exposure']=min(out['gross_event_exposure'],0.95)
    out['position_size_pct']=out['gross_event_exposure']/out['max_concurrent']
    if out['enter_strong'] < out['enter_floor']:
        out['enter_strong']=out['enter_floor']
    out.update(use_hard_cap=False,use_no_follow=False,hard_loss_cap=0.50,no_follow_days=30,no_follow_mfe=0.0)
    return out,policies

def exit_grid(base,benchmark):
    configs=[('baseline',dict(base))]
    for cap in [0.04,0.06,0.08,0.10,0.12]:
        configs.append(('hard_cap',dict(base,use_hard_cap=True,use_no_follow=False,hard_loss_cap=cap)))
    for days in [3,5,7,10]:
        for mfe in [0.01,0.02,0.03]:
            configs.append(('no_follow',dict(base,use_hard_cap=False,use_no_follow=True,no_follow_days=days,no_follow_mfe=mfe)))
    for cap in [0.06,0.08,0.10]:
        for days in [3,5,7,10]:
            for mfe in [0.01,0.02,0.03]:
                configs.append(('combined',dict(base,use_hard_cap=True,use_no_follow=True,hard_loss_cap=cap,no_follow_days=days,no_follow_mfe=mfe)))
    rows=[]; best={}
    for idx,(variant,p) in enumerate(configs):
        score,met,_,_=eval_policy(p,benchmark,'train')
        rows.append({'benchmark':benchmark,'variant':variant,'grid_id':idx,'train_score':score,
                     'hard_loss_cap':p.get('hard_loss_cap'), 'no_follow_days':p.get('no_follow_days'),
                     'no_follow_mfe':p.get('no_follow_mfe'), **met})
        if variant not in best or score>best[variant][0]: best[variant]=(score,p,met)
    return pd.DataFrame(rows),best

rows=[]; grids=[]; policies_out={}
for benchmark in ['SPY','QQQ']:
    base,seed_policies=consensus_policy(benchmark)
    policies_out[f'{benchmark}_consensus_base']=base
    grid,bests=exit_grid(base,benchmark); grids.append(grid)
    for variant,(tr_score,p,tr_met) in bests.items():
        oos_score,oos_met,trades,equity=eval_policy(p,benchmark,'oos')
        policies_out[f'{benchmark}_{variant}']=p
        trades.to_csv(OUT/f'{benchmark.lower()}_{variant}_oos_trades.csv',index=False)
        equity.to_csv(OUT/f'{benchmark.lower()}_{variant}_oos_equity.csv',index=False)
        rows.append({'benchmark':benchmark,'variant':variant,'train_score':tr_score,'oos_score':oos_score,
                     **{f'train_{k}':v for k,v in tr_met.items() if k!='fold_irs'},
                     **{f'oos_{k}':v for k,v in oos_met.items() if k!='fold_irs'},
                     'hard_loss_cap':p.get('hard_loss_cap'),'no_follow_days':p.get('no_follow_days'),
                     'no_follow_mfe':p.get('no_follow_mfe'),'gross_event_exposure':p['gross_event_exposure'],
                     'position_size_pct':p['position_size_pct'],'max_concurrent':p['max_concurrent']})

pd.concat(grids,ignore_index=True).to_csv(OUT/'consensus_exit_grid_train.csv',index=False)
pd.DataFrame(rows).to_csv(OUT/'consensus_train_oos_results.csv',index=False)
(OUT/'consensus_policies.json').write_text(json.dumps(policies_out,indent=2,default=float),encoding='utf-8')
manifest={'inputs':'uploaded local files only','policy_construction':'componentwise median of four independent training-selected policies','prob_surge':'removed','gross_exposure_cap':0.95,'partial_fill_min':0.90,'objective':'benchmark-relative active IR/fold robustness/active drawdown/relative terminal return','oos_selection':'none'}
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(pd.DataFrame(rows)[['benchmark','variant','train_score','oos_score','train_excess_return','oos_excess_return','oos_overall_ir','oos_active_max_dd_pct','oos_total_return','oos_benchmark_return','oos_n_trades','hard_loss_cap','no_follow_days','no_follow_mfe']].to_string(index=False))
