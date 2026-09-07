import argparse,time,re
from pathlib import Path
from datetime import time as dtime
import requests,pandas as pd

BASE='https://api.elections.kalshi.com/trade-api/v2'
UA={'User-Agent':'kalshi-nfl-market-efficiency-v0.05-entry-timing'}
SCHEDULE_URL='https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'
TEAM_ALIAS={'LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC','JAC':'JAX','WSH':'WAS'}
HORIZONS_H=[48,24,12,6,3,1,.5]


def get(path,params=None):
    last=None
    for i in range(7):
        try:
            r=requests.get(BASE+path,params=params,headers=UA,timeout=45)
            if r.status_code==429 or 500<=r.status_code<600:
                last=RuntimeError(f'HTTP {r.status_code}: {r.text[:160]}')
                time.sleep(min(12,.5*2**i)); continue
            r.raise_for_status(); return r.json()
        except Exception as e:
            last=e; time.sleep(min(12,.5*2**i))
    raise last


def val(side):
    if not isinstance(side,dict): return None
    for k in ('close_dollars','close'):
        if side.get(k) is not None:
            try:
                x=float(side[k]); return x/100 if x>1 else x
            except Exception: pass
    return None


def snapshot(cs,target,max_age=60):
    eligible=[c for c in cs if int(c.get('end_period_ts',0))<=target and int(c.get('end_period_ts',0))>=target-max_age]
    if not eligible:return None
    c=max(eligible,key=lambda z:int(z.get('end_period_ts',0)))
    ya=val(c.get('yes_ask')); yb=val(c.get('yes_bid'))
    if ya is None or yb is None:return None
    return {'ts':int(c['end_period_ts']),'yes_ask':ya,'yes_bid':yb,'no_ask':1-yb,'no_bid':1-ya,'age_sec':target-int(c['end_period_ts'])}


def candles(series,ticker,start,end,source):
    paths=[f'/historical/markets/{ticker}/candlesticks',f'/series/{series}/markets/{ticker}/candlesticks']
    if source=='recent_live': paths.reverse()
    last=None
    for p in paths:
        try:
            data=get(p,{'start_ts':start,'end_ts':end,'period_interval':1}).get('candlesticks',[]) or []
            if data:return data
        except Exception as e:last=e
    if last: raise last
    return []


def result_yes(x):
    s=str(x).lower(); return 1 if s=='yes' else (0 if s=='no' else None)


def bucket(p):
    cuts=[(.05,'<5c'),(.10,'5-9c'),(.15,'10-14c'),(.20,'15-19c'),(.30,'20-29c'),(.40,'30-39c'),(.50,'40-49c'),(.60,'50-59c'),(.70,'60-69c'),(.80,'70-79c'),(.90,'80-89c')]
    for x,n in cuts:
        if p<x:return n
    return '90c+'


def norm_team(x):return TEAM_ALIAS.get(str(x).upper(),str(x).upper())


def parse_event(e):
    s=str(e).upper(); m=re.search(r'-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$',s)
    if not m:return None
    yy,mon,dd,teams=m.groups()
    try:d=pd.to_datetime(f'20{yy}-{mon}-{dd}',format='%Y-%b-%d',utc=True)
    except Exception:return None
    return d.date(),teams


def load_schedule():
    g=pd.read_csv(SCHEDULE_URL)
    if 'game_type' in g.columns:g=g[g['game_type'].astype(str).eq('REG')].copy()
    g['gameday']=pd.to_datetime(g['gameday'],errors='coerce').dt.date
    if 'gametime' in g.columns:
        local=pd.to_datetime(g['gameday'].astype(str)+' '+g['gametime'].astype(str),errors='coerce')
        g['kickoff_utc']=local.dt.tz_localize('America/New_York',ambiguous='NaT',nonexistent='shift_forward').dt.tz_convert('UTC')
    elif 'start_time' in g.columns:
        g['kickoff_utc']=pd.to_datetime(g['start_time'],errors='coerce',utc=True)
    else:raise RuntimeError('schedule missing gametime/start_time')
    return g


def map_kickoff(event,sched):
    p=parse_event(event)
    if not p:return None,None,None,None
    d,teams=p; c=sched[sched.gameday.eq(d)]
    for r in c.itertuples(index=False):
        a,h=norm_team(r.away_team),norm_team(r.home_team)
        if teams in (a+h,h+a):
            return pd.Timestamp(r.kickoff_utc),f'{a}@{h}',getattr(r,'season',None),getattr(r,'week',None)
    return None,None,None,None


def td_subtype(series):
    s=str(series).upper()
    if s=='KXNFLANYTD':return 'anytime_td'
    if s=='KXNFLFIRSTTD':return 'first_td'
    if s=='KXNFL2TD':return 'multiple_td'
    return 'other_td'


def game_morning_target(kickoff):
    local=kickoff.tz_convert('America/New_York')
    morning=pd.Timestamp.combine(local.date(),dtime(9,0)).tz_localize('America/New_York')
    return morning.tz_convert('UTC')


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--inventory',required=True); ap.add_argument('--out',required=True); ap.add_argument('--prop-class',required=True); ap.add_argument('--shard-index',type=int,required=True); ap.add_argument('--shard-count',type=int,required=True); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    sched=load_schedule(); inv=pd.read_csv(a.inventory)
    base=inv[(inv.prop_class==a.prop_class)&inv.result.astype(str).str.lower().isin(['yes','no'])].copy().sort_values('market_ticker').reset_index(drop=True)
    base=base.iloc[[i for i in range(len(base)) if i%a.shard_count==a.shard_index]]
    rows=[]; errs=[]; map_fail=0; aug=0
    for j,m in enumerate(base.itertuples(index=False),1):
        kickoff,game,nfl_season,week=map_kickoff(getattr(m,'event_ticker',None),sched)
        if kickoff is None: map_fail+=1; continue
        if kickoff.month==8: aug+=1; continue
        sett=result_yes(m.result)
        if sett is None: continue
        kts=int(kickoff.timestamp())
        morning=game_morning_target(kickoff)
        targets={f'T-{h:g}h':kts-int(h*3600) for h in HORIZONS_H}
        if morning<kickoff:targets['GAME_MORNING_9ET']=int(morning.timestamp())
        start=min(targets.values())-120
        try:cs=candles(m.series_ticker,m.market_ticker,start,kts-int(20*60),getattr(m,'source','historical'))
        except Exception as e:
            errs.append({'ticker':m.market_ticker,'series_ticker':m.series_ticker,'error':repr(e)}); continue
        snaps={name:snapshot(cs,ts,60) for name,ts in targets.items()}
        exit30=snaps.get('T-0.5h')
        subtype=td_subtype(m.series_ticker) if a.prop_class=='player_tds' else a.prop_class
        participant=str(getattr(m,'primary_participant_key','') or '')
        thesis=f'{m.event_ticker}|{participant or m.market_ticker.rsplit("-",1)[-1]}'
        for name,s in snaps.items():
            if not s:continue
            for side in ('yes','no'):
                entry=s[f'{side}_ask']; win=sett if side=='yes' else 1-sett
                move=(exit30[f'{side}_bid']-entry) if name!='T-0.5h' and exit30 else None
                rows.append({'prop_class':a.prop_class,'prop_subtype':subtype,'series_ticker':m.series_ticker,'market_ticker':m.market_ticker,'event_ticker':m.event_ticker,'game':game,'kickoff_utc':kickoff.isoformat(),'nfl_season':nfl_season,'week':week,'participant_key':participant,'thesis_key':thesis,'side':side,'entry_window':name,'target_ts':targets[name],'entry_ts':s['ts'],'quote_age_sec':s['age_sec'],'entry_price':entry,'price_bucket':bucket(entry),'settled_win':win,'settlement_pnl':win-entry,'exit_30m_bid':exit30[f'{side}_bid'] if exit30 else None,'movement_pnl_to_30m':move})
        if j%250==0:print(a.prop_class,a.shard_index,j)
    pd.DataFrame(rows).to_csv(out/'timing_ledger.csv',index=False); pd.DataFrame(errs).to_csv(out/'errors.csv',index=False)
    (out/'SHARD_DONE.txt').write_text(f'{a.prop_class} shard={a.shard_index} markets={len(base)} rows={len(rows)} errors={len(errs)} map_fail={map_fail} august_excluded={aug}\n')
    if not rows:raise SystemExit('No timing rows produced')

if __name__=='__main__':main()
