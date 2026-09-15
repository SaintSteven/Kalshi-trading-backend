#!/usr/bin/env python3
"""Audit availability/impact of rare Sleeper PPR scoring components in nflverse weekly data."""
import json
from pathlib import Path
import pandas as pd

SEASONS=[2022,2023,2024,2025]
URL="https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{}.csv"
OUT=Path("research/nfl_fantasy_points_validation/results"); OUT.mkdir(parents=True,exist_ok=True)
d=pd.concat([pd.read_csv(URL.format(s),low_memory=False).assign(season=s) for s in SEASONS],ignore_index=True)
d=d[d.position.isin(["QB","RB","WR","TE"])].copy()
candidates=[c for c in d.columns if any(k in c.lower() for k in ["two_point","2pt","conversion","return_touchdown","special_teams","fumble_recovery"])]
audit={"test":"01_scoring_component_audit","seasons":SEASONS,"candidate_columns":{}}
for c in candidates:
    x=pd.to_numeric(d[c],errors="coerce")
    audit["candidate_columns"][c]={"non_null":int(x.notna().sum()),"nonzero":int((x.fillna(0)!=0).sum()),"sum":float(x.fillna(0).sum())}
audit["note"]="Core Sleeper PPR scoring uses pass yards .04, pass TD 4, INT -1, rush/receive yards .1, rush/receive TD 6, reception 1, fumble lost -2. This audit identifies whether rare scoring components can be reconstructed from the historical weekly source."
(OUT/"01_scoring_audit.json").write_text(json.dumps(audit,indent=2))
print(json.dumps(audit,indent=2))
