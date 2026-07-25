from pydantic import BaseModel, Field

class LineupExperimentRequest(BaseModel):
    start_date: str
    end_date: str
    max_days: int = Field(default=2, ge=1, le=3)
    model_version: str = "1.1.0"

class ModelComparison(BaseModel):
    observations: int
    baseline_mae: float | None
    lineup_mae: float | None
    mae_change: float | None
    baseline_rmse: float | None
    lineup_rmse: float | None
    rmse_change: float | None
    baseline_mean_error: float | None
    lineup_mean_error: float | None
    improved: bool | None

class LineupExperimentResponse(BaseModel):
    start_date: str
    end_date: str
    records_collected: int
    records_skipped: int
    comparison: ModelComparison
    warnings: list[str]
