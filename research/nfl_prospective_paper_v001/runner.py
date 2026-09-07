import argparse, json, math, re, time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE='https://api.elections.kalshi.com/trade-api/v2'
SCHEDULE_URL='https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
UA={'User-Agent':'kalshi-nfl-prospective-paper-v0.01-qc'}
TEAM_ALIAS={'LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC','JAC':'JAX','WSH':'WAS'}
SERIES_BY_FAMILY={'receiving_yards':'KXNFLRECYDS','receptions':'KXNFLREC','rushing_yards':'KXNFLRSHYDS','passing_yards':'KXNFLPASSYDS','player_tds':'KXNFLANYTD'}
LEDGER_COLUMNS=['run_timestamp','season','week','game_id','kickoff_utc','market_ticker','series_ticker','prop_family','rule_id','player_key','player_name','side','target_horizon_minutes','target_time_utc','quote_time_utc','quote_age_seconds','yes_bid','yes_ask','entry_price','contracts','gross_cost','modeled_fee','net_cost','settlement_result','gross_pnl','net_pnl','status','exclusion_reason','thesis_key']


def api_get(path,params=None):
 last=None
 for i in range(6):
  try:
   r=requests.get(BASE+path,params=params,headers=UA,timeout=35)
   if r.status_code==429 or 500<=r.status_code<600:
    last=RuntimeError(f'HTTP {r.status_code}: {r.text[:180]}');time.sleep(min(10,.5*2**i));continue
   r.raise_for_status();return r.json()
  except Exception as e:last=e;time.sleep(min(10,.5*2**i))
 raise last or RuntimeError('Kalshi request failed')

def norm_team(x):
 s=str(x).upper();return TEAM_ALIAS.get(s,s)

def parse_event(event):
 m=re.search(r'-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$',str(event).upper())
 if not m:return None
 yy,mon,dd,teams=m.groups()
 try:d=pd.to_datetime(f'20{yy}-{mon}-{dd}',format='%Y-%b-%d',utc=True).date()
 except Exception:return None
 return d,teams

def load_schedule():
 g=pd.read_csv(SCHEDULE_URL)
 if 'game_type' in g.columns:g=g[g.game_type.astype(str).eq('REG')].copy()
 g['gameday_d']=pd.to_datetime(g.gameday,errors='coerce').dt.date
 if 'gametime' in g.columns:
  local=pd.to_datetime(g.gameday.astype(str)+' '+g.gametime.astype(str),errors='coerce')
  g['kickoff_utc']=local.dt.tz_localize('America/New_York',ambiguous='NaT',nonexistent='shift_forward').dt.tz_convert('UTC')
 elif 'start_time' in g.columns:g['kickoff_utc']=pd.to_datetime(g.start_time,errors='coerce',utc=True)
 else:raise RuntimeError('Schedule missing gametime/start_time')
 return g

def map_game(event,sched):
 p=parse_event(event)
 if not p:return None
 d,teams=p
 for r in sched[sched.gameday_d.eq(d)].itertuples(index=False):
  a,h=norm_team(r.away_team),norm_team(r.home_team)
  if teams in (a+h,h+a):
   k=pd.Timestamp(r.kickoff_utc);return {'kickoff':k,'season':int(getattr(r,'season',k.year)),'week':getattr(r,'week',''),'game_id':str(getattr(r,'game_id','') or f'{a}@{h}-{d}'),'game':f'{a}@{h}'}
 return None

def all_open_markets(series):
 rows=[];cur=''
 while True:
  p={'limit':1000,'series_ticker':series,'status':'open'}
  if cur:p['cursor']=cur
  d=api_get('/markets',p);rows.extend(d.get('markets',[]) or []);cur=str(d.get('cursor') or '')
  if not cur:return rows

def val(side):
 if not isinstance(side,dict):return None
 for k in ('close_dollars','close'):
  if side.get(k) is not None:
   try:
    x=float(side[k]);return x/100 if x>1 else x
   except:pass
 return None

def target_snapshot(series,ticker,target_ts,now_ts):
 start=target_ts-600;end=min(now_ts,target_ts+600)
 if end<=start:return None
 for path in (f'/series/{series}/markets/{ticker}/candlesticks',f'/historical/markets/{ticker}/candlesticks'):
  try:cs=api_get(path,{'start_ts':start,'end_ts':end,'period_interval':1}).get('candlesticks',[]) or []
  except Exception:continue
  if not cs:continue
  eligible=[c for c in cs if target_ts-60<=int(c.get('end_period_ts',0))<=target_ts]
  if not eligible:continue
  c=max(eligible,key=lambda z:int(z.get('end_period_ts',0)));yb,ya=val(c.get('yes_bid')),val(c.get('yes_ask'))
  if yb is None or ya is None:continue
  return {'ts':int(c['end_period_ts']),'yes_bid':yb,'yes_ask':ya,'age':target_ts-int(c['end_period_ts'])}
 return None

def taker_fee(price,contracts=1):
 return math.ceil((0.07*contracts*price*(1-price)-1e-12)*100)/100

def participant(m):
 return str(m.get('primary_participant_key') or '').strip(),str(m.get('subtitle') or m.get('title') or '').strip()

def load_rules(path):
 d=json.loads(Path(path).read_text());out=[]
 for r in d.get('primary',[])+d.get('secondary',[]):
  x=dict(r);x['series_ticker']=x.get('series_ticker') or SERIES_BY_FAMILY[x['prop_family']];out.append(x)
 return d,out

def empty_ledger():return pd.DataFrame(columns=LEDGER_COLUMNS)
def load_ledger(path):
 p=Path(path)
 if not p.exists() or p.stat().st_size==0:return empty_ledger()
 d=pd.read_csv(p,dtype=str).fillna('')
 for c in LEDGER_COLUMNS:
  if c not in d.columns:d[c]=''
 return d[LEDGER_COLUMNS]

def make_row(now,game,m,rule,snap,status,reason,thesis):
 side=rule['side'].upper();entry=(snap['yes_ask'] if side=='YES' else 1-snap['yes_bid']) if snap else '';fee=taker_fee(entry) if entry!='' and status=='PAPER_ENTRY' else '';key,name=participant(m)
 return {'run_timestamp':now.isoformat(),'season':game['season'],'week':game['week'],'game_id':game['game_id'],'kickoff_utc':game['kickoff'].isoformat(),'market_ticker':m.get('ticker',''),'series_ticker':rule['series_ticker'],'prop_family':rule['prop_family'],'rule_id':rule['rule_id'],'player_key':key,'player_name':name,'side':side,'target_horizon_minutes':rule['horizon_minutes'],'target_time_utc':(game['kickoff']-pd.Timedelta(minutes=int(rule['horizon_minutes']))).isoformat(),'quote_time_utc':datetime.fromtimestamp(snap['ts'],tz=timezone.utc).isoformat() if snap else '','quote_age_seconds':snap['age'] if snap else '','yes_bid':snap['yes_bid'] if snap else '','yes_ask':snap['yes_ask'] if snap else '','entry_price':entry,'contracts':1 if status=='PAPER_ENTRY' else 0,'gross_cost':entry if status=='PAPER_ENTRY' else '','modeled_fee':fee,'net_cost':entry+fee if status=='PAPER_ENTRY' else '','settlement_result':'','gross_pnl':'','net_pnl':'','status':status,'exclusion_reason':reason,'thesis_key':thesis}

def capture(now,rules,ledger,sched,grace_minutes):
 existing=set(ledger.apply(lambda r:f"{r['rule_id']}|{r['thesis_key']}|{r['target_time_utc']}",axis=1)) if len(ledger) else set();added=[];market_cache={};fetch_errors={}
 for rule in rules:
  series=rule['series_ticker']
  if series not in market_cache:
   try:market_cache[series]=all_open_markets(series);fetch_errors[series]=''
   except Exception as e:market_cache[series]=[];fetch_errors[series]=repr(e);print('API_ERROR',series,repr(e))
  groups={}
  for m in market_cache[series]:
   game=map_game(m.get('event_ticker'),sched)
   if not game:continue
   target=game['kickoff']-pd.Timedelta(minutes=int(rule['horizon_minutes']));delta=(now-target).total_seconds()/60
   if delta<0 or delta>grace_minutes or now>=game['kickoff']:continue
   key,_=participant(m)
   if not key:
    thesis=f"INVALID_IDENTITY|{m.get('ticker','')}";k=f"{rule['rule_id']}|{thesis}|{target.isoformat()}"
    if k not in existing:added.append(make_row(now,game,m,rule,None,'INVALID','MISSING_PARTICIPANT_KEY',thesis));existing.add(k)
    continue
   thesis=f"{game['game_id']}|{key}";k=f"{rule['rule_id']}|{thesis}|{target.isoformat()}"
   if k in existing:continue
   groups.setdefault((thesis,target.isoformat()),[]).append((m,game,target))
  for (thesis,target_iso),arr in groups.items():
   evaluated=[]
   for m,game,target in arr:
    snap=target_snapshot(series,m['ticker'],int(target.timestamp()),int(now.timestamp()))
    if not snap:continue
    side=rule['side'].upper();entry=snap['yes_ask'] if side=='YES' else 1-snap['yes_bid'];mid=rule.get('midpoint',(rule['price_min']+rule['price_max'])/2)
    evaluated.append((abs(entry-mid),snap['age'],m['ticker'],m,game,snap,entry))
   inband=[x for x in evaluated if rule['price_min']<=x[6]<=rule['price_max']]
   if inband:
    inband.sort(key=lambda x:(x[0],x[1],x[2]));_,_,_,m,game,snap,_=inband[0];row=make_row(now,game,m,rule,snap,'PAPER_ENTRY','',thesis)
   elif evaluated:
    evaluated.sort(key=lambda x:(x[0],x[1],x[2]));_,_,_,m,game,snap,_=evaluated[0];row=make_row(now,game,m,rule,snap,'NO_ENTRY','PRICE_OUTSIDE_FROZEN_BAND',thesis)
   else:
    m,game,_=sorted(arr,key=lambda x:x[0]['ticker'])[0];row=make_row(now,game,m,rule,None,'INVALID','NO_FRESH_QUOTE_AT_TARGET',thesis)
   added.append(row);existing.add(f"{rule['rule_id']}|{thesis}|{target_iso}")
 return added

def fetch_market_result(ticker):
 for path in (f'/markets/{ticker}',f'/historical/markets/{ticker}'):
  try:
   d=api_get(path);m=d.get('market',d);r=str(m.get('result') or '').lower()
   if r in ('yes','no'):return r
  except Exception:continue
 return ''
def settle(ledger,now):
 changed=False
 for i,r in ledger.iterrows():
  if r['status']!='PAPER_ENTRY' or r['settlement_result']:continue
  try:kickoff=pd.Timestamp(r['kickoff_utc'])
  except:continue
  if now<kickoff+pd.Timedelta(hours=3):continue
  result=fetch_market_result(r['market_ticker'])
  if not result:continue
  win=1 if result==r['side'].lower() else 0;entry=float(r['entry_price']);fee=float(r['modeled_fee']);gross=win-entry;net=gross-fee
  ledger.at[i,'settlement_result']=result.upper();ledger.at[i,'gross_pnl']=f'{gross:.4f}';ledger.at[i,'net_pnl']=f'{net:.4f}';ledger.at[i,'status']='SETTLED';changed=True
 return changed

def max_drawdown(vals):
 if not vals:return 0.0
 eq=0;peak=0;dd=0
 for v in vals:eq+=v;peak=max(peak,eq);dd=min(dd,eq-peak)
 return dd

def scorecard(ledger,outdir):
 p=Path(outdir);p.mkdir(parents=True,exist_ok=True);entries=ledger[ledger.status.isin(['PAPER_ENTRY','SETTLED'])].copy();settled=entries[entries.settlement_result.astype(str).ne('')].copy();rows=[]
 for rid,g in entries.groupby('rule_id'):
  s=settled[settled.rule_id.eq(rid)].copy()
  for c in ('entry_price','gross_pnl','net_pnl','gross_cost','net_cost'):s[c]=pd.to_numeric(s[c],errors='coerce')
  net=s.net_pnl.dropna().tolist() if len(s) else []
  rows.append({'rule_id':rid,'entries':len(g),'settled':len(s),'wins':int((s.net_pnl>0).sum()) if len(s) else 0,'losses':int((s.net_pnl<=0).sum()) if len(s) else 0,'gross_pnl':s.gross_pnl.sum() if len(s) else 0,'net_pnl':s.net_pnl.sum() if len(s) else 0,'net_roi_pct':100*s.net_pnl.sum()/s.net_cost.sum() if len(s) and s.net_cost.sum() else None,'avg_entry_price':pd.to_numeric(g.entry_price,errors='coerce').mean(),'max_drawdown':max_drawdown(net)})
 cols=['rule_id','entries','settled','wins','losses','gross_pnl','net_pnl','net_roi_pct','avg_entry_price','max_drawdown'];sc=pd.DataFrame(rows,columns=cols);sc.to_csv(p/'scorecard.csv',index=False)
 invalid=ledger[ledger.status.eq('INVALID')];noentry=ledger[ledger.status.eq('NO_ENTRY')]
 lines=['# NFL Prospective Paper Scorecard v0.01','',f'Updated: {datetime.now(timezone.utc).isoformat()}','','**PAPER ONLY. Real-money execution is OFF. Frozen price bands, sides, and horizons are unchanged.**','',f'Total observations: {len(ledger)}  ',f'Paper entries: {len(entries)}  ',f'Settled: {len(settled)}  ',f'No-entry price observations: {len(noentry)}  ',f'Invalid/QC observations: {len(invalid)}','','## Rule scorecard','',sc.to_markdown(index=False) if len(sc) else 'No paper entries yet.','','Primary validation decision must use PRIMARY_RECYDS_NO_40_49_T30 separately from exploratory rules.']
 (p/'SCORECARD.md').write_text('\n'.join(lines))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--rules',required=True);ap.add_argument('--ledger',required=True);ap.add_argument('--scorecard-dir',required=True);ap.add_argument('--grace-minutes',type=int,default=12);a=ap.parse_args();cfg,rules=load_rules(a.rules)
 if cfg.get('mode')!='PAPER_ONLY':raise SystemExit('Safety lock: rules config must remain PAPER_ONLY')
 now=pd.Timestamp.now(tz='UTC');sched=load_schedule();ledger=load_ledger(a.ledger);added=capture(now,rules,ledger,sched,a.grace_minutes)
 if added:ledger=pd.concat([ledger,pd.DataFrame(added)],ignore_index=True);print('added observations',len(added))
 changed=settle(ledger,now);Path(a.ledger).parent.mkdir(parents=True,exist_ok=True);ledger.to_csv(a.ledger,index=False);scorecard(ledger,a.scorecard_dir);print(f'ledger rows={len(ledger)} added={len(added)} settlement_changed={changed}')
if __name__=='__main__':main()
