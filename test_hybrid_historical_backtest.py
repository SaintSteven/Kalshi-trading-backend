from datetime import date

from hybrid_historical_backtest import (
    _blank_team,
    _event_date,
    _model_probability,
    _probability_diagnostics,
    _strategy_report,
    _summary,
    _update_team,
)


def test_event_date_parses_game_ticker():
    assert _event_date("KXMLBGAME-26AUG312138NYYLAA").isoformat() == "2026-08-31"


def test_rolling_model_favors_stronger_road_team():
    away, home = _blank_team(), _blank_team()
    for _ in range(70):
        _update_team(away, home=False, won=True, runs_for=5, runs_against=3)
    for _ in range(30):
        _update_team(away, home=False, won=False, runs_for=3, runs_against=5)
    for _ in range(45):
        _update_team(home, home=True, won=True, runs_for=4, runs_against=4)
        _update_team(home, home=True, won=False, runs_for=4, runs_against=4)
    probability, detail = _model_probability(away, home)
    assert probability > 0.5
    assert detail["away_road"] == 0.7


def test_summary_uses_dollars_risked_and_tracks_drawdown():
    rows = [
        {"date": "2026-08-01", "game_start_time": "1", "ticker": "A", "profit_loss": 1.0, "won": True, "edge_points": 6, "entry_price_cents": 50},
        {"date": "2026-08-02", "game_start_time": "1", "ticker": "B", "profit_loss": -1.0, "won": False, "edge_points": 7, "entry_price_cents": 60},
        {"date": "2026-08-03", "game_start_time": "1", "ticker": "C", "profit_loss": -1.0, "won": False, "edge_points": 8, "entry_price_cents": 70},
    ]
    result = _summary(rows, 1)
    assert result["risked"] == 3
    assert result["profit_loss"] == -1
    assert result["maximum_drawdown"] == 2


def test_strategy_report_preserves_chronological_holdout():
    rows = [
        {"date": "2026-08-01", "game_start_time": "1", "ticker": "A", "profit_loss": 1.0, "won": True, "edge_points": 5, "entry_price_cents": 50, "discovery_grade": "B"},
        {"date": "2026-08-25", "game_start_time": "1", "ticker": "B", "profit_loss": -1.0, "won": False, "edge_points": 5, "entry_price_cents": 50, "discovery_grade": "B"},
    ]
    report = _strategy_report(rows, 1, date(2026, 8, 22))
    assert report["training"]["bets"] == 1
    assert report["holdout"]["bets"] == 1


def test_probability_diagnostics_rewards_better_forecast():
    result = _probability_diagnostics([
        {"won": True, "kalshi_probability": 0.50, "external_probability": 0.70, "model_probability": 0.30, "legacy_probability": 0.50},
        {"won": False, "kalshi_probability": 0.50, "external_probability": 0.30, "model_probability": 0.70, "legacy_probability": 0.50},
    ])
    assert result["sportsbook_no_vig"]["brier_score"] < result["independent_model"]["brier_score"]
