import argparse,time
from pathlib import Path
import requests,pandas as pd
BASE='https://api.elections.kalshi.com/trade-api/v2'
UA={'User-Agent':'kalshi-nfl-market-efficiency-v0.03.1-regular-season'}
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
 s=str(x).lower();return 1 if s=='yes' else (0 if s=='no' else None)

def bucket(p):
 if p<.2:return '<20c'
 if p<.4:return '20-39c'
 if p<.6:return '40-59c'
 if p<.8:return '60-79c'
 return '80c+'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--inventory',required=True);ap.add_argument('--out',required=True);ap.add_argument('--prop-class',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);a=ap.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 inv=pd.read_csv(a.inventory)
 base=inv[(inv.prop_class==a.prop_class)&inv.result.astype(str).str.lower().isin(['yes','no'])].copy()
 base['_close_dt']=pd.to_datetime(base['close_time'],errors='coerce',utc=True)
 august=base[base['_close_dt'].dt.month.eq(8)].copy()
 g=base[~base['_close_dt'].dt.month.eq(8)].copy()
 excluded_august=len(august)
 g=g.sort_values('market_ticker').reset_index(drop=True)
 g=g.iloc[[i for i in range(len(g)) if i%a.shard_count==a.shard_index]]
 print(f'{a.prop_class} shard {a.shard_index}/{a.shard_count}: {len(g)} markets after excluding all August markets; class August excluded={excluded_august}')
 rows=[];errs=[]
 for j,m in enumerate(g.itertuples(index=False),1):
  close=ts(getattr(m,'close_time',None));sett=result_yes(m.result)
  if not close or sett is None:continue
  try:cs=candles(m.market_ticker,close-int(7*3600),close-int(20*60))
  except Exception as e:errs.append({'ticker':m.market_ticker,'error':repr(e)});continue
  snaps={h:snapshot(cs,close-int(h*3600)) for h in HOURS};exit30=snaps[.5]
  for h,s in snaps.items():
   if not s:continue
   for side in ('yes','no'):
    entry=s[f'{side}_ask'];settle=(sett if side=='yes' else 1-sett)
    move=None
    if h>.5 and exit30:move=exit30[f'{side}_bid']-entry
    rows.append({'prop_class':a.prop_class,'series_ticker':m.series_ticker,'market_ticker':m.market_ticker,'event_ticker':getattr(m,'event_ticker',None),'close_time':m.close_time,'side':side,'entry_horizon':f'T-{h:g}h','entry_ts':s['ts'],'entry_price':entry,'price_bucket':bucket(entry),'settled_win':settle,'settlement_pnl':settle-entry,'exit_30m_bid':exit30[f'{side}_bid'] if exit30 else None,'movement_pnl_to_30m':move})
  if j%250==0:print(a.prop_class,a.shard_index,j)
 pd.DataFrame(rows).to_csv(out/'executable_ledger.csv',index=False)
 pd.DataFrame(errs).to_csv(out/'errors.csv',index=False)
 (out/'SHARD_DONE.txt').write_text(f'{a.prop_class} shard {a.shard_index}/{a.shard_count} markets={len(g)} rows={len(rows)} errors={len(errs)} august_excluded_class={excluded_august}\n')
 if not rows:raise SystemExit('No executable rows produced')
if __name__=='__main__':main()
