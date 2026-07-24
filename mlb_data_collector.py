from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx

BASE="https://statsapi.mlb.com/api/v1"
ET=ZoneInfo("America/New_York")

def d(target=None): return target or datetime.now(ET).strftime("%Y-%m-%d")
def season(target=None): return datetime.strptime(d(target),"%Y-%m-%d").year
def i(v): 
    try:return int(v)
    except:return 0

async def get(client,path,params=None):
    r=await client.get(BASE+path,params=params,timeout=30);r.raise_for_status();return r.json()

async def schedule(client,target=None):
    p=await get(client,"/schedule",{"sportId":1,"date":d(target),"hydrate":"probablePitcher,team"})
    return [g for x in p.get("dates",[]) for g in x.get("games",[])]

def starters(games):
    out=[]
    for g in games:
        teams=g.get("teams",{})
        for side,opp in (("away","home"),("home","away")):
            b=teams.get(side,{}); o=teams.get(opp,{}); p=b.get("probablePitcher")
            if p: out.append({"player":p.get("fullName"),"player_id":p.get("id"),"team_id":b.get("team",{}).get("id"),"opponent_team_id":o.get("team",{}).get("id"),"starter_confirmed":True})
    return out

async def person(client,pid):
    x=await get(client,f"/people/{pid}"); return (x.get("people") or [{}])[0]

async def pitching(client,pid,yr):
    x=await get(client,f"/people/{pid}/stats",{"stats":"season,career","group":"pitching","season":yr})
    out={"season":{},"career":{}}
    for b in x.get("stats",[]):
        n=b.get("type",{}).get("displayName","").lower(); s=(b.get("splits") or [{}])[0].get("stat",{})
        if "season" in n: out["season"]=s
        elif "career" in n: out["career"]=s
    return out

async def recent(client,pid,yr,target=None):
    end=datetime.strptime(d(target),"%Y-%m-%d").date(); start=end-timedelta(days=45)
    x=await get(client,f"/people/{pid}/stats",{"stats":"byDateRange","group":"pitching","season":yr,"startDate":start.strftime("%m/%d/%Y"),"endDate":end.strftime("%m/%d/%Y")})
    return [s for b in x.get("stats",[]) for s in b.get("splits",[])][-6:]

async def team_hitting(client,tid,yr):
    x=await get(client,f"/teams/{tid}/stats",{"stats":"season","group":"hitting","season":yr})
    for b in x.get("stats",[]):
        if b.get("splits"): return b["splits"][0].get("stat",{})
    return {}

def kp(stat):
    bf=i(stat.get("battersFaced")); return (i(stat.get("strikeOuts"))/bf if bf else 0.0,bf)

def recent_summary(logs):
    k=sum(i(x.get("stat",{}).get("strikeOuts")) for x in logs)
    bf=sum(i(x.get("stat",{}).get("battersFaced")) for x in logs)
    pc=[i(x.get("stat",{}).get("numberOfPitches")) for x in logs if i(x.get("stat",{}).get("numberOfPitches"))>0]
    return (k/bf if bf else 0.0,bf,pc[-3:])

def team_k(stat):
    pa=i(stat.get("plateAppearances")) or i(stat.get("atBats"))+i(stat.get("baseOnBalls"))+i(stat.get("hitByPitch"))+i(stat.get("sacFlies"))
    return i(stat.get("strikeOuts"))/pa if pa else .225

def workload(season_stat,logs):
    vals=[i(x.get("stat",{}).get("battersFaced")) for x in logs if i(x.get("stat",{}).get("battersFaced"))>0]
    if vals:
        e=sum(vals[-5:])/len(vals[-5:]); return round(e,2),max(8,min(vals)-2),min(36,max(vals)+2)
    gs=i(season_stat.get("gamesStarted")); bf=i(season_stat.get("battersFaced"))
    e=bf/gs if gs and bf else 22; return round(e,2),max(12,round(e-4)),min(34,round(e+4))

async def collect(target=None):
    yr=season(target)
    async with httpx.AsyncClient(headers={"User-Agent":"KalshiTradingPlatform/0.6"}) as c:
        out=[]
        for s in starters(await schedule(c,target)):
            per=await person(c,s["player_id"]); st=await pitching(c,s["player_id"],yr); logs=await recent(c,s["player_id"],yr,target); opp=await team_hitting(c,s["opponent_team_id"],yr)
            sk,sbf=kp(st["season"]); ck,cbf=kp(st["career"]); rk,rbf,pcs=recent_summary(logs); ebf,lo,hi=workload(st["season"],logs)
            out.append({**s,"pitcher_hand":per.get("pitchHand",{}).get("code","R"),"season_k_pct":sk,"career_k_pct":ck or sk,"recent_k_pct":rk or sk,"season_batters_faced":sbf,"career_batters_faced":cbf,"recent_batters_faced":rbf,"expected_batters_faced":ebf,"workload_floor":lo,"workload_ceiling":hi,"recent_pitch_counts":pcs,"opponent_lineup_k_pct":team_k(opp),"league_k_pct":.225,"lineup_confirmed":False,"starter_role":"NORMAL","velocity_change_mph":0,"whiff_rate_change":0,"pitch_mix_change_supported":False})
        return out
