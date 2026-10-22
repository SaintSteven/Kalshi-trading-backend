"""Operational smoke check against deployed service, never places trades."""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.getenv("PRICING_API_BASE", "https://prediction-market-quotes-v3.onrender.com").rstrip("/")
def get(path, timeout=25):
    url=BASE+path
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"PredictionMarketsV3-Smoke/0.1"}),timeout=timeout) as r:
            return r.status,json.load(r)
    except urllib.error.HTTPError as e:
        try: content=e.read().decode()[:300]
        except Exception: content=""
        return e.code,{"error":content}
    except Exception as e:
        return 0,{"error":str(e)}

ok=False
for attempt in range(6):
    code,data=get("/health",timeout=30)
    print("health",attempt+1,code,str(data)[:220],flush=True)
    if code==200 and data.get("status")=="ok" and data.get("trading_enabled") is False:
        ok=True
        break
    time.sleep(6)
if not ok:
    sys.exit("FAILED: no valid deployed read-only health")
code,doc=get("/openapi.json")
assert code==200 and "/v1/quote/kalshi/{ticker}" in doc.get("paths",{}),f"OpenAPI invalid: {code}"
print("openapi: OK",flush=True)

endpoints=[("kalshi","/v1/discover/kalshi?series_ticker=KXNFLGAME"),("polymarket-us","/v1/discover/polymarket-us?limit=100&offset=0")]
for venue,path in endpoints:
    code,payload=get(path,timeout=30)
    print("discovery",venue,code, "markets",len(payload.get("markets",[])),str(payload.get("detail",payload.get("error","")))[:180],flush=True)
    if code!=200:
        continue
    found=False
    for market in payload.get("markets",[]):
        if venue=="kalshi":
            ticker=market.get("ticker")
            if not ticker or market.get("status") not in ("active","open"):continue
            route="/v1/quote/kalshi/"+urllib.parse.quote(ticker,safe="")+"?side=YES&contracts=1"
        else:
            slug=market.get("slug")
            if not slug:continue
            route="/v1/quote/polymarket-us/"+urllib.parse.quote(slug,safe="")+"?side=YES&contracts=1"
        status,q=get(route,timeout=30)
        print("quote",venue,status,"id",ticker if venue=="kalshi" else slug,"detail",str(q.get("detail",q.get("pricing",{}).get("status","")))[:200],flush=True)
        found=True
        if status==200:
            assert q["pricing"]["firm_executable_quote"] is False
            assert q["source"]=="DIRECT_PUBLIC_PROVIDER_API"
        break
    if not found:print("quote",venue,"NO_DISCOVERABLE_MARKET",flush=True)
print("SMOKE FINISHED: health and OpenAPI checked; provider status lines above",flush=True)
