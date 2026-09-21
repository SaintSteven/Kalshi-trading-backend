# NFL Game-Day Engine

Purpose: one production path for NFL game days. Research/prospective tracking never blocks current market capture or the actionable card.

## One-command interface
Run the **NFL Game Day Engine** workflow manually. With no inputs it runs the current captured slate. Optional inputs:
- `target_date`: scope captured receiving/rushing markets to `YYYY-MM-DD`.
- `event_filter`: scope to a game/event substring such as `IND-KC`.
- `fail_stage`: test-only failure injection; leave `none` for production.

ChatGPT can use this workflow as the single production entry point, then inspect the resulting raw, models, and final artifacts.

## Contract
Input: current NFL Kalshi slate plus frozen independent models.
Output: raw market checkpoint, model checkpoints, unified candidates, and a health manifest.

## Reliability rules
1. Syntax preflight runs before capture.
2. Raw capture is saved before modeling.
3. Receiving, rushing, and fantasy-points models fail independently.
4. Model outputs are checkpointed before card construction.
5. Prospective-paper settlement/scorecards are not on the production critical path.
6. Git commits are not required for a successful run; Actions artifacts are the run checkpoints.
7. Market prices are overlays only and are never model inputs.
8. Production runs are explicit/manual; development pushes do not launch production.

## Current scope
Production includes receiving yards, rushing yards, and the frozen fantasy-points model. Date/event scoping reduces the receiving/rushing slate at capture; event scoping also filters fantasy-points candidates at card construction. The health manifest records run identity, commit SHA, capture scope/counts, model versions, and stage status.
