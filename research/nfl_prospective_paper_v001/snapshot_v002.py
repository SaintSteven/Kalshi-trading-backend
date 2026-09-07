"""Read-only NFL snapshot collector v0.02. Preserves existing historical observations."""
import argparse, json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import snapshot_collector as old
from identity import player_identity

HISTORY_COLS=old.CURRENT_COLS+['source','observation_status']

def collect(now,sched,old_snap,old_history):
    current=[];new=[];history=[];health=[]
    existing=set(zip(old_snap.market_ticker,old_snap.snapshot_label)) if len(old_snap) else set()
    now_ts=int(now.timestamp())
    for fam,series in old.SERIES.items():
        try:markets=old.open_markets(series);error=''
        except Exception as e:markets=[];error=repr(e)
        mapped=0;bad=0
        for m in markets:
            game=old.map_game(m.get('event_ticker'),sched)
            if not game or int(game['season'])!=2026:continue
            if game['kickoff']<=now:continue
            mapped+=1;key,reason=player_identity(m)
            if reason:bad+=1
            yb,ya,nb,na=old.market_quote(m)
            valid=yb is not None and ya is not None and 0<=yb<=ya<=1
            qc='PASS' if key and valid else 'FAIL' if not key else 'WARN'
            why=reason or ('' if valid else 'NO_VALID_TWO_SIDED_QUOTE')
            base=dict(updated_at=now.isoformat(),season=game['season'],week=game['week'],game_id=game['game_id'],game=game['game'],kickoff_utc=game['kickoff'].isoformat(),market_ticker=m.get('ticker',''),event_ticker=m.get('event_ticker',''),series_ticker=series,prop_family=fam,player_key=key,player_name=m.get('subtitle') or m.get('title') or '',yes_bid=yb,yes_ask=ya,no_bid=nb,no_ask=na,qc_status=qc,qc_reason=why)
            current.append(base)
            if valid:history.append({**base,'source':'market_object','observation_status':'OBSERVED'})
            if not key:continue
            labels=[('FIRST_OBSERVED',now)]+old.labels(game)
            for label,target in labels:
                ticker=m.get('ticker','')
                if (ticker,label) in existing or now<target:continue
                if label=='FIRST_OBSERVED':
                    if not valid:continue
                    s=dict(ts=now_ts,age=0,yb=yb,ya=ya,nb=nb,na=na,source='market_object')
                else:s=old.target_snapshot(series,ticker,int(target.timestamp()),now_ts)
                row={**{k:base[k] for k in old.SNAP_COLS if k in base},'captured_at':now.isoformat(),'snapshot_label':label,'target_time_utc':target.isoformat(),'quote_time_utc':'','quote_age_seconds':'','source':'','qc_status':'INVALID','qc_reason':'NO_FRESH_QUOTE_AT_TARGET'}
                if s and 0<=s['yb']<=s['ya']<=1:
                    row.update(quote_time_utc=datetime.fromtimestamp(s['ts'],tz=timezone.utc).isoformat(),quote_age_seconds=s['age'],yes_bid=s['yb'],yes_ask=s['ya'],no_bid=s['nb'],no_ask=s['na'],source=s['source'],qc_status='PASS',qc_reason='')
                else:row.update(yes_bid='',yes_ask='',no_bid='',no_ask='')
                new.append(row);existing.add((ticker,label))
        health.append(dict(updated_at=now.isoformat(),series_ticker=series,prop_family=fam,fetch_ok=not bool(error),open_markets=len(markets),mapped_markets=mapped,missing_game_map=len(markets)-mapped,missing_participant_key=bad,error=error))
    return current,new,history,health

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--snapshots',required=True);ap.add_argument('--current',required=True);ap.add_argument('--health',required=True);ap.add_argument('--history',required=True);a=ap.parse_args()
    now=pd.Timestamp.now(tz='UTC');sched=old.load_schedule();snap=old.load_csv(a.snapshots,old.SNAP_COLS);hist=old.load_csv(a.history,HISTORY_COLS)
    current,new,history,health=collect(now,sched,snap,hist)
    if new:snap=pd.concat([snap,pd.DataFrame(new)],ignore_index=True)
    if history:
        hist=pd.concat([hist,pd.DataFrame(history)],ignore_index=True)
        hist=hist.drop_duplicates(['market_ticker','updated_at'],keep='first')
    for path,df,cols in [(a.snapshots,snap,old.SNAP_COLS),(a.history,hist,HISTORY_COLS),(a.current,pd.DataFrame(current),old.CURRENT_COLS),(a.health,pd.DataFrame(health),None)]:
        p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);df.to_csv(p,index=False,columns=cols)
    print(json.dumps({'current':len(current),'new_snapshots':len(new),'history_rows':len(hist),'source_failures':sum(bool(x['error']) for x in health)}))
if __name__=='__main__':main()
