# ChatGPT Project bridge for Kalshi + Polymarket US quotes

This is a no-screenshot, no-custom-MCP bridge from ChatGPT's connected GitHub tool to the separate Render read-only pricing API.

Service URL: https://prediction-market-quotes-v3.onrender.com
GitHub repository: SaintSteven/Kalshi-trading-backend

## IMPORTANT setup note

GitHub issue-event workflows become active only when the workflow is present on the repository DEFAULT branch. Merge the bridge PR after tests pass. Existing MLB backend application code is unchanged; GitHub merges may trigger its normal Render redeploy.

This repository is PUBLIC: market quote issue bodies and bot comments are visible to other people. NEVER put secrets, account credentials, financial account numbers or private positions in the issue body.

## Contract discovery

From ChatGPT's connected GitHub tool, create an issue in the repo with a title starting with [discovery-v3]. Use the following JSON body:

    {"version":1,"discover":[{"venue":"kalshi","series_ticker":"KXNFLGAME"},{"venue":"polymarket-us","limit":100,"offset":0,"keywords":["NFL","Bears","Patriots"]}]}

The workflow retrieves public exact market identifiers from the deployed Render API and writes a bot comment. Use the connected GitHub fetch_issue_comments tool to read the response. Kalshi pagination uses next_cursor. Polymarket US pagination uses next_offset. Keyword filtering applies within the requested page only, not the entire exchange: page more if needed. Multiple Kalshi series tickers may be needed to cover spreads, totals, receiving thresholds, fantasy markets and other props. This is not an automatic full-market catalog crawl.

## Exact dollar-stake quotes

Create an issue with title starting with [pricing-v3] and body:

    {"version":1,"quotes":[{"venue":"kalshi","contract":"REPLACE_WITH_EXACT_TICKER","side":"YES","budget_usd":"1.50"},{"venue":"polymarket-us","contract":"replace-with-exact-slug","side":"YES","budget_usd":"1.50"}]}

Limit 16 exact contracts per issue. Use sport-specific saved stakes such as NFL 1u=$2.00, 0.75u=$1.50, 0.5u=$1.00; do not assume other sports match. Each quote checks deployed OpenAPI for its supported dollar-budget parameter and ensures returned pricing explicitly confirms that same budget. No silent default share-count fallback.

The bot replies to the issue with results including exact IDs, title and rules metadata, timestamp, top ask, depth, estimated fee-inclusive cost and unspent stake. Failed, closed, inaccessible, or insufficient-depth quotes are clearly labeled. The bridge cannot buy shares, does not access users' account data and does not give a guaranteed executable order. Polymarket US NO/short remains unsupported.

## Research v3.2 LEAN: TARGETED pricing after research

**Research first; request prices second.** ChatGPT screens the relevant sports slate, independently validates exact-outcome signals, estimates fair probabilities, and shortlists only promising candidates **before** using the GitHub discovery/quote bridge. The bridge is not a prediction model or full-slate screener.

1. Read the latest v3.2 Lean project playbook and master ledger; preserve existing placed positions and saved sport-specific units.
2. Screen full slate including player props, oppositional evidence, lineup/injury/role news using genuinely independent sources. At least TWO independent exact-outcome quantitative/analytical signals are needed for research-backed recommendations. Recompute threshold probability from credible model distributions where applicable; mean projections alone don't validate alternate thresholds.
3. Shortlist candidates with fair probability ranges, positive net-edge potential, uncertainty and a maximum ALL-IN buy price. **Do not run exchange discovery for every market on the slate.**
4. For shortlist ONLY, use existing exact IDs when still applicable or send a small batched [discovery-v3] issue. Batch up to 16 requested exact contracts in [pricing-v3] issues. Avoid redundant calls on rescans; refresh stale/injury-sensitive books.
5. Independently check exact threshold, actual contract question, period, side, settlement and void rules. Compare Kalshi and Polymarket US when a truly equivalent accessible contract exists. Another venue's missing/not-equivalent market **does not block** a sound conditional single-venue recommendation; mark it NOT COMPARABLE.
6. Compare fee-estimated stake-sized average book cost to the model-based all-in ceiling. The bot returns **UNRESERVED PUBLIC SNAPSHOTS**, not firm executable fills or authenticated fee confirmation. Treat research-supported opportunities as **LIMIT ONLY** unless separate real execution-price verification exists. Keep these conditional limits prominent in the card; do NOT imply a trade was placed.
7. Provide verified/nonverified status, risk units and dollar stake, feasible share count, quote time, correlated exposure and five-part transparency audit. WATCH/PASS if source, contract, modeled EV or price-ceiling evidence does not support even a conditional entry.

The bridge is intentionally a narrowly scoped read-only helper. Changes to the research process **do not** change fee verification, execution rights, access restrictions or any deployed endpoint.

## Security and accuracy limitations

Authorized trigger: only issue creation by GitHub login SaintSteven and approved issue title prefixes. A public user cannot arbitrarily run the bridge.

No trade placement or private account access. All incoming contract IDs are validated; the only permitted external pricing host is the hard-coded public Render service. GitHub Actions may take variable time, so use quote timestamps and reject stale entries. Kalshi scalar Ladders and Escalators still require payout-specific models; the current binary quote service does not price them. US Polymarket and international Polymarket have different market access and settlement APIs. If the bot fails, consult GitHub Actions logs rather than assuming a quote exists.

This workflow does NOT imply full automatic listing coverage of every market; continue to audit any omitted series and missing threshold.

## Targeted venue comparison for Research v3.2 Lean cards

For **research-shortlisted exact binary outcomes**, look for the same side/threshold/settlement on Kalshi and Polymarket US. Only quote actual equivalent contracts, compare their stake-sized fee-estimated weighted averages and report depth/whole shares. When an exact market is absent on either venue, say NO EXACT MATCH / NOT COMPARABLE. Do not mistake different thresholds for the same trade or let a failed alternate-venue lookup erase a defensible candidate.

Present: selection/threshold; Kalshi estimated all-in average + contracts if available; Polymarket US same if equivalent; selected accessible venue or NOT COMPARABLE; max all-in limit; intended risk dollars; LIMIT ONLY/QUALIFY/WATCH/PASS and quote timestamps. Public bridge quotes cannot be labeled firm executable. No confirmed trade without user/account evidence.

Polymarket US NO/short is not the same as buying YES: fully collateralized short semantics, order-book YES bids, margin and provider-access restrictions need to be checked in an authenticated preview. International Polymarket is not US executable. Scalar ladders/escalators need a separate payout-aware model. Do not upgrade WATCH based only on a cheaper neighboring threshold.
