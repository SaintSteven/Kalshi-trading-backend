"""Read-only binary orderbook normalization and simulated whole-share fills."""
from decimal import Decimal, ROUND_CEILING

D = Decimal

def number(value):
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or isinstance(value, bool):
        raise ValueError("Missing numeric value")
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite numeric value")
    return result

def _levels(raw):
    levels = []
    for price, size in raw:
        price, size = number(price), number(size)
        if 0 < price < 1 and size > 0:
            levels.append((price, size))
    return sorted(levels, key=lambda item: item[0])

def kalshi_levels(payload, side):
    if side not in ("YES", "NO"):
        raise ValueError("Unknown Kalshi side")
    book = payload.get("orderbook_fp")
    if not isinstance(book, dict):
        raise ValueError("Kalshi orderbook_fp not present")
    opposite = "no_dollars" if side == "YES" else "yes_dollars"
    bids = book.get(opposite)
    if not isinstance(bids, list):
        raise ValueError("Missing opposite-side Kalshi bids")
    if any(not isinstance(row, (tuple, list)) or len(row) != 2 for row in bids):
        raise ValueError("Malformed Kalshi bids")
    return _levels([(D(1)-number(row[0]), row[1]) for row in bids])

def polymarket_us_levels(payload, side):
    """Convert US YES offers to YES asks and US YES bids into synthetic NO costs.

    NO is a fully collateralized short of the single YES instrument:
    taking NO crosses the highest YES bids (net risk=1-YES sale price).
    Source: official Polymarket US Market Structure and Collateral docs.
    """
    if side not in ("YES","NO"):
        raise ValueError("Unknown Polymarket US side")
    data = payload.get("marketData")
    if not isinstance(data,dict) or data.get("state") != "MARKET_STATE_OPEN":
        raise ValueError("Polymarket US book not confirmed open")
    rows = data.get("offers" if side=="YES" else "bids")
    if not isinstance(rows,list):
        raise ValueError("Missing Polymarket US book side")
    if any(not isinstance(row,dict) or "px" not in row or "qty" not in row for row in rows):
        raise ValueError("Malformed Polymarket US book levels")
    return _levels([(row["px"] if side=="YES" else D(1)-number(row["px"]),row["qty"]) for row in rows])

def simulated_buy(levels, count, fee_rate):
    """Depth-weighted gross price + conservative estimate of taker fees.

    Neither fee estimate nor snapshot is an exchange-confirmed executable fill.
    """
    if not isinstance(count, int) or not 1 <= count <= 10000:
        raise ValueError("contracts must be 1..10000 whole shares")
    fee_rate = number(fee_rate)
    if not 0 <= fee_rate <= 1:
        raise ValueError("invalid fee rate")
    left = D(count)
    gross = D(0)
    raw_fee = D(0)
    fills = []
    for px, available in levels:
        if left <= 0:
            break
        size = min(available, left)
        gross += size * px
        raw_fee += fee_rate * size * px * (D(1)-px)
        fills.append({"price_dollars": str(px), "contracts": str(size)})
        left -= size
    got = D(count) - left
    fee = raw_fee.quantize(D("0.01"), rounding=ROUND_CEILING) if got else D(0)
    return {
        "requested_contracts": count,
        "priced_contracts": str(got),
        "complete_depth": left == 0,
        "top_ask_dollars": str(levels[0][0]) if levels else None,
        "gross_cost_usd": str(gross),
        "estimated_fees_usd": str(fee),
        "estimated_total_usd": str(gross + fee),
        "gross_average_dollars": str(gross / got) if got else None,
        "estimated_all_in_average_dollars": str((gross + fee) / got) if got else None,
        "fill_levels": fills,
        "fee_verified": False,
        "firm_executable_quote": False,
        "status": "DEPTH_AVAILABLE_FEES_ESTIMATED" if left == 0 else "INSUFFICIENT_DEPTH",
    }


def affordable_buy(levels, budget_usd, fee_rate, max_contracts=10000):
    """Largest integer binary contract purchase fitting a USD risk budget.

    Fee estimate is conservatively rounded per simulated order. A budget can be
    underutilized because contracts are priced in whole shares; never round up.
    """
    budget = number(budget_usd)
    if not D(0) < budget <= D(100000):
        raise ValueError("stake budget must be positive")
    if not isinstance(max_contracts, int) or not 1 <= max_contracts <= 10000:
        raise ValueError("invalid contract limit")
    lo, hi, best = 1, max_contracts, None
    while lo <= hi:
        middle = (lo + hi) // 2
        candidate = simulated_buy(levels, middle, fee_rate)
        feasible = (candidate["complete_depth"]
                    and number(candidate["estimated_total_usd"]) <= budget)
        if feasible:
            best = candidate
            lo = middle + 1
        else:
            hi = middle - 1
    if best is None:
        return {
            "target_stake_usd": str(budget),
            "complete_depth": False,
            "whole_contracts_priced": 0,
            "estimated_total_usd": "0",
            "unspent_usd": str(budget),
            "status": "NO_AFFORDABLE_WHOLE_CONTRACT",
            "firm_executable_quote": False,
            "fee_verified": False,
        }
    best["target_stake_usd"] = str(budget)
    best["whole_contracts_priced"] = best["requested_contracts"]
    best["unspent_usd"] = str(budget - number(best["estimated_total_usd"]))
    return best
