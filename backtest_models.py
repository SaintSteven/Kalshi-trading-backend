from pydantic import BaseModel, Field

class HistoricalStart(BaseModel):
    player: str
    game_date: str
    actual_strikeouts: int = Field(ge=0)
    projected_strikeouts: float = Field(ge=0)
    ladder_probabilities: dict[str, float]
    features: dict = {}

class BacktestRequest(BaseModel):
    starts: list[HistoricalStart]
    model_version: str = "0.9.1"

class CalibrationBucket(BaseModel):
    bucket_low: float
    bucket_high: float
    observations: int
    average_predicted_probability: float | None
    actual_win_rate: float | None
    calibration_error: float | None

class LadderMetrics(BaseModel):
    threshold: str
    observations: int
    brier_score: float | None
    log_loss: float | None
    calibration_error: float | None

class BacktestResponse(BaseModel):
    model_version: str
    observations: int
    mae: float | None
    rmse: float | None
    mean_error: float | None
    over_projection_rate: float | None
    under_projection_rate: float | None
    exact_projection_rate: float | None
    ladder_metrics: list[LadderMetrics]
    calibration: dict[str, list[CalibrationBucket]]
    feature_segments: dict
    warnings: list[str]
