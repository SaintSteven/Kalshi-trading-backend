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
# Select the actual current NFL week from the schedule, then project the current
# weekly roster. This deliberately does NOT infer the target as max(observed week)+1:
# on Sunday, Thursday's game from the same NFL week is already in player stats.
# It also does not use Kalshi markets or prices to define the player universe.
from datetime import datetime, timezone
import os
SCHED_URL='https://github.com/nflverse/nfldata/raw/master/data/games.csv'
sched=pd.read_csv(SCHED_URL,low_memory=False)
sched['gameday']=pd.to_datetime(sched['gameday'],errors='coerce').dt.date
target_date_raw=os.environ.get('NFL_TARGET_DATE','').strip()
target_date=pd.to_datetime(target_date_raw or datetime.now(timezone.utc).date().isoformat()).date()
season=2026
q=sched[(sched['gameday']==target_date) & (pd.to_numeric(sched['season'],errors='coerce')==season)]
weeks=sorted(set(pd.to_numeric(q['week'],errors='coerce').dropna().astype(int)))
if len(weeks)!=1: raise SystemExit(f'Cannot resolve one NFL week for target_date={target_date}: {weeks}')
target_week=weeks[0]
ROSTER_URL=f'https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_{season}.csv'
roster=pd.read_csv(ROSTER_URL,low_memory=False)
roster['week']=pd.to_numeric(roster['week'],errors='coerce')
roster=roster[roster['week'].eq(target_week)].copy()
id_col=next((x for x in ['gsis_id','player_id'] if x in roster.columns),None)
name_col=next((x for x in ['full_name','player_name','player_display_name'] if x in roster.columns),None)
team_col=next((x for x in ['team','recent_team'] if x in roster.columns),None)
pos_col=next((x for x in ['position','position_group'] if x in roster.columns),None)
if not all([id_col,name_col,team_col,pos_col]): raise SystemExit(f'Weekly roster schema missing required columns: {list(roster.columns)}')
roster=roster[roster[pos_col].isin(POSITIONS)].copy()
roster=roster.drop_duplicates(subset=[id_col],keep='last')
rows=[]
for rr in roster.itertuples(index=False):
 pid=getattr(rr,id_col); pos=str(getattr(rr,pos_col)); team=getattr(rr,team_col); full_name=getattr(rr,name_col)
 # Features are strictly pre-target-week. This prevents Thursday results from
 # leaking into a Sunday player's feature history while still allowing the model
 # fit itself to use all observations available at run time.
 hist=d[(d.player_id==pid) & ((d.season<season) | ((d.season==season)&(d.week<target_week)))].sort_values(['season','week'])
 if len(hist)<2: continue
 vals={}
 for cc in base:
  vals[cc+'_last']=float(hist.iloc[-1][cc])
  for ww in [3,5]: vals[f'{cc}_r{ww}']=float(hist[cc].tail(ww).mean())
 vals['fp_career_mean']=float(hist.fantasy_points.mean())
 sh=hist[hist['season'].eq(season)]
 vals['fp_season_mean']=float(sh.fantasy_points.mean()) if len(sh) else np.nan
 vals['season_games_before']=int(len(sh)); vals['career_games_before']=int(len(hist)); vals['week']=target_week
 for pp in POSITIONS: vals['pos_'+pp]=int(pos==pp)
 X=pd.DataFrame([vals],columns=features); proj=float(np.clip(m.predict(X)[0],0,None))
 prior_seasons=hist[hist['season'].lt(season)].sort_values(['season','week'])
 prior_season_final_team=prior_seasons.iloc[-1].get('recent_team',prior_seasons.iloc[-1].get('team',None)) if len(prior_seasons) else None
 entering_season_team=team
 entering_season_team_changed=bool(pd.notna(team) and pd.notna(prior_season_final_team) and str(team)!=str(prior_season_final_team))
 in_season_team_changed=False
 if len(sh):
  first_team=sh.iloc[0].get('recent_team',sh.iloc[0].get('team',None)); in_season_team_changed=bool(pd.notna(first_team) and pd.notna(team) and str(first_team)!=str(team))
 team_changed=entering_season_team_changed or in_season_team_changed
 hist_name=hist.iloc[-1].get('player_name',None)
 rows.append({'player_id':pid,'player_name':hist_name,'full_name':full_name,'position':pos,'team':team,'prior_season_final_team':prior_season_final_team,'entering_season_team':entering_season_team,'entering_season_team_changed':entering_season_team_changed,'in_season_team_changed':in_season_team_changed,'team_changed':team_changed,'projection_fp':proj,'projection_season':season,'projection_week':target_week,'history_games':len(hist),'market_prices_used':False})
o=pd.DataFrame(rows); o.to_csv(O/'09_current_slate_projections.csv',index=False)
summary={'test':'09_current_slate_projection','model':'NFL-FFPTS-RIDGE-EMPIRICAL-v1','market_prices_used':False,'training_rows':int(len(tr)),'projection_rows':int(len(o)),'latest_observed_season':int(d.season.max()),'latest_observed_week':int(d.loc[d.season.eq(d.season.max()),'week'].max()),'target_date':str(target_date),'target_week':int(target_week),'weekly_roster_rows':int(len(roster)),'full_identity_rows':int(o.full_name.notna().sum()),'team_change_rows':int(o.team_changed.sum()),'method':'Frozen Ridge architecture fit only to observed player history; next-game features use only prior observed stats. Team continuity compares the current-season entering team with the prior-season final observed team and separately flags in-season switches; continuity never alters fair value.'}
(O/'09_current_slate_projection_summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
if o.empty: raise SystemExit('No current projections generated')
