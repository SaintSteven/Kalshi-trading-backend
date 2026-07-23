from projection_inputs import PitcherModelInput

def build_lineup_adjustment(d:PitcherModelInput):
    raw=d.opponent_lineup_k_pct/d.league_k_pct; capped=max(.85,min(1.15,raw))
    applied=capped if d.lineup_confirmed else 1+(capped-1)*.60
    reasons=['Confirmed lineup gets full capped adjustment.' if d.lineup_confirmed else 'Projected lineup gets 60% of capped adjustment.']
    if raw!=capped:reasons.append('Extreme lineup effect capped at 0.85-1.15.')
    return round(applied,4),reasons
