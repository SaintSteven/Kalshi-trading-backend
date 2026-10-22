"""Read-only exact contract discovery through the deployed Render public API."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

BASE = "https://prediction-market-quotes-v3.onrender.com"
ID = re.compile(r"^[a-zA-Z0-9_-]{2,180}$")
MAX_REQUESTS = 4


class InvalidDiscovery(ValueError):
    pass


def parse_discovery(issue: dict) -> list[dict]:
    if issue.get("user", {}).get("login") != "SaintSteven":
        raise InvalidDiscovery("Not authorized")
    if not str(issue.get("title", "")).startswith("[discovery-v3]"):
        raise InvalidDiscovery("Not a discovery issue")
    try:
        body = issue.get("body", "")
        if not isinstance(body, str) or len(body) > 10000:
            raise InvalidDiscovery("Invalid discovery body size")
        request = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise InvalidDiscovery("Invalid JSON") from exc
    if not isinstance(request, dict) or request.get("version") != 1:
        raise InvalidDiscovery("Expected request version 1")
    legs = request.get("discover")
    if not isinstance(legs, list) or not 1 <= len(legs) <= MAX_REQUESTS:
        raise InvalidDiscovery("Expected 1 to 4 discovery requests")
    normalized = []
    for leg in legs:
        if not isinstance(leg, dict) or leg.get("venue") not in ("kalshi", "polymarket-us"):
            raise InvalidDiscovery("Unknown venue")
        venue = leg["venue"]
        if venue == "kalshi":
            ticker = leg.get("series_ticker")
            event_ticker = leg.get("event_ticker")
            cursor = leg.get("cursor")
            if bool(ticker) == bool(event_ticker):
                raise InvalidDiscovery("Supply one Kalshi series_ticker OR event_ticker")
            selected = ticker if ticker else event_ticker
            if not isinstance(selected,str) or not ID.fullmatch(selected):
                raise InvalidDiscovery("Invalid Kalshi market identifier")
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 500):
                raise InvalidDiscovery("Invalid cursor")
            normalized.append({"venue":venue,"series_ticker":ticker,"event_ticker":event_ticker,"cursor":cursor})
        else:
            limit, offset = leg.get("limit", 100), leg.get("offset", 0)
            if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 100 or not 0 <= offset <= 10000:
                raise InvalidDiscovery("Invalid Polymarket US pagination")
            keywords = leg.get("keywords") or []
            if (not isinstance(keywords, list) or len(keywords) > 5
                    or any(not isinstance(k, str) or len(k) > 50 for k in keywords)):
                raise InvalidDiscovery("Invalid discovery keywords")
            event_slug = leg.get("event_slug")
            search = leg.get("search")
            event_detail_slug = leg.get("event_detail_slug")
            keyword = leg.get("keyword","")
            if event_slug is not None and (not isinstance(event_slug,str) or not ID.fullmatch(event_slug)):
                raise InvalidDiscovery("Invalid event slug")
            if event_detail_slug is not None and (not isinstance(event_detail_slug,str) or not ID.fullmatch(event_detail_slug)):
                raise InvalidDiscovery("Invalid event detail slug")
            if search is not None and (not isinstance(search,str) or not 3<=len(search)<=120):
                raise InvalidDiscovery("Invalid search")
            if not isinstance(keyword,str) or len(keyword)>90:
                raise InvalidDiscovery("Invalid keyword")
            normalized.append({"venue": venue, "limit": limit, "offset": offset,
                               "event_slug":event_slug,"event_detail_slug":event_detail_slug,
                               "search":search,"keyword":keyword,
                               "keywords": [k.lower() for k in keywords]})
    return normalized


def fetch_one(leg: dict) -> dict:
    venue = leg["venue"]
    if venue == "kalshi":
        if leg.get("event_ticker"):
            url = BASE + "/v1/discover/kalshi-event/" + leg["event_ticker"]
            if leg.get("cursor"):
                url += "?" + urllib.parse.urlencode({"cursor":leg["cursor"]})
        else:
            query = {"series_ticker": leg["series_ticker"]}
            if leg.get("cursor"):
                query["cursor"] = leg["cursor"]
            url = BASE + "/v1/discover/kalshi?" + urllib.parse.urlencode(query)
    else:
        query = {"limit":leg["limit"],"offset":leg["offset"]}
        if leg.get("search"):
            url = BASE + "/v1/discover/polymarket-us-search?" + urllib.parse.urlencode({"query":leg["search"],"limit":min(30,leg["limit"]),"page":max(1,leg["offset"]+1)})
        elif leg.get("event_detail_slug"):
            url = BASE + "/v1/discover/polymarket-us-event-detail/" + leg["event_detail_slug"]
            url += "?" + urllib.parse.urlencode({"keyword":leg["keyword"],"offset":leg["offset"],"limit":min(100,leg["limit"])})
        elif leg.get("event_slug"):
            url = BASE + "/v1/discover/polymarket-us-event/" + leg["event_slug"]
            url += "?" + urllib.parse.urlencode(query)
        else:
            url = BASE + "/v1/discover/polymarket-us?" + urllib.parse.urlencode(query)
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "ResearchV3-DiscoveryBridge"}),
            timeout=45,
        ) as response:
            data = json.load(response)
        if leg.get("search"):
            events=data.get("events") if isinstance(data,dict) else None
            if not isinstance(events,list):raise ValueError("Unexpected search response")
            return {"venue":venue,"request":leg,"status":"EVENT_SEARCH_SNAPSHOT","events":events[:30],"retrieved_at_utc":data.get("retrieved_at_utc")}
        if not isinstance(data, dict) or not isinstance(data.get("markets"), list):
            raise ValueError("Unexpected discovery response")
        markets = data["markets"]
        if venue == "kalshi":
            keys = ("ticker", "event_ticker", "title", "subtitle", "yes_sub_title",
                    "no_sub_title", "status", "close_time", "rules_primary")
        else:
            keys = ("id", "slug", "title", "outcome", "eventSlug", "description",
                    "active", "closed")
        filtered = []
        for market in markets:
            if not isinstance(market, dict):
                continue
            compact = {key: str(market[key])[:300] for key in keys if market.get(key) is not None}
            if venue == "polymarket-us" and leg["keywords"]:
                haystack = " ".join(str(market.get(k, "")) for k in ("slug", "title", "description", "eventSlug")).lower()
                if not any(keyword in haystack for keyword in leg["keywords"]):
                    continue
            filtered.append(compact)
            if len(filtered) >= (200 if leg.get('event_ticker') or leg.get('event_slug') else 75):
                break
        return {"venue": venue, "request": leg,
                "status": "DISCOVERY_SNAPSHOT",
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "markets_returned": len(markets),
                "markets_after_filter": len(filtered),
                "next_cursor": data.get("cursor") if venue == "kalshi" else None,
                "next_offset": data.get("next_offset") if leg.get("event_detail_slug") else (leg["offset"] + len(markets) if venue == "polymarket-us" and len(markets) else None),
                "event":data.get("event"), "total_event_markets":data.get("total_event_markets"), "matching_market_count":data.get("matching_market_count"),
                "markets": filtered,
                "warning": "Titles/IDs are discovery only; request an exact quote and verify settlement rules."}
    except urllib.error.HTTPError as exc:
        return {"venue": venue, "request": leg, "status": "UNAVAILABLE",
                "provider_http_status": exc.code}
    except Exception as exc:
        return {"venue": venue, "request": leg, "status": "UNAVAILABLE",
                "error_type": type(exc).__name__}


def run_discovery(issue: dict) -> dict:
    try:
        legs = parse_discovery(issue)
    except InvalidDiscovery as exc:
        return {"request_status": "INVALID_REQUEST", "reason": str(exc), "results": []}
    rows = [None] * len(legs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch_one, leg): i for i, leg in enumerate(legs)}
        for future in as_completed(futures):
            rows[futures[future]] = future.result()
    return {"request_status": "COMPLETE",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "results": rows,
            "trade_executed": False,
            "discovery_only": True}
