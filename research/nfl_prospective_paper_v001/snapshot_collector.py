import argparse, csv, re, time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE='https://api.elections.kalshi.com/trade-api/v2'
SCHEDULE_URL='https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
UA={'User-Agent':'kalshi-nfl-week1-snapshot-collector-v0.01'}
TEAM_ALIAS={'LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC','JAC':'JAX','WSH':'WAS'}
SERIES={
 'receiving_yards':'KXNFLRECYDS','receptions':'KXNFLREC','rushing_yards':'KXNFLRSHYDS',
 'passing_yards':'KXNFLPASSYDS','player_tds':'KXNFLANYTD'}
SNAP_COLS=['captured_at','season','week','game_id','game','kickoff_utc','market_ticker','event_ticker','series_ticker','prop_family','player_key','player_name','snapshot_label','target_time_utc','quote_time_utc','quote_age_seconds','yes_bid','yes_ask','no_bid','no_ask','source','qc_status','qc_reason']
CURRENT_COLS=['updated_at','season','week','game_id','game','kickoff_utc','market_ticker','event_ticker','series_ticker','prop_family','player_key','player_name','yes_bid','yes_ask','no_bid','no_ask','qc_status','qc_reason']


def api_get(path,params=None):
 last=None
 for i in range(6):
  try:
   r=requests.get(BASE+path,params=params,headers=UA,timeout=35)
   if r.status_code==429 or 500<=r.status_code<600:
    last=RuntimeError(f'HTTP {r.status_code}: {r.text[:160]}');time.sleep(min(10,.5*2**i));continue
   r.raise_for_status();return r.json()
  except Exception as e:last=e;time.sleep(min(10,.5*2**i))
 raise last or RuntimeError('Kalshi request failed')

def norm_team(x):
 s=str(x).upper();return TEAM_ALIAS.get(s,s)

def parse_event(e):
 m=re.search(r'-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$',str(e).upper())
 if not m:return None
 yy,mon,dd,teams=m.groups()
 try:d=pd.to_datetime(f'20{yy}-{mon}-{dd}',format='%Y-%b-%d',utc=True).date()
 except Exception:return None
 return d,teams

def load_schedule():
 g=pd.read_csv(SCHEDULE_URL)
 if 'game_type' in g.columns:g=g[g.game_type.astype(str).eq('REG')].copy()
 g['gameday_d']=pd.to_datetime(g.gameday,errors='coerce').dt.date
 local=pd.to_datetime(g.gameday.astype(str)+' '+g.gametime.astype(str),errors='coerce')
 g['kickoff_utc']=local.dt.tz_localize('America/New_York',ambiguous='NaT',nonexistent='shift_forward').dt.tz_convert('UTC')
 return g

def map_game(event,sched):
 p=parse_event(event)
 if not p:return None
 d,teams=p
 for r in sched[sched.gameday_d.eq(d)].itertuples(index=False):
  a,h=norm_team(r.away_team),norm_team(r.home_team)
  if teams in (a+h,h+a):
   k=pd.Timestamp(r.kickoff_utc);return {'season':int(getattr(r,'season',k.year)),'week':getattr(r,'week',''),'game_id':str(getattr(r,'game_id','') or f'{a}@{h}-{d}'),'game':f'{a}@{h}','kickoff':k}
 return None

def num(v):
 if v is None or v=='':return None
 try:
  x=float(v);return x/100 if x>1 else x
 except:return None

def market_quote(m):
 def pick(*keys):
  for k in keys:
   if m.get(k) is not None:return num(m.get(k))
  return None
 yb=pick('yes_bid_dollars','yes_bid');ya=pick('yes_ask_dollars','yes_ask')
 return yb,ya,(1-ya if ya is not None else None),(1-yb if yb is not None else None)

def candle_val(side):
 if not isinstance(side,dict):return None
 for k in ('close_dollars','close'):
  if side.get(k) is not None:return num(side[k])
 return None

def target_snapshot(series,ticker,target_ts,now_ts):
 start=target_ts-600;end=min(now_ts,target_ts+600)
 if end<=start:return None
 for path,src in ((f'/series/{series}/markets/{ticker}/candlesticks','live'),(f'/historical/markets/{ticker}/candlesticks','historical')):
  try:cs=api_get(path,{'start_ts':start,'end_ts':end,'period_interval':1}).get('candlesticks',[]) or []
  except Exception:continue
  if not cs:continue
  eligible=[c for c in cs if target_ts-60<=int(c.get('end_period_ts',0))<=target_ts]
  if not eligible:continue
  c=max(eligible,key=lambda z:int(z.get('end_period_ts',0)));yb=candle_val(c.get('yes_bid'));ya=candle_val(c.get('yes_ask'))
  if yb is None or ya is None:continue
  ts=int(c['end_period_ts']);return {'ts':ts,'age':target_ts-ts,'yb':yb,'ya':ya,'nb':1-ya,'na':1-yb,'source':src}
 return None

def open_markets(series):
 rows=[];cur=''
 while True:
  p={'series_ticker':series,'status':'open','limit':1000}
  if cur:p['cursor']=cur
  d=api_get('/markets',p);rows.extend(d.get('markets',[]) or []);cur=str(d.get('cursor') or '')
  if not cur:return rows

def labels(game):
 k=game['kickoff'];d=k.tz_convert('America/New_York').date();gm=pd.Timestamp(datetime(d.year,d.month,d.day,9,0,tzinfo=ZoneInfo('America/New_York'))).tz_convert('UTC')
 return [('GAME_MORNING_9AM_ET',gm),('T24',k-pd.Timedelta(hours=24)),('T12',k-pd.Timedelta(hours=12)),('T6',k-pd.Timedelta(hours=6)),('T3',k-pd.Timedelta(hours=3)),('T1',k-pd.Timedelta(hours=1)),('T30',k-pd.Timedelta(minutes=30))]

def load_csv(path,cols):
 p=Path(path)
 if not p.exists() or p.stat().st_size==0:return pd.DataFrame(columns=cols)
 d=pd.read_csv(p,dtype=str).fillna('')
 for c in cols:
  if c not in d.columns:d[c]=''
 return d[cols]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--snapshots',required=True);ap.add_argument('--current',required=True);ap.add_argument('--health',required=True);a=ap.parse_args()
 now=pd.Timestamp.now(tz='UTC');now_ts=int(now.timestamp());sched=load_schedule();snap=load_csv(a.snapshots,SNAP_COLS);existing=set(zip(snap.market_ticker,snap.snapshot_label)) if len(snap) else set();new=[];current=[];health=[]
 for fam,series in SERIES.items():
  try:markets=open_markets(series);fetch_ok=True;err=''
  except Exception as e:markets=[];fetch_ok=False;err=repr(e)
  mapped=missing_game=missing_identity=0
  for m in markets:
   game=map_game(m.get('event_ticker'),sched)
   if not game:missing_game+=1;continue
   mapped+=1;key=str(m.get('primary_participant_key') or '').strip();name=str(m.get('subtitle') or m.get('title') or '').strip();yb,ya,nb,na=market_quote(m)
   qc='PASS';reason=''
   if not key:qc='FAIL';reason='MISSING_PARTICIPANT_KEY';missing_identity+=1
   elif yb is None or ya is None:qc='WARN';reason='NO_CURRENT_TWO_SIDED_QUOTE'
   base={'updated_at':now.isoformat(),'season':game['season'],'week':game['week'],'game_id':game['game_id'],'game':game['game'],'kickoff_utc':game['kickoff'].isoformat(),'market_ticker':m.get('ticker',''),'event_ticker':m.get('event_ticker',''),'series_ticker':series,'prop_family':fam,'player_key':key,'player_name':name,'yes_bid':yb,'yes_ask':ya,'no_bid':nb,'no_ask':na,'qc_status':qc,'qc_reason':reason}
   current.append(base)
   if qc=='FAIL':continue
   ticker=m.get('ticker','')
   if (ticker,'FIRST_OBSERVED') not in existing and yb is not None and ya is not None:
    new.append({'captured_at':now.isoformat(),**{k:base[k] for k in ['season','week','game_id','game','kickoff_utc','market_ticker','event_ticker','series_ticker','prop_family','player_key','player_name']},'snapshot_label':'FIRST_OBSERVED','target_time_utc':now.isoformat(),'quote_time_utc':now.isoformat(),'quote_age_seconds':0,'yes_bid':yb,'yes_ask':ya,'no_bid':nb,'no_ask':na,'source':'market_object','qc_status':'PASS','qc_reason':''});existing.add((ticker,'FIRST_OBSERVED'))
   for label,target in labels(game):
    if (ticker,label) in existing or now<target:continue
    s=target_snapshot(series,ticker,int(target.timestamp()),now_ts)
    row={'captured_at':now.isoformat(),**{k:base[k] for k in ['season','week','game_id','game','kickoff_utc','market_ticker','event_ticker','series_ticker','prop_family','player_key','player_name']},'snapshot_label':label,'target_time_utc':target.isoformat(),'quote_time_utc':'','quote_age_seconds':'','yes_bid':'','yes_ask':'','no_bid':'','no_ask':'','source':'','qc_status':'INVALID','qc_reason':'NO_FRESH_QUOTE_AT_TARGET'}
    if s:row.update({'quote_time_utc':datetime.fromtimestamp(s['ts'],tz=timezone.utc).isoformat(),'quote_age_seconds':s['age'],'yes_bid':s['yb'],'yes_ask':s['ya'],'no_bid':s['nb'],'no_ask':s['na'],'source':s['source'],'qc_status':'PASS','qc_reason':''})
    new.append(row);existing.add((ticker,label))
  health.append({'updated_at':now.isoformat(),'series_ticker':series,'prop_family':fam,'fetch_ok':fetch_ok,'open_markets':len(markets),'mapped_markets':mapped,'missing_game_map':missing_game,'missing_participant_key':missing_identity,'error':err})
 if new:snap=pd.concat([snap,pd.DataFrame(new)],ignore_index=True)
 Path(a.snapshots).parent.mkdir(parents=True,exist_ok=True);snap.to_csv(a.snapshots,index=False);pd.DataFrame(current,columns=CURRENT_COLS).to_csv(a.current,index=False);pd.DataFrame(health).to_csv(a.health,index=False)
 print(f'snapshots_total={len(snap)} added={len(new)} current={len(current)}')
if __name__=='__main__':main()
