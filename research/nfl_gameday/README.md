# NFL Game-Day Engine

Purpose: a small production path for game days. Research/prospective tracking must never block current market capture or the actionable card.

## Contract
Input: current NFL slate from Kalshi + frozen independent models.
Output: raw market checkpoint, model checkpoints, unified candidates, and a health report.

## Reliability rules
1. Save raw capture before modeling.
2. Models fail independently.
3. Save model outputs before card construction.
4. Prospective-paper settlement/scorecards are not on the production critical path.
5. Git commits are not required for a successful run; Actions artifacts are the run checkpoints.
6. Market prices are overlays only and are never model inputs.

## Current scope
Phase 1 wires receiving yards and rushing yards from the validated main-branch research modules. Fantasy points is intentionally not silently copied from its research branch; it will be integrated as the next audited module so the production engine has one explicit source of truth.
