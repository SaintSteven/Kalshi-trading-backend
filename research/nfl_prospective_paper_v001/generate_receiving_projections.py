"""Validated top-down NFL receiving probability engine.

Historical/current football reality -> Ridge receiving mean -> Normal-SD distribution
-> isotonic calibration -> freeze fair probabilities. Prediction-market prices are
never model inputs.

Candidate selected from 2024-25 walk-forward validation in the frontend research
harness. Execution policy remains prospective and is intentionally absent here.
"""
import argparse, base64, io, math, re, types, urllib.request, zlib
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

GOOD_COMMIT='1c5918dcdada82d0bcbc69cf2bd400341f1ddd71'
LEGACY_MODEL_SHA256='d301abe6efe2f3e4cb9800ccd067cdb5da9148e2b115e0ab4ff9fead5ee9d73b'
BASE=f'https://raw.githubusercontent.com/SaintSteven/Kalshi-trading-backend/{GOOD_COMMIT}/research/nfl_receiving_v0_10/payload'
MODEL_VERSION='NFL-REC-RIDGE-NORMALSD-ISO-v1'
CALIBRATION_VERSION='2024-25-WALKFORWARD-ISO-v1'
CALIBRATION_SHA256='f1dca0a32f6c0e0d09190df22eaef89665800660e6602be76d586191896978cf'
RESIDUAL_SD=23.358161397828415
TEAM_ALIAS={'LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC','JAC':'JAX','WSH':'WAS'}
VALID_POS={'WR','TE','RB','FB'}
FEATURE_BASE=['receiving_yards','targets','receptions','receiving_air_yards','target_share','air_yards_share','wopr']
FEATURES=[]
for _c in FEATURE_BASE:
    FEATURES += [f'{_c}_last',f'{_c}_r3',f'{_c}_r5']
FEATURES += ['yards_career_mean','yards_season_mean','season_games_before','career_games_before','week','pos_WR','pos_TE','pos_RB','pos_FB']

# Exact exported isotonic map from the completed historical harness, zlib+base64.
_CAL_B64='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'

def recover_identity_helpers():
    import hashlib
    parts=[]
    for name in ('b64_00.part','b64_01.part','b64_02.part'):
        with urllib.request.urlopen(f'{BASE}/{name}',timeout=30) as r:
            parts.append(r.read().decode('ascii').strip())
    source=zlib.decompress(base64.b64decode(''.join(parts).encode('ascii'))).decode('utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=LEGACY_MODEL_SHA256:
        raise RuntimeError('identity-helper source checksum mismatch')
    mod=types.ModuleType('legacy_identity_helpers')
    exec(compile(source,'<legacy_identity_helpers>','exec'),mod.__dict__)
    return mod,mod.load_projection_namespace()

def load_calibration():
    path=Path(__file__).with_name('validated_isotonic_map.csv')
    raw=path.read_text()
    import hashlib
    if hashlib.sha256(raw.encode()).hexdigest()!=CALIBRATION_SHA256:
        raise RuntimeError('calibration artifact checksum mismatch')
    df=pd.read_csv(io.StringIO(raw))
    return df.raw_prob.to_numpy(float),df.calibrated_prob.to_numpy(float)

def team_code(x):
    return TEAM_ALIAS.get(str(x).upper(),str(x).upper())

def parse_name(label):
    return str(label or '').split(':',1)[0].strip()

def parse_threshold(label,ticker):
    m=re.search(r':\s*(\d+)\+?\s*receiving',str(label or ''),re.I)
    if m:return int(m.group(1))
    m=re.search(r'-(\d+)$',str(ticker or ''))
    return int(m.group(1)) if m else None

def market_team(player_key,game):
    teams=[team_code(x) for x in str(game or '').split('@') if x]
    key=str(player_key or '').upper()
    hits=[t for t in teams if key.startswith(t)]
    return hits[0] if len(hits)==1 else ''

def normal_sf(z):
    return .5*math.erfc(z/math.sqrt(2.0))

def prepare_history(players):
    d=players.copy()
    if 'team' not in d.columns and 'recent_team' in d.columns:d['team']=d['recent_team']
    d=d[d['position'].astype(str).str.upper().isin(VALID_POS)].copy()
    d['season']=pd.to_numeric(d['season'],errors='coerce')
    d['week']=pd.to_numeric(d['week'],errors='coerce')
    d=d[d.season.notna()&d.week.notna()].copy()
    d['season']=d.season.astype(int);d['week']=d.week.astype(int)
    for c in FEATURE_BASE:
        if c not in d.columns:d[c]=np.nan
        d[c]=pd.to_numeric(d[c],errors='coerce')
    d['receiving_yards']=d.receiving_yards.fillna(0.0)
    d=d.sort_values(['player_id','season','week']).reset_index(drop=True)
    g=d.groupby('player_id',group_keys=False);gs=d.groupby(['player_id','season'],group_keys=False)
    d['career_games_before']=g.cumcount();d['season_games_before']=gs.cumcount()
    for c in FEATURE_BASE:
        d[f'{c}_last']=g[c].shift(1)
        d[f'{c}_r3']=g[c].transform(lambda s:s.shift(1).rolling(3,min_periods=1).mean())
        d[f'{c}_r5']=g[c].transform(lambda s:s.shift(1).rolling(5,min_periods=1).mean())
    d['yards_career_mean']=g.receiving_yards.transform(lambda s:s.shift(1).expanding(min_periods=1).mean())
    d['yards_season_mean']=gs.receiving_yards.transform(lambda s:s.shift(1).expanding(min_periods=1).mean())
    for p in VALID_POS:d[f'pos_{p}']=(d.position.astype(str).str.upper()==p).astype(int)
    d['time_key']=d.season*100+d.week
    return d

def fit_model(d,target_key):
    train=d[(d.time_key<target_key)&(d.career_games_before>=2)&(d.season_games_before>=1)].copy()
    if len(train)<500:raise RuntimeError(f'insufficient Ridge training rows: {len(train)}')
    model=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=25.0))
    model.fit(train[FEATURES],train.receiving_yards.to_numpy(float))
    return model,len(train)

def target_features(pr,season,week):
    h=pr[(pr.season*100+pr.week)<(season*100+week)].sort_values(['season','week']).copy()
    if not len(h):return None
    latest=h.iloc[-1];row={}
    for c in FEATURE_BASE:
        vals=pd.to_numeric(h[c],errors='coerce')
        row[f'{c}_last']=vals.iloc[-1]
        row[f'{c}_r3']=vals.tail(3).mean()
        row[f'{c}_r5']=vals.tail(5).mean()
    row['yards_career_mean']=pd.to_numeric(h.receiving_yards,errors='coerce').mean()
    sh=h[h.season==season]
    row['yards_season_mean']=pd.to_numeric(sh.receiving_yards,errors='coerce').mean() if len(sh) else np.nan
    row['season_games_before']=len(sh);row['career_games_before']=len(h);row['week']=week
    pos=str(latest.get('position','')).upper()
    for p in VALID_POS:row[f'pos_{p}']=1 if pos==p else 0
    return row,pos,latest

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--markets',required=True);ap.add_argument('--projections',required=True);ap.add_argument('--mapping',required=True)
    ap.add_argument('--simulations',type=int,default=0,help='Ignored; retained for workflow compatibility.')
    a=ap.parse_args()
    markets=pd.read_csv(a.markets,dtype=str).fillna('')
    markets=markets[markets.prop_family.eq('receiving_yards')].copy()
    if not len(markets):
        Path(a.projections).parent.mkdir(parents=True,exist_ok=True)
        pd.DataFrame().to_csv(a.projections,index=False);pd.DataFrame().to_csv(a.mapping,index=False);return

    helper,ns=recover_identity_helpers()
    players,teams,loaded=ns['load_history']([2022,2023,2024,2025,2026])
    d=prepare_history(players)
    cal_x,cal_y=load_calibration()
    generated=datetime.now(timezone.utc).isoformat()
    models={};train_counts={}
    projections=[];mapping=[];cache={}

    for _,m in markets.iterrows():
        name=parse_name(m.player_name);threshold=parse_threshold(m.player_name,m.market_ticker)
        team=market_team(m.player_key,m.game)
        try:season=int(m.season);week=int(m.week)
        except:continue
        if not name or threshold is None or not team:continue
        try:
            resolved=ns['resolve_player'](players,name,team=None)
            ids=resolved['player_id'].astype(str).dropna().unique().tolist() if 'player_id' in resolved.columns else []
            if len(ids)!=1:continue
            pid=ids[0]
            mapping.append({'market_ticker':m.market_ticker,'game_id':m.game_id,'player_id':pid,'threshold':threshold})
            key=(season,week,pid,team)
            if key not in cache:
                target_key=season*100+week
                if target_key not in models:
                    models[target_key],train_counts[target_key]=fit_model(d,target_key)
                pr=d[d.player_id.astype(str)==pid].copy()
                tf=target_features(pr,season,week)
                if tf is None:continue
                feat,pos,latest=tf
                mu=max(0.0,float(models[target_key].predict(pd.DataFrame([feat],columns=FEATURES))[0]))
                latest_team=team_code(latest.get('team',''))
                prior_targets=float(feat.get('targets_r3',np.nan))
                season_games=int(feat['season_games_before']);career_games=int(feat['career_games_before'])
                blocking=[];flags=[]
                if pos not in VALID_POS:blocking.append('POSITION_QC')
                if career_games<2:blocking.append('INSUFFICIENT_CAREER_HISTORY')
                if season_games<1:blocking.append('CURRENT_SEASON_HISTORY_UNVALIDATED')
                if latest_team and latest_team!=team:blocking.append('TEAM_CHANGE_UNVERIFIED')
                if pos=='WR' and math.isfinite(prior_targets) and prior_targets>=7:
                    flags.append('HIGH_VOLUME_WR_MODEL_HISTORICALLY_CONSERVATIVE')
                cache[key]=(mu,pos,prior_targets,season_games,career_games,blocking,flags)

            mu,pos,prior_targets,season_games,career_games,blocking,flags=cache[key]
            if threshold<30 or threshold>150:blocking=list(blocking)+['THRESHOLD_OUTSIDE_VALIDATED_30_150_RANGE']
            raw=float(normal_sf((float(threshold)-mu)/RESIDUAL_SD))
            fair=float(np.interp(raw,cal_x,cal_y))
            qc='PASS' if not blocking else 'WARN'
            projections.append({
                'game_id':m.game_id,'player_id':pid,'threshold':threshold,
                'fair_yes':fair,'raw_fair_yes':raw,'model_version':MODEL_VERSION,
                'calibration_version':CALIBRATION_VERSION,'calibration_sha256':CALIBRATION_SHA256,
                'generated_at':generated,'projection':mu,'residual_sd':RESIDUAL_SD,
                'position':pos,'games_used':career_games,'season_games_before':season_games,
                'prior_targets_r3':prior_targets,'training_rows':train_counts[season*100+week],
                'qc_status':qc,'qc_reason':'|'.join(blocking),'context_flags':'|'.join(flags)
            })
        except Exception:
            continue

    mp=pd.DataFrame(mapping).drop_duplicates(['market_ticker'],keep='first') if mapping else pd.DataFrame(columns=['market_ticker','game_id','player_id','threshold'])
    pp=pd.DataFrame(projections).drop_duplicates(['game_id','player_id','threshold'],keep='first') if projections else pd.DataFrame(columns=['game_id','player_id','threshold','fair_yes','model_version','generated_at','qc_status'])
    Path(a.mapping).parent.mkdir(parents=True,exist_ok=True)
    mp.to_csv(a.mapping,index=False);pp.to_csv(a.projections,index=False)
    print({
        'model_version':MODEL_VERSION,'history_seasons':loaded,'receiving_markets':len(markets),
        'mapped_markets':len(mp),'projection_rows':len(pp),'unique_player_games':len(cache),
        'pass_rows':int((pp.qc_status=='PASS').sum()) if len(pp) else 0,
        'warn_rows':int((pp.qc_status=='WARN').sum()) if len(pp) else 0,
        'calibration_sha256':CALIBRATION_SHA256,'residual_sd':RESIDUAL_SD
    })

if __name__=='__main__':main()
