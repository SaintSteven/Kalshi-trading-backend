"""NBA-only research-to-pricing preflight; no other sports or bridge routes changed.

Input is a JSON object with sport='NBA', candidate fields, and discovered
contract fields (including the actual Kalshi ladder rung and verified rules).
Only validated exact outcomes can call the existing read-only quote helper.
"""
from exact_threshold_gate import Candidate, validate_ladder_candidate


def nba_preflight(request: dict) -> dict:
    if request.get("sport") != "NBA":
        return {"status": "OUT_OF_SCOPE", "quote_allowed": False}
    c = request.get("candidate") or {}
    m = request.get("market") or {}
    try:
        candidate = Candidate(
            player=c["player"], stat=c["stat"], direction=c["direction"],
            threshold=float(c["threshold"]),
            supporting_independent_signals=int(c["supporting_independent_signals"]),
        )
        if m.get("venue") != "kalshi":
            return {"status": "WATCH", "reason": "Venue-specific rules not validated", "quote_allowed": False}
        matched, reason = validate_ladder_candidate(
            candidate, int(m["yes_threshold"]), m["side"],
            m["player"], m["stat"], m.get("rules_verified") is True,
        )
        if not matched:
            return {"status": "WATCH", "reason": reason, "quote_allowed": False}
        ticker = m["contract"]
        if not isinstance(ticker, str) or not ticker.startswith("KXNBA"):
            return {"status": "WATCH", "reason": "Not an identified NBA contract", "quote_allowed": False}
        return {"status": "EXACT_MATCH_RESEARCH_ONLY", "quote_allowed": True,
                "quote_leg": {"venue": "kalshi", "contract": ticker,
                              "side": m["side"], "budget_usd": str(request["budget_usd"])}}
    except (KeyError, ValueError, TypeError) as exc:
        return {"status": "WATCH", "reason": "Invalid or incomplete NBA preflight data", "quote_allowed": False}


def nba_quote(request: dict, quote_fn=None) -> dict:
    """Run the existing read-only quote helper only after NBA preflight passes."""
    preflight = nba_preflight(request)
    if not preflight["quote_allowed"]:
        return preflight
    if quote_fn is None:
        from issue_quote_bridge import get_quote
        quote_fn = get_quote
    result = quote_fn(preflight["quote_leg"])
    return {"status": "SNAPSHOT_NOT_FIRM", "preflight": preflight, "quote": result,
            "trade_executed": False, "firm_executable_quote": False}
