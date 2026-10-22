"""GitHub issue -> read-only Render quotes -> issue comment.

Owner-created issues with [pricing-v3] title prefix only. No exchange secrets.
No arbitrary URL requests, no trading, and no private account data.
"""
from __future__ import annotations
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

BASE = "https://prediction-market-quotes-v3.onrender.com"
REPO = "SaintSteven/Kalshi-trading-backend"
MARKET_ID = re.compile(r"^[a-zA-Z0-9_-]{2,180}$")
VENUES = {"kalshi", "polymarket-us"}
MAX_REQUESTS = 16
MAX_BODY = 14000


class InvalidRequest(ValueError):
    pass


def parse_request(issue: dict) -> list[dict]:
    if issue.get("user", {}).get("login") != "SaintSteven":
        raise InvalidRequest("Request not created by authorized GitHub owner")
    if not str(issue.get("title", "")).startswith("[pricing-v3]"):
        raise InvalidRequest("Not a pricing v3 issue")
    body = issue.get("body") or ""
    if not isinstance(body, str) or len(body) > MAX_BODY:
        raise InvalidRequest("Missing or overlong JSON request")
    body = body.strip()
    fence = chr(96) * 3
    if body.startswith(fence + "json") and body.endswith(fence):
        body = body[7:-3].strip()
    try:
        request = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise InvalidRequest("Issue body must be a JSON object") from exc
    if not isinstance(request, dict) or request.get("version") != 1:
        raise InvalidRequest("Expected version 1 request object")
    quotes = request.get("quotes")
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= MAX_REQUESTS:
        raise InvalidRequest(f"Expected 1–{MAX_REQUESTS} quote legs")
    normalized = []
    for raw in quotes:
        if not isinstance(raw, dict):
            raise InvalidRequest("Quote must be an object")
        venue = raw.get("venue")
        contract = raw.get("contract")
        side = raw.get("side", "YES")
        budget = raw.get("budget_usd")
        if venue not in VENUES or not isinstance(contract, str) or not MARKET_ID.fullmatch(contract):
            raise InvalidRequest("Invalid venue or exact contract ID")
        if side not in (("YES", "NO") if venue == "kalshi" else ("YES",)):
            raise InvalidRequest("Unsupported side for venue")
        try:
            value = Decimal(str(budget))
        except (InvalidOperation, ValueError):
            raise InvalidRequest("Invalid budget")
        if not value.is_finite() or not Decimal("0.01") <= value <= Decimal("20.00"):
            raise InvalidRequest("Budget outside permitted $0.01–$20 range")
        normalized.append({
            "venue": venue, "contract": contract, "side": side,
            "budget_usd": f"{value:.2f}",
        })
    return normalized


def get_quote(leg: dict) -> dict:
    venue, contract, side, budget = (
        leg["venue"], leg["contract"], leg["side"], leg["budget_usd"]
    )
    endpoint = f"/v1/quote/{venue}/{urllib.parse.quote(contract, safe='')}"
    query = urllib.parse.urlencode({"side": side, "budget_usd": budget})
    req = urllib.request.Request(
        BASE + endpoint + "?" + query,
        headers={"User-Agent": "PredictionMarketsV3-GitHubBridge/0.1"},
    )
    base = dict(leg)
    try:
        with urllib.request.urlopen(req, timeout=35) as result:
            doc = json.load(result)
        if not isinstance(doc, dict):
            raise ValueError("Unexpected quote response")
        if (doc.get("venue") != venue or doc.get("contract") != contract
                or doc.get("side") != side):
            raise ValueError("Provider identity mismatch")
        market = doc.get("market_rules") or {}
        pricing = doc.get("pricing") or {}
        rule_excerpt = {k: str(v)[:500] for k, v in market.items() if v is not None}
        return {**base,
            "status": "BOOK_SNAPSHOT" if pricing.get("complete_depth") else "NO_FULL_DEPTH",
            "source": doc.get("source"),
            "retrieved_at_utc": doc.get("retrieved_at_utc"),
            "exchange_book_time": doc.get("exchange_book_time"),
            "market_rules": rule_excerpt,
            "pricing": {
                "top_ask_dollars": pricing.get("top_ask_dollars"),
                "requested_contracts": pricing.get("requested_contracts"),
                "whole_contracts_priced": pricing.get("whole_contracts_priced"),
                "complete_depth": pricing.get("complete_depth"),
                "gross_cost_usd": pricing.get("gross_cost_usd"),
                "estimated_fees_usd": pricing.get("estimated_fees_usd"),
                "estimated_total_usd": pricing.get("estimated_total_usd"),
                "estimated_all_in_average_dollars": pricing.get("estimated_all_in_average_dollars"),
                "unspent_usd": pricing.get("unspent_usd"),
                "status": pricing.get("status"),
                "fee_verified": pricing.get("fee_verified"),
                "firm_executable_quote": pricing.get("firm_executable_quote"),
            },
        }
    except urllib.error.HTTPError as exc:
        return {**base, "status": "UNAVAILABLE", "provider_http_status": exc.code,
                "error": "Quote rejected or venue data unavailable"}
    except (TimeoutError, urllib.error.URLError, ValueError, OSError) as exc:
        return {**base, "status": "UNAVAILABLE", "error": type(exc).__name__}


def run(issue: dict) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    try:
        legs = parse_request(issue)
    except InvalidRequest as exc:
        return {"request_status": "INVALID_REQUEST",
                "reason": str(exc), "started_at_utc": started, "results": []}
    outputs = [None] * len(legs)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(get_quote, leg): i for i, leg in enumerate(legs)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                outputs[i] = future.result()
            except Exception as exc:
                outputs[i] = {**legs[i], "status": "UNAVAILABLE",
                              "error": type(exc).__name__}
    return {
        "request_status": "COMPLETE",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "DIRECT_PUBLIC_EXCHANGE_APIS_VIA_RENDER",
        "settlement_equivalence_verified": False,
        "trade_executed": False,
        "results": outputs,
        "limitations": [
            "Book snapshots are unreserved and can change before execution.",
            "Fees are estimates; never call this fee-verified execution.",
            "Only exact contract IDs supplied; no cross-venue equivalence inferred.",
            "No scalar ladders/escalators; Polymarket US NO unsupported.",
        ],
    }


def post_comment(issue_number: int, data: dict) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing GitHub Actions token")
    fence = chr(96) * 3
    body = (
        "### Prediction Markets Research v3 — quote request results\n\n"
        "Read-only order-book snapshot; not a trade or verified fee-inclusive fill.\n\n"
        + fence + "json\n"
        + json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        + "\n" + fence + "\n"
    )
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/issues/{issue_number}/comments",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "PredictionMarketsV3-GitHubBridge",
        },
        data=json.dumps({"body": body}).encode("utf-8"),
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 201:
            raise RuntimeError("Issue comment was not accepted")


if __name__ == "__main__":
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        sys.exit("GITHUB_EVENT_PATH is missing")
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    issue = payload.get("issue")
    if not isinstance(issue, dict):
        sys.exit("No GitHub issue in event")
    result = run(issue)
    post_comment(int(issue["number"]), result)
    print(json.dumps({
        "request_status": result["request_status"],
        "quoted": len(result["results"]),
        "success": sum(row["status"] == "BOOK_SNAPSHOT" for row in result["results"]),
    }))
