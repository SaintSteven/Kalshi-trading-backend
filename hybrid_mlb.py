"""Hybrid MLB discovery, validation, pricing, and CLV research helpers.

Paper/research only. Discovery nominates candidates; QC and price retain veto power.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import floor
from typing import Literal

from pydantic import BaseModel, Field


MarketType = Literal["GAME", "STRIKEOUT"]
SignalKind = Literal["MODEL", "HANDICAPPER", "SHARP_MARKET", "PROJECTION"]
QCStatus = Literal["PASS", "WARN", "FAIL", "PENDING"]
PricingPolicy = Literal["LEGACY_V36", "MARKET_FIRST_V37"]


class DiscoverySignal(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    kind: SignalKind
    supports_candidate: bool = True
    independence_group: str | None = None
    note: str | None = None


class QCCheck(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    status: QCStatus
    note: str | None = None


class HybridCandidateRequest(BaseModel):
    candidate_id: str | None = None
    market_type: MarketType
    selection: str = Field(min_length=1, max_length=160)
    contract_side: Literal["YES", "NO"] = "YES"
    kalshi_price_cents: int = Field(ge=1, le=99)
    model_fair_probability: float = Field(ge=0.01, le=0.99)
    external_market_probability: float | None = Field(default=None, ge=0.01, le=0.99)
    signals: list[DiscoverySignal] = []
    qc_checks: list[QCCheck] = []
    minimum_edge_points: float = Field(default=5.0, ge=0, le=30)
    market_move_points: float = Field(default=0.0, ge=-30, le=30)
    pricing_policy: PricingPolicy = "LEGACY_V36"
    estimated_cost_cents: float = Field(default=0.0, ge=0, le=10)
    notes: str | None = None


class HybridCandidateResult(BaseModel):
    candidate_id: str
    evaluated_at: str
    market_type: MarketType
    selection: str
    contract_side: Literal["YES", "NO"]
    discovery_grade: Literal["A", "B", "C"]
    supporting_sources: int
    independent_sources: int
    signal_kinds: list[str]
    model_fair_probability: float
    external_market_probability: float | None
    blended_fair_probability: float
    decision_fair_probability: float
    fair_value_method: str
    kalshi_price_cents: int
    raw_edge_points: float
    gross_edge_points: float
    net_edge_points: float
    estimated_cost_cents: float
    required_edge_points: float
    maximum_entry_cents: int
    pricing_policy: PricingPolicy
    model_veto_applied: bool
    qc_status: Literal["CLEAN", "WARN", "FAIL", "INCOMPLETE"]
    decision: Literal["BUY", "WATCH", "PASS"]
    reasons: list[str]
    warnings: list[str]
    tracking_record: dict


class CLVRecord(BaseModel):
    candidate_id: str
    market_type: MarketType
    discovery_grade: Literal["A", "B", "C"]
    source_names: list[str] = []
    entry_price_cents: int = Field(ge=1, le=99)
    close_price_cents: int = Field(ge=1, le=99)
    stake: float = Field(default=1.0, ge=0)
    profit_loss: float | None = None


def _independence_key(signal: DiscoverySignal) -> str:
    return (signal.independence_group or signal.source).strip().lower()


def _discovery_grade(request: HybridCandidateRequest) -> tuple[str, int, int, list[str]]:
    supporting = [s for s in request.signals if s.supports_candidate]
    independent = len({_independence_key(s) for s in supporting})
    kinds = sorted({s.kind for s in supporting})
    sharp_confirmation = "SHARP_MARKET" in kinds or request.market_move_points >= 1.0

    if independent >= 4 and len(kinds) >= 3 and sharp_confirmation:
        grade = "A"
    elif independent >= 2 and len(kinds) >= 2:
        grade = "B"
    else:
        grade = "C"
    return grade, len(supporting), independent, kinds


def _qc_status(checks: list[QCCheck]) -> str:
    statuses = {c.status for c in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if not checks or "PENDING" in statuses:
        return "INCOMPLETE"
    if "WARN" in statuses:
        return "WARN"
    return "CLEAN"


def evaluate_candidate(request: HybridCandidateRequest) -> HybridCandidateResult:
    grade, supporting, independent, kinds = _discovery_grade(request)
    qc_status = _qc_status(request.qc_checks)

    external = request.external_market_probability
    market_first = request.market_type == "GAME" and request.pricing_policy == "MARKET_FIRST_V37"
    model_veto = market_first and request.model_fair_probability < 0.50
    if market_first and external is not None:
        blended = external
        fair_value_method = "Sportsbook no-vig baseline; independent model used only as a veto."
    elif external is None:
        blended = request.model_fair_probability
        fair_value_method = "Independent model only; external market unavailable."
    elif request.market_type == "GAME":
        blended = 0.45 * request.model_fair_probability + 0.55 * external
        fair_value_method = "Frozen v3.6 45/55 model-market blend."
    else:
        blended = 0.65 * request.model_fair_probability + 0.35 * external
        fair_value_method = "Strikeout 65/35 model-market blend."

    blended = round(min(0.99, max(0.01, blended)), 4)
    gross_edge = round(blended * 100 - request.kalshi_price_cents, 2)
    costs = request.estimated_cost_cents if market_first else 0.0
    net_edge = round(gross_edge - costs, 2)
    # Retain raw_edge_points for compatibility with the existing mobile UI.
    raw_edge = net_edge if market_first else gross_edge
    grade_penalty = {"A": 0.0, "B": 0.0, "C": 2.0}[grade]
    qc_penalty = 1.0 if qc_status == "WARN" else 0.0
    required_edge = round(request.minimum_edge_points + grade_penalty + qc_penalty, 2)
    maximum_entry = max(1, min(99, floor(blended * 100 - required_edge - costs)))

    reasons = [
        f"Discovery {grade}: {independent} independent supporting source(s) across {len(kinds)} signal type(s).",
        f"Decision fair value {blended * 100:.1f}% versus Kalshi {request.kalshi_price_cents} cents.",
        f"Minimum required edge {required_edge:.1f} points; maximum entry {maximum_entry} cents.",
    ]
    warnings: list[str] = []

    if qc_status == "FAIL":
        decision = "PASS"
        warnings.append("Mandatory QC failed; price and consensus cannot override a hard failure.")
    elif qc_status == "INCOMPLETE":
        decision = "WATCH"
        warnings.append("QC is incomplete; candidate cannot be promoted to BUY.")
    elif market_first and external is None:
        decision = "WATCH"
        warnings.append("Market-first game pricing requires an external no-vig probability.")
    elif market_first and grade == "C":
        decision = "WATCH"
        warnings.append("Grade C is watch-only under the v3.7 game policy.")
    elif model_veto:
        decision = "WATCH"
        warnings.append("The independent model disagrees with the market selection and vetoed BUY.")
    elif raw_edge >= required_edge:
        decision = "BUY"
    elif raw_edge >= required_edge - 2.0:
        decision = "WATCH"
        warnings.append("Candidate is close to the entry threshold; use a limit order or wait for price improvement.")
    else:
        decision = "PASS"
        warnings.append("Current Kalshi price does not provide the required margin of safety.")

    disagreeing = [s.source for s in request.signals if not s.supports_candidate]
    if disagreeing:
        warnings.append("Disagreeing signal(s): " + ", ".join(sorted(set(disagreeing))))
    if market_first and costs:
        warnings.append(f"Net edge includes {costs:.1f} cents of estimated fees and slippage.")
    failed = [c.label for c in request.qc_checks if c.status == "FAIL"]
    pending = [c.label for c in request.qc_checks if c.status == "PENDING"]
    if failed:
        warnings.append("Failed QC: " + ", ".join(failed))
    if pending:
        warnings.append("Pending QC: " + ", ".join(pending))

    candidate_id = request.candidate_id or (
        request.market_type.lower() + "-" + "-".join(request.selection.lower().split())[:60]
    )
    tracking_record = {
        "candidate_id": candidate_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "discovery_method": "TOP_DOWN" if request.market_type == "GAME" else "BOTTOM_UP",
        "consensus_grade": grade,
        "sources_agreeing": supporting,
        "independent_sources": independent,
        "source_names": [s.source for s in request.signals if s.supports_candidate],
        "our_fair_probability": request.model_fair_probability,
        "market_fair_probability": external,
        "blended_fair_probability": blended,
        "decision_fair_probability": blended,
        "fair_value_method": fair_value_method,
        "kalshi_entry_price": request.kalshi_price_cents,
        "estimated_cost_cents": costs,
        "gross_edge_points": gross_edge,
        "net_edge_points": net_edge,
        "kalshi_close_price": None,
        "clv_cents": None,
        "result": None,
        "profit_loss": None,
        "decision": decision,
    }

    return HybridCandidateResult(
        candidate_id=candidate_id,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        market_type=request.market_type,
        selection=request.selection,
        contract_side=request.contract_side,
        discovery_grade=grade,
        supporting_sources=supporting,
        independent_sources=independent,
        signal_kinds=kinds,
        model_fair_probability=request.model_fair_probability,
        external_market_probability=external,
        blended_fair_probability=blended,
        decision_fair_probability=blended,
        fair_value_method=fair_value_method,
        kalshi_price_cents=request.kalshi_price_cents,
        raw_edge_points=raw_edge,
        gross_edge_points=gross_edge,
        net_edge_points=net_edge,
        estimated_cost_cents=costs,
        required_edge_points=required_edge,
        maximum_entry_cents=maximum_entry,
        pricing_policy=request.pricing_policy,
        model_veto_applied=model_veto,
        qc_status=qc_status,
        decision=decision,
        reasons=reasons,
        warnings=warnings,
        tracking_record=tracking_record,
    )


def summarize_clv(records: list[CLVRecord]) -> dict:
    def summary(rows: list[CLVRecord]) -> dict:
        risked = sum(r.stake for r in rows)
        pnl_values = [r.profit_loss for r in rows if r.profit_loss is not None]
        pnl = sum(pnl_values) if pnl_values else None
        return {
            "bets": len(rows),
            "average_clv_cents": round(sum(r.close_price_cents - r.entry_price_cents for r in rows) / len(rows), 2) if rows else None,
            "risked": round(risked, 2),
            "profit_loss": round(pnl, 2) if pnl is not None else None,
            "roi": round(pnl / risked, 4) if pnl is not None and risked else None,
        }

    by_grade: dict[str, list[CLVRecord]] = defaultdict(list)
    by_market: dict[str, list[CLVRecord]] = defaultdict(list)
    by_source: dict[str, list[CLVRecord]] = defaultdict(list)
    for record in records:
        by_grade[record.discovery_grade].append(record)
        by_market[record.market_type].append(record)
        for source in set(record.source_names):
            by_source[source].append(record)

    return {
        "overall": summary(records),
        "by_discovery_grade": {k: summary(v) for k, v in sorted(by_grade.items())},
        "by_market_type": {k: summary(v) for k, v in sorted(by_market.items())},
        "by_source": {k: summary(v) for k, v in sorted(by_source.items())},
    }
