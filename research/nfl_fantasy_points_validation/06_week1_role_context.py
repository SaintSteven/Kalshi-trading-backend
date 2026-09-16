#!/usr/bin/env python3
"""Test 06: fantasy-points Week 1 and role/context diagnostics, OOS empirical engine."""
import json
from pathlib import Path
import numpy as np,pandas as pd
OUT=Path("research/nfl_fantasy_points_validation/results")
z=pd.read_csv(OUT/"04_distribution_recalibration_events.csv")
z=z[z.method.eq("empirical")].copy(); z["p"]=z.raw
pred=pd.read_csv(OUT/"00_walk_forward_predictions.csv")
# Context available without look-ahead: week, position, predicted FP. Role proxy uses prior-model expectation.
z=z.merge(pred[["season","week","player_id","pred_fp"]],on=["season","week","player_id"],how="left")
z["week1"]=z.week.eq(1)
z["role_band"]=pd.cut(z.pred_fp,[-.001,8,14,20,999],labels=["low_lt8","8_14","14_20","20plus"])
z["prob_band"]=pd.cut(z.p,[-.001,.20,.35,.65,1.001],labels=["lt20","20_35","35_65","65plus"])
def met(g):
 p=g.p.to_numpy(); y=g.hit.to_numpy()
 return {"n":int(len(g)),"player_games":int(g[["season","week","player_id"]].drop_duplicates().shape[0]),
 "predicted":float(p.mean()),"actual":float(y.mean()),"gap_pp":float(100*(p.mean()-y.mean())),
 "brier":float(np.mean((p-y)**2))}
summary={"test":"06_week1_role_context","market_prices_used":False,
 "week1":met(z[z.week1]),"non_week1":met(z[~z.week1]),"week1_positions":{},"week1_probability_bands":{},"role_bands":{}}
for pos,g in z[z.week1].groupby("position"): summary["week1_positions"][pos]=met(g)
for b,g in z[z.week1].groupby("prob_band",observed=True): summary["week1_probability_bands"][str(b)]=met(g)
for b,g in z.groupby("role_band",observed=True):
 summary["role_bands"][str(b)]={"overall":met(g),"week1":met(g[g.week1]) if len(g[g.week1]) else None}
(OUT/"06_week1_role_context_summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
