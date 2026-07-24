from statistics import mean,pstdev

def trend(values):
    if len(values)<2:return 0.0
    xm=(len(values)-1)/2; ym=mean(values)
    den=sum((i-xm)**2 for i in range(len(values)))
    return sum((i-xm)*(v-ym) for i,v in enumerate(values))/den if den else 0.0

def build_feature_record(raw):
    pc=raw.get("recent_pitch_counts",[])
    bf=raw.get("recent_start_batters_faced",[])
    sk=raw.get("season_k_pct",0.0); ck=raw.get("career_k_pct",0.0); rk=raw.get("recent_k_pct",0.0)
    ok=raw.get("opponent_lineup_k_pct",0.225); lk=raw.get("league_k_pct",0.225)
    return {
      "player":raw.get("player"),
      "team_name":raw.get("team_name"),
      "opponent_team_name":raw.get("opponent_team_name"),
      "season_k_pct":round(sk,4),
      "career_k_pct":round(ck,4),
      "recent_k_pct":round(rk,4),
      "recent_vs_season_k_pct":round(rk-sk,4),
      "season_vs_career_k_pct":round(sk-ck,4),
      "opponent_k_pct":round(ok,4),
      "opponent_k_multiplier":round(ok/lk if lk else 1.0,4),
      "expected_batters_faced":raw.get("expected_batters_faced"),
      "workload_floor":raw.get("workload_floor"),
      "workload_ceiling":raw.get("workload_ceiling"),
      "workload_range":raw.get("workload_ceiling",0)-raw.get("workload_floor",0),
      "workload_trend_bf_per_start":round(trend(bf),3),
      "pitch_count_average":round(mean(pc),2) if pc else None,
      "pitch_count_volatility":round(pstdev(pc),2) if len(pc)>=2 else 0.0,
      "recent_starts":raw.get("recent_starts",0),
      "lineup_confirmed":raw.get("lineup_confirmed",False),
      "advanced_statcast_active":False,
      "sportsbook_consensus_active":False,
      "weather_active":False,
      "umpire_active":False
    }
