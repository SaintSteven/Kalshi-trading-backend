"""Read-only binary order book pricing. Decimal math, fee estimates, no trades."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

D = Decimal
ONE, ZERO, CENT = D("1"), D("0"), D("0.01")

class BadBook(ValueError):
    pass

def dec(value):
    if isinstance(value, dict):
        value = value.get("value")
    try:
        result = D(str(value))
    except (TypeError, InvalidOperation) as exc:
        raise BadBook(f"Invalid price or quantity: {value!r}") from exc
    if not result.is_finite():
        raise BadBook("Non-finite number in order book")
    return result

def dollars(value):
    return None if value is None else format(value.quantize(D("0.000001")), "f")

def normalized_ask_levels(venue, side, payload):
    """Return ascending (ask price, quantity) levels; never synthesize Poly NO."""
    if side not in {"YES", "NO"}:
        raise BadBook("Side must be YES or NO")
    if venue == "kalshi":
        fp = payload.get("orderbook_fp")
        if not isinstance(fp, dict):
            raise BadBook("Kalshi response lacks orderbook_fp; refusing legacy guesses")
        opposite = "no_dollars" if side == "YES" else "yes_dollars"
        raw = fp.get(opposite, [])
        if not isinstance(raw, list):
            raise BadBook("Invalid Kalshi levels")
        out = []
        for level in raw:
            if not isinstance(level, list) or len(level) < 2:
                raise BadBook("Invalid Kalshi level")
            bid, qty = dec(level[0]), dec(level[1])
            ask = ONE - bid
            if ZERO < ask < ONE and qty > ZERO:
                out.append((ask, qty))
        return sorted(out, key=lambda x: x[0])
    if venue == "polymarket_us":
        if side == "NO":
            raise BadBook("Polymarket US synthetic NO disabled: identify an exact tradable opposite contract")
        data = payload.get("marketData")
        if not isinstance(data, dict):
            raise BadBook("Polymarket US response lacks marketData")
        raw = data.get("offers", [])
        if not isinstance(raw, list):
            raise BadBook("Invalid Polymarket US offers")
        out = []
        for level in raw:
            if not isinstance(level, dict):
                raise BadBook("Invalid Polymarket US level")
            ask, qty = dec(level.get("px")), dec(level.get("qty"))
            if ZERO < ask < ONE and qty > ZERO:
                out.append((ask, qty))
        return sorted(out, key=lambda x: x[0])
    raise BadBook("Unsupported venue")

def _fee(fills, coeff):
    if coeff is None:
        return None
    if coeff < ZERO or coeff > ONE:
        raise BadBook("Invalid fee coefficient")
    # Estimate only: actual fee waivers and rounding may vary by venue or market.
    fee = sum((qty * price * (ONE - price) * coeff for price, qty in fills), ZERO)
    return fee.quantize(CENT, rounding=ROUND_CEILING)

def fill_for_contracts(levels, quantity, fee_coeff):
    if not quantity.is_finite() or quantity <= ZERO or quantity > D("10000"):
        raise BadBook("Quantity must be >0 and <=10000")
    remaining, fills = quantity, []
    for price, available in levels:
        take = min(remaining, available)
        if take <= ZERO:
            continue
        fills.append((price, take))
        remaining -= take
        if remaining <= ZERO:
            break
    filled = quantity - remaining
    cost = sum((p * q for p, q in fills), ZERO)
    fee = _fee(fills, fee_coeff)
    total = cost + fee if fee is not None else None
    return {
        "full_depth": remaining <= ZERO,
        "requested_contracts": dollars(quantity),
        "covered_contracts": dollars(filled),
        "unfilled_contracts": dollars(remaining),
        "best_ask": dollars(levels[0][0]) if levels else None,
        "worst_fill_ask": dollars(fills[-1][0]) if fills else None,
        "raw_vwap": dollars(cost / filled) if filled else None,
        "raw_cost_usd": dollars(cost),
        "estimated_taker_fee_usd": dollars(fee),
        "estimated_total_usd": dollars(total),
        "estimated_all_in_per_contract": dollars(total / filled) if total is not None and filled else None,
        "fee_status": "estimated" if fee is not None else "unknown",
        "levels_used": len(fills),
    }

def fill_for_budget(levels, budget, fee_coeff):
    """Max affordable whole contracts within depth, using estimated fees."""
    if not budget.is_finite() or budget <= ZERO or budget > D("10000"):
        raise BadBook("budget_usd must be >0 and <=10000")
    if fee_coeff is None:
        raise BadBook("Cannot calculate fee-inclusive stake sizing without fee estimate")
    available = sum((q for _, q in levels), ZERO)
    max_n = min(int(available.to_integral_value(rounding=ROUND_FLOOR)), 10000)
    if max_n < 1:
        return {"affordable_contracts": 0, "full_depth": False, "reason": "No whole contract at visible depth"}
    lo, hi = 0, max_n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        test = fill_for_contracts(levels, D(mid), fee_coeff)
        if test["full_depth"] and dec(test["estimated_total_usd"]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    if lo == 0:
        return {"affordable_contracts": 0, "full_depth": False, "reason": "Budget below first-contract all-in cost"}
    result = fill_for_contracts(levels, D(lo), fee_coeff)
    result.update({"affordable_contracts": lo, "budget_usd": dollars(budget),
                   "budget_remaining_usd": dollars(budget - dec(result["estimated_total_usd"])),
                   "whole_contracts_only": True})
    return result

def evaluate_price(fill, fair_probability, max_all_in):
    result = {"max_all_in_price": dollars(max_all_in), "price_within_ceiling": None,
              "fair_probability": dollars(fair_probability), "estimated_ev_usd": None,
              "estimated_edge_percentage_points": None, "research_qualifies": False}
    effective = fill.get("estimated_all_in_per_contract")
    if not fill.get("full_depth") or effective is None:
        return result
    price = dec(effective)
    if max_all_in is not None:
        result["price_within_ceiling"] = price <= max_all_in
    if fair_probability is not None:
        if not ZERO <= fair_probability <= ONE:
            raise BadBook("fair_probability must be 0..1")
        contracts = dec(fill["covered_contracts"])
        result["estimated_ev_usd"] = dollars((fair_probability - price) * contracts)
        result["estimated_edge_percentage_points"] = dollars((fair_probability - price) * D("100"))
    return result
