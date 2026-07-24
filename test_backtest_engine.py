from backtest_engine import run_backtest
from backtest_models import BacktestRequest,HistoricalStart

def s(actual,projected,p5):
    return HistoricalStart(player="Example",game_date="2026-07-01",actual_strikeouts=actual,projected_strikeouts=projected,ladder_probabilities={"4+":min(.99,p5+.15),"5+":p5,"6+":max(.01,p5-.15)},features={"pitcher_hand":"R","lineup_confirmed":False})

def test_metrics():
    result=run_backtest(BacktestRequest(starts=[s(6,5.5,.62),s(4,4.8,.48),s(7,6.3,.70)]))
    assert result.observations==3
    assert result.mae is not None
    assert len(result.ladder_metrics)==3
    assert "5+" in result.calibration
