#!/usr/bin/env python3
"""Test 02: walk-forward recalibration of NFL fantasy-point threshold probabilities.

Compares the Test 01 Gaussian residual assumption with a non-parametric
position-specific empirical residual CDF. Every probability for a scored week
uses residuals from strictly earlier weeks only; no market prices are used.
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
    raise SystemExit("Test 00 predictions missing")
d=pd.read_csv(src).sort_values(["season","week","player_id"]).reset_index(drop=True)
d["time_key"]=d.season*100+d.week
d["resid"]=d.actual_fp-d.pred_fp
offsets=[-12,-9,-6,-3,0,3,6,9,12]

def norm_cdf(x):
    return 0.5*(1.0+erf(x/sqrt(2.0)))

def empirical_over(residuals, offset):
    a=np.asarray(residuals,dtype=float)
    # Jeffreys-style smoothing avoids exact 0/1 probabilities in finite samples.
    return float(((a>offset).sum()+0.5)/(len(a)+1.0))

rows=[]
for key in sorted(d.time_key.unique()):
    cur=d[d.time_key==key]
    prior=d[d.time_key<key]
    global_resid=prior.resid.dropna()
    global_sd=float(global_resid.std(ddof=1)) if len(global_resid)>=100 else np.nan
    for r in cur.itertuples():
        pos_resid=prior.loc[prior.position==r.position,"resid"].dropna()
        hist=pos_resid if len(pos_resid)>=100 else global_resid
        if len(hist)<100: continue
        sd=float(pos_resid.std(ddof=1)) if len(pos_resid)>=100 else global_sd
        if not np.isfinite(sd) or sd<=0: continue
        for off in offsets:
            threshold=float(r.pred_fp+off)
            hit=float(r.actual_fp>threshold)
            p_normal=float(1.0-norm_cdf(off/sd))
            p_emp=empirical_over(hist,off)
            rows.append((r.season,r.week,r.player_id,r.position,threshold,hit,p_normal,p_emp))

o=pd.DataFrame(rows,columns=["season","week","player_id","position","threshold","actual_over","normal","empirical"])

def metrics(x,col):
    p=x[col].to_numpy(); y=x.actual_over.to_numpy()
    brier=float(np.mean((p-y)**2))
    bins=np.arange(0,1.0001,.05)
    bucket=np.minimum(np.digitize(p,bins,right=False)-1,19)
    ece=0.0
    for b in np.unique(bucket):
        m=bucket==b
        ece += float(m.mean())*abs(float(y[m].mean())-float(p[m].mean()))
    mid=(p>=.45)&(p<.55)
    return {"n":int(len(x)),"brier_score":brier,"expected_calibration_error":ece,
            "mid_45_55_n":int(mid.sum()),
            "mid_45_55_predicted":float(p[mid].mean()) if mid.any() else None,
            "mid_45_55_actual":float(y[mid].mean()) if mid.any() else None}

summary={"test":"02_walk_forward_probability_recalibration","market_prices_used":False,
         "threshold_observations":int(len(o)),"methods":{},"by_position":{}}
for col in ["normal","empirical"]:
    summary["methods"][col]=metrics(o,col)
for pos,x in o.groupby("position"):
    summary["by_position"][pos]={col:metrics(x,col) for col in ["normal","empirical"]}
summary["preferred_by_brier"]=min(summary["methods"],key=lambda k:summary["methods"][k]["brier_score"])
summary["preferred_by_ece"]=min(summary["methods"],key=lambda k:summary["methods"][k]["expected_calibration_error"])
summary["promotion_gate"]="Do not promote unless empirical improves aggregate Brier and ECE without material position-level regression."
o.to_csv(OUT/"02_recalibration_predictions.csv",index=False)
(OUT/"02_recalibration_summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
