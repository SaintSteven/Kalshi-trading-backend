V2.6.7 MOBILE UPLOAD

Upload the contents of this ZIP to the root of the existing backend GitHub repository, replacing files with
the same names. Do not upload __pycache__ or .pyc files.

After Render redeploys, /health should report version 2.6.7.

For the July test:
- Start Date: Jul 1, 2026
- End Date: Jul 31, 2026
- Maximum Days: 31
- Entry: T-2 hours
- Daily cap: $5
- Unit: $1
- Minimum Edge: 5

Once the job starts, progress is checkpointed after each completed slate. Do not intentionally redeploy the
backend during the run unless HISTORICAL_JOB_DB_PATH points to a mounted Render persistent disk.
