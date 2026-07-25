from pydantic import BaseModel, Field
from backtest_models import BacktestResponse

class HistoricalBacktestRequest(BaseModel):
    start_date: str
    end_date: str
    max_days: int = Field(default=14, ge=1, le=31)
    model_version: str = "1.0.0"

class HistoricalBacktestResponse(BaseModel):
    start_date: str
    end_date: str
    records_collected: int
    collection_warnings: list[str]
    metrics: BacktestResponse
