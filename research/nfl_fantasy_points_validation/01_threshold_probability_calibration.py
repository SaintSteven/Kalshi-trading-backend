#!/usr/bin/env python3
"""Test 01: out-of-sample threshold-probability calibration for NFL fantasy points.

Consumes Test 00 walk-forward predictions. For each position, uncertainty is
estimated only from PRIOR residuals, preserving walk-forward integrity. We then
evaluate symmetric thresholds around each projection, bucket predicted
probabilities, and compare them with realized hit rates.
"""
import json
from math import erf, sqrt
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path("research/nfl_fantasy_points_validation")
OUT=ROOT/"results"; OUT.mkdir(parents=True,exist_ok=True)
src=OUT/"00_walk_forward_predictions.csv"
if not src.exists():
    raise SystemExit("Test 00 predictions missing; run 00_walk_forward_baseline.py first")
d=pd.read_csv(src).sort_values(["season","week","player_id"]).reset_index(drop=True)
d["time_key"]=d.season*100+d.week
d["resid"]=d.actual_fp-d.pred_fp

def norm_cdf(x):
    return 0.5*(1.0+erf(x/sqrt(2.0)))

rows=[]
# Multiple offsets create threshold observations across the probability range.
offsets=[-12,-9,-6,-3,0,3,6,9,12]
for key in sorted(d.time_key.unique()):
    cur=d[d.time_key==key]
    prior=d[d.time_key<key]
    global_sd=float(prior.resid.std(ddof=1)) if len(prior)>=100 else np.nan
    for r in cur.itertuples():
        pp=prior[prior.position==r.position].resid
        sd=float(pp.std(ddof=1)) if len(pp)>=100 else global_sd
        if not np.isfinite(sd) or sd<=0: continue
        for off in offsets:
            threshold=float(r.pred_fp+off)
            p_over=float(1.0-norm_cdf((threshold-r.pred_fp)/sd))
            hit=float(r.actual_fp>threshold)
            rows.append((r.season,r.week,r.player_id,r.player_name,r.position,
                         r.pred_fp,r.actual_fp,threshold,p_over,hit,sd))
o=pd.DataFrame(rows,columns=["season","week","player_id","player_name","position",
 "pred_fp","actual_fp","threshold","pred_prob_over","actual_over","prior_resid_sd"])

bins=np.arange(0,1.0001,.05)
labels=[f"{int(a*100):02d}-{int(b*100):02d}%" for a,b in zip(bins[:-1],bins[1:])]
o["prob_bucket"]=pd.cut(o.pred_prob_over,bins=bins,labels=labels,include_lowest=True,right=False)

def cal_table(x):
    z=x.groupby("prob_bucket",observed=True).agg(
        n=("actual_over","size"),predicted=("pred_prob_over","mean"),
        actual=("actual_over","mean")).reset_index()
    z["gap"]=z.actual-z.predicted
    return z

cal=cal_table(o)
pos=[]
for p,x in o.groupby("position"):
    z=cal_table(x); z.insert(0,"position",p); pos.append(z)
poscal=pd.concat(pos,ignore_index=True)
brier=float(np.mean((o.pred_prob_over-o.actual_over)**2))
ece=float(np.average(np.abs(cal.gap),weights=cal.n))
summary={"test":"01_threshold_probability_calibration","source_test":"00_fantasy_points_walk_forward_baseline",
 "market_prices_used":False,"threshold_observations":int(len(o)),
 "unique_player_games":int(o[["season","week","player_id"]].drop_duplicates().shape[0]),
 "brier_score":brier,"expected_calibration_error":ece,
 "method":"Normal residual distribution; position-specific SD estimated from prior validation residuals only",
 "probability_buckets":cal.to_dict(orient="records"),
 "by_position":{}}
for p,x in o.groupby("position"):
    c=cal_table(x)
    summary["by_position"][p]={"n":int(len(x)),
      "brier_score":float(np.mean((x.pred_prob_over-x.actual_over)**2)),
      "expected_calibration_error":float(np.average(np.abs(c.gap),weights=c.n))}
o.to_csv(OUT/"01_threshold_predictions.csv",index=False)
cal.to_csv(OUT/"01_calibration_buckets.csv",index=False)
poscal.to_csv(OUT/"01_calibration_by_position.csv",index=False)
(OUT/"01_summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
