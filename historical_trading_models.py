from __future__ import annotations

from pydantic import BaseModel, Field


class HistoricalTradingBacktestRequest(BaseModel):
    start_date: str
    end_date: str
    max_days: int = Field(default=31, ge=1, le=62)
    bankroll: float = Field(default=100.0, ge=0)
    unit_size: float = Field(default=1.0, gt=0)
    minimum_edge_points: float = Field(default=5.0, ge=0, le=50)
    hours_before_first_pitch: float = Field(default=2.0, ge=0.25, le=24.0)
    quote_lookback_hours: float = Field(default=6.0, ge=1.0, le=24.0)
    daily_cap_dollars: float = Field(default=5.0, ge=0)
    model_version: str = "2.6.6-frozen"
    compare_v27_candidate: bool = False


class HistoricalTradingBacktestResponse(BaseModel):
    status: str
    start_date: str
    end_date: str
    days_requested: int
    days_processed: int
    entry_rule: str
    leakage_policy: list[str]
    markets_found: int
    usable_quotes: int
    projected_starters: int
    matched_pitchers: int
    recommendations_evaluated: int
    unique_qualifiers: int
    v27_candidate_unique_qualifiers: int | None = None
    model_correction_validation: dict | None = None
    strategy_results: dict
    daily_results: list[dict]
    research_watchlists: dict
    warnings: list[str]
