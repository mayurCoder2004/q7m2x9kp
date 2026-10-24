# Wind Forecast Intelligence

A React + FastAPI pilot dashboard for evaluating UK national wind-generation forecasts using Elexon BMRS FUELHH actuals and WINDFOR forecasts.

## Scope and assumptions
- Delivery dates are restricted to January 2026.
- Source data is limited to Elexon FUELHH actuals where `fuelType == "WIND"` and WINDFOR forecast records.
- Forecasts are matched to actuals by `startTime`.
- For each delivery period, the app selects the latest forecast published at or before the configured decision cutoff.
- The default cutoff is 11:00 UTC on the day before delivery. This is a configurable analytical assumption for the pilot, not a claim about a specific trading rule. Confirm the appropriate operational cutoff with the client.
- The dashboard is decision support only. It does not calculate total residual demand because consumer demand and other generation sources are outside the requested data scope.

## Tech stack
- Frontend: React, Vite, Recharts
- Backend: Python, FastAPI, Pandas, HTTPX
- Testing: Pytest
- Containerization: Docker and Docker Compose

## Run locally with Docker
```bash
docker compose up --build
```
- Frontend: http://localhost:5173
- Backend API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Run without Docker
Backend:
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Frontend, in another terminal:
```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_URL` if the backend is not at `http://localhost:8000`.
Set `ELEXON_BASE_URL` to override the API base URL if needed.

## API
- `GET /health`
- `GET /api/analysis?date=2026-01-15&cutoff_hour=11&cutoff_minute=0`
- `GET /api/summary?cutoff_hour=11&cutoff_minute=0`

The Elexon request/response formats can evolve. The backend normalizes common response shapes and returns a clear error if the upstream API fails. Check the Elexon API documentation linked below if fields or query parameters change.

## Metrics
- MAE (MW): mean absolute forecast error.
- RMSE (MW): square root of mean squared error.
- Bias (MW): mean forecast minus actual; positive means overforecast.
- WAPE (%): total absolute error divided by total actual generation, multiplied by 100. Undefined if total actual generation is zero.

Missing forecasts and missing actuals are not silently imputed. Only matched, non-null periods are used in accuracy metrics. The API response reports coverage.

## AI tools
AI assistance was used during initial scaffolding and documentation. All generated code and calculations should be reviewed and tested before use. This is a pilot, not an operational trading system.

## Deployment
Deploy the frontend to Vercel with the `frontend` directory as the project root and set `VITE_API_URL` to the public backend URL. Deploy the backend to a Python-compatible host that supports Docker or FastAPI, then enable CORS for the Vercel domain. Verify `/health` and the January 2026 analysis endpoint from an incognito browser before submitting.

## Data source
- Actual generation: https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream
- Wind forecast: https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream
