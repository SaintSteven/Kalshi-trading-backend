import argparse, json
from pathlib import Path
import pandas as pd
import snapshot_collector as old
from identity import player_identity

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--current',required=True)
    ap.add_argument('--health',required=True)
    a=ap.parse_args()
    now=pd.Timestamp.now(tz='UTC')
    sched=old.load_schedule()
    current=[]; health=[]
    for fam,series in old.SERIES.items():
        try:
            markets=old.open_markets(series); error=''
        except Exception as e:
            markets=[]; error=repr(e)
        mapped=0; bad=0
        for m in markets:
            game=old.map_game(m.get('event_ticker'),sched)
            if not game or int(game['season'])!=2026 or game['kickoff']<=now:
                continue
            mapped+=1
            key,reason=player_identity(m)
            if reason: bad+=1
            yb,ya,nb,na=old.market_quote(m)
            valid=yb is not None and ya is not None and 0<=yb<=ya<=1
            qc='PASS' if key and valid else 'FAIL' if not key else 'WARN'
            why=reason or ('' if valid else 'NO_VALID_TWO_SIDED_QUOTE')
            current.append(dict(
                updated_at=now.isoformat(),season=game['season'],week=game['week'],
                game_id=game['game_id'],game=game['game'],kickoff_utc=game['kickoff'].isoformat(),
                market_ticker=m.get('ticker',''),event_ticker=m.get('event_ticker',''),
                series_ticker=series,prop_family=fam,player_key=key,
                player_name=m.get('subtitle') or m.get('title') or '',
                yes_bid=yb,yes_ask=ya,no_bid=nb,no_ask=na,qc_status=qc,qc_reason=why))
        health.append(dict(updated_at=now.isoformat(),series_ticker=series,prop_family=fam,
            fetch_ok=not bool(error),open_markets=len(markets),mapped_markets=mapped,
            missing_game_map=len(markets)-mapped,missing_participant_key=bad,error=error))
    Path(a.current).parent.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(current,columns=old.CURRENT_COLS).to_csv(a.current,index=False)
    pd.DataFrame(health).to_csv(a.health,index=False)
    print(json.dumps({'current':len(current),'source_failures':sum(bool(x['error']) for x in health)}))

if __name__=='__main__':
    main()
