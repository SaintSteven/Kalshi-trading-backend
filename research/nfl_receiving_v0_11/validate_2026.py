#!/usr/bin/env python3
"""Locked 2026 validation for NFL-REC-v0.11-PROSPECTIVE.

Uses only repository-captured pregame quote snapshots. It does not substitute
closing/settlement prices. Model inputs are generated as-of each snapshot by the
existing receiving projection/feed code. Frozen selection rule is read from
RECEIVING_FROZEN_V011.json and is not tunable here.
"""
from pathlib import Path
import json, subprocess, sys, tempfile
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
LEGACY=ROOT/'research/nfl_prospective_paper_v001'
DATA=LEGACY/'data'
SPEC=json.loads((ROOT/'research/nfl_gameday/RECEIVING_FROZEN_V011.json').read_text())
RULE=SPEC['production_rule']
OUT=Path('research/nfl_receiving_v0_11/results'); OUT.mkdir(parents=True,exist_ok=True)

def col(df,*names):
    for n in names:
        if n in df.columns:return n
    return None

def main():
    q=pd.read_csv(DATA/'quote_history.csv',low_memory=False)
    # Fail closed: validation must use captured timestamps and 2026 games only.
    tc=col(q,'captured_at','snapshot_at','timestamp','collected_at'); kc=col(q,'kickoff_utc','kickoff')
    if not tc or not kc: raise SystemExit(f'quote history lacks capture/kickoff timestamps: {list(q.columns)}')
    q[tc]=pd.to_datetime(q[tc],utc=True,errors='coerce'); q[kc]=pd.to_datetime(q[kc],utc=True,errors='coerce')
    q=q[(q[tc].notna())&(q[kc].notna())&(q[tc]<q[kc])].copy()
    q=q[q[kc].dt.year.eq(2026)].copy()
    fam=col(q,'prop_family','family')
    if fam:q=q[q[fam].astype(str).eq('receiving_yards')].copy()
    # One reproducible pregame snapshot per market: latest captured before kickoff.
    ticker=col(q,'market_ticker','ticker')
    if not ticker: raise SystemExit('quote history lacks market ticker')
    q=q.sort_values(tc).groupby(ticker,as_index=False).tail(1).copy()
    if q.empty: raise SystemExit('No 2026 pregame receiving quote snapshots available; cannot claim strategy validation.')
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); markets=td/'markets.csv'; projs=td/'projections.csv'; mapping=td/'mapping.csv'; fair=td/'fair.csv'
        q.to_csv(markets,index=False)
        subprocess.run([sys.executable,LEGACY/'generate_receiving_projections.py','--markets',markets,'--projections',projs,'--mapping',mapping],check=True)
        subprocess.run([sys.executable,LEGACY/'receiving_feed.py','--markets',markets,'--projections',projs,'--mapping',mapping,'--output',fair],check=True)
        f=pd.read_csv(fair)
    keep=[c for c in ['market_ticker','projection','fair_no','qc_status','model_version'] if c in f.columns]
    d=q.merge(f[keep],on='market_ticker',how='inner',suffixes=('_market','_model'))
    th=col(d,'threshold','line','strike'); noask=col(d,'no_ask','no_ask_probability')
    if not th or not noask: raise SystemExit(f'missing threshold/no ask after merge: {list(d.columns)}')
    d[th]=pd.to_numeric(d[th],errors='coerce'); d[noask]=pd.to_numeric(d[noask],errors='coerce'); d['fair_no']=pd.to_numeric(d.fair_no,errors='coerce')
    d['edge']=d.fair_no-d[noask]
    d=d[(d[th]<=float(RULE['threshold_max_yards']))&(d.edge>=float(RULE['minimum_edge_vs_executable_ask']))].copy()
    # Existing feed QC remains mandatory; manual QC cannot be recreated retroactively, so label separately.
    qc=col(d,'qc_status_model','qc_status')
    if qc:d=d[d[qc].isin(['PASS','MODEL_AVAILABLE'])].copy()
    game=col(d,'game','event_ticker'); player=col(d,'player_name','player')
    keys=[c for c in [game,player] if c]
    if keys:d=d.sort_values('edge',ascending=False).drop_duplicates(keys)
    # Grade from nflverse weekly data; fail closed on unresolved player/game outcomes.
    stats=pd.concat([pd.read_csv(f'https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.csv?raw=1')],ignore_index=True)
    # The release schema can change; require explicit receiving_yards and player identifiers.
    if 'receiving_yards' not in stats.columns: raise SystemExit('nflverse stats missing receiving_yards')
    # Prefer gsis/player id if both sides expose one, otherwise exact normalized display name + week.
    pid=col(d,'player_id','gsis_id'); spid=col(stats,'player_id','gsis_id')
    week=col(d,'week'); sweek=col(stats,'week')
    if pid and spid and week and sweek:
        g=d.merge(stats[[spid,sweek,'receiving_yards']],left_on=[pid,week],right_on=[spid,sweek],how='left')
    else:
        raise SystemExit('Cannot safely grade outcomes: no shared player_id/week fields. Add explicit schedule identity mapping; do not fuzzy-match.')
    if g.receiving_yards.isna().any(): raise SystemExit(f'Unresolved outcomes: {int(g.receiving_yards.isna().sum())}')
    g['won']=g.receiving_yards < g[th]
    g['cost']=g[noask]
    g['pnl']=g.won.astype(float)-g.cost
    g.to_csv(OUT/'candidates_2026.csv',index=False)
    summary={'model_version':SPEC['model_version'],'frozen_at':SPEC['frozen_at'],'validation_label':'RETROSPECTIVE_2026_PRE_FREEZE_NOT_PROSPECTIVE','snapshot_policy':'latest repository-captured quote strictly before kickoff','candidates':len(g),'wins':int(g.won.sum()),'cost':float(g.cost.sum()),'pnl':float(g.pnl.sum()),'roi':float(g.pnl.sum()/g.cost.sum()) if len(g) and g.cost.sum() else None,'manual_qc_recreated':False}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
