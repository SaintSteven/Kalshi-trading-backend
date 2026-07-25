from pydantic import BaseModel, Field


class ModelLabRequest(BaseModel):
    start_date: str
    end_date: str
    max_days: int = Field(default=2, ge=1, le=5)
    model_version: str = "1.2.0"


class ExperimentSummary(BaseModel):
    experiment: str
    observations: int
    mae: float | None
    rmse: float | None
    mean_error: float | None
    mae_change_vs_baseline: float | None
    rmse_change_vs_baseline: float | None
    improved_mae: bool | None
    improved_rmse: bool | None
    status: str


class ModelLabResponse(BaseModel):
    start_date: str
    end_date: str
    baseline_name: str
    records_collected: int
    records_skipped: int
    experiments: list[ExperimentSummary]
    best_experiment_by_mae: str | None
    best_experiment_by_rmse: str | None
    warnings: list[str]
