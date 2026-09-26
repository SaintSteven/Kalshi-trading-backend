#!/usr/bin/env python3
"""Audit captured receiving quote identity before any retrospective grading."""
from pathlib import Path
import json, re
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'research/nfl_prospective_paper_v001/data'
OUT=Path('research/nfl_receiving_v0_11/results'); OUT.mkdir(parents=True,exist_ok=True)

def col(df,*names):
    for n in names:
        if n in df.columns:return n

def main():
    q=pd.read_csv(DATA/'quote_history.csv',low_memory=False)
    ticker=col(q,'market_ticker','ticker'); kc=col(q,'kickoff_utc','kickoff'); tc=col(q,'captured_at','snapshot_at','timestamp','collected_at','updated_at')
    fam=col(q,'prop_family','family')
    if fam:q=q[q[fam].astype(str).eq('receiving_yards')].copy()
    q[tc]=pd.to_datetime(q[tc],utc=True,errors='coerce'); q[kc]=pd.to_datetime(q[kc],utc=True,errors='coerce')
    q=q[q[tc].notna() & q[kc].notna() & (q[tc]<q[kc])].sort_values(tc).groupby(ticker,as_index=False).tail(1).copy()
    pat=re.compile(r'KXNFLRECYDS-(\d{2}[A-Z]{3}\d{2})')
    def parse_date(t):
        m=pat.search(str(t))
        return pd.to_datetime(m.group(1),format='%y%b%d',errors='coerce') if m else pd.NaT
    q['ticker_date']=q[ticker].map(parse_date)
    q['stored_kickoff_date']=q[kc].dt.tz_convert('US/Eastern').dt.normalize().dt.tz_localize(None)
    q['ticker_vs_kickoff_days']=(q['stored_kickoff_date']-q['ticker_date']).dt.days

    sched=pd.read_csv('https://github.com/nflverse/nfldata/raw/master/data/games.csv')
    sched['gameday_dt']=pd.to_datetime(sched['gameday'],errors='coerce')
    sched['game_key']=sched['away_team'].astype(str)+'@'+sched['home_team'].astype(str)
    game=col(q,'game','event_ticker')
    if not game: raise SystemExit('quote history lacks game identity')
    aliases={'JAC':'JAX','WSH':'WAS','LA':'LAR'}
    def norm_game(x):
        s=str(x)
        if '@' not in s:return s
        a,h=s.split('@',1); return aliases.get(a,a)+'@'+aliases.get(h,h)
    q['game_normalized']=q[game].map(norm_game)

    # Rename canonical schedule fields before merge so stored quote season/week can never
    # collide with them or be mistaken for verified schedule identity.
    candidates=sched[sched['season'].between(2024,2026)][['season','week','gameday_dt','game_key']].copy()
    candidates=candidates.rename(columns={'season':'schedule_season','week':'schedule_week'})
    exact=q.merge(candidates,left_on=['ticker_date','game_normalized'],right_on=['gameday_dt','game_key'],how='left')
    exact['schedule_exact_match']=exact['schedule_season'].notna()
    cols=[ticker,game,'game_normalized','ticker_date',kc,'stored_kickoff_date','ticker_vs_kickoff_days']
    for c in ['season','week']:
        if c in q.columns: cols.append(c)
    audit=exact[cols+['schedule_season','schedule_week','schedule_exact_match']].copy()
    audit.to_csv(OUT/'quote_identity_audit.csv',index=False)

    total=q[ticker].nunique(); parsed=int(q.ticker_date.notna().sum()); kickoff_match=int((q.ticker_vs_kickoff_days==0).sum()); exact_n=int(exact.loc[exact.schedule_exact_match,ticker].nunique())
    by_date=q.groupby(q.ticker_date.dt.date)[ticker].nunique().to_dict()
    schedule_matches=exact[exact.schedule_exact_match].groupby(['schedule_season','schedule_week'])[ticker].nunique().to_dict()
    summary={'unique_markets':total,'ticker_dates_parsed':parsed,'ticker_date_equals_stored_kickoff_date':kickoff_match,'exact_ticker_date_plus_matchup_schedule_matches':exact_n,'markets_without_exact_schedule_identity':total-exact_n,'markets_by_ticker_date':{str(k):int(v) for k,v in by_date.items()},'exact_schedule_matches_by_season_week':{str(k):int(v) for k,v in schedule_matches.items()},'grading_allowed':bool(exact_n==total)}
    (OUT/'quote_identity_summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
    if exact_n!=total: raise SystemExit('Quote identity audit failed: historical capture cannot be safely graded until every ticker date+matchup maps to canonical schedule.')
if __name__=='__main__':main()
