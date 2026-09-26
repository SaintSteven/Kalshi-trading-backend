#!/usr/bin/env python3
"""Audit 2026 quote/game week identity before locked REC-v0.11 grading."""
from pathlib import Path
import json, subprocess, sys, tempfile
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
LEGACY=ROOT/'research/nfl_prospective_paper_v001'; DATA=LEGACY/'data'
SPEC=json.loads((ROOT/'research/nfl_gameday/RECEIVING_FROZEN_V011.json').read_text()); RULE=SPEC['production_rule']
OUT=Path('research/nfl_receiving_v0_11/results'); OUT.mkdir(parents=True,exist_ok=True)

def col(df,*names):
    for n in names:
        if n in df.columns:return n
    return None

def main():
    q=pd.read_csv(DATA/'quote_history.csv',low_memory=False)
    tc=col(q,'captured_at','snapshot_at','timestamp','collected_at','updated_at'); kc=col(q,'kickoff_utc','kickoff')
    if not tc or not kc: raise SystemExit(f'quote history lacks capture/kickoff timestamps: {list(q.columns)}')
    q[tc]=pd.to_datetime(q[tc],utc=True,errors='coerce'); q[kc]=pd.to_datetime(q[kc],utc=True,errors='coerce')
    q=q[(q[tc].notna())&(q[kc].notna())&(q[tc]<q[kc]) & q[kc].dt.year.eq(2026)].copy()
    fam=col(q,'prop_family','family')
    if fam:q=q[q[fam].astype(str).eq('receiving_yards')].copy()
    ticker=col(q,'market_ticker','ticker'); event=col(q,'event_ticker','game')
    if not ticker: raise SystemExit('quote history lacks market ticker')
    q=q.sort_values(tc).groupby(ticker,as_index=False).tail(1).copy()
    if q.empty: raise SystemExit('No 2026 pregame receiving quote snapshots available.')

    # Audit raw repository coverage independently of the stored `week` column.
    # Event tickers contain the scheduled calendar date (e.g. 26SEP13); kickoff is authoritative.
    audit_cols=[c for c in [event,'game',kc,'week',tc] if c and c in q.columns]
    audit=q[audit_cols].drop_duplicates().sort_values(kc)
    audit.to_csv(OUT/'quote_game_week_audit.csv',index=False)
    print('QUOTE/GAME/WEEK AUDIT')
    print(audit.to_string(index=False))
    print('\nCOUNTS BY STORED WEEK')
    if 'week' in q.columns: print(q.groupby('week')[ticker].nunique().to_string())
    print('\nCOUNTS BY KICKOFF DATE')
    print(q.assign(kickoff_date=q[kc].dt.date).groupby('kickoff_date')[ticker].nunique().to_string())

    # Derive NFL week from actual 2026 schedule, not the historical quote row's stored week.
    sched=pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv')
    seasonc=col(sched,'season'); weekc=col(sched,'week'); datec=col(sched,'gameday','game_date'); homec=col(sched,'home_team'); awayc=col(sched,'away_team')
    if not all([seasonc,weekc,datec,homec,awayc]): raise SystemExit(f'schedule schema unsupported: {list(sched.columns)}')
    sched=sched[pd.to_numeric(sched[seasonc],errors='coerce').eq(2026)].copy(); sched[datec]=pd.to_datetime(sched[datec],errors='coerce').dt.date
    # Map by game string TEAM@TEAM + kickoff calendar date when available.
    if 'game' not in q.columns: raise SystemExit('quote history lacks game identity needed for schedule audit')
    sched['game']=sched[awayc].astype(str)+'@'+sched[homec].astype(str)
    q['kickoff_date']=q[kc].dt.date
    q=q.merge(sched[['game',datec,weekc]].rename(columns={datec:'kickoff_date','week':'schedule_week'}),on=['game','kickoff_date'],how='left')
    if q['schedule_week'].isna().any():
        bad=q[q.schedule_week.isna()][['game','kickoff_date',ticker]].drop_duplicates(); bad.to_csv(OUT/'unmapped_schedule_games.csv',index=False)
        raise SystemExit(f'Could not map {len(bad)} quote games to 2026 schedule; audit artifact saved.')
    q['week_original']=q['week'] if 'week' in q.columns else pd.NA; q['week']=q['schedule_week'].astype(int)
    print('\nSCHEDULE-DERIVED WEEK COUNTS')
    print(q.groupby('week')[ticker].nunique().to_string())
    mism=q[q.week_original.notna() & (pd.to_numeric(q.week_original,errors='coerce')!=q.week)][['game','kickoff_date','week_original','week']].drop_duplicates()
    mism.to_csv(OUT/'week_mismatches.csv',index=False); print(f'\nStored-vs-schedule week mismatches: {len(mism)}')

    with tempfile.TemporaryDirectory() as td:
        td=Path(td); markets=td/'markets.csv'; projs=td/'projections.csv'; mapping=td/'mapping.csv'; fair=td/'fair.csv'
        q.to_csv(markets,index=False)
        subprocess.run([sys.executable,LEGACY/'generate_receiving_projections.py','--markets',markets,'--projections',projs,'--mapping',mapping],check=True)
        subprocess.run([sys.executable,LEGACY/'receiving_feed.py','--markets',markets,'--projections',projs,'--mapping',mapping,'--output',fair],check=True)
        f=pd.read_csv(fair)
    keep=[c for c in ['market_ticker','player_id','threshold','projection','fair_no','qc_status','model_version'] if c in f.columns]
    d=q.merge(f[keep],on='market_ticker',how='inner',suffixes=('_market','_model'))
    th=col(d,'threshold','threshold_model','line','strike'); noask=col(d,'no_ask','no_ask_probability')
    if not th or not noask: raise SystemExit('missing mapped threshold/no ask')
    d[th]=pd.to_numeric(d[th],errors='coerce'); d[noask]=pd.to_numeric(d[noask],errors='coerce'); d['fair_no']=pd.to_numeric(d.fair_no,errors='coerce'); d['edge']=d.fair_no-d[noask]
    d=d[(d[th]<=float(RULE['threshold_max_yards']))&(d.edge>=float(RULE['minimum_edge_vs_executable_ask']))].copy()
    qc=col(d,'qc_status_model','qc_status')
    if qc:d=d[d[qc].isin(['PASS','MODEL_AVAILABLE'])].copy()
    player=col(d,'player_name','player'); keys=[c for c in ['game',player] if c]
    if keys:d=d.sort_values('edge',ascending=False).drop_duplicates(keys)

    stats=pd.read_csv('https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.csv?raw=1')
    pid=col(d,'player_id','player_id_model','gsis_id'); spid=col(stats,'player_id','gsis_id'); sweek=col(stats,'week')
    if not (pid and spid and sweek): raise SystemExit('Cannot safely grade outcomes: no shared player_id/week fields.')
    g=d.merge(stats[[spid,sweek,'receiving_yards']],left_on=[pid,'week'],right_on=[spid,sweek],how='left')
    unresolved=g[g.receiving_yards.isna()].copy()
    if len(unresolved):
        cols=[c for c in ['market_ticker',pid,'week','game',player,th,noask,'fair_no','edge'] if c in unresolved.columns]
        unresolved[cols].to_csv(OUT/'unresolved_outcomes.csv',index=False); print('\nUNRESOLVED AFTER SCHEDULE WEEK FIX'); print(unresolved[cols].to_string(index=False))
        raise SystemExit(f'Unresolved outcomes after schedule-derived week mapping: {len(unresolved)}')
    g['won']=g.receiving_yards < g[th]; g['cost']=g[noask]; g['pnl']=g.won.astype(float)-g.cost
    g.to_csv(OUT/'candidates_2026.csv',index=False)
    summary={'model_version':SPEC['model_version'],'validation_label':'RETROSPECTIVE_2026_PRE_FREEZE_NOT_PROSPECTIVE','weeks':sorted(g.week.unique().tolist()),'candidates':len(g),'wins':int(g.won.sum()),'cost':float(g.cost.sum()),'pnl':float(g.pnl.sum()),'roi':float(g.pnl.sum()/g.cost.sum()) if len(g) and g.cost.sum() else None}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)); print('\nFINAL\n'+json.dumps(summary,indent=2))
if __name__=='__main__':main()
