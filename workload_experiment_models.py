from pydantic import BaseModel, Field

class WorkloadExperimentRequest(BaseModel):
    start_date: str
    end_date: str
    max_days: int = Field(default=2, ge=1, le=5)
    model_version: str = "1.3.0"

class WorkloadComparison(BaseModel):
    observations: int
    baseline_mae: float | None
    workload_mae: float | None
    mae_change: float | None
    baseline_rmse: float | None
    workload_rmse: float | None
    rmse_change: float | None
    baseline_mean_error: float | None
    workload_mean_error: float | None
    improved: bool | None

class WorkloadExperimentResponse(BaseModel):
    start_date: str
    end_date: str
    records_collected: int
    records_skipped: int
    comparison: WorkloadComparison
    warnings: list[str]
