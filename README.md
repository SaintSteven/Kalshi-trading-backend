# Kalshi Trading Backend v0.1

Sprint 1 of the backend brain for the mobile Kalshi Trading Platform.

## Working now
- `GET /health`
- `GET /markets`
- `POST /build-card`
- Live MLB strikeout market collection
- Eastern-time ticker-date generation
- Market and price normalization
- CORS configured for the Vercel frontend
- FastAPI docs at `/docs`
- Basic tests

## Important limitation
`POST /build-card` does not create real picks yet. It returns markets as `PASS` because no independent projection or QC engine is connected. This prevents the system from inventing fair probabilities.

## Deploy to Render
1. Create a new GitHub repository named `kalshi-trading-backend`.
2. Upload every file from this package.
3. In Render choose **New → Blueprint**.
4. Connect the repository.
5. Render reads `render.yaml` and deploys it.
6. Test `/health`, `/docs`, and `/markets` on the Render URL.

## Local test
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest
```

## Sprint 2
Projection inputs, expected strikeouts, ladder probabilities, edge calculation, QC, and ranked recommendations.
