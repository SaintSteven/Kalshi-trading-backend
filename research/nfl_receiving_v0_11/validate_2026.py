#!/usr/bin/env python3
"""REC-v0.11 2026 W1/W2 retrospective: grade captured W1, recover W2 from Kalshi historical candles, report weekly+combined."""
from pathlib import Path
import json,re,subprocess,sys,tempfile,unicodedata
import pandas as pd
import requests
ROOT=Path(__file__).resolve().parents[2]; LEGACY=ROOT/'research/nfl_prospective_paper_v001'; DATA=LEGACY/'data'; OUT=Path('research/nfl_receiving_v0_11/results'); OUT.mkdir(parents=True,exist_ok=True)
SPEC=json.loads((ROOT/'research/nfl_gameday/RECEIVING_FROZEN_V011.json').read_text()); RULE=SPEC['production_rule']; BASE='https://api.elections.kalshi.com/trade-api/v2'; UA={'User-Agent':'rec-v011-2026-retro/1.0'}
def col(df,*ns):
 for n in ns:
  if n in df.columns:return n
def api(path,params=None):
 r=requests.get(BASE+path,params=params,headers=UA,timeout=40); r.raise_for_status(); return r.json()
def num(x):
 if x is None or x=='':return None
 try:
  v=float(x); return v/100 if v>1 else v
 except:return None
def cv(side):
 if not isinstance(side,dict):return None
 return num(side.get('close_dollars',side.get('close')))
def normteam(x): return {'JAC':'JAX','WSH':'WAS','LA':'LAR','STL':'LAR','OAK':'LV','SD':'LAC'}.get(str(x).upper(),str(x).upper())
def normname(x):
 s=unicodedata.normalize('NFKD',str(x)).encode('ascii','ignore').decode().lower(); s=re.sub(r':.*$','',s); s=re.sub(r'\b(jr|sr|ii|iii|iv)\b','',s); return re.sub(r'[^a-z0-9]','',s)
def ticker_player_key(ticker):
 p=str(ticker or '').upper().split('-'); return p[-2] if len(p)>=4 else ''
def parse_event(e):
 m=re.search(r'-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$',str(e).upper())
 if not m:return None
 yy,mon,dd,teams=m.groups()
 try:d=pd.to_datetime(f'20{yy}-{mon}-{dd}',format='%Y-%b-%d').date()
 except:return None
 return d,teams
def load_schedule():
 s=pd.read_csv('https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'); s=s[(s.season==2026)&(s.game_type.astype(str)=='REG')].copy(); s['date']=pd.to_datetime(s.gameday).dt.date
 local=pd.to_datetime(s.gameday.astype(str)+' '+s.gametime.astype(str),errors='coerce'); s['kickoff']=local.dt.tz_localize('America/New_York',ambiguous='NaT',nonexistent='shift_forward').dt.tz_convert('UTC'); return s
def map_event(e,s):
 p=parse_event(e)
 if not p:return None
 d,teams=p
 for r in s[s.date.eq(d)].itertuples(index=False):
  a,h=normteam(r.away_team),normteam(r.home_team)
  if teams in (a+h,h+a):return {'season':2026,'week':int(r.week),'game_id':str(r.game_id),'game':f'{a}@{h}','kickoff_utc':pd.Timestamp(r.kickoff)}
 return None
def model_select(markets):
 with tempfile.TemporaryDirectory() as td:
  td=Path(td); mi=td/'m.csv'; pr=td/'p.csv'; mp=td/'map.csv'; fv=td/'f.csv'; markets.to_csv(mi,index=False)
  subprocess.run([sys.executable,LEGACY/'generate_receiving_projections.py','--markets',mi,'--projections',pr,'--mapping',mp],check=True)
  subprocess.run([sys.executable,LEGACY/'receiving_feed.py','--markets',mi,'--projections',pr,'--mapping',mp,'--output',fv],check=True); f=pd.read_csv(fv)
 keep=[c for c in ['market_ticker','player_id','threshold','projection','fair_no','qc_status','model_version'] if c in f]
 d=markets.merge(f[keep],on='market_ticker',how='inner',suffixes=('_market','_model')); th=col(d,'threshold','threshold_model','line','strike'); na=col(d,'no_ask','no_ask_probability')
 if not th or not na: raise SystemExit(f'model feed missing threshold/no ask; columns={list(d.columns)}')
 d[th]=pd.to_numeric(d[th],errors='coerce'); d[na]=pd.to_numeric(d[na],errors='coerce'); d['fair_no']=pd.to_numeric(d.fair_no,errors='coerce'); d['edge']=d.fair_no-d[na]
 d=d[(d[th]<=float(RULE['threshold_max_yards']))&(d.edge>=float(RULE['minimum_edge_vs_executable_ask']))].copy(); qc=col(d,'qc_status_model','qc_status')
 if qc:d=d[d[qc].isin(['PASS','MODEL_AVAILABLE'])]
 player=col(d,'player_name','player'); keys=[c for c in ['game',player] if c]
 if keys:d=d.sort_values('edge',ascending=False).drop_duplicates(keys)
 return d,th,na
def grade(d,th,na,stats,week,settlements):
 if d.empty:return d.copy(),d.copy()
 sw=stats[(pd.to_numeric(stats.season,errors='coerce')==2026)&(pd.to_numeric(stats.week,errors='coerce')==week)].copy(); pid=col(d,'player_id','player_id_model','gsis_id'); spid=col(sw,'player_id','gsis_id'); namec=col(d,'player_name','player'); sn=col(sw,'player_display_name','player_name','player'); teamc=col(sw,'recent_team','team')
 keep=[c for c in [spid,'receiving_yards',sn,teamc] if c]
 left=d.copy(); left['_join_pid']=left[pid].fillna('').astype(str) if pid else ''
 if spid:
  right=sw[keep].copy(); right['_join_pid']=right[spid].fillna('').astype(str); g=left.merge(right.drop(columns=[spid] if spid in right.columns else []),on='_join_pid',how='left',suffixes=('','_stat'))
 else:g=left.copy(); g['receiving_yards']=pd.NA
 if sn:
  sw['_nn']=sw[sn].map(normname)
  for i,r in g[g.receiving_yards.isna()].iterrows():
   nn=normname(r.get(namec,'')); cand=sw[sw._nn.eq(nn)]
   if teamc and '@' in str(r.get('game','')):
    teams={normteam(x) for x in str(r.game).split('@')}; cand=cand[cand[teamc].astype(str).map(normteam).isin(teams)]
   if spid:cand=cand.drop_duplicates(subset=[spid])
   if len(cand)==1:g.at[i,'receiving_yards']=cand.iloc[0].receiving_yards; g.at[i,'outcome_match_method']='nflverse_unique_name_team'
 # Authoritative fallback for binary grading: Kalshi's settled market result directly
 # determines whether the model's NO contract paid $1. This avoids depending on nflverse's
 # 2026 weekly-stat publication lag while preserving exact market-level outcome identity.
 g['settlement_result']=g['market_ticker'].map(settlements)
 g['won']=pd.NA; yards=pd.to_numeric(g['receiving_yards'],errors='coerce'); have_yards=yards.notna(); g.loc[have_yards,'won']=yards[have_yards] < pd.to_numeric(g.loc[have_yards,th],errors='coerce'); g.loc[have_yards,'outcome_match_method']=g.loc[have_yards,'outcome_match_method'].fillna('nflverse_player_id')
 need=g['won'].isna(); g.loc[need & g.settlement_result.eq('no'),'won']=True; g.loc[need & g.settlement_result.eq('yes'),'won']=False; g.loc[need & g.settlement_result.isin(['yes','no']),'outcome_match_method']='kalshi_settlement'
 bad=g[g.won.isna()].copy(); bad.to_csv(OUT/f'unresolved_week{week}.csv',index=False)
 good=g[g.won.notna()].copy(); good['won']=good.won.astype(bool); good['cost']=pd.to_numeric(good[na],errors='coerce'); good['pnl']=good.won.astype(float)-good.cost; good['week']=week
 return good,bad
def week1():
 q=pd.read_csv(DATA/'quote_history.csv',low_memory=False); fam=col(q,'prop_family','family'); ticker=col(q,'market_ticker','ticker')
 if fam:q=q[q[fam].astype(str).eq('receiving_yards')].copy()
 tc=col(q,'captured_at','snapshot_at','timestamp','collected_at','updated_at','quote_time_utc'); kc=col(q,'kickoff_utc','kickoff','start_time','scheduled_time')
 if not tc or not ticker:raise SystemExit(f'Week1 quote schema missing capture/ticker; columns={list(q.columns)}')
 q[tc]=pd.to_datetime(q[tc],utc=True,errors='coerce')
 if kc:q[kc]=pd.to_datetime(q[kc],utc=True,errors='coerce'); q=q[q[tc].notna()&q[kc].notna()&(q[tc]<q[kc])].copy()
 else:
  sched=load_schedule(); ev=col(q,'event_ticker'); gm=col(q,'game'); mapped=[]
  for _,r in q.iterrows():
   m=map_event(r.get(ev,''),sched) if ev else None
   if not m and gm and '@' in str(r.get(gm,'')):
    a,h=[normteam(x) for x in str(r[gm]).split('@',1)]; cand=sched[(sched.week==1)&(((sched.away_team.map(normteam)==a)&(sched.home_team.map(normteam)==h))|((sched.away_team.map(normteam)==h)&(sched.home_team.map(normteam)==a)))]
    if len(cand)==1:m={'kickoff_utc':cand.iloc[0].kickoff}
   mapped.append(m.get('kickoff_utc') if m else pd.NaT)
  q['kickoff_utc']=pd.to_datetime(mapped,utc=True,errors='coerce'); kc='kickoff_utc'; q=q[q[tc].notna()&q[kc].notna()&(q[tc]<q[kc])].copy()
 q=q.sort_values(tc).groupby(ticker,as_index=False).tail(1).copy()
 if 'week' in q.columns:q=q[pd.to_numeric(q.week,errors='coerce').eq(1)].copy()
 print(f'WEEK1_CAPTURE rows={len(q)} capture_col={tc} kickoff_col={kc}'); return q
def settled_receiving():
 rows=[]; cur=''
 while True:
  p={'series_ticker':'KXNFLRECYDS','status':'settled','limit':1000}
  if cur:p['cursor']=cur
  d=api('/markets',p); rows+=d.get('markets',[]) or []; cur=str(d.get('cursor') or '')
  if not cur:return rows
def recover_week2(sched,markets):
 rows=[]; print(f'Kalshi settled receiving markets scanned={len(markets)}')
 for m in markets:
  gm=map_event(m.get('event_ticker'),sched)
  if not gm or gm['week']!=2:continue
  ko=int(gm['kickoff_utc'].timestamp()); ticker=m.get('ticker',''); cs=[]
  for path in (f'/series/KXNFLRECYDS/markets/{ticker}/candlesticks',f'/historical/markets/{ticker}/candlesticks'):
   try:
    cs=api(path,{'start_ts':ko-7200,'end_ts':ko,'period_interval':1}).get('candlesticks',[]) or []
    if cs:break
   except Exception:pass
  elig=[c for c in cs if int(c.get('end_period_ts',0))<=ko]
  if not elig:continue
  c=max(elig,key=lambda z:int(z.get('end_period_ts',0))); yb=cv(c.get('yes_bid')); ya=cv(c.get('yes_ask'))
  if yb is None or ya is None:continue
  qt=pd.to_datetime(int(c['end_period_ts']),unit='s',utc=True); name=str(m.get('subtitle') or m.get('title') or '').strip(); key=ticker_player_key(ticker)
  rows.append({'captured_at':qt.isoformat(),'season':2026,'week':2,'game_id':gm['game_id'],'game':gm['game'],'kickoff_utc':gm['kickoff_utc'].isoformat(),'market_ticker':ticker,'event_ticker':m.get('event_ticker',''),'series_ticker':'KXNFLRECYDS','prop_family':'receiving_yards','player_key':key,'player_name':name,'yes_bid':yb,'yes_ask':ya,'no_bid':1-ya,'no_ask':1-yb,'qc_status':'PASS' if key else 'FAIL','qc_reason':'' if key else 'MISSING_TICKER_PARTICIPANT_KEY','recovery_source':'Kalshi historical 1m candle latest <= kickoff','quote_time_utc':qt.isoformat()})
 w=pd.DataFrame(rows); w.to_csv(OUT/'recovered_week2_quotes.csv',index=False); print(f'WEEK2_RECOVERED rows={len(w)} ticker_participant_keys={int(w.player_key.ne("").sum()) if len(w) else 0}'); return w
def summ(g,bad,week,source):
 cost=float(g.cost.sum()) if len(g) else 0; pnl=float(g.pnl.sum()) if len(g) else 0
 return {'week':week,'source':source,'graded_candidates':int(len(g)),'unresolved_candidates':int(len(bad)),'wins':int(g.won.sum()) if len(g) else 0,'losses':int((~g.won).sum()) if len(g) else 0,'win_rate':float(g.won.mean()) if len(g) else None,'cost':cost,'pnl':pnl,'roi':pnl/cost if cost else None}
def main():
 sched=load_schedule(); stats=pd.read_csv('https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats.csv?raw=1'); settled=settled_receiving(); settlements={str(m.get('ticker')):str(m.get('result','')).lower() for m in settled}
 print(f'SETTLEMENT_MAP settled={len(settlements)} yes_no={sum(v in ("yes","no") for v in settlements.values())}')
 w1=week1(); d1,th1,na1=model_select(w1); g1,b1=grade(d1,th1,na1,stats,1,settlements); g1.to_csv(OUT/'graded_week1.csv',index=False)
 w2=recover_week2(sched,settled)
 if len(w2):d2,th2,na2=model_select(w2); g2,b2=grade(d2,th2,na2,stats,2,settlements); g2.to_csv(OUT/'graded_week2.csv',index=False)
 else:g2=pd.DataFrame(); b2=pd.DataFrame(); print('WEEK2_RECOVERY_EMPTY')
 s1=summ(g1,b1,1,'repository quote_history latest pre-kickoff'); s2=summ(g2,b2,2,'Kalshi historical 1m candles recovered after the fact')
 allg=pd.concat([g1,g2],ignore_index=True) if len(g2) else g1.copy(); cost=float(allg.cost.sum()) if len(allg) else 0; pnl=float(allg.pnl.sum()) if len(allg) else 0
 combined={'graded_candidates':int(len(allg)),'unresolved_candidates':int(len(b1)+len(b2)),'wins':int(allg.won.sum()) if len(allg) else 0,'losses':int((~allg.won).sum()) if len(allg) else 0,'win_rate':float(allg.won.mean()) if len(allg) else None,'cost':cost,'pnl':pnl,'roi':pnl/cost if cost else None}
 report={'model_version':SPEC['model_version'],'label':'RETROSPECTIVE_2026_W1_W2_MECHANICAL_QC_ONLY_NOT_PROSPECTIVE','manual_injury_role_correlation_qc_recreated':False,'outcome_grading':'nflverse receiving yards when available; otherwise authoritative Kalshi settled binary result for the exact ticker','week1':s1,'week2':s2,'combined':combined,'comparability_note':'W1 uses repository-captured quotes; W2 is recovered from Kalshi historical 1-minute candles. Combined result is diagnostic and should not be represented as pristine prospective evidence.'}
 (OUT/'summary_weeks1_2_2026.json').write_text(json.dumps(report,indent=2)); allg.to_csv(OUT/'graded_weeks1_2.csv',index=False); print(json.dumps(report,indent=2))
 if len(b1)+len(b2):print(f'WARNING unresolved candidates={len(b1)+len(b2)}')
if __name__=='__main__':main()
