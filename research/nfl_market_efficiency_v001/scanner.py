import argparse, json, math, re, time
from pathlib import Path
import requests, pandas as pd, numpy as np

BASE='https://api.elections.kalshi.com/trade-api/v2'
SERIES=['KXNFLRECYDS','KXNFLRECEPTIONS','KXNFLPASSYDS','KXNFLRUSHYDS','KXNFLPASSATT','KXNFLPASSCOMPS','KXNFLRUSHATT','KXNFLTDS','KXNFLPASSINGTDS']

def get(path, params=None):
    for i in range(6):
        r=requests.get(BASE+path,params=params,timeout=30)
        if r.status_code==429:
            time.sleep(min(2**i,20)); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError('rate limit')

def markets(series):
    out=[]; cur=None
    while True:
        p={'series_ticker':series,'status':'settled','limit':1000}
        if cur:p['cursor']=cur
        d=get('/markets',p); out += d.get('markets',[]); cur=d.get('cursor')
        if not cur:break
    return out

def cents(m,key):
    v=m.get(key)
    if v is None:return np.nan
    try:
        x=float(v); return x/100 if x>1 else x
    except:return np.nan

def settlement(m):
    r=str(m.get('result','')).lower()
    if r=='yes':return 1
    if r=='no':return 0
    return np.nan

def bucket(x):
    if pd.isna(x):return 'NA'
    if x<.2:return '<20c'
    if x<.4:return '20-39c'
    if x<.6:return '40-59c'
    if x<.8:return '60-79c'
    return '80c+'

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',default='nfl_market_efficiency_output'); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    rows=[]; inventory=[]
    for s in SERIES:
        try: ms=markets(s)
        except Exception as e:
            inventory.append({'series':s,'status':'ERROR','n':0,'error':str(e)}); continue
        inventory.append({'series':s,'status':'OK','n':len(ms),'error':''})
        for m in ms:
            y=settlement(m)
            # Market-level settlement audit using final quoted/last prices only as discovery.
            # This is NOT yet a tradable pregame backtest; promising series must get a candle-level follow-up.
            yp=cents(m,'last_price_dollars')
            if pd.isna(yp): yp=cents(m,'last_price')
            if pd.isna(yp) or pd.isna(y):continue
            yp=max(.01,min(.99,yp)); np_=1-yp
            rows.append({'series':s,'ticker':m.get('ticker'),'title':m.get('title'),'settled_yes':y,
                         'reference_yes_price':yp,'reference_no_price':np_,
                         'yes_pnl':y-yp,'no_pnl':(1-y)-np_,
                         'yes_bucket':bucket(yp),'no_bucket':bucket(np_),
                         'close_time':m.get('close_time'),'expiration_time':m.get('expiration_time')})
    inv=pd.DataFrame(inventory); inv.to_csv(out/'series_inventory.csv',index=False)
    df=pd.DataFrame(rows); df.to_csv(out/'market_reference_ledger.csv',index=False)
    if df.empty:
        (out/'SUMMARY.md').write_text('# NFL Market Efficiency Scanner v0.01\n\nNo settled reference-price rows found.\n'); return
    def agg(g, side):
        p=f'{side}_pnl'; price=f'reference_{side}_price'
        return pd.Series({'n':len(g),'avg_reference_price':g[price].mean(),'hit_rate':(g.settled_yes if side=='yes' else 1-g.settled_yes).mean(),
                          'gross_pnl_per_$1_contract':g[p].sum(),'gross_roi_on_cost':g[p].sum()/g[price].sum() if g[price].sum() else np.nan})
    parts=[]
    for side in ['yes','no']:
        z=df.groupby('series').apply(lambda g:agg(g,side),include_groups=False).reset_index(); z['side']=side; parts.append(z)
    summ=pd.concat(parts); summ.to_csv(out/'series_direction_summary.csv',index=False)
    buckets=[]
    for side in ['yes','no']:
        bcol=f'{side}_bucket'; z=df.groupby(['series',bcol]).apply(lambda g:agg(g,side),include_groups=False).reset_index(); z['side']=side; z=z.rename(columns={bcol:'price_bucket'}); buckets.append(z)
    bs=pd.concat(buckets); bs.to_csv(out/'series_price_bucket_summary.csv',index=False)
    stable=bs[bs.n>=50].sort_values('gross_roi_on_cost',ascending=False)
    lines=['# NFL Market Efficiency Scanner v0.01','',
           '**Discovery only. Reference prices are market-level final/last prices, not reconstructed executable pregame asks. Do not interpret ROI as tradable performance.**','',
           '## Series inventory','',inv.to_markdown(index=False),'','## Direction summary','',summ.to_markdown(index=False),'',
           '## Highest reference-return slices (n>=50)','',stable.head(20).to_markdown(index=False),'',
           'Next gate: only promising series/sides advance to candle-level kickoff-minus-30m executable YES/NO ask reconstruction, chronological splits, one thesis per player-game, and fee-aware P/L.']
    (out/'SUMMARY.md').write_text('\n'.join(lines)); print('\n'.join(lines))
if __name__=='__main__':main()
