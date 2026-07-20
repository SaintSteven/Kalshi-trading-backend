from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from card_builder import build_market_only_card
from config import ALLOWED_ORIGINS
from market_collector import collect_mlb_strikeout_markets
from models import CardRequest, CardResponse, Market


app = FastAPI(
    title="Kalshi Trading Engine",
    version="0.2.0",
    description="Backend brain for the mobile-first Kalshi Trading Platform.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict:
    return {
        "service": "Kalshi Trading Engine",
        "version": "0.2.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": "0.2.0",
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/markets", response_model=list[Market])
async def markets(
    date: str | None = Query(
        default=None,
        description="Optional Eastern date in YYYY-MM-DD format.",
    ),
) -> list[Market]:
    try:
        _, output = await collect_mlb_strikeout_markets(date)
        return output
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kalshi market request failed: {exc}",
        ) from exc


@app.post("/build-card", response_model=CardResponse)
async def build_card(request: CardRequest) -> CardResponse:
    try:
        target_date, output = await collect_mlb_strikeout_markets(request.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kalshi market request failed: {exc}",
        ) from exc

    recommendations = build_market_only_card(output)

    return CardResponse(
        status="market_collection_complete",
        target_date=target_date,
        markets_reviewed=len(output),
        recommendations=recommendations,
        message=(
            "Sprint 1 is working: live markets were collected and normalized. "
            "No real picks are produced yet because the independent projection "
            "and QC engines are intentionally not connected."
        ),
    )
