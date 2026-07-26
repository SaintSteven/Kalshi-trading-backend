from __future__ import annotations

from collections import defaultdict

from edge_models import (
    EdgeAnalysisRequest,
    EdgeAnalysisResponse,
    PriceBucketResult,
    SideResult,
)


PRICE_BUCKETS = [
    (1, 20, "1-20¢"),
    (21, 40, "21-40¢"),
    (41, 60, "41-60¢"),
    (61, 80, "61-80¢"),
    (81, 99, "81-99¢"),
]


def _won(record) -> bool:
    threshold = int(record.threshold.rstrip("+"))
    yes_wins = record.actual_strikeouts >= threshold
    return yes_wins if record.side == "YES" else not yes_wins


def _edge_points(record) -> float:
    return record.model_probability * 100 - record.entry_price_cents


def _profit_for_one_contract(record, won: bool) -> float:
    price = record.entry_price_cents / 100
    return (1 - price) if won else -price


def _bucket_name(price_cents: int) -> str:
    for low, high, name in PRICE_BUCKETS:
        if low <= price_cents <= high:
            return name
    return "Other"


def _summarize(records, fee_rate: float):
    bets = len(records)
    wins = sum(1 for row in records if row["won"])
    losses = bets - wins
    amount_risked = sum(row["risk"] for row in records)
    gross_profit = sum(row["gross_profit"] for row in records)
    fees = sum(abs(row["gross_profit"]) * fee_rate for row in records)
    net_profit = gross_profit - fees
    edges = [row["edge_points"] for row in records]
    clv = [
        row["closing_price_cents"] - row["entry_price_cents"]
        for row in records
        if row["closing_price_cents"] is not None
    ]

    return {
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / bets if bets else None,
        "amount_risked": amount_risked,
        "gross_profit": gross_profit,
        "estimated_fees": fees,
        "net_profit": net_profit,
        "roi": net_profit / amount_risked if amount_risked else None,
        "average_edge_points": sum(edges) / len(edges) if edges else None,
        "average_clv_cents": sum(clv) / len(clv) if clv else None,
    }


def analyze_edges(request: EdgeAnalysisRequest) -> EdgeAnalysisResponse:
    warnings: list[str] = []

    qualified = []
    for record in request.records:
        edge = _edge_points(record)
        if edge < request.minimum_edge_points:
            continue

        won = _won(record)
        risk = record.entry_price_cents / 100
        gross_profit = _profit_for_one_contract(record, won)

        qualified.append(
            {
                "side": record.side,
                "entry_price_cents": record.entry_price_cents,
                "closing_price_cents": record.closing_price_cents,
                "model_probability": record.model_probability,
                "edge_points": edge,
                "won": won,
                "risk": risk,
                "gross_profit": gross_profit,
            }
        )

    overall = _summarize(qualified, request.fee_rate)

    by_side = []
    for side in ("YES", "NO"):
        rows = [row for row in qualified if row["side"] == side]
        summary = _summarize(rows, request.fee_rate)
        by_side.append(
            SideResult(
                side=side,
                bets=summary["bets"],
                wins=summary["wins"],
                losses=summary["losses"],
                win_rate=round(summary["win_rate"], 5)
                if summary["win_rate"] is not None else None,
                amount_risked=round(summary["amount_risked"], 2),
                net_profit=round(summary["net_profit"], 2),
                roi=round(summary["roi"], 5)
                if summary["roi"] is not None else None,
                average_edge_points=round(summary["average_edge_points"], 3)
                if summary["average_edge_points"] is not None else None,
                average_clv_cents=round(summary["average_clv_cents"], 3)
                if summary["average_clv_cents"] is not None else None,
            )
        )

    bucket_rows = defaultdict(list)
    for row in qualified:
        bucket_rows[_bucket_name(row["entry_price_cents"])].append(row)

    by_price_bucket = []
    for _, _, bucket in PRICE_BUCKETS:
        rows = bucket_rows.get(bucket, [])
        summary = _summarize(rows, request.fee_rate)
        by_price_bucket.append(
            PriceBucketResult(
                bucket=bucket,
                bets=summary["bets"],
                wins=summary["wins"],
                losses=summary["losses"],
                win_rate=round(summary["win_rate"], 5)
                if summary["win_rate"] is not None else None,
                amount_risked=round(summary["amount_risked"], 2),
                net_profit=round(summary["net_profit"], 2),
                roi=round(summary["roi"], 5)
                if summary["roi"] is not None else None,
                average_edge_points=round(summary["average_edge_points"], 3)
                if summary["average_edge_points"] is not None else None,
                average_clv_cents=round(summary["average_clv_cents"], 3)
                if summary["average_clv_cents"] is not None else None,
            )
        )

    if overall["bets"] < 100:
        warnings.append(
            "Fewer than 100 qualifying bets were analyzed. Treat ROI and win rate as preliminary."
        )

    if not any(row["closing_price_cents"] is not None for row in qualified):
        warnings.append(
            "No closing prices were supplied, so closing-line value could not be evaluated."
        )

    average_model_probability = (
        sum(row["model_probability"] for row in qualified) / len(qualified)
        if qualified else None
    )
    average_market_probability = (
        sum(row["entry_price_cents"] / 100 for row in qualified) / len(qualified)
        if qualified else None
    )

    return EdgeAnalysisResponse(
        records_reviewed=len(request.records),
        qualifying_bets=overall["bets"],
        wins=overall["wins"],
        losses=overall["losses"],
        win_rate=round(overall["win_rate"], 5)
        if overall["win_rate"] is not None else None,
        amount_risked=round(overall["amount_risked"], 2),
        gross_profit=round(overall["gross_profit"], 2),
        estimated_fees=round(overall["estimated_fees"], 2),
        net_profit=round(overall["net_profit"], 2),
        roi=round(overall["roi"], 5)
        if overall["roi"] is not None else None,
        average_model_probability=round(average_model_probability, 5)
        if average_model_probability is not None else None,
        average_market_probability=round(average_market_probability, 5)
        if average_market_probability is not None else None,
        average_edge_points=round(overall["average_edge_points"], 3)
        if overall["average_edge_points"] is not None else None,
        average_clv_cents=round(overall["average_clv_cents"], 3)
        if overall["average_clv_cents"] is not None else None,
        by_side=by_side,
        by_price_bucket=by_price_bucket,
        warnings=warnings,
    )
