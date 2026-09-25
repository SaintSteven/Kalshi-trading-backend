import argparse
from pathlib import Path
import numpy as np
import pandas as pd

# Diagnostic rules are deliberately fixed before looking at target-week results.
# They reflect the previously identified receiving-NO hypothesis: shorter thresholds,
# especially <=60 yards. No target-week threshold/edge tuning is permitted.
SUBSETS = {
    'all_no': lambda x: pd.Series(True, index=x.index),
    'no_le_60': lambda x: x['threshold'] <= 60,
    'no_le_50': lambda x: x['threshold'] <= 50,
    'no_51_60': lambda x: (x['threshold'] > 50) & (x['threshold'] <= 60),
}
EDGE_MIN = 0.03
MIN_PRIOR_BETS = 75

def pnl(x):
    if len(x)==0: return (0,0,0.0,0.0,np.nan)
    y=x['side_won'].astype(bool).astype(int)
    cost=float(x['contract_cost'].sum())
    profit=float(x['gross_pnl_per_contract'].sum())
    return len(x),int(y.sum()),cost,profit,profit/cost if cost else np.nan

def one_per_player(x):
    keys=[c for c in ['season','week','player_id'] if c in x.columns]
    if not keys: keys=[c for c in ['season','week','player'] if c in x.columns]
    return x.sort_values('edge_points',ascending=False).drop_duplicates(keys) if keys else x

def select(d, rule):
    x=d[(d['side']=='NO') & (d['edge_points'] >= 100*EDGE_MIN)].copy()
    x=x[rule(x)].copy()
    return one_per_player(x)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    d=pd.read_csv(a.input).sort_values(['season','week']).reset_index(drop=True)
    required={'season','week','side','edge_points','side_won','contract_cost','gross_pnl_per_contract','threshold'}
    missing=required-set(d.columns)
    if missing: raise ValueError(f'Missing required columns: {sorted(missing)}')
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    weeks=sorted(d[['season','week']].drop_duplicates().itertuples(index=False,name=None))
    rows=[]; allbets=[]
    for name,rule in SUBSETS.items():
        for season,week in weeks:
            prior=d[(d.season<season)|((d.season==season)&(d.week<week))]
            test=d[(d.season==season)&(d.week==week)]
            if len(prior)<150: continue
            prior_sel=select(prior,rule)
            if len(prior_sel)<MIN_PRIOR_BETS: continue
            x=select(test,rule); n,w,c,p,r=pnl(x)
            rows.append({'subset':name,'season':season,'week':week,'prior_bets':len(prior_sel),'bets':n,'wins':w,'cost':c,'pnl':p,'roi':r})
            if n: allbets.append(x.assign(subset=name))
    weekly=pd.DataFrame(rows); weekly.to_csv(out/'weekly_results.csv',index=False)
    if allbets: pd.concat(allbets).to_csv(out/'subset_bets.csv',index=False)
    summary=[]
    for name,g in weekly.groupby('subset'):
        n=int(g.bets.sum()); c=float(g.cost.sum()); p=float(g.pnl.sum())
        summary.append({'subset':name,'weeks':len(g),'bets':n,'cost':c,'pnl':p,'roi':p/c if c else np.nan,'positive_weeks':int((g.pnl>0).sum())})
    s=pd.DataFrame(summary).sort_values('subset'); s.to_csv(out/'summary.csv',index=False)
    print('FIXED EDGE MIN:',EDGE_MIN)
    print(s.to_string(index=False))
    print('\nWEEKLY\n',weekly.to_string(index=False))
if __name__=='__main__': main()
