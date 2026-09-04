import argparse,time,math
from pathlib import Path
from datetime import datetime,timezone
import requests,pandas as pd
BASE='https://api.elections.kalshi.com/trade-api/v2'
UA={'User-Agent':'kalshi-nfl-market-efficiency-v0.03'}
CLASSES={'player_tds','receiving_yards','receptions','rushing_yards','passing_yards'}
HOURS=[6,3,1,.5]

def get(path,params=None):
 last=None
 for i in range(7):
  try:
   r=requests.get(BASE+path,params=params,headers=UA,timeout=45)
   if r.status_code==429 or 500<=r.status_code<600:
    last=RuntimeError(f'HTTP {r.status_code}: {r.text[:160]}');time.sleep(min(12,.5*2**i));continue
   r.raise_for_status();return r.json()
  except Exception as e:last=e;time.sleep(min(12,.5*2**i))
 raise last

def ts(x):
 try:return int(pd.Timestamp(x).timestamp())
 except:return None

def val(side):
 if not isinstance(side,dict):return None
 for k in ('close_dollars','close'):
  if side.get(k) is not None:
   try:
    x=float(side[k]);return x/100 if x>1 else x
   except:pass
 return None

def candles(ticker,start,end):
 return get(f'/historical/markets/{ticker}/candlesticks',{'start_ts':start,'end_ts':end,'period_interval':1}).get('candlesticks',[]) or []

def snapshot(cs,target,lookback=7200):
 eligible=[c for c in cs if int(c.get('end_period_ts',0))<=target and int(c.get('end_period_ts',0))>=target-lookback]
 if not eligible:return None
 c=max(eligible,key=lambda z:int(z.get('end_period_ts',0)))
 ya=val(c.get('yes_ask'));yb=val(c.get('yes_bid'))
 if ya is None or yb is None:return None
 return {'ts':int(c['end_period_ts']),'yes_ask':ya,'yes_bid':yb,'no_ask':1-yb,'no_bid':1-ya}

def result_yes(x):
 s=str(x).lower()
 return 1 if s=='yes' else (0 if s=='no' else None)

def bucket(p):
 if p<.2:return '<20c'
 if p<.4:return '20-39c'
 if p<.6:return '40-59c'
 if p<.8:return '60-79c'
 return '80c+'

def summarize(df,kind):
 out=[]
 if df.empty:return pd.DataFrame()
 keys=['prop_class','side','entry_horizon','price_bucket']
 for k,g in df.groupby(keys,dropna=False):
  pnl=g[kind].dropna();cost=g.loc[pnl.index,'entry_price']
  out.append(dict(zip(keys,k),n=len(pnl),pnl=pnl.sum(),cost=cost.sum(),roi=pnl.sum()/cost.sum() if cost.sum() else None,win_rate=(pnl>0).mean()))
 return pd.DataFrame(out)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--inventory',required=True);ap.add_argument('--out',default='nfl_market_efficiency_v003_output');ap.add_argument('--max-per-class',type=int,default=0);a=ap.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 inv=pd.read_csv(a.inventory);inv=inv[inv.prop_class.isin(CLASSES)].copy()
 inv=inv[inv.result.astype(str).str.lower().isin(['yes','no'])]
 rows=[];errs=[]
 for cls,g in inv.groupby('prop_class'):
  if a.max_per_class:g=g.head(a.max_per_class)
  print(f'{cls}: {len(g)} markets')
  for j,m in enumerate(g.itertuples(index=False),1):
   close=ts(getattr(m,'close_time',None));sett=result_yes(m.result)
   if not close or sett is None:continue
   try:cs=candles(m.market_ticker,close-int(7*3600),close-int(20*60))
   except Exception as e:errs.append({'ticker':m.market_ticker,'error':repr(e)});continue
   snaps={h:snapshot(cs,close-int(h*3600)) for h in HOURS}
   exit30=snaps[.5]
   for h,s in snaps.items():
    if not s:continue
    for side in ('yes','no'):
     entry=s[f'{side}_ask'];settle=(sett if side=='yes' else 1-sett)
     settle_pnl=settle-entry
     move_pnl=None
     if h>.5 and exit30: move_pnl=exit30[f'{side}_bid']-entry
     rows.append({'prop_class':cls,'series_ticker':m.series_ticker,'market_ticker':m.market_ticker,'event_ticker':getattr(m,'event_ticker',None),'close_time':m.close_time,'side':side,'entry_horizon':f'T-{h:g}h','entry_ts':s['ts'],'entry_price':entry,'price_bucket':bucket(entry),'settled_win':settle,'settlement_pnl':settle_pnl,'exit_30m_bid':exit30[f'{side}_bid'] if exit30 else None,'movement_pnl_to_30m':move_pnl})
   if j%250==0:print(cls,j)
 df=pd.DataFrame(rows);df.to_csv(out/'executable_ledger.csv',index=False);pd.DataFrame(errs).to_csv(out/'errors.csv',index=False)
 settle=summarize(df,'settlement_pnl');settle.to_csv(out/'settlement_summary.csv',index=False)
 move=summarize(df[df.entry_horizon!='T-0.5h'],'movement_pnl_to_30m');move.to_csv(out/'movement_summary.csv',index=False)
 broad=[]
 for typ,sd in [('SETTLEMENT',settle),('MOVEMENT_TO_30M',move)]:
  if len(sd):
   z=sd.groupby(['prop_class','side','entry_horizon']).agg(n=('n','sum'),pnl=('pnl','sum'),cost=('cost','sum')).reset_index();z['roi']=z.pnl/z.cost;z['test']=typ;broad.append(z)
 broad=pd.concat(broad,ignore_index=True) if broad else pd.DataFrame()
 broad.to_csv(out/'broad_summary.csv',index=False)
 lines=['# NFL Market Efficiency Scanner v0.03 — Executable Price Discovery','',f'Market-side-horizon rows: **{len(df):,}**',f'Candlestick errors: **{len(errs):,}**','', '**Prices are executable quote proxies from 1-minute candle closes: entry YES ask; entry NO ask = 1-YES bid; movement exits use the corresponding bid at T-30m. Settlement P/L is gross before fees.**','','## Broad results','',broad.sort_values('roi',ascending=False).to_markdown(index=False) if len(broad) else 'No results.','','This is discovery, not a betting strategy. Any promising slice must be frozen and retested chronologically with one thesis per player-game and fees before promotion.']
 (out/'SUMMARY.md').write_text('\n'.join(lines));print('\n'.join(lines))
 if df.empty:raise SystemExit('No executable rows produced')
if __name__=='__main__':main()
