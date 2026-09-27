#!/usr/bin/env python3
"""Locked REC-v0.11 retrospective grading on identity-verified 2026 Week 1 quotes."""
from pathlib import Path
import json, re, subprocess, sys, tempfile
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
LEGACY=ROOT/'research/nfl_prospective_paper_v001'; DATA=LEGACY/'data'
SPEC=json.loads((ROOT/'research/nfl_gameday/RECEIVING_FROZEN_V011.json').read_text()); RULE=SPEC['production_rule']
OUT=Path('research/nfl_receiving_v0_11/results'); OUT.mkdir(parents=True,exist_ok=True)
def col(df,*names):
    for n in names:
        if n in df.columns:return n

def main():
    q=pd.read_csv(DATA/'quote_history.csv',low_memory=False)
    ticker=col(q,'market_ticker','ticker'); kc=col(q,'kickoff_utc','kickoff'); tc=col(q,'captured_at','snapshot_at','timestamp','collected_at','updated_at'); game=col(q,'game','event_ticker')
    fam=col(q,'prop_family','family')
    if fam:q=q[q[fam].astype(str).eq('receiving_yards')].copy()
    q[tc]=pd.to_datetime(q[tc],utc=True,errors='coerce'); q[kc]=pd.to_datetime(q[kc],utc=True,errors='coerce')
    q=q[q[tc].notna() & q[kc].notna() & (q[tc]<q[kc])].sort_values(tc).groupby(ticker,as_index=False).tail(1).copy()
    pat=re.compile(r'KXNFLRECYDS-(\d{2}[A-Z]{3}\d{2})')
    q['ticker_date']=q[ticker].map(lambda t: pd.to_datetime(pat.search(str(t)).group(1),format='%y%b%d',errors='coerce') if pat.search(str(t)) else pd.NaT)
    aliases={'JAC':'JAX','WSH':'WAS','LA':'LAR'}
    def nt(x): return aliases.get(str(x),str(x))
    def ng(x):
        s=str(x)
        if '@' not in s:return s
        a,h=s.split('@',1); return nt(a)+'@'+nt(h)
    q['game_normalized']=q[game].map(ng)
    sched=pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv'); sched['gameday_dt']=pd.to_datetime(sched.gameday,errors='coerce'); sched['game_key']=sched.away_team.astype(str)+'@'+sched.home_team.astype(str)
    s=sched[sched.season.between(2024,2026)][['season','week','gameday_dt','game_key','away_team','home_team']].rename(columns={'season':'schedule_season','week':'schedule_week'})
    ident=q.merge(s,left_on=['ticker_date','game_normalized'],right_on=['gameday_dt','game_key'],how='left'); ident['identity_method']='exact_home_away'
    lookup={}
    for _,r in s.iterrows(): lookup.setdefault((r.gameday_dt,tuple(sorted([nt(r.away_team),nt(r.home_team)]))),[]).append(r)
    for idx,r in ident[ident.schedule_season.isna()].iterrows():
        if '@' not in str(r.game_normalized):continue
        a,h=r.game_normalized.split('@',1); hits=lookup.get((r.ticker_date,tuple(sorted([a,h]))),[])
        if len(hits)==1:
            z=hits[0]
            for c in ['schedule_season','schedule_week','gameday_dt','game_key','away_team','home_team']: ident.at[idx,c]=z[c]
            ident.at[idx,'identity_method']='unique_date_unordered_pair'
    if ident.schedule_season.isna().any(): raise SystemExit(f'Identity gate failed for {int(ident.schedule_season.isna().sum())} markets')
    if set(ident.schedule_season.astype(int))!={2026} or set(ident.schedule_week.astype(int))!={1}: raise SystemExit('Verified capture is not exclusively 2026 Week 1')
    # Feed canonical week into existing projection generator; preserve every verified market.
    ident['week']=ident.schedule_week.astype(int)
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); markets=td/'markets.csv'; projs=td/'projections.csv'; mapping=td/'mapping.csv'; fair=td/'fair.csv'
        ident.to_csv(markets,index=False)
        subprocess.run([sys.executable,LEGACY/'generate_receiving_projections.py','--markets',markets,'--projections',projs,'--mapping',mapping],check=True)
        subprocess.run([sys.executable,LEGACY/'receiving_feed.py','--markets',markets,'--projections',projs,'--mapping',mapping,'--output',fair],check=True)
        f=pd.read_csv(fair)
    keep=[c for c in ['market_ticker','player_id','threshold','projection','fair_no','qc_status','model_version'] if c in f.columns]
    d=ident.merge(f[keep],on='market_ticker',how='inner',suffixes=('_market','_model'))
    th=col(d,'threshold','threshold_model','line','strike'); noask=col(d,'no_ask','no_ask_probability')
    if not th or not noask: raise SystemExit('Mapped feed lacks threshold/no_ask')
    d[th]=pd.to_numeric(d[th],errors='coerce'); d[noask]=pd.to_numeric(d[noask],errors='coerce'); d['fair_no']=pd.to_numeric(d.fair_no,errors='coerce'); d['edge']=d.fair_no-d[noask]
    d=d[(d[th]<=float(RULE['threshold_max_yards']))&(d.edge>=float(RULE['minimum_edge_vs_executable_ask']))].copy()
    qc=col(d,'qc_status_model','qc_status')
    if qc:d=d[d[qc].isin(['PASS','MODEL_AVAILABLE'])].copy()
    player=col(d,'player_name','player'); keys=[c for c in ['game_key',player] if c]
    if keys:d=d.sort_values('edge',ascending=False).drop_duplicates(keys)
    # This is a retrospective mechanical diagnostic only: frozen spec requires manual
    # injury/role/correlation QC, which cannot be recreated faithfully after the fact.
    stats=pd.read_csv('https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.csv?raw=1')
    if 'season' not in stats.columns: raise SystemExit('nflverse player_stats lacks season')
    stats=stats[(pd.to_numeric(stats.season,errors='coerce')==2026)&(pd.to_numeric(stats.week,errors='coerce')==1)].copy()
    pid=col(d,'player_id','player_id_model','gsis_id'); spid=col(stats,'player_id','gsis_id')
    if not pid or not spid: raise SystemExit('No safe player-id outcome join')
    g=d.merge(stats[[spid,'receiving_yards']],left_on=pid,right_on=spid,how='left')
    if g.receiving_yards.isna().any():
        bad=g[g.receiving_yards.isna()].copy(); bad.to_csv(OUT/'unresolved_verified_week1.csv',index=False)
        raise SystemExit(f'Outcome gate failed for {len(bad)} selected candidates')
    g['won']=g.receiving_yards < g[th]; g['cost']=g[noask]; g['pnl']=g.won.astype(float)-g.cost
    g.to_csv(OUT/'candidates_verified_week1_2026.csv',index=False)
    summary={'model_version':SPEC['model_version'],'validation_label':'RETROSPECTIVE_2026_WEEK1_PRE_FREEZE_MECHANICAL_QC_ONLY_NOT_PROSPECTIVE','verified_markets':int(ident[ticker].nunique()),'identity_methods':{str(k):int(v) for k,v in ident.groupby('identity_method')[ticker].nunique().to_dict().items()},'candidates':int(len(g)),'wins':int(g.won.sum()),'losses':int((~g.won).sum()),'win_rate':float(g.won.mean()) if len(g) else None,'cost':float(g.cost.sum()),'pnl':float(g.pnl.sum()),'roi':float(g.pnl.sum()/g.cost.sum()) if len(g) and g.cost.sum() else None,'manual_injury_role_correlation_qc_recreated':False,'week2_quote_data_available':False}
    (OUT/'summary_verified_week1_2026.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
