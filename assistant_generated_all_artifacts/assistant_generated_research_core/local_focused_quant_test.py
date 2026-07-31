from __future__ import annotations
import json, math, time
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import qmc

ROOT=Path('/mnt/data')
OUT=ROOT/'local_focused_quant_results'
OUT.mkdir(exist_ok=True)

# Load definitions only; do not execute any prior experiment main.
src=(ROOT/'benchmark_exit_search.py').read_text(encoding='utf-8')
src=src.split('# ---------------------------------------------------------------------------\n# Experiments.')[0]
src=src.replace("OUT = ROOT / 'benchmark_exit_search'", "OUT = ROOT / 'local_focused_quant_results'")
src=src.replace("source = source.replace('@njit(cache=True)', '@njit(cache=False)')", "__KEEP_NESTED_NUMBA_REPLACE__")
src=src.replace('@njit(cache=True)', '@njit(cache=False)')
src=src.replace('__KEEP_NESTED_NUMBA_REPLACE__', "source = source.replace('@njit(cache=True)', '@njit(cache=False)')")
exec(compile(src,'local_focused_prefix.py','exec'),globals())

# Anchor policies are from the corrected local rerun, not GitHub.
anchors=json.load(open(ROOT/'proper_execution_rerun'/'proper_retrained_policies.json'))

def normalize_anchor(p):
    p=dict(p)
    p.pop('max_prob_surge',None)
    mc=int(p['max_concurrent'])
    gross=min(float(p['position_size_pct'])*mc,0.95)
    p['gross_event_exposure']=gross
    p['position_size_pct']=gross/mc
    p['use_hard_cap']=False; p['use_no_follow']=False
    p['hard_loss_cap']=0.50; p['no_follow_days']=30; p['no_follow_mfe']=0.0
    return p

def policy_to_u(policy,variant='baseline'):
    vals=[]
    for name,lo,hi,integer in specs_for(variant):
        x=float(policy[name])
        vals.append(np.clip((x-lo)/(hi-lo),0,1))
    return np.asarray(vals,float)

def eval_policy(policy,benchmark,split='train'):
    if split=='train':
        trades,eq,stats=run_portfolio(train_df,policy,benchmark,as_utc_day(train_df['t_theta'].min()),train_eval_end)
    else:
        trades,eq,stats=run_portfolio(oos_df,policy,benchmark,OOS_START,OOS_END)
    score,active=objective_from_run(trades,eq,stats)
    return score,{**stats,**active},trades,eq

def local_sobol(anchor,benchmark,n=96,seed=1):
    dim=len(specs_for('baseline'))
    # One-third broad global points; the rest perturb the corrected anchor.
    m=int(math.ceil(math.log2(max(n,8))))
    raw=qmc.Sobol(d=dim,scramble=True,seed=seed).random_base2(m)
    broad_n=max(8,n//3)
    broad=raw[:broad_n]
    ua=policy_to_u(anchor)
    local=np.clip(ua + (raw[broad_n:n]-0.5)*0.50,0,1)
    pts=np.vstack([ua[None,:],broad,local])[:n]
    rows=[]; best=(-1e99,None,None,None)
    for i,u in enumerate(pts):
        score,p,metrics=evaluate_u(u,'baseline',benchmark)
        rows.append({'method':'local_sobol','eval':i,'train_score':score,**p,**metrics})
        if score>best[0]: best=(score,p,metrics,u.copy())
    return best,pd.DataFrame(rows)

def matched_cem(anchor,benchmark,pop=16,iters=6,seed=1):
    dim=len(specs_for('baseline')); rng=np.random.default_rng(seed)
    mean=policy_to_u(anchor); std=np.full(dim,0.20)
    rows=[]; best=(-1e99,None,None,None); eid=0
    for it in range(iters):
        pts=np.clip(rng.normal(mean,std,size=(pop,dim)),0,1)
        evaluated=[]
        for u in pts:
            score,p,metrics=evaluate_u(u,'baseline',benchmark)
            rows.append({'method':'pure_cem','iteration':it+1,'eval':eid,'train_score':score,**p,**metrics}); eid+=1
            evaluated.append((score,u.copy(),p,metrics))
            if score>best[0]: best=(score,p,metrics,u.copy())
        evaluated.sort(key=lambda x:x[0],reverse=True)
        elite_n=max(4,int(round(0.20*pop)))
        elite=np.asarray([x[1] for x in evaluated[:elite_n] if x[0]>-1e8])
        if len(elite):
            mean=elite.mean(axis=0); std=np.maximum(elite.std(axis=0),0.05)
    return best,pd.DataFrame(rows)

def exit_grid(base_policy,benchmark):
    candidates=[]
    # Baseline itself.
    candidates.append(('baseline',dict(base_policy),{}))
    for cap in [0.04,0.06,0.08,0.10,0.12]:
        p=dict(base_policy,use_hard_cap=True,use_no_follow=False,hard_loss_cap=cap)
        candidates.append(('hard_cap',p,{'hard_loss_cap':cap}))
    for days in [3,5,7,10]:
        for mfe in [0.01,0.02,0.03]:
            p=dict(base_policy,use_hard_cap=False,use_no_follow=True,no_follow_days=days,no_follow_mfe=mfe)
            candidates.append(('no_follow',p,{'no_follow_days':days,'no_follow_mfe':mfe}))
    for cap in [0.06,0.08,0.10]:
        for days in [3,5,7]:
            for mfe in [0.01,0.02]:
                p=dict(base_policy,use_hard_cap=True,use_no_follow=True,hard_loss_cap=cap,no_follow_days=days,no_follow_mfe=mfe)
                candidates.append(('combined',p,{'hard_loss_cap':cap,'no_follow_days':days,'no_follow_mfe':mfe}))
    rows=[]; best_by_variant={}
    for i,(variant,p,params) in enumerate(candidates):
        score,metrics,_,_=eval_policy(p,benchmark,'train')
        row={'benchmark':benchmark,'variant':variant,'grid_id':i,'train_score':score,**params,**metrics}
        rows.append(row)
        if variant not in best_by_variant or score>best_by_variant[variant][0]:
            best_by_variant[variant]=(score,p,metrics)
    return pd.DataFrame(rows),best_by_variant

all_search=[]; all_exit=[]; selected_rows=[]; method_rows=[]; selected_policies={}
for benchmark in ['SPY','QQQ']:
    anchor=normalize_anchor(anchors[benchmark])
    # Evaluate anchor after requested structural fixes.
    a_score,a_metrics,a_tr,a_eq=eval_policy(anchor,benchmark,'train')
    ao_score,ao_metrics,ao_tr,ao_eq=eval_policy(anchor,benchmark,'oos')
    method_rows.append({'benchmark':benchmark,'method':'corrected_anchor','train_score':a_score,'oos_score':ao_score,
                        **{f'train_{k}':v for k,v in a_metrics.items() if k!='fold_irs'},
                        **{f'oos_{k}':v for k,v in ao_metrics.items() if k!='fold_irs'}})

    method_winners=[]
    for seed_idx in range(2):
        sob_seed=7001+(0 if benchmark=='SPY' else 100)+seed_idx*1000
        cem_seed=9001+(0 if benchmark=='SPY' else 100)+seed_idx*1000
        sob_best,sob_trace=local_sobol(anchor,benchmark,n=96,seed=sob_seed)
        cem_best,cem_trace=matched_cem(anchor,benchmark,pop=16,iters=6,seed=cem_seed)
        sob_trace['benchmark']=benchmark; sob_trace['seed_index']=seed_idx+1
        cem_trace['benchmark']=benchmark; cem_trace['seed_index']=seed_idx+1
        all_search.extend([sob_trace,cem_trace])
        for method,best in [('local_sobol',sob_best),('pure_cem',cem_best)]:
            tr_score,p,tr_metrics,_=best
            oos_score,oos_metrics,oos_trades,oos_eq=eval_policy(p,benchmark,'oos')
            method_rows.append({'benchmark':benchmark,'method':method,'seed_index':seed_idx+1,'train_score':tr_score,'oos_score':oos_score,
                                **{f'train_{k}':v for k,v in tr_metrics.items() if k!='fold_irs'},
                                **{f'oos_{k}':v for k,v in oos_metrics.items() if k!='fold_irs'}})
            method_winners.append((tr_score,f'{method}_seed{seed_idx+1}',p,tr_metrics))
    # Training-only selection between equal-budget search methods.
    method_winners.sort(key=lambda x:x[0],reverse=True)
    _,chosen_method,base_policy,_=method_winners[0]
    selected_policies[f'{benchmark}_optimized_baseline']={'search_method':chosen_method,**base_policy}

    grid,bests=exit_grid(base_policy,benchmark); all_exit.append(grid)
    for variant,(tr_score,p,tr_metrics) in bests.items():
        oos_score,oos_metrics,oos_trades,oos_eq=eval_policy(p,benchmark,'oos')
        selected_policies[f'{benchmark}_{variant}']=p
        oos_trades.to_csv(OUT/f'{benchmark.lower()}_{variant}_oos_trades.csv',index=False)
        oos_eq.to_csv(OUT/f'{benchmark.lower()}_{variant}_oos_equity.csv',index=False)
        selected_rows.append({'benchmark':benchmark,'variant':variant,'base_search_method':chosen_method,
                              'train_score':tr_score,'oos_score':oos_score,
                              **{f'train_{k}':v for k,v in tr_metrics.items() if k!='fold_irs'},
                              **{f'oos_{k}':v for k,v in oos_metrics.items() if k!='fold_irs'},
                              'hard_loss_cap':p.get('hard_loss_cap',np.nan),
                              'no_follow_days':p.get('no_follow_days',np.nan),
                              'no_follow_mfe':p.get('no_follow_mfe',np.nan),
                              'gross_event_exposure':p.get('gross_event_exposure'),
                              'position_size_pct':p.get('position_size_pct'),
                              'max_concurrent':p.get('max_concurrent')})

pd.concat(all_search,ignore_index=True).to_csv(OUT/'optimizer_search_trace.csv',index=False)
pd.concat(all_exit,ignore_index=True).to_csv(OUT/'exit_grid_train_results.csv',index=False)
pd.DataFrame(method_rows).to_csv(OUT/'optimizer_method_comparison.csv',index=False)
pd.DataFrame(selected_rows).to_csv(OUT/'selected_train_oos_results.csv',index=False)
(OUT/'selected_policies.json').write_text(json.dumps(selected_policies,indent=2,default=float),encoding='utf-8')
manifest={
 'inputs':'uploaded local files only',
 'probability_surge':'removed',
 'gross_exposure':'<=95%, position_size=gross/max_concurrent, reject <90% target fill',
 'objective':'active log-return IR + four chronological-block robustness + active drawdown + terminal relative return',
 'optimizer_test':'96-evaluation local/global Sobol versus 96-evaluation CEM, two seeds each; training-only selection',
 'exit_grid':{'hard_caps':[0.04,0.06,0.08,0.10,0.12], 'no_follow_days':[3,5,7,10], 'no_follow_mfe':[0.01,0.02,0.03], 'combined_caps':[0.06,0.08,0.10]},
 'oos_selection':'none; all selections made using train objective only'
}
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(pd.DataFrame(method_rows)[['benchmark','method','train_score','oos_score','train_excess_return','oos_excess_return','oos_overall_ir','oos_max_dd','oos_n_trades']].to_string(index=False))
print('\nSELECTED EXIT VARIANTS')
print(pd.DataFrame(selected_rows)[['benchmark','variant','base_search_method','train_score','oos_score','train_excess_return','oos_excess_return','oos_overall_ir','oos_max_dd','oos_n_trades','hard_loss_cap','no_follow_days','no_follow_mfe']].to_string(index=False))
