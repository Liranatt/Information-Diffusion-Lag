import pickle, pandas as pd, numpy as np
from pathlib import Path
root=Path('/mnt/data')
old=pickle.load(open(root/'prices(2).pkl','rb'))
new=pickle.load(open(root/'prices_1.pkl','rb'))
y=pd.read_csv(root/'yahoo_audit/yahoo_daily_ohlc.csv.gz')

def day(v):
    t=pd.Timestamp(v)
    t=t.tz_localize('UTC') if t.tz is None else t.tz_convert('UTC')
    return t.normalize()
newmaps={s:{day(b[0]):b for b in bars} for s,bars in new.items()}
y['day']=pd.to_datetime(y['date'],utc=True).dt.normalize()
ymaps={}
for sym,g in y.groupby('symbol',sort=False):
    ymaps[sym]={r.day:r for r in g.itertuples(index=False)}

cnt={}; unresolved=[]; sourcecnt={}
merged={}
for sym,bars in old.items():
    nm=newmaps.get(sym,{})
    ym=ymaps.get(sym,{})
    out=[]
    for ob in bars:
        d=day(ob[0]); oh,ol,oc=map(float,ob[1:4])
        oldhlc=np.array([oh,ol,oc])
        op=np.nan; cls=None; src=None
        nb=nm.get(d)
        if nb is not None:
            no,nh,nl,nc=map(float,nb[1:5]); newhlc=np.array([nh,nl,nc])
            if np.allclose(oldhlc,newhlc,rtol=1e-7,atol=1e-6): op=no; cls='exact'; src='prices_1'
            else:
                ratios=oldhlc/newhlc
                f=float(np.median(ratios))
                if np.allclose(oldhlc,newhlc*f,rtol=2e-5,atol=2e-4): op=no*f; cls='factor'; src='prices_1'
        if not np.isfinite(op):
            r=ym.get(d)
            if r is not None:
                # test raw then adjusted
                candidates=[('yahoo_raw',float(r.open_raw),np.array([r.high_raw,r.low_raw,r.close_raw],float)),
                            ('yahoo_adj',float(r.open_adjusted),np.array([r.high_adjusted,r.low_adjusted,r.close_adjusted],float))]
                best=None
                for name,o,hlc in candidates:
                    if np.allclose(oldhlc,hlc,rtol=1e-7,atol=1e-6): best=(o,'exact',name,0.0); break
                    if np.all(np.abs(hlc)>1e-12):
                        f=float(np.median(oldhlc/hlc))
                        err=float(np.max(np.abs(oldhlc-hlc*f)/np.maximum(np.abs(oldhlc),1e-9)))
                        if np.allclose(oldhlc,hlc*f,rtol=2e-5,atol=2e-4):
                            cand=(o*f,'factor',name,err)
                            if best is None or cand[3]<best[3]: best=cand
                if best: op,cls,src,_=best
        if not np.isfinite(op):
            cls='unresolved'; src='none'; unresolved.append((sym,str(d.date()),oh,ol,oc))
        cnt[cls]=cnt.get(cls,0)+1; sourcecnt[src]=sourcecnt.get(src,0)+1
        out.append((ob[0],float(op) if np.isfinite(op) else np.nan,oh,ol,oc))
    merged[sym]=out
print('cnt',cnt,'source',sourcecnt,'unresolved',len(unresolved))
print('first unresolved',unresolved[:30])
pickle.dump(merged,open(root/'prices_open_merged.pkl','wb'))
pd.DataFrame(unresolved,columns=['symbol','date','high','low','close']).to_csv(root/'prices_open_unresolved.csv',index=False)
