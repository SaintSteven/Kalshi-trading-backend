# MLB Market Efficiency v0.01 — Data Quality Audit

Status: **INVALID FOR EDGE INFERENCE**

The completed v0.01 artifact was audited before using any ROI result as evidence.

## What the artifact actually contains

- 2,983 market contracts
- 64 events
- 2 series only: `KXMLBHR` (1,117) and `KXMLBTB` (1,866)
- Every row came from the historical tier
- Every row is dated **July 1 or July 2, 2026**
- Requested research window was July 1 through August 31, 2026

Therefore the artifact does **not** represent a two-month, broad-MLB efficiency map.

## Critical defect 1 — wrong time anchor

v0.01 used market `close_time` / expiration fields as the anchor for T-4h, T-2h and T-1h snapshots.

For daily player-prop markets, the market close can be hours after first pitch. Example from the artifact:

- event ticker: `KXMLBHR-26JUL021510MIACOL`
- ticker encodes 3:10 PM ET first pitch
- artifact anchor: approximately 6:10 PM ET

That means nominal T-2h and T-1h observations can be **in-play**, not pregame. The apparent improvement in Brier score toward T-1h is therefore contaminated by game information.

Fix: v0.02 derives the game start directly from the dated/time-stamped market ticker and anchors every snapshot to first pitch.

## Critical defect 2 — incomplete storage-tier coverage

The run requested July 1-August 31 but only retained July 1-2. Kalshi moves older settled markets to the historical archive while newer settled markets remain in the recent tier. v0.01 did not verify coverage by requested calendar date and silently accepted a partial universe.

Fix: v0.02 explicitly merges recent and historical listings, filters markets using the date/time encoded in each ticker, and emits per-date coverage diagnostics. A run fails validation if requested dates are silently absent.

## Critical defect 3 — universe coverage

The inventory discovered 166 MLB-related series, including the major daily markets, but the final ledger contained only home-run and total-bases props. Important daily series omitted from the actual output included game winner, game total, spread, first-five markets, strikeouts, hits, team totals, outs, RBIs and others.

Fix: v0.02 uses an explicit preregistered daily-MLB series universe rather than mixing daily contracts with season awards/futures and then hoping all series survive the scan.

## Critical defect 4 — price-unit ambiguity

v0.01 used a generic parser where a numeric value of `1` could mean either 1 cent or $1.00. Kalshi candle payloads now expose fixed-point dollar fields alongside legacy cent fields.

Fix: v0.02 parses `close_dollars` / `close_fp` as dollars and legacy `close` explicitly as cents. No unit guessing from value magnitude.

## Consequence

The v0.01 ROI table, including the +1.93% T-2h NO 61-80c segment, is **quarantined**. It is not a candidate betting rule and must not be used for threshold selection or real-money decisions.

v0.02 is a clean descriptive rebuild. Real money remains OFF.
