# Kalshi Trading Backend v0.2 — Phone-Friendly Flat Build

This version is intentionally flat because GitHub's mobile upload flow can flatten
folders. Every Python file lives at the repository root, and Render starts the app
with:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

## Endpoints

- `GET /health`
- `GET /markets`
- `POST /build-card`
- Interactive docs at `/docs`

## Current limitation

The backend collects live MLB strikeout markets but does not create real picks yet.
Until the independent projection and QC engines are connected, all markets are
returned as `PASS`.

## Render deployment

Use **New → Blueprint** and connect this repository. Render will read
`render.yaml` automatically.
