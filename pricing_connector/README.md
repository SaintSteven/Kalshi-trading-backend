# Research v3 cross-sport pricing connector

READ-ONLY. Direct public Kalshi and Polymarket US order-book snapshots for whole-share standard binary contracts. Intended for NFL, NBA, CFB, MLB, EPL and other sports with $0/$1 outcomes. No trade submission, private account data or position execution.

## Routes
- GET /health
- GET /openapi.json
- GET /v1/discover/kalshi?series_ticker=KX...
- GET /v1/discover/polymarket-us?limit=50&offset=0
- GET /v1/quote/kalshi/{ticker}?side=YES&contracts=3
- GET /v1/quote/kalshi/{ticker}?side=NO&contracts=3
- GET /v1/quote/polymarket-us/{slug}?side=YES&contracts=3
- POST /v1/compare with JSON kalshi_ticker, polymarket_us_slug, contracts, same_settlement_rules_checked, equivalence_evidence

The service computes available depth, weighted costs, estimated fees, and price timestamps; it does NOT present snapshots as guaranteed executable fills. Kalshi opposite-side bids are inverted to asks. Polymarket US uses marketData.offers (NOT international Polymarket CLOB). On the compare endpoint, price winner stays null until an analyst attests identical settlement rules and provides evidence; that assertion is not automatically validated.

## Render deployment

This folder is ISOLATED; existing backend main.py and root render.yaml are unchanged.

Use Render > New > Blueprint. Select GitHub repository SaintSteven/Kalshi-trading-backend and the main branch, Blueprint path pricing_connector/render.yaml. Review the plan and create the new service. Alternatively create a separate Web Service with:
- Root directory: pricing_connector
- Build: pip install -r requirements.txt
- Start: uvicorn app:app --host 0.0.0.0 --port $PORT
- Health: /health

Optionally set PRICING_READ_KEY as a Render secret. When set, quote/discovery/compare calls require header X-Pricing-Key; health remains public. Never put secrets in GitHub. With no key, public quote endpoints can be used by anyone and may face provider rate limits.

## Connect ChatGPT

Deployment alone does NOT grant ChatGPT tool access. Connect the deployed service's /openapi.json via a compatible custom ChatGPT action or app integration (with header authentication if enabled). Then the standard Research v3 sport-specific card can call its quotes. The connector does not research probabilities, assess source consensus, or synchronize held positions.

## Truthful limitations

- This is a LIVE BOOK SNAPSHOT, not a firm order or guaranteed fill; exchange liquidity may change and the exchange timestamp can be absent.
- Fee rates are *estimates* using defaults Kalshi=0.07 and Polymarket US=0.0695, overrideable with environment variables KALSHI_FEE_RATE and POLYMARKET_US_FEE_RATE. Fee schedules, minimum sizes and fill rounding may vary. These quotes by themselves cannot be described as fee-verified executable all-in prices.
- Polymarket US NO/short is intentionally UNSUPPORTED until its semantics are verified; Kalshi YES/NO are supported.
- Standard $1 binary contracts only; fantasy ladders and nonlinear escalators require a separate payout-aware engine.
- Matching contracts requires manually checking exact threshold, date, void rules and settlement. Names alone cannot prove equivalence.
- No account-level positions, fills, settlement or trading features.
- If book shapes change, a market is closed, or insufficient depth exists, the service fails closed instead of inventing a price.

## Local test

From pricing_connector, install requirements.txt and pytest; run python -m pytest -q. Then run uvicorn app:app --reload. These are synthetic tests; before relying on the tool for bets, run smoke tests against current active contracts on BOTH exchanges and confirm actual exchange fee tiers. Render free instance sleep can delay near-kickoff requests.

## Next phases

Verified settlement-rule matcher, configurable fee tiers and authenticated read-only order previews; stake-budget optimizer; scalar payout modeling; read-only account-ledger reconciliation. Never automate trading without separately authorized action.
