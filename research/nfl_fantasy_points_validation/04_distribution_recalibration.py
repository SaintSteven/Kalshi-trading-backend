#!/usr/bin/env python3
"""Test 04: strict walk-forward fantasy-points distribution/recalibration comparison."""
import json
from math import erf,sqrt
from pathlib import Path
import numpy as np,pandas as pd
from scipy.stats import t as student_t
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

OUT=Path("research/nfl_fantasy_points_validation/results")
d=pd.read_csv(OUT/"00_walk_forward_predictions.csv").sort_values(["season","week","player_id"]).reset_index(drop=True)
d["time_key"]=d.season*100+d.week; d["resid"]=d.actual_fp-d.pred_fp
offsets=[-12,-9,-6,-3,0,3,6,9,12]
def ncdf(x): return .5*(1+erf(x/sqrt(2)))
def emp(a,x): return ((np.asarray(a)>x).sum()+.5)/(len(a)+1)
rows=[]
for key in sorted(d.time_key.unique()):
 cur=d[d.time_key==key]; prior=d[d.time_key<key]
 for r in cur.itertuples():
  h=prior.loc[prior.position==r.position,"resid"].dropna()
  if len(h)<200: continue
  sd=max(float(h.std(ddof=1)),.1); mad=float(np.median(np.abs(h-np.median(h))))*1.4826; mad=max(mad,.1)
  for off in offsets:
   hit=int(r.actual_fp>r.pred_fp+off)
   rows.append([r.season,r.week,r.player_id,r.position,off,hit,
    1-ncdf(off/sd),1-student_t.cdf(off/sd,df=5),1-ncdf(off/mad),emp(h,off)])
o=pd.DataFrame(rows,columns=["season","week","player_id","position","offset","hit","normal_sd","student_t5","normal_mad","empirical"])
methods=["normal_sd","student_t5","normal_mad","empirical"]
# Strict recalibration: calibrator for a scored week sees only threshold events from earlier weeks.
out=[]
for key in sorted((o.season*100+o.week).unique()):
 cur=o[(o.season*100+o.week)==key].copy(); hist=o[(o.season*100+o.week)<key].copy()
 if len(hist)<1000: continue
 for m in methods:
  iso=IsotonicRegression(y_min=0,y_max=1,out_of_bounds="clip").fit(hist[m],hist.hit)
  lr=LogisticRegression(C=1e6,max_iter=1000).fit(hist[[m]],hist.hit)
  z=cur.copy(); z["method"]=m; z["raw"]=z[m]; z["isotonic"]=iso.predict(z[m]); z["platt"]=lr.predict_proba(z[[m]])[:,1]
  out.append(z[["season","week","player_id","position","offset","hit","method","raw","isotonic","platt"]])
z=pd.concat(out,ignore_index=True)
def metrics(g,col):
 p=g[col].to_numpy(); y=g.hit.to_numpy(); b=np.arange(0,1.0001,.05); ix=np.minimum(np.digitize(p,b)-1,19)
 e=sum((ix==k).mean()*abs(y[ix==k].mean()-p[ix==k].mean()) for k in np.unique(ix))
 return {"n":len(g),"brier":float(np.mean((p-y)**2)),"ece":float(e)}
summary={"test":"04_distribution_recalibration_comparison","market_prices_used":False,"rows":int(len(z)),"results":{}}
for m in methods:
 g=z[z.method==m]; summary["results"][m]={c:metrics(g,c) for c in ["raw","isotonic","platt"]}
best=min(((v[c]["brier"],m,c) for m,v in summary["results"].items() for c in ["raw","isotonic","platt"]))
summary["best_by_brier"]={"method":best[1],"calibration":best[2],"brier":best[0]}
(OUT/"04_distribution_recalibration_summary.json").write_text(json.dumps(summary,indent=2)); z.to_csv(OUT/"04_distribution_recalibration_events.csv",index=False)
print(json.dumps(summary,indent=2))
