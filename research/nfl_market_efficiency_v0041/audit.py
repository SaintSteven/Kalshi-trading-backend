import argparse, json, re
from pathlib import Path
import pandas as pd
from importlib.util import spec_from_file_location, module_from_spec

ROOT=Path(__file__).resolve().parents[2]
spec=spec_from_file_location('inventory',ROOT/'research/nfl_market_efficiency_v002/scanner.py')
mod=module_from_spec(spec);spec.loader.exec_module(mod)
SERIES=['KXNFLRECYDS','KXNFLREC','KXNFLRSHYDS','KXNFLPASSYDS','KXNFLANYTD']

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 sched=pd.read_csv('https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv')
 sched=sched[sched.game_type.eq('REG')].copy();sched['gameday']=pd.to_datetime(sched.gameday).dt.date
 sched['season']=pd.to_numeric(sched.season,errors='coerce')
 schedule_counts=sched.groupby('season').agg(scheduled_games=('game_id','nunique'),first_game=('gameday','min'),last_game=('gameday','max')).reset_index()
 schedule_counts.to_csv(out/'schedule_coverage.csv',index=False)
 rows=[];errors=[];samples=[]
 for series in SERIES:
  for source,fn in [('historical',mod.hist_markets),('recent_live',mod.recent_settled_markets)]:
   try:
    markets,pages=fn(series)
    for m in markets:
     rows.append({**{k:m.get(k) for k in ['ticker','event_ticker','result','open_time','close_time','settlement_ts','primary_participant_key']},'series_ticker':series,'source':source})
    samples.append(dict(series_ticker=series,source=source,count=len(markets),pages=pages))
   except Exception as e:errors.append(dict(series_ticker=series,source=source,error=repr(e)))
 if errors:pd.DataFrame(errors).to_csv(out/'errors.csv',index=False)
 if errors:raise SystemExit('Archive retrieval incomplete; see errors.csv')
 d=pd.DataFrame(rows).drop_duplicates(['series_ticker','ticker']);d['event_date']=pd.to_datetime(d.event_ticker.astype(str).str.extract(r'-(\d{2}[A-Z]{3}\d{2})')[0],format='%y%b%d',errors='coerce').dt.date
 d['event_year']=pd.to_datetime(d.event_date).dt.year
 d['open_dt']=pd.to_datetime(d.open_time,utc=True,errors='coerce');d['close_dt']=pd.to_datetime(d.close_time,utc=True,errors='coerce')
 d['settled']=d.result.astype(str).str.lower().isin(['yes','no'])
 # Match exact event date and both teams; use official season rather than calendar year.
 teams=sched[['season','game_id','gameday','away_team','home_team']].copy()
 teams['team_pair']=teams.away_team+teams.home_team
 aliases={'LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC','JAC':'JAX','WSH':'WAS'}
 def pair(e):
  m=re.search(r'-(\d{2}[A-Z]{3}\d{2})([A-Z]+)$',str(e).upper())
  if not m:return None
  t=m.group(2)
  for a,b in aliases.items():pass
  return t
 d['team_pair']=d.event_ticker.map(pair)
 lookup={}
 for r in teams.itertuples():
  for key in (r.away_team+r.home_team,r.home_team+r.away_team):lookup[(r.gameday,key)]=(r.season,r.game_id)
 mapped=d.apply(lambda r:lookup.get((r.event_date,r.team_pair),(None,None)),axis=1)
 d['nfl_season']=[x[0] for x in mapped];d['game_id']=[x[1] for x in mapped]
 d['mapping_status']=d.game_id.notna().map({True:'MAPPED',False:'UNMAPPED'})
 d.to_csv(out/'market_inventory_audit.csv',index=False)
 coverage=d.groupby(['series_ticker','event_year','nfl_season'],dropna=False).agg(markets=('ticker','nunique'),events=('event_ticker','nunique'),mapped_games=('game_id','nunique'),settled=('settled','sum'),first_open=('open_dt','min'),last_close=('close_dt','max')).reset_index()
 coverage.to_csv(out/'archive_coverage.csv',index=False)
 pd.DataFrame(samples).to_csv(out/'retrieval_counts.csv',index=False)
 # Explicitly report all older event years, including zero rows.
 years=[]
 for y in range(2021,2027):
  g=d[d.event_year.eq(y)];years.append(dict(event_year=y,markets=g.ticker.nunique(),events=g.event_ticker.nunique(),mapped_games=g.game_id.nunique(),settled=int(g.settled.sum())))
 pd.DataFrame(years).to_csv(out/'year_coverage.csv',index=False)
 lines=['# v0.04.1 Archive Coverage Audit','','This is a diagnostic, not a new strategy backtest. No frozen rules are changed.','', '## Retrieval counts','',pd.DataFrame(samples).to_markdown(index=False),'','## Event-year coverage','',pd.DataFrame(years).to_markdown(index=False),'','## Official NFL season coverage','',coverage.to_markdown(index=False),'','Calendar year is not NFL season. January games belong to the previous season. Counts of markets, events and games are distinct. Missing older markets are not proof they never existed.','','## Retrieval errors','',str(errors)]
 (out/'SUMMARY.md').write_text('\n'.join(lines));print('\n'.join(lines))
if __name__=='__main__':main()
