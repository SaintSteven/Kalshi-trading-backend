#!/usr/bin/env python3
"""Test 03: direct current Kalshi KXNFLFFPTS market discovery smoke test.
Research-only; no orders are placed. Discovers markets, enriches per ticker,
normalizes quote fields, and maps supported offensive players with collision-safe
name+position identity for the current slate.
"""
import json,re,urllib.parse,urllib.request
from pathlib import Path
import pandas as pd
ROOT=Path('research/nfl_fantasy_points_validation'); OUT=ROOT/'results'; OUT.mkdir(parents=True,exist_ok=True)
pred=OUT/'00_walk_forward_predictions.csv'
if not pred.exists(): raise SystemExit('Test 00 predictions missing')
d=pd.read_csv(pred); d['resid']=d.actual_fp-d.pred_fp
BASE='https://api.elections.kalshi.com/trade-api/v2'; SERIES='KXNFLFFPTS'; SUPPORTED={'QB','RB','WR','TE'}
# Full-name identity is authoritative when abbreviated nflverse display labels collide.
POSITION_OVERRIDES={'jameson williams':'WR','mike evans':'WR','javonte williams':'RB'}
# When Test09 has already run, use full current-slate identity to resolve abbreviated historical collisions.
current_path=OUT/'09_current_slate_projections.csv'
CURRENT_FULL={}; CURRENT_BY_ABBR={}
if current_path.exists():
 cur=pd.read_csv(current_path)
 def nfull(s):
  s=str(s or '').lower(); s=re.sub(r'\b(jr|sr|ii|iii|iv)\.?\b','',s); return re.sub(r'[^a-z0-9]','',s)
 for rr in cur.itertuples():
  CURRENT_FULL.setdefault(nfull(getattr(rr,'full_name','')),[]).append(rr)
  # Build an authoritative current-slate abbreviated-key index. Historical
  # walk-forward output intentionally lacks full names, so player_id cannot be
  # joined directly across a collided historical abbreviation. Resolve the
  # market to the exact current player first, then use that player's position.
  fn=str(getattr(rr,'full_name','') or '').strip()
  fn=re.sub(r'\\s+(?:Jr\\.?|Sr\\.?|II|III|IV)$','',fn,flags=re.I)
  pp=fn.split()
  if len(pp)>=2:
   ab=(pp[0][0].upper()+'.'+' '.join(pp[1:])).lower()
   CURRENT_BY_ABBR.setdefault(ab,[]).append(rr)
def get(path,params=None):
 url=BASE+path
 if params:url+='?'+urllib.parse.urlencode(params)
 req=urllib.request.Request(url,headers={'User-Agent':'nfl-fantasy-points-research/1.5'})
 with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
def dollars(m,key):
 v=m.get(key)
 if isinstance(v,(int,float)): return float(v)/100
 v=m.get(key+'_dollars')
 try:return float(v) if v not in (None,'') else None
 except:return None

events=[]; markets=[]; errors=[]
try:
 cursor=None
 for _ in range(20):
  q={'series_ticker':SERIES,'status':'open','limit':200,'with_nested_markets':'true'}
  if cursor:q['cursor']=cursor
  p=get('/events',q); batch=p.get('events',[]); events+=batch
  for e in batch:markets+=e.get('markets',[]) or []
  cursor=p.get('cursor')
  if not cursor:break
except Exception as e:errors.append('events_query: '+str(e))
uniq={}
for m in markets:
 t=str(m.get('ticker','')).upper(); ev=str(m.get('event_ticker','')).upper()
 if t.startswith(SERIES) or ev.startswith(SERIES):uniq[t or ev]=m
markets=list(uniq.values()); enriched=[]
for m in markets:
 t=str(m.get('ticker','')).strip()
 try:
  full=get('/markets/'+urllib.parse.quote(t,safe='')); fm=full.get('market',full); x=dict(m); x.update(fm); enriched.append(x)
 except Exception as e:errors.append('market_detail_%s: %s'%(t,e)); enriched.append(m)
markets=enriched
latest=d.sort_values(['season','week']).groupby(['player_name','position'],as_index=False).tail(1)
proj={}
for r in latest.itertuples():proj.setdefault(str(r.player_name).lower(),[]).append(r)
def pname(m):
 for k in ['title','yes_sub_title','subtitle']:
  hit=re.match(r'^(.+?):\s*Over\s+\d',str(m.get(k,'')).strip(),re.I)
  if hit:return hit.group(1).strip()
def hkey(n):
 if not n:return None
 s=re.sub(r'\s+(?:Jr\.?|Sr\.?|II|III|IV)$','',n.strip(),flags=re.I); p=s.split()
 if len(p)<2:return None
 if s.lower().startswith('amon-ra st. brown'):return 'A.St. Brown'
 return f"{p[0][0].upper()}.{' '.join(p[1:])}"
def dst(n):return bool(n and re.search(r'\b(?:D/ST|DST|Defense)\b',n,re.I))
def kicker(n):return str(n or '').lower() in {'tyler bass','jake bates'}
rows=[]
for m in markets:
 n=pname(m); key=hkey(n); matched=None; exclusion=None; collision=False
 if dst(n):exclusion='DST_UNSUPPORTED'
 elif kicker(n):exclusion='K_UNSUPPORTED'
 elif key and key.lower() in proj:
  candidates=[r for r in proj[key.lower()] if str(r.position).upper() in SUPPORTED]
  want=POSITION_OVERRIDES.get(str(n or '').lower())
  if not want and CURRENT_FULL:
   fc=CURRENT_FULL.get(nfull(n),[])
   poss=sorted(set(str(rr.position).upper() for rr in fc if str(rr.position).upper() in SUPPORTED))
   if len(poss)==1: want=poss[0]
  if want:candidates=[r for r in candidates if str(r.position).upper()==want]
  if len(candidates)>1 and CURRENT_FULL:
   # Exact Kalshi full name -> exact Test09 current player -> stable player_id.
   # Historical candidates from Test00 are then narrowed by that stable ID.
   fc=CURRENT_FULL.get(nfull(n),[])
   current_ids={str(rr.player_id) for rr in fc if str(rr.position).upper() in SUPPORTED}
   narrowed=[r for r in candidates if str(getattr(r,'player_id','')) in current_ids]
   if len(narrowed)==1:candidates=narrowed
  if len(candidates)==1:matched=candidates[0]
  elif len(candidates)>1:exclusion='AMBIGUOUS_NAME_POSITION'; collision=True
  else:exclusion='NO_SUPPORTED_POSITION_MATCH'
 else:
  # Fail closed: any market that cannot map to a supported QB/RB/WR/TE identity
  # is excluded before the downstream identity gate (e.g. kickers such as Nick Folk).
  exclusion='UNMAPPED_OR_UNSUPPORTED'
 nums=[float(x) for x in re.findall(r'(?<![A-Za-z])\d+(?:\.\d+)?',' '.join(str(m.get(k,'')) for k in ['title','subtitle','yes_sub_title']))]; th=nums[-1] if nums else None
 fair=None
 if matched is not None and th is not None:
  hist=d.loc[d.position==matched.position,'resid'].dropna(); off=th-float(matched.pred_fp)
  fair=float(((hist>off).sum()+.5)/(len(hist)+1)) if len(hist)>=100 else None
 ya=dollars(m,'yes_ask'); yb=dollars(m,'yes_bid'); na=dollars(m,'no_ask'); nb=dollars(m,'no_bid')
 rows.append({'ticker':m.get('ticker'),'event_ticker':m.get('event_ticker'),'title':m.get('title'),'kalshi_player_name':n,'historical_name_key':key,'matched_player':getattr(matched,'player_name',None) if matched is not None else None,'position':getattr(matched,'position',None) if matched is not None else None,'identity_collision':collision,'exclusion_reason':exclusion,'threshold':th,'yes_bid_probability':yb,'yes_ask_probability':ya,'no_bid_probability':nb,'no_ask_probability':na,'volume':m.get('volume'),'open_interest':m.get('open_interest'),'smoke_test_fair_probability':fair,'smoke_test_yes_edge':fair-ya if fair is not None and ya is not None else None})
o=pd.DataFrame(rows); o.to_csv(OUT/'03_kalshi_fantasy_markets.csv',index=False)
summary={'test':'03_kalshi_fantasy_market_scan','series':SERIES,'research_only':True,'orders_placed':False,'fantasy_point_markets':len(markets),'supported_player_markets':int(o.exclusion_reason.isna().sum()),'player_names_matched':int(o.matched_player.notna().sum()),'markets_with_executable_yes_ask':int(o.yes_ask_probability.notna().sum()),'identity_collisions_unresolved':int((o.exclusion_reason=='AMBIGUOUS_NAME_POSITION').sum()),'mapped_smoke_test_markets':int(o.smoke_test_fair_probability.notna().sum()),'api_errors':errors}
(OUT/'03_kalshi_scan_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2));print(o.to_json(orient='records',indent=2))
if summary['identity_collisions_unresolved']>0: raise SystemExit('Unresolved current-slate player identity collision')
