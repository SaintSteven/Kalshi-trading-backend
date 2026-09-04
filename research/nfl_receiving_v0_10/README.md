# NFL Receiving v0.10 — GitHub Actions research runner

Research/paper-only historical profitability audit for NFL receiving-yard Kalshi markets. It does not place trades and does not modify the existing MLB production pipeline.

## What it tests

- Frozen receiving projection/calibration path carried forward from the validated NFL research model.
- Historical `KXNFLRECYDS` markets.
- Entry snapshot anchored to scheduled kickoff, using the final hourly candle ending at least 30 minutes before kickoff.
- YES entry = observed YES ask.
- NO entry = `1 - YES bid` from the same candle, which reconstructs the executable NO ask. It never uses `1 - YES ask`.
- Separate YES and NO edge/QC results, combined best-side results, one-bet-per-player/game results, and a 2025 held-out view.
- Gross P/L before fees.

## Run from phone

Open this repo on GitHub → Actions → **NFL Receiving v0.10 YES + NO Backtest** → **Run workflow**. Leave the defaults unless intentionally running a smaller test. You can close GitHub after launching; the runner continues remotely. Results appear in the workflow summary and as a downloadable artifact.

## Implementation note

The large standalone backtest source is stored as compressed payload chunks so it can be installed safely through the ChatGPT GitHub integration. `run_v010.py` reconstructs it in a temporary directory at runtime and forwards all command-line arguments to the backtest.
