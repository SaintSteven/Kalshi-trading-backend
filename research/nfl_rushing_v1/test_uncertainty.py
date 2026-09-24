import argparse, pandas as pd, numpy as np
from pathlib import Path
from scipy.special import ndtr
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss

def metrics(g,pcol):
 y=g.won.astype(int); p=g[pcol].clip(1e-6,1-1e-6); price=g.entry_price
 cost=price.sum(); pnl=np.where(y==1,1-price,-price).sum()
 return {'n':len(g),'brier':brier_score_loss(y,p),'logloss':log_loss(y,p,labels=[0,1]),'avg_prob':p.mean(),'hit':y.mean(),'cal_error_pp':100*(y.mean()-p.mean()),'roi_at_original_entries':pnl/cost if cost else np.nan}

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--out',required=True); a=ap.parse_args(); o=Path(a.out); o.mkdir(parents=True,exist_ok=True)
 d=pd.read_csv(a.input)
 # IMPORTANT: evaluate probabilities, not reselect bets from same sample.
 # Reconstruct raw YES probability from projection/threshold under alternative SDs.
 yes_outcome=np.where(d.side.eq('YES'),d.won,~d.won.astype(bool)).astype(int)
 d['yes_outcome']=yes_outcome
 rows=[]
 for sd in [17.5,20,22.5,25,27.5,30,35,40]:
  py=ndtr((d.projection-d.threshold)/sd)
  ps=np.where(d.side.eq('YES'),py,1-py)
  tmp=d.copy(); tmp['p']=ps
  m=metrics(tmp,'p'); m.update({'variant':f'normal_sd_{sd:g}','sd':sd}); rows.append(m)
 # conditional SD: workload/projection scaled uncertainty
 for base,slope in [(18,.15),(18,.25),(20,.15),(20,.25),(22,.15),(22,.25)]:
  sd=np.maximum(base,base+slope*d.projection)
  py=ndtr((d.projection-d.threshold)/sd); ps=np.where(d.side.eq('YES'),py,1-py)
  tmp=d.copy(); tmp['p']=ps; m=metrics(tmp,'p'); m.update({'variant':f'conditional_{base:g}_{slope:g}','sd':np.nan}); rows.append(m)
 res=pd.DataFrame(rows).sort_values(['brier','logloss']); res.to_csv(o/'uncertainty_variants.csv',index=False)
 # Honest split recalibration: fit isotonic on early 2025 weeks, evaluate later weeks only.
 # This avoids fitting/evaluating calibration on identical observations.
 raw_yes=ndtr((d.projection-d.threshold)/17.499131191796593)
 raw_side=np.where(d.side.eq('YES'),raw_yes,1-raw_yes)
 for cutoff in [6,8,10,12]:
  train=d.week<=cutoff; test=d.week>cutoff
  if train.sum()<100 or test.sum()<100: continue
  iso=IsotonicRegression(out_of_bounds='clip').fit(raw_side[train],d.won.astype(int)[train])
  p=iso.predict(raw_side[test]); g=d[test].copy(); g['p']=p
  m=metrics(g,'p'); m.update({'variant':f'isotonic_weeks1_{cutoff}_test_after','sd':np.nan}); rows.append(m)
 pd.DataFrame(rows).sort_values(['brier','logloss']).to_csv(o/'all_variants.csv',index=False)
 print('BASELINE MODEL',metrics(d.assign(p=d.fair_probability),'p'))
 print('MARKET',metrics(d.assign(p=d.entry_price),'p'))
 print('\nALTERNATIVE UNCERTAINTY\n',res.to_string(index=False))
 print('\nALL INCLUDING TEMPORAL RECALIBRATION\n',pd.DataFrame(rows).sort_values(['brier','logloss']).to_string(index=False))
if __name__=='__main__': main()
