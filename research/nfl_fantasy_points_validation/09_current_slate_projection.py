#!/usr/bin/env python3
"""Generate independent current-slate NFL fantasy-point projections.

Fits the frozen Ridge(alpha=25) architecture on all PRIOR observed NFL games and
constructs each current player's features strictly from observed history. Kalshi
prices are never loaded here. Output is consumed by Test 08 only after it is
fully frozen.
"""
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

R=Path('research/nfl_fantasy_points_validation'); O=R/'results'; O.mkdir(parents=True,exist_ok=True)
URL='https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{}.csv'
POSITIONS=['QB','RB','WR','TE']; HIST=[2022,2023,2024,2025,2026]
parts=[]
for s in HIST:
 try:
  x=pd.read_csv(URL.format(s),low_memory=False); x['season']=s; parts.append(x)
 except Exception as e:
  print(f'INFO season {s} unavailable: {e}')
d=pd.concat(parts,ignore_index=True); d=d[d.position.isin(POSITIONS)].copy()
stat=['passing_yards','passing_tds','interceptions','rushing_yards','rushing_tds','receptions','receiving_yards','receiving_tds','rushing_fumbles_lost','receiving_fumbles_lost','sack_fumbles_lost']
for c in stat:
 if c not in d: d[c]=0
 d[c]=pd.to_numeric(d[c],errors='coerce').fillna(0)
fumbles=d[['rushing_fumbles_lost','receiving_fumbles_lost','sack_fumbles_lost']].max(axis=1)
d['fantasy_points']=(.04*d.passing_yards+4*d.passing_tds-d.interceptions+.1*d.rushing_yards+6*d.rushing_tds+d.receptions+.1*d.receiving_yards+6*d.receiving_tds-2*fumbles)
d['week']=pd.to_numeric(d.week,errors='coerce'); d=d[d.week.notna()].copy(); d.week=d.week.astype(int)
d=d.sort_values(['player_id','season','week']).reset_index(drop=True)
g=d.groupby('player_id',group_keys=False); gs=d.groupby(['player_id','season'],group_keys=False)
d['career_games_before']=g.cumcount(); d['season_games_before']=gs.cumcount(); features=[]
base=['fantasy_points','passing_yards','passing_tds','rushing_yards','rushing_tds','receptions','receiving_yards','receiving_tds']
for c in base:
 d[c+'_last']=g[c].shift(1); features.append(c+'_last')
 for w in [3,5]:
  n=f'{c}_r{w}'; d[n]=g[c].transform(lambda q,w=w:q.shift(1).rolling(w,min_periods=1).mean()); features.append(n)
d['fp_career_mean']=g.fantasy_points.transform(lambda q:q.shift(1).expanding(min_periods=1).mean())
d['fp_season_mean']=gs.fantasy_points.transform(lambda q:q.shift(1).expanding(min_periods=1).mean())
features += ['fp_career_mean','fp_season_mean','season_games_before','career_games_before','week']
for p in POSITIONS:
 n='pos_'+p; d[n]=(d.position==p).astype(int); features.append(n)
tr=d[(d.career_games_before>=2)].copy()
m=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=25.0)); m.fit(tr[features],tr.fantasy_points)
# Project the current NFL slate, not merely the game after each player's latest
# historical appearance. Players with no current-season observation must not
# masquerade as current projections (e.g. a 2025 postseason row -> 2025 W23).
current_season=int(d.season.max())
current_week=int(d.loc[d.season.eq(current_season),'week'].max())
latest=d.sort_values(['season','week']).groupby('player_id',as_index=False).tail(1).copy()
latest=latest[(latest.season==current_season) & (latest.week==current_week)].copy()
rows=[]
for r in latest.itertuples():
 hist=d[d.player_id==r.player_id].sort_values(['season','week'])
 if len(hist)<2: continue
 season=current_season; next_week=current_week+1
 vals={}
 for c in base:
  vals[c+'_last']=float(hist.iloc[-1][c])
  for w in [3,5]: vals[f'{c}_r{w}']=float(hist[c].tail(w).mean())
 vals['fp_career_mean']=float(hist.fantasy_points.mean())
 sh=hist[hist.season==season]; vals['fp_season_mean']=float(sh.fantasy_points.mean())
 vals['season_games_before']=int(len(sh)); vals['career_games_before']=int(len(hist)); vals['week']=next_week
 for p in POSITIONS: vals['pos_'+p]=int(r.position==p)
 X=pd.DataFrame([vals],columns=features); proj=float(np.clip(m.predict(X)[0],0,None))
 full_name=getattr(r,'player_display_name',None)
 if full_name is None or pd.isna(full_name): full_name=getattr(r,'player_name',None)
 team=getattr(r,'recent_team',getattr(r,'team',None))
 # Continuity QC is descriptive only; it never changes projection/fair value.
 current_season=hist[hist.season==season].sort_values('week')
 prior_seasons=hist[hist.season<season].sort_values(['season','week'])
 entering_team=current_season.iloc[0].get('recent_team',current_season.iloc[0].get('team',None)) if len(current_season) else team
 prior_season_final_team=prior_seasons.iloc[-1].get('recent_team',prior_seasons.iloc[-1].get('team',None)) if len(prior_seasons) else None
 entering_season_team_changed=bool(pd.notna(entering_team) and pd.notna(prior_season_final_team) and str(entering_team)!=str(prior_season_final_team))
 in_season_team_changed=bool(pd.notna(team) and pd.notna(entering_team) and str(team)!=str(entering_team))
 team_changed=entering_season_team_changed or in_season_team_changed
 rows.append({'player_id':r.player_id,'player_name':r.player_name,'full_name':full_name,'position':r.position,'team':team,'prior_season_final_team':prior_season_final_team,'entering_season_team':entering_team,'entering_season_team_changed':entering_season_team_changed,'in_season_team_changed':in_season_team_changed,'team_changed':team_changed,'projection_fp':proj,'projection_season':season,'projection_week':next_week,'history_games':len(hist),'market_prices_used':False})
o=pd.DataFrame(rows); o.to_csv(O/'09_current_slate_projections.csv',index=False)
summary={'test':'09_current_slate_projection','model':'NFL-FFPTS-RIDGE-EMPIRICAL-v1','market_prices_used':False,'training_rows':int(len(tr)),'projection_rows':int(len(o)),'latest_observed_season':int(d.season.max()),'latest_observed_week':int(d.loc[d.season.eq(d.season.max()),'week'].max()),'full_identity_rows':int(o.full_name.notna().sum()),'team_change_rows':int(o.team_changed.sum()),'method':'Frozen Ridge architecture fit only to observed player history; next-game features use only prior observed stats. Team continuity compares the current-season entering team with the prior-season final observed team and separately flags in-season switches; continuity never alters fair value.'}
(O/'09_current_slate_projection_summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
if o.empty: raise SystemExit('No current projections generated')
