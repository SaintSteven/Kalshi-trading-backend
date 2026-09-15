#!/usr/bin/env python3
"""NFL fantasy-points historical baseline. Independent of prediction-market prices.
Scoring follows Kalshi's stated Sleeper PPR basis; initial harness uses standard
PPR stat components and emits an explicit scoring audit before model validation.
"""
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SEASONS=[2022,2023,2024,2025]
URL="https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{}.csv"
OUT=Path("research/nfl_fantasy_points_validation/results"); OUT.mkdir(parents=True,exist_ok=True)

parts=[]
for s in SEASONS:
    x=pd.read_csv(URL.format(s),low_memory=False)
    x["season"]=s
    parts.append(x)
d=pd.concat(parts,ignore_index=True)
d=d[d.position.isin(["QB","RB","WR","TE"])].copy()
for c in ["passing_yards","passing_tds","interceptions","rushing_yards","rushing_tds",
          "receptions","receiving_yards","receiving_tds","rushing_fumbles_lost","receiving_fumbles_lost","sack_fumbles_lost"]:
    if c not in d: d[c]=0
    d[c]=pd.to_numeric(d[c],errors="coerce").fillna(0)

# Sleeper PPR commonly uses: pass yds .04, pass TD 4, INT -2, rush/rec yds .1,
# rush/rec TD 6, reception 1, fumble lost -2. We record this as a scoring
# assumption to verify against Kalshi's formal rules before deployment.
fumbles=d[["rushing_fumbles_lost","receiving_fumbles_lost","sack_fumbles_lost"]].max(axis=1)
d["fantasy_points"]=(.04*d.passing_yards+4*d.passing_tds-2*d.interceptions+
                     .1*d.rushing_yards+6*d.rushing_tds+d.receptions+
                     .1*d.receiving_yards+6*d.receiving_tds-2*fumbles)

d["week"]=pd.to_numeric(d.week,errors="coerce"); d=d[d.week.notna()].copy(); d.week=d.week.astype(int)
d=d.sort_values(["player_id","season","week"]).reset_index(drop=True)
g=d.groupby("player_id",group_keys=False); gs=d.groupby(["player_id","season"],group_keys=False)
d["career_games_before"]=g.cumcount(); d["season_games_before"]=gs.cumcount()
features=[]
for c in ["fantasy_points","passing_yards","passing_tds","rushing_yards","rushing_tds","receptions","receiving_yards","receiving_tds"]:
    last=c+"_last"; d[last]=g[c].shift(1); features.append(last)
    for w in [3,5]:
        n=f"{c}_r{w}"; d[n]=g[c].transform(lambda q,w=w:q.shift(1).rolling(w,min_periods=1).mean()); features.append(n)
d["fp_career_mean"]=g.fantasy_points.transform(lambda q:q.shift(1).expanding(min_periods=1).mean())
d["fp_season_mean"]=gs.fantasy_points.transform(lambda q:q.shift(1).expanding(min_periods=1).mean())
features += ["fp_career_mean","fp_season_mean","season_games_before","career_games_before","week"]
for p in ["QB","RB","WR","TE"]:
    n="pos_"+p; d[n]=(d.position==p).astype(int); features.append(n)
d["time_key"]=d.season*100+d.week

rows=[]
for season in [2024,2025]:
  for week in sorted(d.loc[d.season.eq(season),"week"].unique()):
    key=season*100+week
    tr=d[(d.time_key<key)&(d.career_games_before>=2)&((d.season<season)|(d.season_games_before>=1))].copy()
    te=d[(d.season==season)&(d.week==week)&(d.career_games_before>=2)].copy()
    if len(tr)<100 or te.empty: continue
    m=make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),Ridge(alpha=25.0))
    m.fit(tr[features],tr.fantasy_points)
    pred=np.clip(m.predict(te[features]),0,None)
    for r,p in zip(te.itertuples(),pred):
      rows.append((season,week,r.player_id,r.player_name,r.position,float(p),float(r.fantasy_points)))
o=pd.DataFrame(rows,columns=["season","week","player_id","player_name","position","pred_fp","actual_fp"])
e=o.pred_fp-o.actual_fp
summary={"test":"00_fantasy_points_walk_forward_baseline","validation_seasons":[2024,2025],
 "market_prices_used":False,"player_games":int(len(o)),"mae":float(abs(e).mean()),
 "bias":float(e.mean()),"rmse":float(np.sqrt(np.mean(e*e))),
 "by_position":{},"scoring_status":"ASSUMPTION_PENDING_FORMAL_KALSHI_RULE_AUDIT"}
for k,z in o.groupby("position"):
 q=z.pred_fp-z.actual_fp
 summary["by_position"][k]={"n":int(len(z)),"mae":float(abs(q).mean()),"bias":float(q.mean()),"rmse":float(np.sqrt(np.mean(q*q)))}
o.to_csv(OUT/"00_walk_forward_predictions.csv",index=False)
(OUT/"00_summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
