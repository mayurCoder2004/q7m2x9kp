# Testing checklist

## Backend unit tests
Run from the repository root:

```bash
python -m pip install -r backend/requirements.txt
python -m pytest
```

## Docker smoke test

```bash
docker compose up --build
```

Then verify:
- `http://localhost:8000/health`
- `http://localhost:8000/docs`
- `http://localhost:5173`

The dashboard intentionally displays an upstream/API error instead of fake metrics if Elexon is unavailable. Validate live Elexon response shape and historical January 2026 coverage before using any metrics in a client demo.

## Before deployment
1. Deploy the backend to a Python/Docker host.
2. Set `FRONTEND_ORIGINS` to the exact deployed frontend origin.
3. Deploy `frontend/` as the Vercel project root.
4. Set `VITE_API_URL` to the public backend base URL before building.
5. Test `/health` and `/api/analysis?date=2026-01-15&cutoff_hour=11&cutoff_minute=0` from an incognito browser.
