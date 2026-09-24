import argparse,pandas as pd,numpy as np
from pathlib import Path
from scipy.special import ndtr
from sklearn.isotonic import IsotonicRegression

def pnl(g):
 if len(g)==0:return {'bets':0,'wins':0,'cost':0,'pnl':0,'roi':np.nan}
 y=g.won.astype(int); c=g.entry_price.sum(); p=np.where(y==1,1-g.entry_price,-g.entry_price).sum()
 return {'bets':len(g),'wins':int(y.sum()),'cost':c,'pnl':p,'roi':p/c if c else np.nan}

def select(d,p,edge=0.03):
 x=d.copy(); x['new_fair']=p; x['new_edge']=x.new_fair-x.entry_price
 x=x[x.new_edge>=edge].copy()
 # preserve one-bet-per-player/game discipline using corrected edge
 keys=[c for c in ['season','week','game_id','player_id'] if c in x.columns]
 if not keys: keys=[c for c in ['season','week','event_ticker','player_name'] if c in x.columns]
 x=x.sort_values('new_edge',ascending=False).drop_duplicates(keys,keep='first')
 return x

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 d=pd.read_csv(a.input);o=Path(a.out);o.mkdir(parents=True,exist_ok=True)
 # raw side probability under widened uncertainty candidates
 variants={}
 for sd in [30,35,40,45,50]:
  py=ndtr((d.projection-d.threshold)/sd); variants[f'sd_{sd}']=np.where(d.side.eq('YES'),py,1-py)
 # walk-forward isotonic: for each week, calibrate only on earlier weeks; minimum 100 rows
 raw=variants['sd_40']; wf=np.full(len(d),np.nan)
 for w in sorted(d.week.unique()):
  tr=d.week<w; te=d.week==w
  if tr.sum()<100: continue
  iso=IsotonicRegression(out_of_bounds='clip').fit(raw[tr],d.won.astype(int)[tr]); wf[te]=iso.predict(raw[te])
 variants['sd40_walkforward_iso']=wf
 rows=[]; selected=[]
 for name,p in variants.items():
  for edge in [.03,.05,.08,.10,.15]:
   valid=~pd.isna(p); x=select(d[valid],np.asarray(p)[valid],edge)
   m=pnl(x);m.update({'variant':name,'edge_min':edge});rows.append(m)
   if len(x): x=x.assign(variant=name,edge_min=edge);selected.append(x)
 res=pd.DataFrame(rows);res.to_csv(o/'strategy_summary.csv',index=False)
 if selected: pd.concat(selected).to_csv(o/'selected_bets.csv',index=False)
 print(res.to_string(index=False))
if __name__=='__main__':main()
