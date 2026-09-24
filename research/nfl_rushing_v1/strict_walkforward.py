import argparse,pandas as pd,numpy as np
from pathlib import Path
from scipy.special import ndtr
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss,log_loss

SDS=[30,35,40,45,50]
EDGES=[.03,.05,.08,.10,.15]

def side_prob(d,sd):
 py=ndtr((d.projection-d.threshold)/sd)
 return np.where(d.side.eq('YES'),py,1-py)

def score_prob(d,p):
 y=d.won.astype(int)
 return brier_score_loss(y,np.clip(p,1e-6,1-1e-6))

def select(d,p,edge):
 x=d.copy();x['fair']=p;x['edge']=x.fair-x.entry_price;x=x[x.edge>=edge].copy()
 keys=[c for c in ['season','week','game_id','player_id'] if c in x]
 if not keys: keys=[c for c in ['season','week','event_ticker','player_name'] if c in x]
 return x.sort_values('edge',ascending=False).drop_duplicates(keys)

def pnl(x):
 if len(x)==0:return (0,0,0.,0.)
 y=x.won.astype(int);cost=x.entry_price.sum();profit=np.where(y==1,1-x.entry_price,-x.entry_price).sum()
 return len(x),int(y.sum()),cost,profit

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 d=pd.read_csv(a.input).sort_values(['season','week']).reset_index(drop=True);o=Path(a.out);o.mkdir(parents=True,exist_ok=True)
 weeks=sorted(d[['season','week']].drop_duplicates().itertuples(index=False,name=None))
 out=[];bets=[]
 # For each target week, choose SD and edge cutoff ONLY from prior weeks.
 for season,week in weeks:
  prior=d[(d.season<season)|((d.season==season)&(d.week<week))]
  test=d[(d.season==season)&(d.week==week)]
  if len(prior)<150: continue
  # choose SD by prior-data probability quality only
  sd_scores={sd:score_prob(prior,side_prob(prior,sd)) for sd in SDS}
  sd=min(sd_scores,key=sd_scores.get)
  # fit calibration on prior data only; compare raw vs isotonic by prior Brier using temporal holdback:
  # use final 25% of prior chronological weeks as validation when enough history exists.
  pprior=side_prob(prior,sd); use_iso=False; iso=None
  prior_keys=prior[['season','week']].drop_duplicates()
  if len(prior_keys)>=6:
   cut=max(1,int(len(prior_keys)*.75)); trainkeys=set(map(tuple,prior_keys.iloc[:cut].values)); valmask=prior.apply(lambda r:(r.season,r.week) not in trainkeys,axis=1)
   tr=~valmask
   if tr.sum()>=100 and valmask.sum()>=50:
    cand=IsotonicRegression(out_of_bounds='clip').fit(pprior[tr],prior.won.astype(int)[tr])
    rawb=score_prob(prior[valmask],pprior[valmask]); isob=score_prob(prior[valmask],cand.predict(pprior[valmask]))
    use_iso=isob<rawb
  if use_iso:
   iso=IsotonicRegression(out_of_bounds='clip').fit(pprior,prior.won.astype(int))
  # choose edge threshold by prior realized ROI, requiring >=75 prior bets; tie-break larger sample
  best=None
  for e in EDGES:
   pp=iso.predict(pprior) if use_iso else pprior
   x=select(prior,pp,e);n,w,c,pr=pnl(x);roi=pr/c if c else -999
   if n>=75 and (best is None or roi>best[0]):best=(roi,e,n)
  if best is None: continue
  edge=best[1];ptest=side_prob(test,sd);ptest=iso.predict(ptest) if use_iso else ptest
  x=select(test,ptest,edge);n,w,c,pr=pnl(x)
  out.append({'season':season,'week':week,'prior_rows':len(prior),'chosen_sd':sd,'use_iso':use_iso,'chosen_edge':edge,'bets':n,'wins':w,'cost':c,'pnl':pr,'roi':pr/c if c else np.nan})
  if n: bets.append(x.assign(chosen_sd=sd,use_iso=use_iso,chosen_edge=edge))
 res=pd.DataFrame(out);res.to_csv(o/'weekly_results.csv',index=False)
 if bets:pd.concat(bets).to_csv(o/'walkforward_bets.csv',index=False)
 n=res.bets.sum();w=res.wins.sum();c=res.cost.sum();pr=res.pnl.sum()
 print(res.to_string(index=False))
 print('\nTOTAL',{'weeks':len(res),'bets':int(n),'wins':int(w),'cost':round(c,2),'pnl':round(pr,2),'roi':pr/c if c else np.nan})
if __name__=='__main__':main()

# trigger rerun
