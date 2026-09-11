# NFL Receiving v0.11 Historical Validation

Purpose: strengthen the live top-down receiving model without fitting to 2026 outcomes.

The audit reruns the frozen v0.10 historical model on 2024 and 2025, then measures probability calibration, core-versus-tail calibration, Brier score, log loss, and—only when genuine historical executable prices are present—three execution policies: maximum-edge single threshold, near-even core only, and a 1.0u/0.5u/0.25u core-middle-tail ladder.

Guardrails:
- v0.10 model is unchanged.
- No 2026 live result is used to fit coefficients.
- No synthetic sportsbook or Kalshi prices are created.
- If row-level price fields are absent, profitability/ladder ROI is reported unavailable.
- New live rules are not promoted automatically from this run.
