import argparse
from pathlib import Path
import numpy as np
import pandas as pd

EDGE_GRID=[0.03,0.05,0.08,0.10,0.15]
MIN_PRIOR_BETS=75

def pnl(x):
    if len(x)==0:return (0,0,0.0,0.0,np.nan)
    y=x['side_won'].astype(bool).astype(int)
    cost=x['contract_cost'].sum()
    profit=x['gross_pnl_per_contract'].sum()
    return len(x),int(y.sum()),float(cost),float(profit),float(profit/cost) if cost else np.nan

def select(d,edge,side='NO'):
    x=d[d['side'].eq(side)].copy()
    x=x[x['edge_points']>=100*edge]
    keys=[c for c in ['season','week','player_id'] if c in x.columns]
    if not keys: keys=[c for c in ['season','week','player'] if c in x.columns]
    if keys:
        x=x.sort_values('edge_points',ascending=False).drop_duplicates(keys)
    return x

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    d=pd.read_csv(a.input).sort_values(['season','week']).reset_index(drop=True)
    required={'season','week','side','edge_points','side_won','contract_cost','gross_pnl_per_contract'}
    missing=required-set(d.columns)
    if missing: raise ValueError(f'Missing required columns: {sorted(missing)}')
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    weeks=sorted(d[['season','week']].drop_duplicates().itertuples(index=False,name=None))
    rows=[];bets=[]
    for season,week in weeks:
        prior=d[(d.season<season)|((d.season==season)&(d.week<week))]
        test=d[(d.season==season)&(d.week==week)]
        if len(prior)<150:continue
        best=None
        for edge in EDGE_GRID:
            x=select(prior,edge,'NO');n,w,c,p,r=pnl(x)
            if n>=MIN_PRIOR_BETS and (best is None or r>best[0]):best=(r,edge,n)
        if best is None:continue
        edge=best[1]
        x=select(test,edge,'NO');n,w,c,p,r=pnl(x)
        rows.append({'season':season,'week':week,'prior_rows':len(prior),'chosen_edge':edge,'prior_bets_at_choice':best[2],'bets':n,'wins':w,'cost':c,'pnl':p,'roi':r})
        if len(x):bets.append(x.assign(chosen_edge=edge))
    res=pd.DataFrame(rows);res.to_csv(out/'weekly_results.csv',index=False)
    if bets:pd.concat(bets).to_csv(out/'walkforward_bets.csv',index=False)
    n=int(res.bets.sum()) if len(res) else 0;c=float(res.cost.sum()) if len(res) else 0;p=float(res.pnl.sum()) if len(res) else 0
    summary=pd.DataFrame([{'weeks':len(res),'bets':n,'cost':c,'pnl':p,'roi':p/c if c else np.nan}]);summary.to_csv(out/'summary.csv',index=False)
    print(res.to_string(index=False));print('\nTOTAL\n',summary.to_string(index=False))
if __name__=='__main__':main()
# workflow rerun after schema inspection
