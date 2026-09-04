# NFL Market Efficiency Scanner v0.01

Top-down discovery harness. It inventories settled Kalshi NFL player-prop series and measures settlement behavior by side and price bucket before deciding which prop category deserves a dedicated model.

Important: v0.01 uses market-level final/last prices only as a discovery reference. It does **not** claim those prices were executable pregame entries. Any interesting slice must advance to a separate candle-level validation using a fixed pregame cutoff, executable YES ask and NO ask reconstructed as 1-YES bid, chronological stability, one independent player-game thesis, and fee-aware P/L.

No trades are placed. No discovered slice is promoted directly to a strategy.
