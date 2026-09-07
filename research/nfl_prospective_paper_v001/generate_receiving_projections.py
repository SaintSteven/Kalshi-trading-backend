"""Generate research-only 2026 receiving projections from the exact validated v0.10 source.
Kalshi prices are never model inputs. Ambiguous identity/team changes fail closed.
"""
import argparse, base64, math, re, types, urllib.request, zlib
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

GOOD_COMMIT='1c5918dcdada82d0bcbc69cf2bd400341f1ddd71'
MODEL_SHA256='d301abe6efe2f3e4cb9800ccd067cdb5da9148e2b115e0ab4ff9fead5ee9d73b'
MODEL_VERSION='NFL-REC-v0.10-recovered'
BASE=f'https://raw.githubusercontent.com/SaintSteven/Kalshi-trading-backend/{GOOD_COMMIT}/research/nfl_receiving_v0_10/payload'
TEAM_ALIAS={'LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC','JAC':'JAX','WSH':'WAS'}

def recover_model():
    import hashlib
    parts=[]
    for name in ('b64_00.part','b64_01.part','b64_02.part'):
        with urllib.request.urlopen(f'{BASE}/{name}',timeout=30) as r:parts.append(r.read().decode('ascii').strip())
    source=zlib.decompress(base64.b64decode(''.join(parts).encode('ascii'))).decode('utf-8')
    sha=hashlib.sha256(source.encode()).hexdigest()
    if sha!=MODEL_SHA256:raise RuntimeError(f'model checksum mismatch {sha}')
    mod=types.ModuleType('validated_nfl_receiving_v010');mod.__file__='<recovered-v010>';exec(compile(source,mod.__file__,'exec'),mod.__dict__)
    return mod

def team_code(x):return TEAM_ALIAS.get(str(x).upper(),str(x).upper())

def parse_name(label):
    s=str(label or '').split(':',1)[0].strip()
    return s

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

def opponent(team,game):
    teams=[team_code(x) for x in str(game or '').split('@') if x]
    other=[t for t in teams if t!=team]
    return other[0] if len(other)==1 else ''

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--markets',required=True);ap.add_argument('--projections',required=True);ap.add_argument('--mapping',required=True);ap.add_argument('--simulations',type=int,default=5000);a=ap.parse_args()
    markets=pd.read_csv(a.markets,dtype=str).fillna('');markets=markets[markets.prop_family.eq('receiving_yards')].copy()
    mod=recover_model();ns=mod.load_projection_namespace()
    players,teams,loaded=ns['load_history']([2024,2025,2026])
    generated=datetime.now(timezone.utc).isoformat();projections=[];mapping=[];cache={}
    for _,m in markets.iterrows():
        name=parse_name(m.player_name);threshold=parse_threshold(m.player_name,m.market_ticker);team=market_team(m.player_key,m.game);opp=opponent(team,m.game)
        if not name or threshold is None or not team or not opp:continue
        try:
            pr=ns['resolve_player'](players,name,team=None)
            ids=pr['player_id'].astype(str).dropna().unique().tolist() if 'player_id' in pr.columns else []
            if len(ids)!=1:continue
            pid=ids[0]
            mapping.append({'market_ticker':m.market_ticker,'game_id':m.game_id,'player_id':pid,'threshold':threshold})
            key=(m.game_id,pid,team,opp)
            if key not in cache:
                latest=pr.sort_values(['season','week']).iloc[-1]
                latest_team=team_code(latest.get('team',''))
                p=mod.make_projection(ns,pr,teams,team,opp,a.simulations,20260907+(abs(hash('|'.join(map(str,key))))%100000))
                qc='PASS';reason=''
                if latest_team and latest_team!=team:qc='WARN';reason='TEAM_CHANGE_UNVERIFIED'
                if int(p.get('games_used',0))<4:qc='WARN';reason=reason or 'THIN_SAMPLE'
                if str(p.get('position','')).upper() not in {'WR','TE','RB','FB'}:qc='WARN';reason=reason or 'POSITION_QC'
                cache[key]=(p,qc,reason)
            p,qc,reason=cache[key]
            raw=p['probs'].get(int(threshold))
            if raw is None or not math.isfinite(float(raw)):continue
            fair=mod.calibrate_probability(p.get('position',''),float(raw))
            projections.append({'game_id':m.game_id,'player_id':pid,'threshold':threshold,'fair_yes':fair,'raw_fair_yes':float(raw),'model_version':MODEL_VERSION,'model_source_sha256':MODEL_SHA256,'generated_at':generated,'projection':p.get('projected_receiving_yards',''),'position':p.get('position',''),'games_used':p.get('games_used',''),'role_certainty':p.get('role_certainty',''),'qc_status':qc,'qc_reason':reason})
        except Exception:
            continue
    mp=pd.DataFrame(mapping).drop_duplicates(['market_ticker'],keep='first') if mapping else pd.DataFrame(columns=['market_ticker','game_id','player_id','threshold'])
    pp=pd.DataFrame(projections).drop_duplicates(['game_id','player_id','threshold'],keep='first') if projections else pd.DataFrame(columns=['game_id','player_id','threshold','fair_yes','model_version','generated_at','qc_status'])
    Path(a.mapping).parent.mkdir(parents=True,exist_ok=True);mp.to_csv(a.mapping,index=False);pp.to_csv(a.projections,index=False)
    print({'loaded_seasons':loaded,'receiving_markets':len(markets),'mapped_markets':len(mp),'projection_rows':len(pp),'unique_player_games':len(cache),'pass_rows':int((pp.qc_status=='PASS').sum()) if len(pp) else 0})
if __name__=='__main__':main()
