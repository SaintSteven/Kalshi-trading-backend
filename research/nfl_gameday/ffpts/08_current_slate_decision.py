#!/usr/bin/env python3
"""FFPTS v1 current-slate decision pipeline. Research/paper only."""
import json,re,os
from pathlib import Path
from datetime import datetime
import pandas as pd
R=Path('research/nfl_fantasy_points_validation'); O=R/'results'
model=json.loads((R/'FROZEN_MODEL_V1.json').read_text())
hist=pd.read_csv(O/'00_walk_forward_predictions.csv'); hist['resid']=hist.actual_fp-hist.pred_fp
p=pd.read_csv(O/'09_current_slate_projections.csv'); m=pd.read_csv(O/'03_kalshi_fantasy_markets.csv')
SCHED_URL='https://github.com/nflverse/nfldata/raw/master/data/games.csv'
sched=pd.read_csv(SCHED_URL,low_memory=False); sched['gameday']=pd.to_datetime(sched['gameday'],errors='coerce').dt.date
def event_date(event_ticker):
 hit=re.search(r'-(\d{2}[A-Z]{3}\d{2})',str(event_ticker).upper()); return datetime.strptime(hit.group(1),'%y%b%d').date() if hit else None
def slate_week(event_ticker):
 dt=event_date(event_ticker); q=sched[(sched.gameday==dt)&(pd.to_numeric(sched.season,errors='coerce')==2026)]; weeks=sorted(set(pd.to_numeric(q.week,errors='coerce').dropna().astype(int)))
 if len(weeks)!=1: raise SystemExit(f'Slate consistency gate failed: event={event_ticker} date={dt} schedule_weeks={weeks}')
 return weeks[0]
def norm(s):
 s=str(s or '').lower(); s=re.sub(r'\b(jr|sr|ii|iii|iv)\.?\b','',s); return re.sub(r'[^a-z0-9]','',s)
# Scope before identity/slate evaluation. NFL_EVENT_FILTER is human-friendly (ATL-GB),
# while Kalshi event tickers concatenate teams (ATLGB); normalize both sides.
scope=os.environ.get('NFL_EVENT_FILTER','').strip()
if scope and not m.empty:
 needle=norm(scope)
 m=m[m.event_ticker.astype(str).map(norm).str.contains(needle,regex=False,na=False)].copy()
 if m.empty: raise SystemExit(f'FFPTS scope matched zero markets: {scope}')
FULL={}
for r in p.itertuples(): FULL.setdefault(norm(getattr(r,'full_name','')),[]).append(r)
ABBR={}
for r in p.itertuples(): ABBR.setdefault(str(r.player_name).lower(),[]).append(r)
rows=[]; excluded=[]
for x in m.itertuples():
 if pd.notna(getattr(x,'exclusion_reason',None)): continue
 pos=str(x.position).upper(); player_token=str(x.ticker).replace(str(x.event_ticker)+'-','').split('-')[0].upper(); team_prefix=lambda t:{'LV':'LVA'}.get(str(t).upper(),str(t).upper()); full_candidates=[r for r in FULL.get(norm(x.kalshi_player_name),[]) if str(r.position).upper()==pos and player_token.startswith(team_prefix(getattr(r,'team','')))]
 identity_method='full_name'
 if len(full_candidates)>=1:candidates=full_candidates
 else:
  name=str(x.matched_player).lower(); candidates=[r for r in ABBR.get(name,[]) if str(r.position).upper()==pos and player_token.startswith(team_prefix(getattr(r,'team','')))]; identity_method='abbrev_position_team_fallback'
 if len(candidates)!=1:
  excluded.append({'ticker':x.ticker,'player':x.kalshi_player_name,'position':pos,'reason':'UNRESOLVED_CURRENT_PROJECTION_IDENTITY','candidates':len(candidates)}); continue
 r=candidates[0]; market_week=slate_week(x.event_ticker)
 if int(r.projection_season)!=2026 or int(r.projection_week)!=int(market_week): raise SystemExit(f'Slate consistency gate failed: {x.ticker} market_week={market_week} projection={r.projection_season}-W{r.projection_week}')
 proj=float(r.projection_fp); th=float(x.threshold); residuals=hist.loc[hist.position==r.position,'resid'].dropna().to_numpy(); fair=float(((residuals>(th-proj)).sum()+.5)/(len(residuals)+1)); ask=float(x.yes_ask_probability) if pd.notna(x.yes_ask_probability) else None; bid=float(x.yes_bid_probability) if pd.notna(x.yes_bid_probability) else None; edge=fair-ask if ask is not None else None
 tail=fair<.15 or fair>.85; qc_flags=[]
 if int(getattr(r,'projection_week',99))==1 and r.position=='QB':qc_flags.append('WEEK1_QB_REVIEW')
 if int(getattr(r,'projection_week',99))==1 and proj<8:qc_flags.append('WEEK1_LOW_ROLE_REVIEW')
 if bool(getattr(r,'team_changed',False)):qc_flags.append('TEAM_CHANGE_REVIEW')
 if ask is None:decision='PASS';qc_flags.append('NO_EXECUTABLE_QUOTE')
 elif tail:decision='WATCH';qc_flags.append('TAIL_CALIBRATION')
 elif qc_flags:decision='WATCH'
 elif edge>=.08:decision='PAPER'
 elif edge>=.03:decision='WATCH';qc_flags.append('MARGINAL_EDGE')
 else:decision='PASS';qc_flags.append('NO_EDGE')
 rows.append({'ticker':x.ticker,'player':x.kalshi_player_name,'player_id':r.player_id,'position':r.position,'team':getattr(r,'team',None),'identity_method':identity_method,'projection_fp':proj,'projection_season':int(r.projection_season),'projection_week':int(r.projection_week),'market_week':int(market_week),'event_ticker':x.event_ticker,'threshold':th,'fair_yes':fair,'yes_bid':bid,'yes_ask':ask,'edge_vs_ask':edge,'decision':decision,'qc':'|'.join(qc_flags) if qc_flags else 'PASS','model':model.get('model_version','NFL-FFPTS-RIDGE-EMPIRICAL-v1')})
o=pd.DataFrame(rows)
if not o.empty:o=o.sort_values(['decision','edge_vs_ask'],ascending=[True,False])
o.to_csv(O/'08_current_slate_decisions.csv',index=False)
summary={'pipeline':'NFL FFPTS v1','research_only':True,'orders_placed':False,'frozen_model':model.get('model_version','NFL-FFPTS-RIDGE-EMPIRICAL-v1'),'scope':scope or None,'supported_markets':len(o),'excluded_unresolved_identities':len(excluded),'quotes_present':int(o.yes_ask.notna().sum()) if len(o) else 0,'full_name_identity_matches':int((o.identity_method=='full_name').sum()) if len(o) else 0,'fallback_identity_matches':int((o.identity_method=='abbrev_position_team_fallback').sum()) if len(o) else 0,'team_change_reviews':int(o.qc.str.contains('TEAM_CHANGE_REVIEW').sum()) if len(o) else 0,'slate_consistency_pass':bool(len(o) and (o.projection_week==o.market_week).all()),'PAPER':int((o.decision=='PAPER').sum()) if len(o) else 0,'WATCH':int((o.decision=='WATCH').sum()) if len(o) else 0,'PASS':int((o.decision=='PASS').sum()) if len(o) else 0,'method':'Independent current-slate Ridge point projection plus frozen empirical position residual threshold probability; Kalshi executable ask used only after fair probability is produced.','projection_source':'09_current_slate_projections.csv','identity_gate':'full Kalshi name -> full nflverse display name+position+market team; guarded abbreviated-name+position+market-team fallback only when unique','slate_gate':'Kalshi event date -> nflverse schedule season/week must equal projection season/week; fail closed on mismatch','qc_gate':'Frozen Week 1 QB, low-role, team-change, and tail-calibration review rules are applied before PAPER classification.','warning':'Research/paper only. Market prices do not alter model projections or fair probabilities.'}
(O/'08_current_slate_summary.json').write_text(json.dumps(summary,indent=2));(O/'08_excluded_identities.json').write_text(json.dumps(excluded,indent=2));print(json.dumps(summary,indent=2));print(o.to_json(orient='records',indent=2));print(json.dumps({'excluded_identities':excluded},indent=2))
if len(o)==0 or summary['quotes_present']!=len(o) or not summary['slate_consistency_pass']:raise SystemExit('Current-slate gate failed')
