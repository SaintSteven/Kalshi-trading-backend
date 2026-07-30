from __future__ import annotations
import asyncio
from datetime import datetime
import httpx
from edge_models import SavedTradeSnapshot, HistoricalMarketRecord

BASE='https://statsapi.mlb.com/api/v1'

def norm(s): return ''.join(c.lower() for c in (s or '') if c.isalnum())

async def _fetch(client,path,params=None):
    r=await client.get(BASE+path,params=params,timeout=45); r.raise_for_status(); return r.json()

async def settle_snapshots(records:list[SavedTradeSnapshot]):
    by_date={}
    for r in records: by_date.setdefault(r.game_date,[]).append(r)
    settled=[]; pending=[]; warnings=[]
    async with httpx.AsyncClient(headers={'User-Agent':'KalshiTradingPlatform/2.0'}) as client:
        for game_date,rows in by_date.items():
            try:
                sched=await _fetch(client,'/schedule',{'sportId':1,'date':game_date})
                games=[g for d in sched.get('dates',[]) for g in d.get('games',[])]
                finals=[g for g in games if g.get('status',{}).get('abstractGameState')=='Final']
                if not finals:
                    pending.extend(rows); continue
                actual={}
                for g in finals:
                    box=await _fetch(client,f"/game/{g['gamePk']}/boxscore")
                    for side in ('away','home'):
                        for p in box.get('teams',{}).get(side,{}).get('players',{}).values():
                            st=p.get('stats',{}).get('pitching',{})
                            if int(st.get('gamesStarted') or 0)>=1:
                                actual[norm(p.get('person',{}).get('fullName'))]=int(st.get('strikeOuts') or 0)
                for row in rows:
                    k=actual.get(norm(row.player))
                    if k is None:
                        pending.append(row); warnings.append(f"Could not match {row.player} on {game_date} to a final-game starter.")
                    else:
                        settled.append(HistoricalMarketRecord(**row.model_dump(exclude={'captured_at'}),actual_strikeouts=k))
            except Exception as exc:
                pending.extend(rows); warnings.append(f"{game_date}: settlement lookup failed: {exc}")
            await asyncio.sleep(.05)
    return settled,pending,warnings
