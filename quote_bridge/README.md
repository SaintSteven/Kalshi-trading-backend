# Prediction Markets Research v3.1 — read-only cross-sport quote service

Isolated module; **does not modify** the existing Kalshi/MLB service. Only fetches public market data; never sends an order or accesses accounts.

## Capabilities
- Kalshi orderbook_fp: YES asks from NO bids and NO asks from YES bids, with depth.
- Polymarket US: YES order-book offers; synthetic NO disabled until exact tradable contract verified.
- Weighted buy prices and maximum affordable whole contracts for small dollar stakes.
- Fee estimates using adjustable coefficients; never labeled guaranteed account-specific fees.
- Closed/stale/missing books, unsafe identifiers and unverified contract equivalence are rejected.
- Optional fair probabilities for research; **never automatic QUALIFY**.

## API
- GET /health — server uptime only, not exchange connectivity
- GET /docs — interactive docs
- POST /v1/quote — exact ticker or slug, side and stake
- GET /v1/quote — same parameters in query string (may be cached by intermediaries)
- POST /v1/compare — same stake/side and explicitly verified settlement-rule equivalence
- GET /v1/discover/{venue}?query=...&series_ticker=... — first-page discovery; NOT full slate

Example POST /v1/quote JSON:
{"venue":"kalshi","market_id":"EXACT_TICKER","side":"YES","budget_usd":"1.50","max_all_in_price":"0.50"}

For Polymarket US:
{"venue":"polymarket_us","market_id":"EXACT_SLUG","side":"YES","contracts":"2"}

## Render deployment
Deploy a **separate service** from GitHub SaintSteven/Kalshi-trading-backend, branch feature/read-only-cross-sport-pricing.

- Runtime Python; free plan for the prototype.
- Build command: pip install -r quote_bridge/requirements.txt
- Start command: uvicorn quote_bridge.main:app --host 0.0.0.0 --port $PORT
- Leave existing Kalshi service on main untouched.
- Optional env vars: KALSHI_ESTIMATED_TAKER_FEE_COEFF (default 0.07), POLY_US_ESTIMATED_TAKER_FEE_COEFF (default 0.0695).
- Never configure trading keys on this read-only service.

## Tests
pip install -r quote_bridge/requirements.txt pytest
python -m pytest -q quote_bridge/tests

## Research v3 limitations
This is a pricing bridge, NOT a research-model, automated bet placer, full market scanner, source-consensus generator, position tracker, or a ChatGPT-connected app.

- Quotes and depth can change before any purchase.
- Fee schedules are estimates; exact account/market rules need verification.
- First-page discovery may miss markets: prefer series/event filters and exact instrument IDs.
- Compare only contracts with verified event, date, threshold, side, settlement and payout rules.
- A custom ChatGPT app/action integration is a separate setup; hosting on Render alone does NOT automatically make it callable inside ChatGPT.
