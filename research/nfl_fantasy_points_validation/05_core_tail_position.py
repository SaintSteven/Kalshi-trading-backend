#!/usr/bin/env python3
"""Test 05: OOS empirical fantasy-points calibration by probability band and position."""
import json
from pathlib import Path
import numpy as np,pandas as pd
OUT=Path("research/nfl_fantasy_points_validation/results")
z=pd.read_csv(OUT/"04_distribution_recalibration_events.csv")
z=z[z.method.eq("empirical")].copy(); z["p"]=z.raw
z["band"]=pd.cut(z.p,[-.001,.20,.35,.65,1.001],labels=["lt20","20_35","35_65","65plus"])
def met(g):
 p=g.p.to_numpy(); y=g.hit.to_numpy()
 return {"n":int(len(g)),"player_games":int(g[["season","week","player_id"]].drop_duplicates().shape[0]),
 "predicted":float(p.mean()),"actual":float(y.mean()),"gap_pp":float(100*(p.mean()-y.mean())),
 "brier":float(np.mean((p-y)**2))}
summary={"test":"05_empirical_core_tail_position","market_prices_used":False,"overall":met(z),"bands":{},"positions":{}}
for b,g in z.groupby("band",observed=True): summary["bands"][str(b)]=met(g)
for pos,g in z.groupby("position"):
 summary["positions"][pos]={"overall":met(g),"bands":{str(b):met(x) for b,x in g.groupby("band",observed=True)}}
(OUT/"05_core_tail_position_summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
