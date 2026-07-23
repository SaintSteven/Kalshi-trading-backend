from projection_inputs import PitcherModelInput

def build_recent_change_multiplier(d:PitcherModelInput):
    m=1.0; reasons=[]
    if abs(d.velocity_change_mph)>=.7:
        e=max(-.05,min(.05,d.velocity_change_mph*.02));m+=e;reasons.append(f'Velocity adjustment {e:+.3f}.')
    if abs(d.whiff_rate_change)>=.015:
        e=max(-.06,min(.06,d.whiff_rate_change*1.5));m+=e;reasons.append(f'Whiff adjustment {e:+.3f}.')
    if d.pitch_mix_change_supported:m+=.015;reasons.append('Supported pitch-mix change adds small adjustment.')
    if not reasons:reasons.append('No supported recent-change adjustment.')
    return round(max(.90,min(1.10,m)),4),reasons
