MLB v4 top-down strikeout research run

The GitHub Actions workflow .github/workflows/mlb-v4-strikeout-topdown-auto.yml runs the frozen July 1-August 31, 2026 test on GitHub-hosted infrastructure and uploads mlb_v4_strikeout_topdown_result.json as an artifact.

Frozen settings: $1 unit, T-2h entry, 6h quote lookback, max quote age 10m, max spread 12c, 10-90c entry range, minimum net edge 5pt, 35% chronological training, 7% Kalshi fee parameter, 2,000 bootstrap iterations.

Research only. No trades are placed.
