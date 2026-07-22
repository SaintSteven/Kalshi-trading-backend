from models import PitcherProjectionInput
from probability_engine import poisson_probability_at_least
from projection_engine import build_projection

def test_projection_mean():
    r=build_projection(PitcherProjectionInput(player="Example Pitcher",baseline_k_per_batter=0.25,expected_batters_faced=24))
    assert r.status=="READY" and r.projected_strikeouts==6.0

def test_unconfirmed_starter_is_insufficient():
    r=build_projection(PitcherProjectionInput(player="Example Pitcher",baseline_k_per_batter=0.25,expected_batters_faced=24,starter_confirmed=False))
    assert r.status=="INSUFFICIENT_DATA"

def test_probability_is_bounded():
    assert 0<=poisson_probability_at_least(6.0,6)<=1
