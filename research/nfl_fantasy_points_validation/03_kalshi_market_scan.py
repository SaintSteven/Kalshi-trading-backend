#!/usr/bin/env python3
"""Test 03: direct current Kalshi KXNFLFFPTS market discovery smoke test.

Research-only. Queries the known NFL single-game fantasy-points series directly,
enriches every discovered contract from its individual market endpoint so quote
fields are present, and maps only supported offensive player positions.
Historical projections remain plumbing diagnostics; no orders are placed.
"""
import json, re, urllib.parse, urllib.request
from pathlib import Path
import numpy as np, pandas as pd

ROOT=Path("research/nfl_fantasy_points_validation"); OUT=ROOT/"results"; OUT.mkdir(parents=True,exist_ok=True)
pred=OUT/"00_walk_forward_predictions.csv"
if not pred.exists(): raise SystemExit("Test 00 predictions missing")
d=pd.read_csv(pred); d["resid"]=d.actual_fp-d.pred_fp

BASE="https://api.elections.kalshi.com/trade-api/v2"; SERIES="KXNFLFFPTS"
SUPPORTED={"QB","RB","WR","TE"}
def get(path, params=None):
    url=BASE+path
    if params: url+="?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={"User-Agent":"nfl-fantasy-points-research/1.3"})
    with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)

events=[]; markets=[]; errors=[]
try:
    cursor=None
    for _ in range(20):
        q={"series_ticker":SERIES,"status":"open","limit":200,"with_nested_markets":"true"}
        if cursor: q["cursor"]=cursor
        p=get("/events",q); batch=p.get("events",[]); events.extend(batch)
        for e in batch: markets.extend(e.get("markets",[]) or [])
        cursor=p.get("cursor")
        if not cursor: break
except Exception as e: errors.append("events_query: "+str(e))
if not markets:
    try:
        cursor=None
        for _ in range(20):
            q={"series_ticker":SERIES,"status":"open","limit":1000}
            if cursor: q["cursor"]=cursor
            p=get("/markets",q); markets.extend(p.get("markets",[])); cursor=p.get("cursor")
            if not cursor: break
    except Exception as e: errors.append("markets_query: "+str(e))

uniq={}
for m in markets:
    ticker=str(m.get("ticker","")).upper(); ev=str(m.get("event_ticker","")).upper()
    if ticker.startswith(SERIES) or ev.startswith(SERIES): uniq[ticker or ev]=m
markets=list(uniq.values())

# Nested event payloads can omit executable quotes. Enrich each contract from
# the individual market endpoint; preserve discovery metadata if enrichment fails.
enriched=[]
for m in markets:
    ticker=str(m.get("ticker","")).strip()
    if not ticker: enriched.append(m); continue
    try:
        full=get("/markets/"+urllib.parse.quote(ticker,safe=""))
        fm=full.get("market",full)
        merged=dict(m); merged.update(fm if isinstance(fm,dict) else {})
        enriched.append(merged)
    except Exception as e:
        errors.append("market_detail_%s: %s"%(ticker,e)); enriched.append(m)
markets=enriched

latest=d.sort_values(["season","week"]).groupby("player_name",as_index=False).tail(1)
proj={str(r.player_name).lower():r for r in latest.itertuples()}

def kalshi_player_name(m):
    for k in ["title","yes_sub_title","subtitle"]:
        s=str(m.get(k,"")).strip(); hit=re.match(r"^(.+?):\s*Over\s+\d",s,re.I)
        if hit: return hit.group(1).strip()
    return None

def historical_name_key(full_name):
    if not full_name: return None
    s=re.sub(r"\s+(?:Jr\.?|Sr\.?|II|III|IV)$","",full_name.strip(),flags=re.I); parts=s.split()
    if len(parts)<2: return None
    if s.lower().startswith("amon-ra st. brown"): return "A.St. Brown"
    return f"{parts[0][0].upper()}.{' '.join(parts[1:])}"

def is_dst(name): return bool(name and re.search(r"\b(?:D/ST|DST|Defense)\b",name,re.I))
def is_known_kicker(name):
    # Current smoke-slate safeguards. Stable production identity should move to
    # roster/player IDs; never allow an abbreviated-name collision to classify these as TE/WR/etc.
    return str(name or "").lower() in {"tyler bass","jake bates"}
def text(m): return " ".join(str(m.get(k,"")) for k in ["ticker","event_ticker","title","subtitle","yes_sub_title","no_sub_title"])

rows=[]
for m in markets:
    raw=text(m); matched=None; display_name=kalshi_player_name(m); candidate=historical_name_key(display_name)
    exclusion=None
    if is_dst(display_name): exclusion="DST_UNSUPPORTED"
    elif is_known_kicker(display_name): exclusion="K_UNSUPPORTED"
    elif candidate and candidate.lower() in proj:
        r=proj[candidate.lower()]
        if str(r.position).upper() in SUPPORTED: matched=r
        else: exclusion="POSITION_UNSUPPORTED_"+str(r.position).upper()
    else:
        low=raw.lower()
        for name,r in proj.items():
            if name and name in low and str(r.position).upper() in SUPPORTED: matched=r; break

    nums=[float(x) for x in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", " ".join(str(m.get(k,"")) for k in ["title","subtitle","yes_sub_title"]))]
    threshold=nums[-1] if nums else None; fair=None
    if matched is not None and threshold is not None:
        hist=d.loc[d.position==matched.position,"resid"].dropna().to_numpy(); off=threshold-float(matched.pred_fp)
        fair=float(((hist>off).sum()+.5)/(len(hist)+1)) if len(hist)>=100 else None
    yes_ask=m.get("yes_ask"); edge=(fair-yes_ask/100) if fair is not None and isinstance(yes_ask,(int,float)) else None
    rows.append({"ticker":m.get("ticker"),"event_ticker":m.get("event_ticker"),"title":m.get("title"),"subtitle":m.get("subtitle"),
      "yes_sub_title":m.get("yes_sub_title"),"no_sub_title":m.get("no_sub_title"),"yes_bid":m.get("yes_bid"),"yes_ask":yes_ask,"no_bid":m.get("no_bid"),"no_ask":m.get("no_ask"),
      "volume":m.get("volume"),"open_interest":m.get("open_interest"),"kalshi_player_name":display_name,"historical_name_key":candidate,
      "matched_player":getattr(matched,"player_name",None) if matched is not None else None,"position":getattr(matched,"position",None) if matched is not None else None,
      "exclusion_reason":exclusion,"threshold":threshold,"smoke_test_fair_probability":fair,"smoke_test_yes_edge":edge})

o=pd.DataFrame(rows)
if len(o): o.to_csv(OUT/"03_kalshi_fantasy_markets.csv",index=False)
quote_count=int(o.yes_ask.notna().sum()) if len(o) else 0
summary={"test":"03_kalshi_fantasy_market_scan","series":SERIES,"research_only":True,"orders_placed":False,"open_events_found":len(events),
 "fantasy_point_markets":len(markets),"player_names_matched":int(o.matched_player.notna().sum()) if len(o) else 0,
 "supported_player_markets":int((o.exclusion_reason.isna()).sum()) if len(o) else 0,"markets_with_yes_ask":quote_count,
 "mapped_smoke_test_markets":int(o.smoke_test_fair_probability.notna().sum()) if len(o) else 0,"api_errors":errors,
 "note":"Direct KXNFLFFPTS discovery with per-ticker market-detail enrichment. K/DST are explicitly excluded; historical abbreviated-name matches are accepted only for QB/RB/WR/TE. Fair values remain plumbing-only, not trade-ready current-slate forecasts.",
 "next_gate":"Confirm executable quotes and supported-player mapping, then freeze development and run current-slate projections for paper/live research."}
(OUT/"03_kalshi_scan_summary.json").write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
if len(o): print(o.to_json(orient="records",indent=2))
