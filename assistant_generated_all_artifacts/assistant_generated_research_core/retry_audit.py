from __future__ import annotations
import json, pickle, re, math, hashlib, sys, os
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('/mnt/data')
NB_PATH=ROOT/'standard_cem_strategy(1).ipynb'
CAND_PATH=ROOT/'candidates_orig.pkl'
OLD_PRICE_PATH=ROOT/'prices(2).pkl'
OPEN_PRICE_PATH=ROOT/'prices_1.pkl'
PROB_PATH=ROOT/'probs_h1.pkl'
POLARITY_PATH=ROOT/'polarity_labels(1).json'
POLICY_PATH=ROOT/'cem_fitted_parameters.json'
OUT=ROOT/'stop_execution_audit_retry'
OUT.mkdir(exist_ok=True)

# --- load exact notebook implementation ---
nb=json.load(open(NB_PATH,'r',encoding='utf-8'))
src4=''.join(nb['cells'][4]['source'])
src5=''.join(nb['cells'][5]['source'])
src6=''.join(nb['cells'][6]['source'])
# Force transparent pure-Python execution of the identical _scan body.
start=src5.index('try:\n    from numba import njit')
end=src5.index('\n\n_DAY_NS',start)
replacement='''HAVE_NUMBA = False\ndef njit(*args, **kwargs):\n    if args and callable(args[0]) and len(args) == 1 and not kwargs:\n        return args[0]\n    def _wrap(fn):\n        return fn\n    return _wrap\n'''
src5=src5[:start]+replacement+src5[end:]
ns={'__name__':'audit_notebook','np':np,'pd':pd,'re':re,'math':math,'json':json,
    'Path':Path,'POLARITY_PATH':POLARITY_PATH}
exec(compile(src4,'notebook_cell4.py','exec'),ns)
exec(compile(src5,'notebook_cell5.py','exec'),ns)
exec(compile(src6,'notebook_cell6.py','exec'),ns)

with open(CAND_PATH,'rb') as f: candidates=pickle.load(f)
with open(OLD_PRICE_PATH,'rb') as f: prices=pickle.load(f)
with open(OPEN_PRICE_PATH,'rb') as f: open_prices=pickle.load(f)
with open(PROB_PATH,'rb') as f: probs=pickle.load(f)
policies=json.load(open(POLICY_PATH,'r',encoding='utf-8'))

# Exact notebook OOS candidate universe.
df=candidates.copy()
if 'cem_eligible' in df.columns:
    df=df.loc[df['cem_eligible'].fillna(False).astype(bool)].copy()
df=df[df[ns['RELEVANCE_COL']].astype(float)>0.5].copy()
if 'split' in df.columns:
    df['split']=df['split'].astype(str).str.lower().str.strip().replace({'val':'test'})
df['t_theta']=pd.to_datetime(df['t_theta'],utc=True)
df['t_e']=pd.to_datetime(df['t_e'],utc=True)
OOS_START=pd.Timestamp('2026-01-01',tz='UTC')
OOS_END=ns['as_utc_day'](df['t_theta'].max())
oos_df=df[(df['t_theta']>=OOS_START)&(df['t_theta']<=OOS_END)].copy()

# --- build open supplement aligned to the frozen HLC scale ---
def utc_day(v):
    t=pd.Timestamp(v)
    t=t.tz_localize('UTC') if t.tz is None else t.tz_convert('UTC')
    return t.normalize()

open_maps={s:{utc_day(b[0]):b for b in bars} for s,bars in open_prices.items()}
open_info={}
validation=[]
for sym,bars in prices.items():
    nm=open_maps.get(sym,{})
    for ob in bars:
        day=utc_day(ob[0])
        nb=nm.get(day)
        oh,ol,oc=map(float,ob[1:4])
        if nb is None:
            cls='missing_open'; factor=np.nan; op=np.nan
        else:
            no,nh,nl,nc=map(float,nb[1:5])
            oldhlc=np.array([oh,ol,oc],float)
            newhlc=np.array([nh,nl,nc],float)
            if np.allclose(oldhlc,newhlc,rtol=1e-7,atol=1e-6):
                cls='exact_hlc'; factor=1.0; op=no
            else:
                valid=np.abs(newhlc)>1e-12
                ratios=oldhlc[valid]/newhlc[valid]
                factor=float(np.median(ratios)) if len(ratios) else np.nan
                if np.isfinite(factor) and np.allclose(oldhlc,newhlc*factor,rtol=2e-5,atol=2e-4):
                    cls='common_factor_rescaled'; op=no*factor
                else:
                    cls='inconsistent_hlc'; op=np.nan
        open_info[(sym,day.value)]={'open':float(op) if np.isfinite(op) else np.nan,
                                    'class':cls,'factor':float(factor) if np.isfinite(factor) else np.nan}
        validation.append((sym,str(day.date()),cls,factor,op))
valdf=pd.DataFrame(validation,columns=['symbol','date','classification','scale_factor','aligned_open'])
valsum=valdf.groupby('classification').size().rename('rows').reset_index()
valsum['pct']=valsum['rows']/len(valdf)*100
valsum.to_csv(OUT/'open_scale_validation_summary.csv',index=False)
valdf[valdf.classification!='exact_hlc'].to_csv(OUT/'open_scale_nonexact_rows.csv',index=False)

# --- frozen original replay ---
def reset_caches():
    ns['clear_kernel_caches'](); ns['_CLOSE_CACHE'].clear(); ns['_PATH_CUTOFF_CACHE'].clear()

original_results={}
original_candidate={}
for bench in ('SPY','QQQ'):
    reset_caches()
    # candidate-level count, independent of allocation
    cand=[]
    for _,row in oos_df.sort_values('t_theta').iterrows():
        tr=ns['simulate_one'](row,prices,probs,policies[bench])
        if tr is not None: cand.append(tr)
    original_candidate[bench]=pd.DataFrame(cand)
    reset_caches()
    trades,equity,stats=ns['sim_opp_cost'](oos_df,prices,probs,policies[bench],bench_sym=bench,
                                          start_date=OOS_START,end_date=OOS_END)
    original_results[bench]=(trades,equity,stats)
    trades.to_csv(OUT/f'{bench.lower()}_original_reproduced_trades.csv',index=False)
    equity.to_csv(OUT/f'{bench.lower()}_original_reproduced_daily_equity.csv',index=False)

# --- open-aware minimal patch, preserving trigger/order/priority ---
def scan_candidate_open(prices_arg, probs_arg, sym, mkt, t_theta, t_e, is_earnings,
                        p_surge, r_surge, policy):
    bar_value,bar_norm,bar_high,bar_low,bar_close,bars=ns['_symbol_arrays'](prices_arg,sym)
    if bar_value.shape[0]<2: return None
    pt_value,pval_raw,day_uni,pval_uni,points=ns['_market_arrays'](probs_arg,mkt)
    if pt_value.shape[0]==0: return None
    w_start=ns['_bisect_left'](bar_value,np.int64(t_theta.value)-30*ns['_DAY_NS'])
    w_end=ns['_bisect_right'](bar_value,np.int64(t_e.value))
    if w_end-w_start<2: return None
    e0=ns['_bisect_left'](pt_value,np.int64(t_theta.normalize().value))
    if e0>=pt_value.shape[0]: return None
    entry_pt_index=-1; held=0; k=e0
    while k<pt_value.shape[0]:
        if pval_raw[k]>=float(policy['enter_strong']): entry_pt_index=k; break
        elif pval_raw[k]>=float(policy['enter_floor']):
            held+=1
            if held>=int(policy['hold_days']): entry_pt_index=k; break
        else: held=0
        k+=1
    if entry_pt_index<0: return None
    if p_surge is not None:
        try:
            ps=float(p_surge)
            if np.isfinite(ps) and ps>float(policy.get('max_prob_surge',999.0)): return None
        except Exception: pass
    if r_surge is not None:
        try:
            rs=float(r_surge)
            if np.isfinite(rs) and rs>float(policy.get('max_price_runup',999.0)): return None
        except Exception: pass
    gi=ns['_bisect_left'](bar_value,pt_value[entry_pt_index])
    if gi<w_start: gi=w_start
    if gi>=w_end or w_end-gi<2: return None
    resolution_cut=np.int64((t_e-pd.Timedelta(days=1)).value)
    if bar_value[gi]>=resolution_cut: return None
    hold_end=ns['_bisect_left'](bar_value,resolution_cut)
    if hold_end>w_end: hold_end=w_end
    if hold_end-gi<2: return None
    entry_price=float(bar_close[gi])
    h_start=max(w_start,gi-15)
    trs=[]
    for j in range(h_start+1,gi+1):
        hh=float(bar_high[j]); ll=float(bar_low[j]); pc=float(bar_close[j-1])
        trs.append(max(hh-ll,abs(hh-pc),abs(ll-pc)))
    if not trs: return None
    atr=float(sum(trs)/len(trs))
    if atr==0 or entry_price==0: return None
    atr_pct=atr/entry_price
    peak=0.0
    for gj in range(gi,hold_end):
        i_rel=gj-gi
        hh=float(bar_high[gj]); ll=float(bar_low[gj]); cc=float(bar_close[gj])
        ret_c=cc/entry_price-1.0; ret_h=hh/entry_price-1.0; ret_l=ll/entry_price-1.0
        reason=0; hard_floor_pct=0; active_stop=np.nan; fill_class='not_stop'; aligned_open=np.nan; open_class='not_needed'
        original_fill=np.nan
        if i_rel>0:
            stop_dist=float(policy['atr_mult'])*atr_pct
            pv=1.0
            idx=ns['_bisect_left'](day_uni,bar_norm[gj])
            if idx<day_uni.shape[0] and day_uni[idx]==bar_norm[gj]: pv=float(pval_uni[idx])
            if pv<float(policy['theta_out']):
                reason=3
            elif ret_l<=peak-stop_dist:
                reason=1
                active_stop=entry_price*(1.0+peak-stop_dist)
                original_fill=max(ll,active_stop)
                info=open_info.get((sym,int(bar_norm[gj])),{'open':np.nan,'class':'missing_open'})
                aligned_open=float(info['open']); open_class=info['class']
                if np.isfinite(aligned_open):
                    if aligned_open<=active_stop:
                        cc=aligned_open; fill_class='overnight_gap_through_stop'
                    else:
                        cc=active_stop; fill_class='valid_intraday_crossing'
                else:
                    cc=original_fill
                    fill_class='missing_or_inconsistent_open'
                ret_c=cc/entry_price-1.0
            elif peak>=float(policy['lock_activate']):
                hard_floor_pct=int(peak*100.0)
                hard_floor=hard_floor_pct/100.0
                if ret_l<hard_floor:
                    reason=2
                    active_stop=entry_price*(1.0+hard_floor)
                    original_fill=max(ll,active_stop)
                    info=open_info.get((sym,int(bar_norm[gj])),{'open':np.nan,'class':'missing_open'})
                    aligned_open=float(info['open']); open_class=info['class']
                    if np.isfinite(aligned_open):
                        if aligned_open<=active_stop:
                            cc=aligned_open; fill_class='overnight_gap_through_stop'
                        else:
                            cc=active_stop; fill_class='valid_intraday_crossing'
                    else:
                        cc=original_fill
                        fill_class='missing_or_inconsistent_open'
                    ret_c=cc/entry_price-1.0
            if reason==0 and gj==hold_end-1: reason=4
        if reason!=0:
            lo=min(float(bar_low[k])/entry_price-1.0 for k in range(gi,gj+1))
            return (bars[gi][0],float(points[int(entry_pt_index)][1]),entry_price,bars[gj][0],float(cc),int(reason),
                    int(hard_floor_pct),float(peak),float(lo),float(ret_c),
                    {'active_stop':active_stop,'aligned_open':aligned_open,'open_class':open_class,
                     'stop_fill_class':fill_class,'original_stop_fill':original_fill,
                     'day_high':hh,'day_low':ll,'day_close':float(bar_close[gj])})
        if i_rel==0: peak=0.0
        elif ret_h>peak: peak=ret_h
    last=hold_end-1; cc=float(bar_close[last]); ret_c=cc/entry_price-1.0
    lo=min(float(bar_low[k])/entry_price-1.0 for k in range(gi,hold_end))
    return (bars[gi][0],float(points[int(entry_pt_index)][1]),entry_price,bars[last][0],cc,5,0,float(peak),float(lo),float(ret_c),
            {'active_stop':np.nan,'aligned_open':np.nan,'open_class':'not_needed','stop_fill_class':'not_stop',
             'original_stop_fill':np.nan,'day_high':float(bar_high[last]),'day_low':float(bar_low[last]),'day_close':cc})

def simulate_one_open(row,prices_arg,probs_arg,policy):
    sym,mkt=row['symbol'],row['market_id']; question=str(row.get('question',''))
    polarity,polarity_source=ns['resolve_polarity'](question,sym)
    if polarity==0: return None
    probs_eff=ns['effective_probs'](probs_arg,mkt,polarity)
    t_theta=pd.Timestamp(row['t_theta']).tz_convert('UTC'); t_e=pd.Timestamp(row['t_e']).tz_convert('UTC')
    scanned=scan_candidate_open(prices_arg,probs_eff,sym,mkt,t_theta,t_e,
                                'earnings' in str(row.get('feat_archetype','')).lower(),
                                ns['effective_prob_surge'](row,polarity),row.get('feat_runup_since_t0'),policy)
    if scanned is None: return None
    entry_ts,entry_prob,entry_price,exit_ts,exit_price,reason_code,hard_floor_pct,peak,trough,ret_c,meta=scanned
    if reason_code==1: reason=f"trailing_{policy['atr_mult']:.1f}ATR"
    elif reason_code==2: reason=f'profit_lock_{hard_floor_pct}%'
    elif reason_code==3: reason=f"poly<{policy['theta_out']}"
    elif reason_code==4: reason='resolution-1d'
    else: reason='end_of_window'
    mkt_probs=probs_eff.get(mkt,[])
    converged='YES' if mkt_probs and mkt_probs[-1][1]>=0.5 else 'NO' if mkt_probs else 'UNKNOWN'
    out=dict(market_id=mkt,symbol=sym,question=question,polarity=polarity,polarity_source=polarity_source,
             pct=round(entry_prob,3),converged=converged,asset_confidence=row.get('confidence_score'),
             question_confidence=row.get('feat_llm_confidence'),archetype=row.get('feat_archetype',''),
             relevance=round(float(row.get(ns['RELEVANCE_COL'],0)),3),split=row.get('split',''),
             entry_date=str(entry_ts.date()),entry_prob=round(entry_prob,3),entry_price=round(entry_price,2),
             exit_date=str(exit_ts.date()),exit_price=round(exit_price,2),exit_reason=reason,
             peak_pct=round(peak*100,2),trough_pct=round(trough*100,2),return_pct=round(ret_c*100,2))
    out.update({f'_audit_{k}':v for k,v in meta.items()})
    return out

# Swap only trade simulator; portfolio, prices, costs and allocation are unchanged.
original_simulate=ns['simulate_one']
ns['simulate_one']=simulate_one_open
corrected_results={}; corrected_candidate={}
for bench in ('SPY','QQQ'):
    reset_caches()
    cand=[]
    for _,row in oos_df.sort_values('t_theta').iterrows():
        tr=simulate_one_open(row,prices,probs,policies[bench])
        if tr is not None: cand.append(tr)
    corrected_candidate[bench]=pd.DataFrame(cand)
    reset_caches()
    trades,equity,stats=ns['sim_opp_cost'](oos_df,prices,probs,policies[bench],bench_sym=bench,
                                          start_date=OOS_START,end_date=OOS_END)
    corrected_results[bench]=(trades,equity,stats)
    trades.to_csv(OUT/f'{bench.lower()}_open_aware_trades.csv',index=False)
    equity.to_csv(OUT/f'{bench.lower()}_open_aware_daily_equity.csv',index=False)
ns['simulate_one']=original_simulate

# Reproduction checks against supplied original outputs.
repro=[]
for bench in ('SPY','QQQ'):
    got=original_results[bench][0].copy(); ref=pd.read_csv(ROOT/f'{bench.lower()}_trades.csv')
    keys=['market_id','symbol','entry_date','exit_date','exit_reason']
    same_n=len(got)==len(ref)
    same_keys=same_n and got[keys].astype(str).reset_index(drop=True).equals(ref[keys].astype(str).reset_index(drop=True))
    max_exit=float(np.nanmax(np.abs(got['exit_price'].to_numpy(float)-ref['exit_price'].to_numpy(float)))) if same_n else np.nan
    max_pnl=float(np.nanmax(np.abs(got['pnl'].to_numpy(float)-ref['pnl'].to_numpy(float)))) if same_n else np.nan
    repro.append({'benchmark':bench,'same_trade_count':same_n,'same_trade_keys':same_keys,
                  'max_abs_exit_price_diff':max_exit,'max_abs_pnl_diff':max_pnl,
                  'reproduced_return_pct':original_results[bench][2]['total_return']})
pd.DataFrame(repro).to_csv(OUT/'original_reproduction_check.csv',index=False)

# Frozen-policy summary.
comp=[]
for bench in ('SPY','QQQ'):
    os=original_results[bench][2]; cs=corrected_results[bench][2]
    comp.append({'benchmark':bench,'policy':'frozen_original_policy',
                 'original_return_pct':os['total_return'],'open_aware_return_pct':cs['total_return'],
                 'return_change_pp':cs['total_return']-os['total_return'],
                 'benchmark_return_pct':os['benchmark_return'],
                 'original_excess_return_pct':os['excess_return'],'open_aware_excess_return_pct':cs['excess_return'],
                 'original_sharpe':os['sharpe'],'open_aware_sharpe':cs['sharpe'],
                 'original_max_dd_pct':os['max_dd'],'open_aware_max_dd_pct':cs['max_dd'],
                 'original_n_trades':os['n_trades'],'open_aware_n_trades':cs['n_trades'],
                 'original_total_txn_cost':os['total_txn_cost'],'open_aware_total_txn_cost':cs['total_txn_cost'],
                 'candidate_trades_original':len(original_candidate[bench]),
                 'candidate_trades_open_aware':len(corrected_candidate[bench])})
compdf=pd.DataFrame(comp); compdf.to_csv(OUT/'frozen_policy_comparison.csv',index=False)

# Candidate-level consistency / missing-open diagnostics.
cand_diag=[]
for bench in ('SPY','QQQ'):
    a=original_candidate[bench]; b=corrected_candidate[bench]
    keys=['market_id','symbol','entry_date','exit_date','exit_reason']
    merged=a.merge(b,on=keys,suffixes=('_orig','_corr'),how='outer',indicator=True)
    stop=b[b.exit_reason.str.startswith(('trailing_','profit_lock_'),na=False)].copy()
    cand_diag.append({'benchmark':bench,'original_candidate_trades':len(a),'corrected_candidate_trades':len(b),
                      'same_key_count':int((merged['_merge']=='both').sum()),
                      'only_original':int((merged['_merge']=='left_only').sum()),
                      'only_corrected':int((merged['_merge']=='right_only').sum()),
                      'candidate_stop_exits':len(stop),
                      'candidate_missing_or_inconsistent_open':int((stop['_audit_stop_fill_class']=='missing_or_inconsistent_open').sum())})
pd.DataFrame(cand_diag).to_csv(OUT/'candidate_path_consistency.csv',index=False)

# Attribution on original realized allocations and quantities: only exit fill/cost changes.
attr_rows=[]
for bench in ('SPY','QQQ'):
    ot=original_results[bench][0].copy()
    cc=corrected_candidate[bench]
    # join corrected candidate metadata by unique trade key
    keys=['market_id','symbol','entry_date','exit_date','exit_reason']
    cols=keys+['exit_price','_audit_active_stop','_audit_aligned_open','_audit_open_class',
               '_audit_stop_fill_class','_audit_original_stop_fill','_audit_day_high','_audit_day_low','_audit_day_close','archetype']
    join=ot.merge(cc[cols],on=keys,how='left',suffixes=('_orig','_corr'))
    stop=join[join.exit_reason.str.startswith(('trailing_','profit_lock_'),na=False)].copy()
    # original realized qty and entry notional; recompute only asset-sale leg cost and pnl.
    stop['original_exit_value']=stop['_qty']*stop['exit_price_orig']
    stop['corrected_exit_value']=stop['_qty']*stop['exit_price_corr']
    stop['original_asset_sell_cost']=stop.apply(lambda r: ns['ib_cost'](r['_qty'],r['exit_price_orig'],True),axis=1)
    stop['corrected_asset_sell_cost']=stop.apply(lambda r: ns['ib_cost'](r['_qty'],r['exit_price_corr'],True),axis=1)
    # pnl field includes benchmark funding sell + asset buy + asset sell, but not benchmark rebuy cost? use original formula by replacing exit proceeds/sell cost.
    stop['corrected_pnl_fixed_allocation']=stop['pnl']+(stop['corrected_exit_value']-stop['original_exit_value'])-(stop['corrected_asset_sell_cost']-stop['original_asset_sell_cost'])
    stop['pnl_difference_fixed_allocation']=stop['corrected_pnl_fixed_allocation']-stop['pnl']
    stop['benchmark']=bench
    # category exactly required
    stop['exit_category']=np.where(stop['_audit_stop_fill_class']=='valid_intraday_crossing','valid intraday crossing',
                           np.where(stop['_audit_stop_fill_class']=='overnight_gap_through_stop','overnight gap through stop',
                           np.where(stop['_audit_open_class']=='missing_open','missing Open',
                           np.where(stop['_audit_open_class']=='inconsistent_hlc','inconsistent OHLC or adjustment','other'))))
    attr_rows.append(stop)
attr=pd.concat(attr_rows,ignore_index=True)
attr.to_csv(OUT/'stop_exit_attribution_detail.csv',index=False)
summary=(attr.groupby(['benchmark','exit_category'],dropna=False)
         .agg(number_of_trades=('symbol','size'),original_pnl=('pnl','sum'),
              corrected_pnl=('corrected_pnl_fixed_allocation','sum'),difference=('pnl_difference_fixed_allocation','sum'))
         .reset_index())
# add required zero categories
cats=['valid intraday crossing','overnight gap through stop','missing Open','inconsistent OHLC or adjustment','other']
full=[]
for bench in ('SPY','QQQ'):
    sub=summary[summary.benchmark==bench].set_index('exit_category')
    for cat in cats:
        if cat in sub.index:
            r=sub.loc[cat]; full.append([bench,cat,int(r.number_of_trades),r.original_pnl,r.corrected_pnl,r.difference])
        else: full.append([bench,cat,0,0.0,0.0,0.0])
summary=pd.DataFrame(full,columns=['benchmark','exit_category','number_of_trades','original_pnl','corrected_pnl','difference'])
summary.to_csv(OUT/'stop_exit_attribution_summary.csv',index=False)

# Diagnostics and largest corrections.
diag=[]; largest=[]
for bench in ('SPY','QQQ'):
    s=attr[attr.benchmark==bench].copy()
    gap=s[s['_audit_stop_fill_class']=='overnight_gap_through_stop'].copy()
    above=(s['exit_price_orig']>s['_audit_day_high']+1e-8).sum()
    below=(s['exit_price_orig']<s['_audit_day_low']-1e-8).sum()
    recovered=(gap['_audit_day_high']>=gap['_audit_active_stop']).sum()
    never=(gap['_audit_day_high']<gap['_audit_active_stop']).sum()
    earnings=gap['archetype_corr'].astype(str).str.contains('earnings',case=False,na=False)
    corr_total=-gap['pnl_difference_fixed_allocation'].sum()
    earnings_corr=-gap.loc[earnings,'pnl_difference_fixed_allocation'].sum()
    abs_corr=(-gap['pnl_difference_fixed_allocation']).sort_values(ascending=False)
    diag.append({'benchmark':bench,'stop_exits':len(s),'gap_affected_exits':len(gap),
                 'gap_affected_pct':len(gap)/len(s)*100 if len(s) else 0,
                 'original_fills_above_daily_high':int(above),'original_fills_below_daily_low':int(below),
                 'gap_then_recovered_to_stop':int(recovered),'gap_never_reached_stop':int(never),
                 'earnings_gap_exits':int(earnings.sum()),'earnings_gap_pct':float(earnings.mean()*100) if len(gap) else 0,
                 'earnings_share_of_gap_correction_pct':float(earnings_corr/corr_total*100) if corr_total else 0,
                 'largest_trade_share_pct':float(abs_corr.head(1).sum()/corr_total*100) if corr_total else 0,
                 'top_5_share_of_correction_pct':float(abs_corr.head(5).sum()/corr_total*100) if corr_total else 0,
                 'top_10_share_of_correction_pct':float(abs_corr.head(10).sum()/corr_total*100) if corr_total else 0,
                 'top_20_share_of_correction_pct':float(abs_corr.head(20).sum()/corr_total*100) if corr_total else 0})
    largest.append(s.sort_values('pnl_difference_fixed_allocation').head(25))
pd.DataFrame(diag).to_csv(OUT/'stop_exit_diagnostics.csv',index=False)
pd.concat(largest,ignore_index=True).to_csv(OUT/'largest_individual_corrections.csv',index=False)

# Synthetic tests.
def original_fill(active_stop,o,h,l,c):
    return max(l,active_stop) if l<=active_stop else np.nan
def patched_fill(active_stop,o,h,l,c):
    if o<=active_stop: return o
    if l<=active_stop: return active_stop
    return np.nan
cases=[
('normal intraday stop crossing',100,104,108,98,102),
('overnight gap through stop',100,94,98,90,96),
('no stop crossing',100,104,110,102,108),
('same-bar ordering: prior stop 90, current H=115 L=95',90,100,115,95,108),
('gap below stop followed by recovery',100,90,105,85,102)]
syn=[]
for name,st,o,h,l,c in cases:
    of=original_fill(st,o,h,l,c); pf=patched_fill(st,o,h,l,c)
    syn.append({'case':name,'active_stop':st,'open':o,'high':h,'low':l,'close':c,
                'original_fill':of,'open_aware_fill':pf,'stop_exit':bool(l<=st)})
pd.DataFrame(syn).to_csv(OUT/'synthetic_tests.csv',index=False)

# Probability timestamp diagnostics.
prob_times=[]
for pts in probs.values():
    for t,v in pts:
        ts=pd.Timestamp(t)
        prob_times.append((ts.hour,ts.minute,ts.second))
prob_midnight=sum(x==(0,0,0) for x in prob_times)
ttheta=pd.to_datetime(df['t_theta'],utc=True)
meta={'old_prices_sha256':hashlib.sha256(OLD_PRICE_PATH.read_bytes()).hexdigest(),
      'open_prices_sha256':hashlib.sha256(OPEN_PRICE_PATH.read_bytes()).hexdigest(),
      'probs_sha256':hashlib.sha256(PROB_PATH.read_bytes()).hexdigest(),
      'candidates_sha256':hashlib.sha256(CAND_PATH.read_bytes()).hexdigest(),
      'policy_sha256':hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest(),
      'old_symbols':len(prices),'open_symbols':len(open_prices),'oos_candidates':len(oos_df),
      'probability_points':len(prob_times),'probability_points_midnight_utc':prob_midnight,
      't_theta_rows':len(ttheta),'t_theta_midnight_utc':int(((ttheta.dt.hour==0)&(ttheta.dt.minute==0)&(ttheta.dt.second==0)).sum())}
json.dump(meta,open(OUT/'audit_manifest.json','w'),indent=2)

print('DONE')
print(compdf.to_string(index=False))
print('\nREPRO')
print(pd.DataFrame(repro).to_string(index=False))
print('\nOPEN VALIDATION')
print(valsum.to_string(index=False))
print('\nATTR')
print(summary.to_string(index=False))
print('\nDIAG')
print(pd.DataFrame(diag).to_string(index=False))
