from types import SimpleNamespace
from historical_trading_backtest import _record_from_rec
from historical_diagnostics import build_diagnostics


def _rec(side='YES'):
    return SimpleNamespace(
        side=side, market_price_cents=35, calibrated_fair_probability=.50, fair_probability=.48,
        player='Test Pitcher', threshold='6+', confidence={'overall':85,'tier':'HIGH','pitcher_skill':90,'lineup':72,'workload':88,'workload_stability':80,'recent_change':76},
        adjusted_edge_points=10, unlimited_bankroll_stake=1.0, model_units=1.0, research_only=False,
        research_units=0.0,research_stake=0.0,research_reason=None,ticker='TEST',matchup='A @ B',selector_score=80,selector_rank=1,selector_method='v2',portfolio_selected=True,
        projected_strikeouts=6.4,
    )

def test_record_captures_projection_and_components():
    r=_record_from_rec(_rec(), '2026-06-01', 5, 1.0, '2.6.6-frozen')
    assert r.projected_strikeouts == 6.4
    assert round(r.projection_error,1) == 1.4
    assert round(r.projection_side_gap,1) == .4
    assert r.confidence_skill == 90
    assert r.confidence_recent == 76

def test_diagnostics_report_projection_accuracy():
    r1=_record_from_rec(_rec(), '2026-06-01', 5, 1.0, '2.6.6-frozen')
    r2=_record_from_rec(_rec('NO'), '2026-06-02', 7, 1.0, '2.6.6-frozen')
    d=build_diagnostics([r1.model_dump(),r2.model_dump()])
    assert d['projection_accuracy']['observations'] == 2
    assert d['availability']['diagnostic_capture_available']
    assert d['component_diagnostics']['skill']
