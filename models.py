from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Market(BaseModel):
    sport: str = "MLB"
    market_type: str = "Strikeouts"
    ticker: str
    event_ticker: str | None = None
    title: str
    player: str
    threshold: str
    yes_bid_cents: int | None = None
    yes_ask_cents: int | None = None
    no_bid_cents: int | None = None
    no_ask_cents: int | None = None
    volume: float | None = None
    liquidity_dollars: float | None = None
    close_time: datetime | None = None


class CardRequest(BaseModel):
    bankroll: float = Field(default=100.0, ge=0)
    already_committed_today: float = Field(default=0.0, ge=0)
    max_bet: float = Field(default=3.0, ge=0)
    date: str | None = None


class CardRecommendation(BaseModel):
    ticker: str
    player: str
    threshold: str
    price_cents: int | None
    decision: Literal["BUY", "WATCH", "PASS"]
    reason: list[str]
    projection: float | None = None
    fair_probability: float | None = None
    edge_points: float | None = None
    confidence: int | None = None
    suggested_stake: float = 0.0


class CardResponse(BaseModel):
    status: str
    target_date: str
    markets_reviewed: int
    recommendations: list[CardRecommendation]
    message: str
