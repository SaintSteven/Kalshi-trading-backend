import asyncio, math
from datetime import timedelta
import httpx
from automatic_input_builder import automatic_input
from historical_backtest_collector import actual_starters, aggregate, boxscore, game_log, historical_career, kp, pd, person, prior_logs, recent, schedule, si, team_hitting_as_of, team_k, workload, year_by_year
from projection_engine import build_full_projection

def workload_variant(season_stat, previous_logs):
    rows=previous_logs[-6:]
    bf=[si(r.get("stat",{}).get("battersFaced")) for r in rows if si(r.get("stat",{}).get("battersFaced"))>0]
    pc=[si(r.get("stat",{}).get("numberOfPitches")) for r in rows if si(r.get("stat",{}).get("numberOfPitches"))>0]
    if not bf:return 22.0,16,28
    l3=bf[-3:]; l5=bf[-5:]
    a3=sum(l3)/len(l3); a5=sum(l5)/len(l5)
    starts=si(season_stat.get("gamesStarted")); sbf=si(season_stat.get("battersFaced"))
    season_avg=sbf/starts if starts else a5
    pitch_bf=(sum(pc[-3:])/len(pc[-3:]))/3.85 if pc else a3
    expected=a3*.45+a5*.25+season_avg*.20+pitch_bf*.10
    floor=max(10,min(l5)-2); ceiling=min(34,max(l5)+2)
    return round(max(floor,min(ceiling,expected)),2),floor,ceiling

async def raw(client,starter,experimental):
    target=pd(starter["game_date"]); season=target.year
    pinfo,years,logs,opp=await asyncio.gather(
        person(client,starter["player_id"]),
        year_by_year(client,starter["player_id"]),
        game_log(client,starter["player_id"],season),
        team_hitting_as_of(client,starter["opponent_team_id"],season,target-timedelta(days=1))
    )
    prev=prior_logs(logs,target); sstat=aggregate(prev)
    if si(sstat.get("gamesStarted"))<1:return None
    cstat=historical_career(years,season,sstat)
    skr,sbf=kp(sstat); ckr,cbf=kp(cstat); rec=recent(prev)
    ebf,lo,hi=workload_variant(sstat,prev) if experimental else workload(sstat,prev)
    return {**starter,"starter_confirmed":True,"pitcher_hand":pinfo.get("pitchHand",{}).get("code","R"),
    "season_k_pct":skr,"career_k_pct":ckr or skr,"recent_k_pct":rec["recent_k_pct"] or skr,
    "season_batters_faced":sbf,"career_batters_faced":cbf,"recent_batters_faced":rec["recent_batters_faced"],
    "recent_starts":rec["recent_starts"],"recent_start_batters_faced":rec["recent_start_batters_faced"],
    "expected_batters_faced":ebf,"workload_floor":lo,"workload_ceiling":hi,
    "recent_pitch_counts":rec["recent_pitch_counts"],"opponent_lineup_k_pct":team_k(opp),
    "league_k_pct":.225,"lineup_confirmed":False,"starter_role":"NORMAL","velocity_change_mph":0.0,
    "whiff_rate_change":0.0,"pitch_mix_change_supported":False,"data_warnings":[]}

def metrics(a,p):
    if not a:return {"mae":None,"rmse":None,"mean_error":None}
    n=len(a)
    return {"mae":sum(abs(x-y) for x,y in zip(a,p))/n,
    "rmse":math.sqrt(sum((x-y)**2 for x,y in zip(a,p))/n),
    "mean_error":sum(y-x for x,y in zip(a,p))/n}

async def run_workload_experiment(start_date,end_date,max_days=2):
    start=pd(start_date); end=pd(end_date)
    if end<start:raise ValueError("end_date must be on or after start_date.")
    if (end-start).days+1>max_days:raise ValueError(f"Requested {(end-start).days+1} days; maximum is {max_days}.")
    rows=[]; warnings=[]; skipped=0
    async with httpx.AsyncClient(headers={"User-Agent":"KalshiTradingPlatform/1.3"}) as client:
        current=start
        while current<=end:
            games=await schedule(client,current.isoformat())
            for game in games:
                try:
                    b=await boxscore(client,game["gamePk"])
                    for starter in actual_starters(game,b):
                        base,exp=await asyncio.gather(raw(client,starter,False),raw(client,starter,True))
                        if not base or not exp:
                            skipped+=1; continue
                        bp=build_full_projection(automatic_input(base))
                        ep=build_full_projection(automatic_input(exp))
                        rows.append({"actual":starter["actual_strikeouts"],"baseline":bp["projected_strikeouts"],"workload":ep["projected_strikeouts"]})
                except Exception as exc:
                    warnings.append(f"{current} game {game.get('gamePk')}: {exc}")
            current+=timedelta(days=1)
    a=[r["actual"] for r in rows]; b=[r["baseline"] for r in rows]; w=[r["workload"] for r in rows]
    bm=metrics(a,b); wm=metrics(a,w)
    mc=wm["mae"]-bm["mae"] if bm["mae"] is not None else None
    rc=wm["rmse"]-bm["rmse"] if bm["rmse"] is not None else None
    return {"records_collected":len(rows),"records_skipped":skipped,
    "comparison":{"observations":len(rows),"baseline_mae":round(bm["mae"],5) if bm["mae"] is not None else None,
    "workload_mae":round(wm["mae"],5) if wm["mae"] is not None else None,
    "mae_change":round(mc,5) if mc is not None else None,
    "baseline_rmse":round(bm["rmse"],5) if bm["rmse"] is not None else None,
    "workload_rmse":round(wm["rmse"],5) if wm["rmse"] is not None else None,
    "rmse_change":round(rc,5) if rc is not None else None,
    "baseline_mean_error":round(bm["mean_error"],5) if bm["mean_error"] is not None else None,
    "workload_mean_error":round(wm["mean_error"],5) if wm["mean_error"] is not None else None,
    "improved":mc<0 if mc is not None else None},"warnings":warnings}
