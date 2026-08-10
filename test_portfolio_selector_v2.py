from models import PaperRecommendation
from pipeline_card_builder import _selector_score


def _rec(name: str, edge: float, confidence: int):
    return PaperRecommendation(
        ticker=name,
        player=name,
        threshold="6+",
        side="YES",
        market_price_cents=30,
        fair_probability=0.45,
        raw_edge_points=edge,
        adjusted_edge_points=edge,
        projected_strikeouts=5.5,
        baseline_k_pct=0.25,
        adjusted_k_pct=0.25,
        expected_batters_faced=24,
        workload_floor=20,
        workload_ceiling=28,
        confidence={"overall": confidence},
        decision="MODEL EDGE",
        suggested_stake=0,
        reasons=[],
        warnings=[],
    )


def test_selector_prioritizes_reliability_not_edge_alone():
    # A modestly lower edge can rank higher when QC confidence is materially stronger.
    taillon = _rec("Taillon", 9.5, 84)
    cameron = _rec("Cameron", 8.7, 88)
    assert _selector_score(cameron) > _selector_score(taillon)


def test_selector_caps_edge_contribution():
    a = _rec("A", 15.0, 85)
    b = _rec("B", 30.0, 85)
    assert _selector_score(a) == _selector_score(b)
