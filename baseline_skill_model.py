from projection_inputs import PitcherModelInput

def _rel(n:int,full:int)->float:return 0 if n<=0 else min(1,n/full)

def build_regressed_k_rate(d:PitcherModelInput):
    sw=.45*_rel(d.season_batters_faced,500); cw=.35*_rel(d.career_batters_faced,1500); rw=.20*_rel(d.recent_batters_faced,150)
    lw=max(.15,1-(sw+cw+rw)); w={'season':sw,'career':cw,'recent':rw,'league':lw}; total=sum(w.values()); w={k:v/total for k,v in w.items()}
    rate=d.season_k_pct*w['season']+d.career_k_pct*w['career']+d.recent_k_pct*w['recent']+d.league_k_pct*w['league']
    reasons=[]
    if _rel(d.recent_batters_faced,150)<.5:reasons.append('Recent rate heavily regressed due to small sample.')
    if _rel(d.season_batters_faced,500)<.5:reasons.append('Season rate partially regressed toward career and league.')
    if not reasons:reasons.append('Pitcher skill sample support is adequate.')
    return round(rate,4),w,reasons
