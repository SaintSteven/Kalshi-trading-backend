from projection_inputs import PitcherModelInput
from projection_engine import build_full_projection

def sample():return PitcherModelInput(player='Example',season_k_pct=.27,career_k_pct=.26,recent_k_pct=.29,season_batters_faced=500,career_batters_faced=1800,recent_batters_faced=120,expected_batters_faced=24,workload_floor=20,workload_ceiling=28,recent_pitch_counts=[96,101,94],opponent_lineup_k_pct=.24,lineup_confirmed=True)
def test_ready():assert build_full_projection(sample())['status']=='READY'
def test_monotonic():
    p=build_full_projection(sample())['ladder_probabilities'];ks=sorted(p,key=lambda x:int(x.rstrip('+')));vs=[p[k] for k in ks];assert all(vs[i]>=vs[i+1] for i in range(len(vs)-1))
