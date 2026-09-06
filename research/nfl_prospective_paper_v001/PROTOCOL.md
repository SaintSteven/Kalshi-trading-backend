# NFL Prospective Paper Protocol v0.01

Status: PAPER ONLY — no real-money execution.

Purpose: obtain genuinely prospective 2026 evidence for NFL Kalshi prop-market rules discovered historically and frozen in v0.03.3. Do not optimize rules using 2026 outcomes while this protocol is active.

## Primary rule (PROMOTED TO PROSPECTIVE TEST)
- Market family: receiving yards
- Side: NO
- Entry price: 40–49 cents inclusive
- Entry time: target T-30 minutes relative to verified NFL kickoff
- Quote QC: quote/candle age <= 60 seconds; executable NO ask = 1 - YES bid
- One thesis per player/game: at most one qualifying receiving-yards NO position per player/game. If multiple ladders qualify, select the candidate closest to 45c; tie-break by freshest quote, then ticker lexicographically for determinism.
- Stake for paper accounting: 1 contract per thesis.
- Outcome: hold to settlement for primary evaluation.
- Fees: record modeled Kalshi taker fee consistently with v0.03.3 and also retain gross P/L.

## Secondary frozen candidates
Track, but do not promote or alter based on early 2026 results. These are observational candidates carried forward from v0.03.3 and remain separate from the primary strategy.

1. Anytime TD — NO 50–59c at T-6h
2. Receptions — NO 15–19c at T-30m
3. Receptions — NO 20–29c at T-3h
4. Receptions — NO 15–19c at T-3h
5. Rushing yards — YES 10–14c at T-3h
6. Rushing yards — NO 15–19c at T-30m
7. Passing yards — NO 30–39c at T-6h

Use the same verified kickoff, <=60-second quote-age, executable-price, one-thesis/player-game and fee-accounting principles. Keep each rule's results separate.

## Anti-overfitting lock
- No changing price bands, entry horizons, side, market family, tie-break rules, or QC after seeing 2026 results.
- No adding new rules to the prospective scorecard because they look good during 2026.
- Any newly discovered hypothesis goes into a separate research queue and cannot count as prospective validation.
- Missed/invalid observations remain documented; do not backfill them using later prices.

## Required ledger fields
run_timestamp, season, week, game_id, kickoff_utc, market_ticker, series_ticker, prop_family, rule_id, player_key/player_name when available, side, target_horizon_minutes, target_time_utc, quote_time_utc, quote_age_seconds, yes_bid, yes_ask, entry_price, contracts, gross_cost, modeled_fee, net_cost, settlement_result, gross_pnl, net_pnl, status, exclusion_reason.

## Scorecard
Report for PRIMARY and each SECONDARY rule separately:
- opportunities seen
- valid paper entries
- wins/losses
- gross P/L and ROI
- modeled-fee net P/L and ROI
- max drawdown
- average entry price
- by-week results

Also report a combined secondary portfolio, but never combine it with the PRIMARY result when deciding whether the primary rule validated.

## Validation posture
Historical v0.03.3 results were post-selection robustness evidence, not untouched validation. The 2026 ledger begins the genuinely prospective test. Real money remains OFF. Do not declare the strategy validated from a small number of games/weeks. Accumulate evidence and assess stability over time rather than stopping when a favorable ROI is reached.
