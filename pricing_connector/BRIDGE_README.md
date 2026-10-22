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

## Workflow for every betting card

1. Follow Prediction Markets Research v3 playbook, source consensus, injuries and existing ledger.
2. Identify exact contract thresholds and retrieve IDs through the discovery issue path.
3. Retrieve direct venue book snapshots for the actual stake-sized amounts through quote issues.
4. Independently check that compared contracts share exact terms, sides, void rules and payout semantics.
5. Compute estimated net edge and risk with quote freshness. Unreserved books and unverified fees are snapshots, not firm executable orders.
6. Mark QUALIFY only where pricing, rules, accessibility and net EV are genuinely verified; otherwise LIMIT ONLY, WATCH or PASS. No invented quotes or artificial card size.
7. Include v3 audit: sources/disagreement, slate/market coverage and gaps, quote verification, existing/proposed exposure, risks, reasons for WATCH/PASS.

## Security and accuracy limitations

Authorized trigger: only issue creation by GitHub login SaintSteven and approved issue title prefixes. A public user cannot arbitrarily run the bridge.

No trade placement or private account access. All incoming contract IDs are validated; the only permitted external pricing host is the hard-coded public Render service. GitHub Actions may take variable time, so use quote timestamps and reject stale entries. Kalshi scalar Ladders and Escalators still require payout-specific models; the current binary quote service does not price them. US Polymarket and international Polymarket have different market access and settlement APIs. If the bot fails, consult GitHub Actions logs rather than assuming a quote exists.

This workflow does NOT imply full automatic listing coverage of every market; continue to audit any omitted series and missing threshold.

## Mandatory venue comparison for every Research v3 card

For every potential binary NFL/NBA/CFB/MLB/EPL/soccer/futures bet, identify the exact outcome and threshold on Kalshi AND Polymarket US. Fetch the freshest available direct-book quotes for the same SIDE and same threshold, with intended budget (sport-specific 1.0u, 0.75u, 0.5u); validate settlement/void rules and venue access. When exact threshold is not listed, say NO EXACT MATCH and do not compare a neighboring threshold as though it were equivalent. Treat both unavailable/unpriced as UNVERIFIED and do not invent a venue winner.

Present these columns by default: exact selection/threshold; Kalshi all-in average + number of contracts; Polymarket US all-in average + number of contracts; better venue or TIE/NOT COMPARABLE; maximum all-in price; proposed dollars; classification and quote timestamp. Consider effective available liquidity, fee rounding, fractional-vs-whole shares, and exit depth. When quotes are equal, don't claim a price winner; prioritize verified availability and practical management.

Polymarket US has one YES instrument per outcome; buying NO is implemented as a fully collateralized short of that instrument, using the opposite (YES bid) side of its order book. Its NO effective loss/buying-power consumption is 1 - YES sale price plus fees, while $1 full margin per contract is posted and sale proceeds are credited. The read-only price engine estimates this from YES bids; authenticate exact availability and preview before calling it executable. Never compare an offshore Polymarket price with a US executable quote without proving access.

Never upgrade WATCH to QUALIFY solely because a different threshold is cheaper. If two sources support over 14.5 attempts, a 16+ attempt market is not automatically exact-market consensus. This comparison is REQUIRED for all future cards, scans and updates using the bridge.
