from typing import Literal
from pydantic import BaseModel, Field


class HistoricalMarketRecord(BaseModel):
    player: str
    game_date: str
    threshold: str
    side: Literal["YES", "NO"]
    model_probability: float = Field(ge=0, le=1)
    entry_price_cents: int = Field(ge=1, le=99)
    actual_strikeouts: int = Field(ge=0)
    closing_price_cents: int | None = Field(default=None, ge=1, le=99)
    model_version: str = "1.4.0"
    confidence: float | None = Field(default=None, ge=0, le=1)


class EdgeAnalysisRequest(BaseModel):
    records: list[HistoricalMarketRecord]
    minimum_edge_points: float = Field(default=5.0, ge=0, le=50)
    fee_rate: float = Field(default=0.0, ge=0, le=0.25)


class PriceBucketResult(BaseModel):
    bucket: str
    bets: int
    wins: int
    losses: int
    win_rate: float | None
    amount_risked: float
    net_profit: float
    roi: float | None
    average_edge_points: float | None
    average_clv_cents: float | None


class SideResult(BaseModel):
    side: Literal["YES", "NO"]
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
    qualifying_bets: int
    wins: int
    losses: int
    win_rate: float | None
    amount_risked: float
    gross_profit: float
    estimated_fees: float
    net_profit: float
    roi: float | None
    average_model_probability: float | None
    average_market_probability: float | None
    average_edge_points: float | None
    average_clv_cents: float | None
    by_side: list[SideResult]
    by_price_bucket: list[PriceBucketResult]
    warnings: list[str]
