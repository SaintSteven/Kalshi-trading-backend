from projection_inputs import PitcherModelInput
Q={'HIGH':90,'MEDIUM':72,'LOW':52}

def build_confidence(d:PitcherModelInput):
    s=Q[d.data_quality.pitcher_skill];l=Q[d.data_quality.lineup];w=Q[d.data_quality.workload];r=Q[d.data_quality.recent_change]
    if not d.lineup_confirmed:l-=12
    if not d.starter_confirmed:w-=25
    if d.starter_role!='NORMAL':w-=10
    if d.season_batters_faced<200:s-=8
    if not d.recent_pitch_counts:w-=8
    s,l,w,r=[max(1,min(99,x)) for x in (s,l,w,r)];overall=round(s*.35+l*.25+w*.30+r*.10)
    return {'pitcher_skill':s,'lineup':l,'workload':w,'recent_change':r,'overall':overall}
