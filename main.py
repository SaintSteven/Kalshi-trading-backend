from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from card_builder import build_market_only_card
from config import ALLOWED_ORIGINS
from market_collector import collect_mlb_strikeout_markets
from models import CardRequest, CardResponse, Market, MarketSummary

app = FastAPI(title="Kalshi Trading Engine", version="0.3.0", description="Backend brain for the mobile-first Kalshi Trading Platform.")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])

@app.get("/")
async def root():
    return {"service": "Kalshi Trading Engine", "version": "0.3.0", "docs": "/docs"}

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.3.0", "time_utc": datetime.now(timezone.utc).isoformat()}

@app.get("/markets", response_model=list[Market])
async def markets(date: str | None = None, tradable_only: bool = True, min_ask: int = Query(2, ge=1, le=49), max_ask: int = Query(98, ge=51, le=100), max_combined_ask: int = Query(110, ge=100, le=200)):
    try:
        _, visible, _ = await collect_mlb_strikeout_markets(date, tradable_only=tradable_only, min_ask=min_ask, max_ask=max_ask, max_combined_ask=max_combined_ask)
        return visible
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Kalshi market request failed: {exc}") from exc

@app.get("/market-summary", response_model=MarketSummary)
async def market_summary(date: str | None = None, min_ask: int = Query(2, ge=1, le=49), max_ask: int = Query(98, ge=51, le=100), max_combined_ask: int = Query(110, ge=100, le=200)):
    target_date, _, all_markets = await collect_mlb_strikeout_markets(date, tradable_only=False, min_ask=min_ask, max_ask=max_ask, max_combined_ask=max_combined_ask)
    tradable_count = sum(1 for m in all_markets if m.tradable)
    return MarketSummary(target_date=target_date, total_markets=len(all_markets), tradable_markets=tradable_count, hidden_markets=len(all_markets)-tradable_count, pitchers=len({m.player for m in all_markets}))

@app.post("/build-card", response_model=CardResponse)
async def build_card(request: CardRequest):
    try:
        target_date, output, _ = await collect_mlb_strikeout_markets(request.date, tradable_only=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Kalshi market request failed: {exc}") from exc
    return CardResponse(status="tradable_market_collection_complete", target_date=target_date, markets_reviewed=len(output), recommendations=build_market_only_card(output), message="Tradable markets collected. Projection and QC engines are not connected yet.")
