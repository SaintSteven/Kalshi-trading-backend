#!/usr/bin/env python3
"""Test 03: current Kalshi NFL fantasy-points market discovery and research edge scan.

Uses the promoted walk-forward empirical residual distribution from Test 02.
This is research-only: it discovers open Kalshi markets whose metadata indicates
NFL fantasy points, records executable quotes, and emits candidates when a
market can be conservatively mapped to a player/threshold. No orders are placed.
"""
import json, re, urllib.parse, urllib.request
from pathlib import Path
import numpy as np, pandas as pd

ROOT=Path("research/nfl_fantasy_points_validation"); OUT=ROOT/"results"; OUT.mkdir(parents=True,exist_ok=True)
pred=OUT/"00_walk_forward_predictions.csv"
if not pred.exists(): raise SystemExit("Test 00 predictions missing")
d=pd.read_csv(pred); d["resid"]=d.actual_fp-d.pred_fp

BASE="https://api.elections.kalshi.com/trade-api/v2"
def get(path, params=None):
    url=BASE+path
    if params: url+="?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={"User-Agent":"nfl-fantasy-points-research/1.0"})
    with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)

markets=[]; cursor=None
for _ in range(20):
    q={"status":"open","limit":1000}
    if cursor: q["cursor"]=cursor
    try: payload=get("/markets",q)
    except Exception as e:
        (OUT/"03_kalshi_scan_summary.json").write_text(json.dumps({"test":"03_kalshi_fantasy_market_scan","status":"API_ERROR","error":str(e)},indent=2))
        raise
    batch=payload.get("markets",[]); markets.extend(batch)
    cursor=payload.get("cursor")
    if not cursor: break

def text(m):
    return " ".join(str(m.get(k,"")) for k in ["ticker","event_ticker","title","subtitle","yes_sub_title","no_sub_title"]).lower()
nfl=[m for m in markets if "nfl" in text(m) or "football" in text(m)]
fantasy=[m for m in nfl if "fantasy" in text(m) and ("point" in text(m) or "pts" in text(m))]

# Latest validated projection per player is used only as a plumbing smoke test.
# Historical/current-slate projection integration is the next gate if markets exist.
latest=d.sort_values(["season","week"]).groupby("player_name",as_index=False).tail(1)
proj={str(r.player_name).lower():r for r in latest.itertuples()}
rows=[]
for m in fantasy:
    t=text(m)
    matched=None
    for name,r in proj.items():
        if name and name in t: matched=r; break
    nums=[float(x) for x in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", " ".join(str(m.get(k,"")) for k in ["title","subtitle","yes_sub_title"]))]
    threshold=nums[-1] if nums else None
    fair=None
    if matched is not None and threshold is not None:
        hist=d.loc[d.position==matched.position,"resid"].dropna().to_numpy()
        off=threshold-float(matched.pred_fp)
        fair=float(((hist>off).sum()+.5)/(len(hist)+1)) if len(hist)>=100 else None
    yes_ask=m.get("yes_ask")
    edge=(fair-yes_ask/100) if fair is not None and isinstance(yes_ask,(int,float)) else None
    rows.append({"ticker":m.get("ticker"),"title":m.get("title"),"subtitle":m.get("subtitle"),
                 "yes_bid":m.get("yes_bid"),"yes_ask":yes_ask,"volume":m.get("volume"),
                 "matched_player":getattr(matched,"player_name",None) if matched is not None else None,
                 "position":getattr(matched,"position",None) if matched is not None else None,
                 "threshold":threshold,"smoke_test_fair_probability":fair,"smoke_test_yes_edge":edge})

o=pd.DataFrame(rows)
if len(o): o.to_csv(OUT/"03_kalshi_fantasy_markets.csv",index=False)
summary={"test":"03_kalshi_fantasy_market_scan","research_only":True,"orders_placed":False,
         "open_markets_scanned":len(markets),"nfl_like_markets":len(nfl),"fantasy_point_markets":len(fantasy),
         "mapped_smoke_test_markets":int(o.smoke_test_fair_probability.notna().sum()) if len(o) else 0,
         "note":"Any fair values here use latest historical validation projections only as a mapping/plumbing smoke test; they are NOT trade-ready current-slate forecasts.",
         "next_gate":"If fantasy-point markets are discovered, wire current-slate projections and exact Kalshi rule/threshold parsing before paper-trading evaluation."}
(OUT/"03_kalshi_scan_summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
if len(o): print(o.to_json(orient="records",indent=2))
