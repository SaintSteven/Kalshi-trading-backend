"""Validated top-down NFL rushing probability engine.
Football reality -> Ridge rushing mean -> Normal-SD -> isotonic calibration -> freeze.
Prediction-market prices are never model inputs.
"""
import argparse, base64, io, math, urllib.request, zlib, re, types, hashlib
from pathlib import Path
from datetime import datetime, timezone
import numpy as np, pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

GOOD_COMMIT='1c5918dcdada82d0bcbc69cf2bd400341f1ddd71'
LEGACY_MODEL_SHA256='d301abe6efe2f3e4cb9800ccd067cdb5da9148e2b115e0ab4ff9fead5ee9d73b'
BASE=f'https://raw.githubusercontent.com/SaintSteven/Kalshi-trading-backend/{GOOD_COMMIT}/research/nfl_receiving_v0_10/payload'
MODEL_VERSION='NFL-RUSH-RIDGE-NORMALSD-ISO-v1'
CALIBRATION_VERSION='2024-25-WALKFORWARD-ISO-v1'
CALIBRATION_SHA256='8be61f9f4442b75fbacd335b0997d4ea8e5b412930fea99d98c12040deaa1086'
RESIDUAL_SD=17.499131191796593
CAL_B64='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'
TEAM_ALIAS={'LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC','JAC':'JAX','WSH':'WAS'}
VALID_POS={'QB','RB','FB','WR'}
FEATURE_BASE=['rushing_yards','carries','rushing_tds']
FEATURES=[]
for c in FEATURE_BASE: FEATURES += [f'{c}_last',f'{c}_r3',f'{c}_r5']
FEATURES += ['yards_career_mean','yards_season_mean','season_games_before','career_games_before','week','pos_QB','pos_RB','pos_FB','pos_WR']

def recover_helpers():
    parts=[]
    for name in ('b64_00.part','b64_01.part','b64_02.part'):
        with urllib.request.urlopen(f'{BASE}/{name}',timeout=30) as r: parts.append(r.read().decode().strip())
    src=zlib.decompress(base64.b64decode(''.join(parts))).decode()
    if hashlib.sha256(src.encode()).hexdigest()!=LEGACY_MODEL_SHA256: raise RuntimeError('identity checksum mismatch')
    mod=types.ModuleType('legacy');exec(compile(src,'<legacy>','exec'),mod.__dict__)
    return mod.load_projection_namespace()

def calibration():
    raw=zlib.decompress(base64.b64decode(CAL_B64)).decode()
    if hashlib.sha256(raw.encode()).hexdigest()!=CALIBRATION_SHA256: raise RuntimeError('calibration checksum mismatch')
    x=pd.read_csv(io.StringIO(raw));return x.raw_prob.to_numpy(float),x.calibrated_prob.to_numpy(float)

def team_code(x): return TEAM_ALIAS.get(str(x).upper(),str(x).upper())
def parse_name(x): return str(x or '').split(':',1)[0].strip()
def parse_threshold(label,ticker):
    m=re.search(r':\s*(\d+)\+?\s*rushing',str(label or ''),re.I)
    if m:return int(m.group(1))
    m=re.search(r'-(\d+)$',str(ticker or ''));return int(m.group(1)) if m else None
def market_team(key,game):
    teams=[team_code(x) for x in str(game or '').split('@') if x];k=str(key or '').upper();h=[t for t in teams if k.startswith(t)]
    return h[0] if len(h)==1 else ''
def normal_sf(z): return .5*math.erfc(z/math.sqrt(2))

def prepare(players):
    d=players.copy()
    if 'team' not in d.columns and 'recent_team' in d.columns:d['team']=d.recent_team
    d=d[d.position.astype(str).str.upper().isin(VALID_POS)].copy()
    d['season']=pd.to_numeric(d.season,errors='coerce');d['week']=pd.to_numeric(d.week,errors='coerce')
    d=d[d.season.notna()&d.week.notna()].copy();d.season=d.season.astype(int);d.week=d.week.astype(int)
    for c in FEATURE_BASE:
        if c not in d.columns:d[c]=np.nan
        d[c]=pd.to_numeric(d[c],errors='coerce')
    d.rushing_yards=d.rushing_yards.fillna(0)
    d=d.sort_values(['player_id','season','week']).reset_index(drop=True)
    g=d.groupby('player_id',group_keys=False);gs=d.groupby(['player_id','season'],group_keys=False)
    d['career_games_before']=g.cumcount();d['season_games_before']=gs.cumcount()
    for c in FEATURE_BASE:
        d[f'{c}_last']=g[c].shift(1)
        d[f'{c}_r3']=g[c].transform(lambda s:s.shift(1).rolling(3,min_periods=1).mean())
        d[f'{c}_r5']=g[c].transform(lambda s:s.shift(1).rolling(5,min_periods=1).mean())
    d['yards_career_mean']=g.rushing_yards.transform(lambda s:s.shift(1).expanding(min_periods=1).mean())
    d['yards_season_mean']=gs.rushing_yards.transform(lambda s:s.shift(1).expanding(min_periods=1).mean())
    for p in VALID_POS:d[f'pos_{p}']=(d.position.astype(str).str.upper()==p).astype(int)
    d['time_key']=d.season*100+d.week
    return d

def fit_model(d,key):
    tr=d[(d.time_key<key)&(d.career_games_before>=2)&(d.season_games_before>=1)].copy()
    if len(tr)<500: raise RuntimeError('insufficient training rows')
    model=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=25.0))
    model.fit(tr[FEATURES],tr.rushing_yards.to_numpy(float));return model,len(tr)

def features(pr,season,week):
    h=pr[(pr.season*100+pr.week)<(season*100+week)].sort_values(['season','week']).copy()
    if not len(h):return None
    latest=h.iloc[-1];row={}
    for c in FEATURE_BASE:
        v=pd.to_numeric(h[c],errors='coerce');row[f'{c}_last']=v.iloc[-1];row[f'{c}_r3']=v.tail(3).mean();row[f'{c}_r5']=v.tail(5).mean()
    row['yards_career_mean']=pd.to_numeric(h.rushing_yards,errors='coerce').mean()
    sh=h[h.season==season];row['yards_season_mean']=pd.to_numeric(sh.rushing_yards,errors='coerce').mean() if len(sh) else np.nan
    row['season_games_before']=len(sh);row['career_games_before']=len(h);row['week']=week
    pos=str(latest.position).upper()
    for p in VALID_POS:row[f'pos_{p}']=1 if pos==p else 0
    prior=h[h.season==season-1]
    pc=float(pd.to_numeric(prior.carries,errors='coerce').fillna(0).sum()) if len(prior) else 0
    pyg=float(pd.to_numeric(prior.rushing_yards,errors='coerce').fillna(0).sum()) if len(prior) else 0
    pg=int(prior.week.nunique()) if len(prior) else 0
    return row,pos,latest,pc,(pc/pg if pg else 0),(pyg/pg if pg else 0)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--markets',required=True);ap.add_argument('--projections',required=True);ap.add_argument('--mapping',required=True);a=ap.parse_args()
    markets=pd.read_csv(a.markets,dtype=str).fillna('');markets=markets[markets.prop_family.eq('rushing_yards')].copy()
    ns=recover_helpers();players,teams,loaded=ns['load_history']([2022,2023,2024,2025,2026]);d=prepare(players);cx,cy=calibration()
    generated=datetime.now(timezone.utc).isoformat();models={};counts={};cache={};projections=[];mapping=[]
    for _,m in markets.iterrows():
        name=parse_name(m.player_name);thr=parse_threshold(m.player_name,m.market_ticker);team=market_team(m.player_key,m.game)
        try:season=int(m.season);week=int(m.week)
        except:continue
        if not name or thr is None or not team:continue
        try:
            resolved=ns['resolve_player'](players,name,team=None);ids=resolved.player_id.astype(str).dropna().unique().tolist() if 'player_id' in resolved.columns else []
            if len(ids)!=1:continue
            pid=ids[0];mapping.append({'market_ticker':m.market_ticker,'game_id':m.game_id,'player_id':pid,'threshold':thr})
            key=(season,week,pid,team)
            if key not in cache:
                tkey=season*100+week
                if tkey not in models:models[tkey],counts[tkey]=fit_model(d,tkey)
                tf=features(d[d.player_id.astype(str)==pid],season,week)
                if tf is None:continue
                feat,pos,latest,prior_carries,prior_cpg,prior_ypg=tf
                mu=max(0,float(models[tkey].predict(pd.DataFrame([feat],columns=FEATURES))[0]))
                latest_team=team_code(latest.get('team',''));sg=int(feat['season_games_before']);cg=int(feat['career_games_before'])
                blocking=[];flags=[]
                if pos not in VALID_POS:blocking.append('POSITION_QC')
                if cg<2:blocking.append('INSUFFICIENT_CAREER_HISTORY')
                if latest_team and latest_team!=team:flags.append('TEAM_CHANGE_OR_NEW_TEAM')
                if week==1:
                    flags.append('WEEK1_RUSHING_CONTEXT')
                    if pos=='QB':
                        flags.append('WEEK1_QB_RUSHING_REVIEW')
                        if prior_cpg>=4 or prior_ypg>=20:flags.append('MOBILE_QB_WEEK1_UNDERESTIMATION_RISK')
                    elif pos=='RB':
                        if latest_team and latest_team!=team:flags.append('WEEK1_CHANGED_TEAM_REVIEW')
                        elif prior_carries>=100:flags.append('WEEK1_ESTABLISHED_RETURNING_RB')
                        else:flags.append('WEEK1_ROLE_NOT_ESTABLISHED_REVIEW')
                    else:flags.append('WEEK1_NON_RB_RESEARCH_ONLY')
                cache[key]=(mu,pos,sg,cg,prior_carries,prior_cpg,prior_ypg,blocking,flags)
            mu,pos,sg,cg,pc,pcpg,pypg,blocking,flags=cache[key]
            if thr<10 or thr>150:blocking=list(blocking)+['THRESHOLD_OUTSIDE_VALIDATED_10_150_RANGE']
            if thr<=30:flags=list(flags)+['LOW_THRESHOLD_10_30_CALIBRATION_CAUTION']
            raw=normal_sf((float(thr)-mu)/RESIDUAL_SD);fair=float(np.interp(raw,cx,cy));qc='PASS' if not blocking else 'WARN'
            projections.append({'game_id':m.game_id,'player_id':pid,'threshold':thr,'fair_yes':fair,'raw_fair_yes':raw,
                'model_version':MODEL_VERSION,'calibration_version':CALIBRATION_VERSION,'calibration_sha256':CALIBRATION_SHA256,
                'generated_at':generated,'projection':mu,'residual_sd':RESIDUAL_SD,'position':pos,'season_games_before':sg,
                'games_used':cg,'prior_carries':pc,'prior_carries_pg':pcpg,'prior_rush_yds_pg':pypg,'training_rows':counts[season*100+week],
                'qc_status':qc,'qc_reason':'|'.join(blocking),'context_flags':'|'.join(flags)})
        except Exception:continue
    mp=pd.DataFrame(mapping).drop_duplicates(['market_ticker'],keep='first') if mapping else pd.DataFrame(columns=['market_ticker','game_id','player_id','threshold'])
    pp=pd.DataFrame(projections).drop_duplicates(['game_id','player_id','threshold'],keep='first') if projections else pd.DataFrame()
    Path(a.mapping).parent.mkdir(parents=True,exist_ok=True);mp.to_csv(a.mapping,index=False);pp.to_csv(a.projections,index=False)
    print({'model_version':MODEL_VERSION,'history_seasons':loaded,'rushing_markets':len(markets),'mapped_markets':len(mp),
      'projection_rows':len(pp),'pass_rows':int((pp.qc_status=='PASS').sum()) if len(pp) else 0,'warn_rows':int((pp.qc_status=='WARN').sum()) if len(pp) else 0,
      'week1_flagged':int(pp.context_flags.astype(str).str.contains('WEEK1_').sum()) if len(pp) else 0,'residual_sd':RESIDUAL_SD})

if __name__=='__main__':main()
