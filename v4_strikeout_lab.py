"""v4 MLB strikeout distribution challenger.

Research-only, leakage-safe test:
  * calibrate a negative-binomial distribution on the chronological training set
  * treat Kalshi's YES midpoint as the baseline forecast
  * fit one train-only residual weight between the market and baseball model
  * evaluate every available ladder on both executable YES and NO asks
  * take at most one trade per pitcher-game
  * hold to settlement and include the published Kalshi taker fee

The module deliberately avoids hand-picked ladders, sides, or price buckets.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from random import Random

from pydantic import BaseModel, Field, model_validator


EPS = 1e-6


def _norm(value: str | None) -> str:
    return "".join(char.lower() for char in (value or "") if char.isalnum())


class V4StrikeoutRequest(BaseModel):
    start_date: str
    end_date: str
    max_days: int = Field(default=62, ge=7, le=62)
    unit_size: float = Field(default=1.0, ge=0.25, le=100)
    hours_before_first_pitch: float = Field(default=2.0, ge=0.25, le=12)
    quote_lookback_hours: float = Field(default=6.0, ge=1, le=24)
    maximum_quote_age_minutes: float = Field(default=10.0, ge=1, le=60)
    maximum_spread_cents: int = Field(default=12, ge=1, le=50)
    minimum_entry_cents: int = Field(default=10, ge=1, le=49)
    maximum_entry_cents: int = Field(default=90, ge=51, le=99)
    minimum_net_edge_points: float = Field(default=5.0, ge=0, le=30)
    training_fraction: float = Field(default=0.35, ge=0.25, le=0.60)
    fee_rate: float = Field(default=0.07, ge=0, le=0.20)
    bootstrap_iterations: int = Field(default=2000, ge=200, le=10000)

    @model_validator(mode="after")
    def validate_range(self):
        if self.minimum_entry_cents >= self.maximum_entry_cents:
            raise ValueError("Minimum entry must be below maximum entry.")
        return self


def _clip(p: float) -> float:
    return min(1 - EPS, max(EPS, float(p)))


def _logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-min(60, x))
        return 1 / (1 + z)
    z = math.exp(max(-60, x))
    return z / (1 + z)


def _threshold(value: str | int) -> int:
    return int(str(value).rstrip("+"))


def _fit_mean_calibration(starts: list[dict]) -> tuple[float, float]:
    pairs = [
        (float(row["projected_strikeouts"]), float(row["actual_strikeouts"]))
        for row in starts
        if row.get("projected_strikeouts") is not None and row.get("actual_strikeouts") is not None
    ]
    if len(pairs) < 20:
        return 0.0, 1.0
    mx = sum(x for x, _ in pairs) / len(pairs)
    my = sum(y for _, y in pairs) / len(pairs)
    denominator = sum((x - mx) ** 2 for x, _ in pairs)
    slope = sum((x - mx) * (y - my) for x, y in pairs) / denominator if denominator else 1.0
    slope = min(1.5, max(0.5, slope))
    intercept = min(2.0, max(-2.0, my - slope * mx))
    return intercept, slope


def _nb_log_pmf(k: int, mean: float, shape: float) -> float:
    mean = max(0.05, mean)
    shape = max(0.10, shape)
    return (
        math.lgamma(k + shape) - math.lgamma(shape) - math.lgamma(k + 1)
        + shape * math.log(shape / (shape + mean))
        + k * math.log(mean / (shape + mean))
    )


def _fit_shape(starts: list[dict], intercept: float, slope: float) -> float:
    candidates = (0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 50.0, 100.0)
    scored = []
    for shape in candidates:
        nll = 0.0
        n = 0
        for row in starts:
            if row.get("projected_strikeouts") is None or row.get("actual_strikeouts") is None:
                continue
            mean = max(0.05, intercept + slope * float(row["projected_strikeouts"]))
            nll -= _nb_log_pmf(int(row["actual_strikeouts"]), mean, shape)
            n += 1
        scored.append((nll / max(1, n), shape))
    return min(scored)[1]


def negative_binomial_at_least(mean: float, shape: float, threshold: int) -> float:
    if threshold <= 0:
        return 1.0
    cumulative = 0.0
    for k in range(threshold):
        cumulative += math.exp(_nb_log_pmf(k, mean, shape))
    return _clip(1.0 - cumulative)


def _market_yes_probability(quote: dict) -> float | None:
    bid, ask = quote.get("yes_bid_cents"), quote.get("yes_ask_cents")
    if bid is None or ask is None:
        return None
    return _clip((float(bid) + float(ask)) / 200.0)


def _anchored_probability(market_yes: float, baseball_yes: float, weight: float) -> float:
    return _clip(_sigmoid(_logit(market_yes) + weight * (_logit(baseball_yes) - _logit(market_yes))))


def _fit_residual_weight(training_rows: list[dict]) -> tuple[float, list[dict]]:
    candidates = (0.0, 0.20, 0.40, 0.60, 0.80, 1.0)
    scores = []
    for weight in candidates:
        loss = 0.0
        for row in training_rows:
            p = _anchored_probability(row["market_yes"], row["baseball_yes"], weight)
            loss += (p - row["yes_outcome"]) ** 2
        score = loss / len(training_rows) if training_rows else 1.0
        scores.append({"weight": weight, "brier": round(score, 6)})
    winner = min(scores, key=lambda row: (row["brier"], row["weight"]))
    return float(winner["weight"]), scores


def kalshi_fee_cents(contracts: int, price_cents: float, fee_rate: float) -> int:
    if contracts <= 0 or not 0 < price_cents < 100 or fee_rate <= 0:
        return 0
    p = price_cents / 100.0
    return math.ceil(100 * fee_rate * contracts * p * (1 - p) - 1e-12)


def _execution(price: int, probability: float, unit_size: float, fee_rate: float) -> dict | None:
    budget = round(unit_size * 100)
    contracts = budget // price
    while contracts > 0:
        fee = kalshi_fee_cents(contracts, price, fee_rate)
        cost = contracts * price + fee
        if cost <= budget:
            break
        contracts -= 1
    if contracts < 1:
        return None
    fee = kalshi_fee_cents(contracts, price, fee_rate)
    cost = contracts * price + fee
    effective_price = cost / contracts
    expected_profit = probability * contracts * 100 - cost
    return {
        "contracts": int(contracts),
        "capital_used": round(cost / 100, 2),
        "entry_fee": round(fee / 100, 2),
        "effective_price_cents": round(effective_price, 3),
        "net_edge_points": round(probability * 100 - effective_price, 3),
        "expected_profit": round(expected_profit / 100, 4),
    }


def _settle(execution: dict, won: bool) -> float:
    proceeds = execution["contracts"] if won else 0.0
    return round(proceeds - execution["capital_used"], 2)


def _metrics(trades: list[dict]) -> dict:
    risk = round(sum(row["capital_used"] for row in trades), 2)
    pnl = round(sum(row["profit_loss"] for row in trades), 2)
    wins = sum(bool(row["won"]) for row in trades)
    equity = peak = drawdown = 0.0
    for row in sorted(trades, key=lambda x: (x["date"], x["player"])):
        equity += row["profit_loss"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "bets": len(trades),
        "wins": wins,
        "win_rate": round(wins / len(trades), 4) if trades else None,
        "capital_used": risk,
        "profit_loss": pnl,
        "roi": round(pnl / risk, 4) if risk else None,
        "average_net_edge_points": round(sum(row["net_edge_points"] for row in trades) / len(trades), 3) if trades else None,
        "maximum_drawdown": round(drawdown, 2),
    }


def _cluster_bootstrap(trades: list[dict], iterations: int, seed: int = 4042026) -> dict:
    groups = defaultdict(list)
    for row in trades:
        groups[(row["date"], row["player"])].append(row)
    keys = list(groups)
    if not keys:
        return {"clusters": 0, "iterations": iterations, "roi_p025": None, "roi_median": None, "roi_p975": None, "probability_roi_positive": None}
    rng = Random(seed)
    values = []
    for _ in range(iterations):
        sampled = [groups[keys[rng.randrange(len(keys))]] for _j in range(len(keys))]
        risk = sum(row["capital_used"] for group in sampled for row in group)
        pnl = sum(row["profit_loss"] for group in sampled for row in group)
        if risk:
            values.append(pnl / risk)
    values.sort()
    def quantile(q: float):
        return values[min(len(values) - 1, max(0, round(q * (len(values) - 1))))] if values else None
    return {
        "clusters": len(keys), "iterations": iterations,
        "roi_p025": round(quantile(.025), 4), "roi_median": round(quantile(.5), 4), "roi_p975": round(quantile(.975), 4),
        "probability_roi_positive": round(sum(v > 0 for v in values) / len(values), 4) if values else None,
    }


def _brier(rows: list[dict], key: str) -> float | None:
    if not rows:
        return None
    return round(sum((float(row[key]) - int(row["yes_outcome"])) ** 2 for row in rows) / len(rows), 6)


def analyze_v4_universe(starts: list[dict], quotes: list[dict], request: V4StrikeoutRequest) -> dict:
    start_day = datetime.strptime(request.start_date, "%Y-%m-%d").date()
    end_day = datetime.strptime(request.end_date, "%Y-%m-%d").date()
    total_days = (end_day - start_day).days + 1
    split_day = start_day + timedelta(days=max(2, math.floor(total_days * request.training_fraction)))
    training_starts = [row for row in starts if datetime.strptime(row["game_date"], "%Y-%m-%d").date() < split_day]
    evaluation_starts = [row for row in starts if datetime.strptime(row["game_date"], "%Y-%m-%d").date() >= split_day]
    intercept, slope = _fit_mean_calibration(training_starts)
    shape = _fit_shape(training_starts, intercept, slope)

    start_map = {(row["game_date"], _norm(row["player"])): row for row in starts}
    universe = []
    rejected = defaultdict(int)
    for quote in quotes:
        player, threshold = quote.get("player"), quote.get("threshold")
        date = str(quote.get("date") or quote.get("game_date") or "")
        if not date and quote.get("game_start_et"):
            date = str(quote["game_start_et"])[:10]
        start = start_map.get((date, _norm(player)))
        if not start or not threshold:
            rejected["unmatched_pitcher"] += 1
            continue
        market_yes = _market_yes_probability(quote)
        if market_yes is None:
            rejected["missing_bid_ask"] += 1
            continue
        age = quote.get("quote_age_minutes")
        if age is None or float(age) > request.maximum_quote_age_minutes:
            rejected["stale_quote"] += 1
            continue
        bid, ask = int(quote["yes_bid_cents"]), int(quote["yes_ask_cents"])
        if ask - bid > request.maximum_spread_cents:
            rejected["wide_spread"] += 1
            continue
        mean = max(0.05, intercept + slope * float(start["projected_strikeouts"]))
        baseball_yes = negative_binomial_at_least(mean, shape, _threshold(threshold))
        universe.append({
            "date": date, "player": player, "ticker": quote.get("ticker"), "threshold": str(threshold),
            "actual_strikeouts": int(start["actual_strikeouts"]), "projected_strikeouts": round(float(start["projected_strikeouts"]), 3),
            "calibrated_mean": round(mean, 3), "market_yes": market_yes, "baseball_yes": baseball_yes,
            "yes_outcome": int(int(start["actual_strikeouts"]) >= _threshold(threshold)),
            "yes_ask": int(quote["yes_ask_cents"]), "no_ask": int(quote["no_ask_cents"]),
            "yes_bid": bid, "quote_age_minutes": round(float(age), 2),
        })

    train_quotes = [row for row in universe if datetime.strptime(row["date"], "%Y-%m-%d").date() < split_day]
    holdout = [row for row in universe if datetime.strptime(row["date"], "%Y-%m-%d").date() >= split_day]
    residual_weight, weight_grid = _fit_residual_weight(train_quotes)
    for row in universe:
        row["anchored_yes"] = _anchored_probability(row["market_yes"], row["baseball_yes"], residual_weight)

    candidates = []
    for row in holdout:
        for side in ("YES", "NO"):
            price = row["yes_ask"] if side == "YES" else row["no_ask"]
            probability = row["anchored_yes"] if side == "YES" else 1 - row["anchored_yes"]
            if not request.minimum_entry_cents <= price <= request.maximum_entry_cents:
                rejected["price_range"] += 1
                continue
            execution = _execution(price, probability, request.unit_size, request.fee_rate)
            if not execution or execution["net_edge_points"] < request.minimum_net_edge_points:
                rejected["below_net_edge"] += 1
                continue
            won = bool(row["yes_outcome"]) if side == "YES" else not bool(row["yes_outcome"])
            candidates.append({**row, "side": side, "entry_price_cents": price,
                "model_probability": round(probability, 6), **execution, "won": won,
                "profit_loss": _settle(execution, won)})

    best = {}
    for row in candidates:
        key = (row["date"], _norm(row["player"]))
        previous = best.get(key)
        if previous is None or (row["net_edge_points"], row["expected_profit"], -row["entry_price_cents"]) > (previous["net_edge_points"], previous["expected_profit"], -previous["entry_price_cents"]):
            best[key] = row
    trades = sorted(best.values(), key=lambda row: (row["date"], row["player"]))

    # Executable, settlement-guaranteed baskets within each pitcher ladder.
    structural = []
    by_pitcher = defaultdict(list)
    for row in holdout:
        by_pitcher[(row["date"], _norm(row["player"]))].append(row)
        pair_cost = row["yes_ask"] + row["no_ask"] + kalshi_fee_cents(1, row["yes_ask"], request.fee_rate) + kalshi_fee_cents(1, row["no_ask"], request.fee_rate)
        if pair_cost < 100:
            structural.append({"date": row["date"], "player": row["player"], "type": "same-contract", "contracts": [row["ticker"]], "cost_cents": pair_cost, "minimum_payout_cents": 100, "guaranteed_profit_cents": 100 - pair_cost})
    for (date, _), rows in by_pitcher.items():
        ordered = sorted(rows, key=lambda row: _threshold(row["threshold"]))
        for lower, higher in zip(ordered, ordered[1:]):
            cost = lower["yes_ask"] + higher["no_ask"] + kalshi_fee_cents(1, lower["yes_ask"], request.fee_rate) + kalshi_fee_cents(1, higher["no_ask"], request.fee_rate)
            if cost < 100:
                structural.append({"date": date, "player": lower["player"], "type": "adjacent-ladder", "contracts": [lower["ticker"], higher["ticker"]], "cost_cents": cost, "minimum_payout_cents": 100, "guaranteed_profit_cents": 100 - cost})

    bootstrap = _cluster_bootstrap(trades, request.bootstrap_iterations)
    by_month = {month: _metrics([row for row in trades if row["date"].startswith(month)]) for month in sorted({row["date"][:7] for row in trades})}
    by_side = {side: _metrics([row for row in trades if row["side"] == side]) for side in ("YES", "NO")}
    metrics = _metrics(trades)
    positive_months = sum(1 for value in by_month.values() if (value.get("roi") or 0) > 0)
    gates = {
        "minimum_100_pitcher_games": metrics["bets"] >= 100,
        "positive_roi": (metrics.get("roi") or 0) > 0,
        "bootstrap_lower_bound_nonnegative": bootstrap["roi_p025"] is not None and bootstrap["roi_p025"] >= 0,
        "anchored_brier_beats_market": (_brier(holdout, "anchored_yes") or 1) < (_brier(holdout, "market_yes") or 0),
        "multiple_positive_months": positive_months >= 2,
    }
    promoted = all(gates.values())
    return {
        "version": "4.0.0", "status": "complete", "mode": "research-only-full-ladder-strikeout-challenger",
        "split_date": split_day.isoformat(), "training_starts": len(training_starts), "holdout_starts": len(evaluation_starts),
        "training_contracts": len(train_quotes), "holdout_contracts": len(holdout),
        "fit": {"mean_intercept": round(intercept, 5), "mean_slope": round(slope, 5), "negative_binomial_shape": shape,
                "market_residual_weight": residual_weight, "weight_grid": weight_grid},
        "calibration": {"market_brier": _brier(holdout, "market_yes"), "baseball_brier": _brier(holdout, "baseball_yes"), "anchored_brier": _brier(holdout, "anchored_yes")},
        "overall": metrics, "by_month": by_month, "by_side": by_side, "cluster_bootstrap": bootstrap,
        "promotion": {"eligible": promoted, "decision": "PAPER ELIGIBLE" if promoted else "REJECT / KEEP RESEARCH-ONLY", "gates": gates},
        "coverage": {"starts": len(starts), "quotes_received": len(quotes), "usable_contracts": len(universe), "qualifying_contract_sides": len(candidates), **dict(rejected)},
        "structural_opportunities": structural, "trades": trades,
        "rules": [
            "The first chronological portion is calibration-only; every reported trade occurs on or after the frozen split date.",
            "The negative-binomial mean correction, dispersion, and market-residual weight are fit only on training dates.",
            "Every reconstructable ladder is evaluated on both executable YES and NO asks; no side or threshold is preselected.",
            "One maximum-edge position is retained per pitcher-game so correlated ladders do not inflate the bet count.",
            "Trades are held to settlement; the exact contract count and entry taker fee must fit inside the unit budget.",
            "The result remains research-only unless every displayed promotion gate passes on an adequate multi-month holdout.",
        ],
    }


async def run_v4_strikeout_backtest(request: V4StrikeoutRequest, progress_callback=None) -> dict:
    # Lazy imports keep the pure probability/validation functions independently
    # testable while reusing the production collectors at runtime.
    import httpx
    from historical_backtest_collector import collect_historical_starts
    from historical_trading_backtest import _quotes_for_date

    start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    days = (end - start).days + 1
    if days > request.max_days:
        raise ValueError(f"Requested {days} days; Maximum Days is {request.max_days}.")
    if end >= datetime.now().astimezone().date():
        raise ValueError("The backtest end date must be before today.")

    async def emit(phase: str, percent: int, message: str):
        if progress_callback:
            value = progress_callback({"phase": phase, "percent": percent, "message": message})
            if hasattr(value, "__await__"):
                await value

    await emit("features", 3, "Rebuilding leakage-safe pitcher inputs and actual results…")
    starts, warnings = await collect_historical_starts(request.start_date, request.end_date, request.max_days)
    quotes = []
    await emit("quotes", 38, "Reconstructing both YES and NO executable asks for every strikeout ladder…")
    async with httpx.AsyncClient(headers={"User-Agent": "KalshiTradingPlatform/4.0.0-strikeout-lab"}, timeout=60) as client:
        current = start
        completed = 0
        while current <= end:
            day_quotes, _found = await _quotes_for_date(client, current.isoformat(), request.hours_before_first_pitch, request.quote_lookback_hours, warnings)
            for row in day_quotes:
                row["date"] = current.isoformat()
            quotes.extend(day_quotes)
            completed += 1
            await emit("quotes", 38 + round(47 * completed / days), f"Reconstructed {completed}/{days} slate days…")
            current += timedelta(days=1)
    await emit("analysis", 90, "Fitting the training-only distribution and evaluating the untouched holdout…")
    result = analyze_v4_universe(starts, quotes, request)
    result["warnings"] = warnings
    await emit("complete", 100, f"Complete: {result['overall']['bets']} independent holdout trades qualified.")
    return result
