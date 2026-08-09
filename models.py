from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
from projection_inputs import PitcherModelInput


class Market(BaseModel):
    sport: str = "MLB"
    market_type: str = "Strikeouts"
    ticker: str
    event_ticker: str | None = None
    title: str
    player: str
    threshold: str
    away_team: str | None = None
    away_team_name: str | None = None
    home_team: str | None = None
    home_team_name: str | None = None
    matchup: str | None = None
    game_start_time: datetime | None = None
    game_start_display: str | None = None
    game_status: Literal["UPCOMING", "LIVE", "STARTED", "UNKNOWN"] = "UNKNOWN"
    yes_bid_cents: int | None = None
    yes_ask_cents: int | None = None
    no_bid_cents: int | None = None
    no_ask_cents: int | None = None
    volume: float | None = None
    liquidity_dollars: float | None = None
    close_time: datetime | None = None
    tradable: bool = False
    tradability_reasons: list[str] = []


class PaperCardRequest(BaseModel):
    bankroll: float = Field(default=100, ge=0)
    already_committed_today: float = Field(default=0, ge=0)
    max_bet: float = Field(default=1, ge=0)
    date: str | None = None
    minimum_edge_points: float = Field(default=5, ge=0, le=50)
    use_automatic_data: bool = True
    pitchers: list[PitcherModelInput] = []


class PaperRecommendation(BaseModel):
    ticker: str
    player: str
    threshold: str
    away_team: str | None = None
    away_team_name: str | None = None
    home_team: str | None = None
    home_team_name: str | None = None
    matchup: str | None = None
    game_start_time: datetime | None = None
    game_start_display: str | None = None
    game_status: Literal["UPCOMING", "LIVE", "STARTED", "UNKNOWN"] = "UNKNOWN"
    side: Literal["YES", "NO", "NONE"]
    market_price_cents: int | None
    fair_probability: float | None
    calibrated_fair_probability: float | None = None
    calibration_method: str | None = None
    calibration_factor: float | None = None
    calibrated_edge_points: float | None = None
    uncalibrated_adjusted_edge_points: float | None = None
    raw_edge_points: float | None
    adjusted_edge_points: float | None
    projected_strikeouts: float | None
    baseline_k_pct: float | None
    adjusted_k_pct: float | None
    expected_batters_faced: float | None
    workload_floor: int | None
    workload_ceiling: int | None
    confidence: dict
    decision: Literal["MODEL EDGE", "WATCH", "PASS", "INSUFFICIENT DATA"]
    model_units: float = 0.0
    unlimited_bankroll_stake: float = 0.0
    research_only: bool = False
    research_units: float = 0.0
    research_stake: float = 0.0
    research_reason: str | None = None
    suggested_stake: float
    stake_status: str = "NO STAKE"
    reasons: list[str]
    warnings: list[str]


class PaperCardResponse(BaseModel):
    status: str
    requested_slate: str
    selected_slate: str
    slate_shifted: bool
    markets_reviewed: int
    live_markets_filtered: int = 0
    live_games_filtered: int = 0
    automatic_pitchers_collected: int
    projections_matched: int
    recommendations: list[PaperRecommendation]
    message: str


class MarketSummary(BaseModel):
    requested_slate: str
    selected_slate: str
    slate_shifted: bool
    total_markets: int
    tradable_markets: int
    upcoming_markets: int = 0
    started_markets: int = 0
    hidden_markets: int
    pitchers: int


class ExportCardRequest(BaseModel):
    card_date: str | None = None
    generated_at: str | None = None
    model_version: str = "2.5.1"
    bankroll: float = Field(default=100, ge=0)
    already_committed_today: float = Field(default=0, ge=0)
    selected_slate: str | None = None
    recommendations: list[PaperRecommendation] = []
