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
    if side != "YES":
        raise ValueError("Polymarket US NO/short mechanics not yet validated")
    data = payload.get("marketData")
    if not isinstance(data, dict) or data.get("state") != "MARKET_STATE_OPEN":
        raise ValueError("Polymarket US book not confirmed open")
    offers = data.get("offers")
    if not isinstance(offers, list):
        raise ValueError("Missing Polymarket US offers")
    if any(not isinstance(row, dict) or "px" not in row or "qty" not in row for row in offers):
        raise ValueError("Malformed Polymarket US offers")
    return _levels([(row["px"], row["qty"]) for row in offers])

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


def simulated_budget(levels, budget_usd, fee_rate):
    """Find greatest whole-contract purchase within a target dollar stake.

    Tests total cost INCLUDING estimated taker fees against the budget.
    Does not assume exchange min order size or account eligibility.
    """
    budget = number(budget_usd)
    if not Decimal("0") < budget <= Decimal("1000"):
        raise ValueError("stake_usd must be between 0 and 1000 dollars")
    if not levels:
        return {
            "requested_stake_usd": str(budget),
            "affordable_whole_contracts": 0,
            "status": "NO_ASK_LIQUIDITY",
            "quote": None,
        }
    # The best ask bounds the maximum possible contract count.
    high = min(10000, int(budget / levels[0][0]))
    low, best = 0, None
    while low < high:
        candidate = (low + high + 1) // 2
        fill = simulated_buy(levels, candidate, fee_rate)
        if fill["complete_depth"] and number(fill["estimated_total_usd"]) <= budget:
            low, best = candidate, fill
        else:
            high = candidate - 1
    if low and best is None:
        best = simulated_buy(levels, low, fee_rate)
    return {
        "requested_stake_usd": str(budget),
        "affordable_whole_contracts": low,
        "unspent_stake_usd": str(budget - number(best["estimated_total_usd"])) if best else str(budget),
        "status": "STAKED_BOOK_SNAPSHOT_FEES_ESTIMATED" if best else "BUDGET_TOO_SMALL_OR_DEPTH_INSUFFICIENT",
        "quote": best,
    }
