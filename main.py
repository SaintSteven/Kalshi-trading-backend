from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from automatic_input_builder import automatic_input
from card_builder import build_paper_card
from config import ALLOWED_ORIGINS
from market_collector import collect_mlb_strikeout_markets, kalshi_ticker_date
from mlb_data_collector import collect_automatic_pitcher_data
from models import Market, MarketSummary, PaperCardRequest, PaperCardResponse

app = FastAPI(title="Kalshi Trading Engine", version="0.7.0", description="Paper-only MLB research engine using validated individual game logs.")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])

@app.get("/")
async def root(): return {"service": "Kalshi Trading Engine", "version": "0.7.0", "mode": "paper-only", "docs": "/docs"}
@app.get("/health")
async def health(): return {"status": "ok", "version": "0.7.0", "mode": "paper-only", "time_utc": datetime.now(timezone.utc).isoformat()}
@app.get("/research-inputs")
async def research_inputs(date: str | None = None):
    try: return await collect_automatic_pitcher_data(date)
    except ValueError as exc: raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD.") from exc
    except httpx.HTTPError as exc: raise HTTPException(status_code=502, detail=f"MLB data request failed: {exc}") from exc
@app.get("/markets", response_model=list[Market])
async def markets(date: str | None = None, tradable_only: bool = True, min_ask: int = Query(2, ge=1, le=49), max_ask: int = Query(98, ge=51, le=100), max_combined_ask: int = Query(110, ge=100, le=200)):
    _, visible, _ = await collect_mlb_strikeout_markets(date, tradable_only=tradable_only, min_ask=min_ask, max_ask=max_ask, max_combined_ask=max_combined_ask)
    return visible
@app.get("/market-summary", response_model=MarketSummary)
async def market_summary(date: str | None = None):
    requested = kalshi_ticker_date(date)
    selected, _, all_markets = await collect_mlb_strikeout_markets(date, tradable_only=False)
    tradable = sum(1 for market in all_markets if market.tradable)
    return MarketSummary(requested_slate=requested, selected_slate=selected, slate_shifted=selected != requested, total_markets=len(all_markets), tradable_markets=tradable, hidden_markets=len(all_markets)-tradable, pitchers=len({m.player for m in all_markets}))
@app.post("/build-card", response_model=PaperCardResponse)
async def build_card(request: PaperCardRequest):
    requested = kalshi_ticker_date(request.date)
    selected, markets, _ = await collect_mlb_strikeout_markets(request.date, tradable_only=True)
    automatic_raw = []
    pitcher_inputs = list(request.pitchers)
    if request.use_automatic_data:
        automatic_raw = await collect_automatic_pitcher_data(request.date)
        automatic_map = {item["player"].strip().lower(): automatic_input(item) for item in automatic_raw if not item.get("data_warnings")}
        manual_names = {item.player.strip().lower() for item in pitcher_inputs}
        pitcher_inputs.extend(item for name, item in automatic_map.items() if name not in manual_names)
    recommendations, matched = build_paper_card(markets, request.model_copy(update={"pitchers": pitcher_inputs}))
    return PaperCardResponse(status="automatic_paper_projection_complete", requested_slate=requested, selected_slate=selected, slate_shifted=selected != requested, markets_reviewed=len(markets), automatic_pitchers_collected=len(automatic_raw), projections_matched=matched, recommendations=recommendations, message="Validated individual pitching game logs are active. Pitchers with failed workload sanity checks are excluded. Opponent adjustment still uses team season K%, and advanced Statcast/QC inputs remain neutral.")
