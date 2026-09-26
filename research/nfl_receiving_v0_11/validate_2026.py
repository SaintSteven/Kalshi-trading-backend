#!/usr/bin/env python3
"""Locked REC-v0.11 retrospective validation on captured 2026 Week-1 quotes only."""
from pathlib import Path
import json, subprocess, sys, tempfile
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]; LEGACY=ROOT/'research/nfl_prospective_paper_v001'; DATA=LEGACY/'data'
SPEC=json.loads((ROOT/'research/nfl_gameday/RECEIVING_FROZEN_V011.json').read_text()); RULE=SPEC['production_rule']
OUT=Path('research/nfl_receiving_v0_11/results'); OUT.mkdir(parents=True,exist_ok=True)
def col(df,*names):
    for n in names:
        if n in df.columns:return n

def main():
    q=pd.read_csv(DATA/'quote_history.csv',low_memory=False)
    tc=col(q,'captured_at','snapshot_at','timestamp','collected_at','updated_at'); kc=col(q,'kickoff_utc','kickoff'); ticker=col(q,'market_ticker','ticker')
    q[tc]=pd.to_datetime(q[tc],utc=True,errors='coerce'); q[kc]=pd.to_datetime(q[kc],utc=True,errors='coerce')
    q=q[(q[tc].notna())&(q[kc].notna())&(q[tc]<q[kc])&q[kc].dt.year.eq(2026)].copy()
    fam=col(q,'prop_family','family')
    if fam:q=q[q[fam].astype(str).eq('receiving_yards')]
    q=q.sort_values(tc).groupby(ticker,as_index=False).tail(1).copy()
    # Repository audit established this capture contains only 2026 Week 1. Do not
    # manufacture Week 2 quotes. Preserve the stored week for this one-week test.
    weeks=sorted(pd.to_numeric(q['week'],errors='coerce').dropna().unique().tolist()) if 'week' in q else []
    if weeks != [1]: raise SystemExit(f'Expected audited Week-1-only capture, found weeks={weeks}')
    print(f'AUDITED COVERAGE: Week 1 only; {q[ticker].nunique()} unique receiving markets. Week 2 historical quotes unavailable.')
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); markets=td/'markets.csv'; projs=td/'projections.csv'; mapping=td/'mapping.csv'; fair=td/'fair.csv'
        q.to_csv(markets,index=False)
        subprocess.run([sys.executable,LEGACY/'generate_receiving_projections.py','--markets',markets,'--projections',projs,'--mapping',mapping],check=True)
        subprocess.run([sys.executable,LEGACY/'receiving_feed.py','--markets',markets,'--projections',projs,'--mapping',mapping,'--output',fair],check=True)
        f=pd.read_csv(fair)
    keep=[c for c in ['market_ticker','player_id','threshold','projection','fair_no','qc_status','model_version'] if c in f.columns]
    d=q.merge(f[keep],on='market_ticker',how='inner',suffixes=('_market','_model'))
    th=col(d,'threshold','threshold_model','line','strike'); noask=col(d,'no_ask','no_ask_probability')
    d[th]=pd.to_numeric(d[th],errors='coerce'); d[noask]=pd.to_numeric(d[noask],errors='coerce'); d['fair_no']=pd.to_numeric(d.fair_no,errors='coerce'); d['edge']=d.fair_no-d[noask]
    d=d[(d[th]<=float(RULE['threshold_max_yards']))&(d.edge>=float(RULE['minimum_edge_vs_executable_ask']))]
    qc=col(d,'qc_status_model','qc_status')
    if qc:d=d[d[qc].isin(['PASS','MODEL_AVAILABLE'])]
    player=col(d,'player_name','player'); keys=[c for c in ['game',player] if c]
    if keys:d=d.sort_values('edge',ascending=False).drop_duplicates(keys)
    stats=pd.read_csv('https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.csv?raw=1')
    # Restrict outcomes explicitly to 2026 Week 1; previous failures were caused by
    # joining a multi-season player_stats release on player_id+week without season.
    if 'season' not in stats.columns: raise SystemExit('nflverse player_stats lacks season')
    stats=stats[(pd.to_numeric(stats.season,errors='coerce')==2026)&(pd.to_numeric(stats.week,errors='coerce')==1)].copy()
    pid=col(d,'player_id','player_id_model','gsis_id'); spid=col(stats,'player_id','gsis_id')
    g=d.merge(stats[[spid,'receiving_yards']],left_on=pid,right_on=spid,how='left')
    unresolved=g[g.receiving_yards.isna()].copy()
    if len(unresolved):
        cols=[c for c in ['market_ticker',pid,'game',player,th,noask,'fair_no','edge'] if c in unresolved]
        unresolved[cols].to_csv(OUT/'unresolved_week1.csv',index=False); print(unresolved[cols].to_string(index=False))
        raise SystemExit(f'Week 1 still has {len(unresolved)} unresolved selected outcomes; refusing biased grading.')
    g['won']=g.receiving_yards < g[th]; g['cost']=g[noask]; g['pnl']=g.won.astype(float)-g.cost
    g.to_csv(OUT/'candidates_week1_2026.csv',index=False)
    summary={'model_version':SPEC['model_version'],'validation_label':'RETROSPECTIVE_2026_WEEK1_ONLY_PRE_FREEZE_NOT_PROSPECTIVE','historical_quote_weeks_available':[1],'week2_quote_data_available':False,'candidates':len(g),'wins':int(g.won.sum()),'losses':int((~g.won).sum()),'win_rate':float(g.won.mean()) if len(g) else None,'cost':float(g.cost.sum()),'pnl':float(g.pnl.sum()),'roi':float(g.pnl.sum()/g.cost.sum()) if len(g) and g.cost.sum() else None}
    (OUT/'summary_week1_2026.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
