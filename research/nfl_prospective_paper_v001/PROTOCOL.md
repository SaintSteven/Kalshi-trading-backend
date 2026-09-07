# NFL Prospective Paper Protocol v0.01

Status: PAPER ONLY — no real-money execution.

Purpose: obtain genuinely prospective 2026 evidence for NFL Kalshi prop-market rules discovered historically and frozen in v0.03.3. Do not optimize rules using 2026 outcomes while this protocol is active.

## Primary rule
- Receiving yards — NO 40–49c at T-30m.
- Quote age <=60 seconds; executable NO ask = 1 - YES bid.
- One thesis per player/game. If multiple ladders qualify, select closest to 45c; tie-break by freshest quote, then ticker.
- One paper contract; hold to settlement; track gross and modeled-fee net results.

## Secondary frozen candidates
1. Anytime TD — NO 50–59c at T-6h
2. Receptions — NO 15–19c at T-30m
3. Receptions — NO 20–29c at T-3h
4. Receptions — NO 15–19c at T-3h
5. Rushing yards — YES 10–14c at T-3h
6. Rushing yards — NO 15–19c at T-30m
7. Passing yards — NO 30–39c at T-6h

## Anti-overfitting lock
No changing price bands, entry horizons, side, family, tie-breaks, or quote QC after seeing 2026 outcomes. Newly discovered ideas stay outside this prospective scorecard. Missed/invalid target observations are recorded and are not replaced with later prices.

## Operational behavior
The scheduled runner executes every 10 minutes on the default branch. During a short grace window after each frozen target horizon, it queries the 1-minute candlestick ending at or before the target time. Only a quote no more than 60 seconds old can generate a paper entry. The job must still run before kickoff, so outcomes are unknown when an observation is written. Entry fields are never re-optimized or replaced. Settlement fields are filled only after the market resolves.

The persistent ledger is committed back to the repository so GitHub artifact expiration cannot erase the forward record. Real-money order endpoints are not used.
