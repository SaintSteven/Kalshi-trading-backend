#!/usr/bin/env python3
"""Test 07: historical team-change diagnostic for OOS fantasy-points empirical engine."""
import json
from pathlib import Path
import numpy as np,pandas as pd
OUT=Path("research/nfl_fantasy_points_validation/results")
URL="https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{}.csv"
raw=pd.concat([pd.read_csv(URL.format(s),low_memory=False).assign(season=s) for s in [2022,2023,2024,2025]],ignore_index=True)
raw=raw[raw.position.isin(["QB","RB","WR","TE"])].copy()
teamcol=next((c for c in ["recent_team","team"] if c in raw.columns),None)
if teamcol is None: raise RuntimeError("No historical team column available")
raw["week"]=pd.to_numeric(raw.week,errors="coerce"); raw=raw[raw.week.notna()].copy(); raw.week=raw.week.astype(int)
raw=raw.sort_values(["player_id","season","week"])
raw["prior_team"]=raw.groupby("player_id")[teamcol].shift(1)
raw["team_changed"]=(raw["prior_team"].notna() & raw[teamcol].notna() & raw["prior_team"].ne(raw[teamcol]))
ctx=raw[["season","week","player_id",teamcol,"prior_team","team_changed"]].drop_duplicates(["season","week","player_id"])
z=pd.read_csv(OUT/"04_distribution_recalibration_events.csv")
z=z[z.method.eq("empirical")].copy(); z["p"]=z.raw
z=z.merge(ctx,on=["season","week","player_id"],how="left"); z["team_changed"]=z.team_changed.fillna(False)
z["week1"]=z.week.eq(1)
def met(g):
 p=g.p.to_numpy(); y=g.hit.to_numpy()
 return {"n":int(len(g)),"player_games":int(g[["season","week","player_id"]].drop_duplicates().shape[0]),
 "predicted":float(p.mean()),"actual":float(y.mean()),"gap_pp":float(100*(p.mean()-y.mean())),
 "brier":float(np.mean((p-y)**2))}
summary={"test":"07_team_change_context","market_prices_used":False,"team_column":teamcol,
 "changed":met(z[z.team_changed]),"unchanged":met(z[~z.team_changed]),
 "week1_changed":met(z[z.team_changed & z.week1]) if len(z[z.team_changed & z.week1]) else None,
 "by_position":{}}
for pos,g in z.groupby("position"):
 summary["by_position"][pos]={"changed":met(g[g.team_changed]) if len(g[g.team_changed]) else None,
                              "unchanged":met(g[~g.team_changed])}
(OUT/"07_team_change_context_summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
