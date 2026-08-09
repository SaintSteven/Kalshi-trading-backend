from typing import Literal
from pydantic import BaseModel, Field


class HistoricalMarketRecord(BaseModel):
    player: str
    game_date: str
    threshold: str
    side: Literal["YES", "NO"]
    model_probability: float = Field(ge=0, le=1)
    raw_model_probability: float | None = Field(default=None, ge=0, le=1)
    entry_price_cents: int = Field(ge=1, le=99)
    actual_strikeouts: int = Field(ge=0)
    closing_price_cents: int | None = Field(default=None, ge=1, le=99)
    model_version: str = "2.0.0"
    confidence: float | None = Field(default=None, ge=0, le=100)
    confidence_tier: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    adjusted_edge_points: float | None = None
    stake: float = Field(default=1.0, ge=0)
    model_stake: float | None = Field(default=None, ge=0)
    model_units: float | None = Field(default=None, ge=0)
    paper_included: bool | None = None
    research_only: bool = False
    research_units: float | None = Field(default=None, ge=0)
    research_stake: float | None = Field(default=None, ge=0)
    research_reason: str | None = None
    ticker: str | None = None
    matchup: str | None = None


class SavedTradeSnapshot(BaseModel):
    player: str
    game_date: str
    threshold: str
    side: Literal["YES", "NO"]
    model_probability: float = Field(ge=0, le=1)
    raw_model_probability: float | None = Field(default=None, ge=0, le=1)
    entry_price_cents: int = Field(ge=1, le=99)
    model_version: str = "2.0.0"
    confidence: float | None = Field(default=None, ge=0, le=100)
    confidence_tier: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    adjusted_edge_points: float | None = None
    stake: float = Field(default=1.0, ge=0)
    model_stake: float | None = Field(default=None, ge=0)
    model_units: float | None = Field(default=None, ge=0)
    paper_included: bool | None = None
    research_only: bool = False
    research_units: float | None = Field(default=None, ge=0)
    research_stake: float | None = Field(default=None, ge=0)
    research_reason: str | None = None
    ticker: str | None = None
    matchup: str | None = None
    captured_at: str | None = None


class SettleSnapshotsRequest(BaseModel):
    records: list[SavedTradeSnapshot]


class SettleSnapshotsResponse(BaseModel):
    settled_records: list[HistoricalMarketRecord]
    pending_records: list[SavedTradeSnapshot]
    warnings: list[str]


class EdgeAnalysisRequest(BaseModel):
    records: list[HistoricalMarketRecord]
    minimum_edge_points: float = Field(default=5.0, ge=0, le=100)
    minimum_confidence: float = Field(default=68.0, ge=0, le=100)
    fee_rate: float = Field(default=0.0, ge=0, le=0.25)


class SegmentResult(BaseModel):
    segment: str
    bets: int
    wins: int
    losses: int
    win_rate: float | None
    amount_risked: float
    net_profit: float
    roi: float | None
    average_edge_points: float | None
    average_clv_cents: float | None


class EdgeAnalysisResponse(BaseModel):
    records_reviewed: int
    unique_markets_reviewed: int
    qualifying_bets: int
    unique_qualifying_markets: int
    wins: int
    losses: int
    win_rate: float | None
    amount_risked: float
    gross_profit: float
    estimated_fees: float
    net_profit: float
    roi: float | None
    max_drawdown: float
    average_model_probability: float | None
    average_market_probability: float | None
    average_edge_points: float | None
    average_clv_cents: float | None
    by_side: list[SegmentResult]
    by_price_bucket: list[SegmentResult]
    by_edge_bucket: list[SegmentResult]
    by_confidence_tier: list[SegmentResult]
    by_ladder: list[SegmentResult]
    warnings: list[str]
