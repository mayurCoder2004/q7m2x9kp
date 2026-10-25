# Windwise · Wind Forecast Intelligence

Windwise is a React + FastAPI dashboard that measures how accurate the GB national wind-generation forecast was **at the moment a day-ahead decision had to be made**. It covers delivery dates in January 2026, using public Elexon BMRS data.

## The problem

A forecast is only useful if it was available before the decision it informs. Comparing actual generation with the *latest* forecast overstates accuracy, because later forecasts were not available at decision time. Windwise enforces a configurable **decision cutoff** (default 11:00 UK time on the day before delivery). For each delivery hour it uses only the most recent forecast published at or before that cutoff, then quantifies the error against metered outturn.

## Features

- **Single-day or full-month analysis** for January 2026 delivery dates, with a configurable decision cutoff.
- **KPI cards:** MAE, RMSE, bias, WAPE and forecast coverage. Unavailable values show "N/A", never 0.
- **Charts:** actual vs forecast generation (hourly). An error chart shows signed hourly error for a single day, or daily MAE for the month.
- **Executive summary** generated from the calculated results: performance, bias, coverage, largest miss, and daily and hour-of-day patterns.
- **Data-quality warnings:** missing forecasts, missing or incomplete actuals, duplicates, discarded records.
- **Period-detail table:** delivery period, actual, forecast, signed and absolute error, forecast publish time, lead time and status.
- **CSV export** built from the same row model as the table, with a metadata header (cutoff, sources, metrics, coverage).
- **Methodology and limitations** panel.
- Loading, empty and error states. The UI never substitutes invented data.

## Architecture

```text
Browser (React/Vite SPA) ──HTTP──▶ FastAPI backend ──HTTPS──▶ Elexon Insights API
  frontend/src/App.jsx              backend/app/main.py         /datasets/FUELHH/stream
  frontend/src/lib.js (API, CSV)    · fetch + validate           /datasets/WINDFOR/stream
                                    · vintage selection
                                    · metrics & summary
                                    · in-memory TTL cache
```

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite 6, Recharts, lucide-react; served by nginx in Docker |
| Backend | Python 3.12, FastAPI, pandas, NumPy, HTTPX, zoneinfo (+ tzdata) |
| Tests | pytest (backend), Vitest + Testing Library + jsdom (frontend) |
| Runtime | Docker / Docker Compose; GitHub Actions CI |

## Data sources

| Purpose | Endpoint | Notes |
|---|---|---|
| Actual generation | [`GET /datasets/FUELHH/stream`](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream) | Queried with `settlementDateFrom`, `settlementDateTo` (UK local dates) and `fuelType=WIND`. Half-hourly average MW. |
| Wind forecast | [`GET /datasets/WINDFOR/stream`](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream) | Queried with `publishDateTimeFrom` and `publishDateTimeTo` (inclusive). Returns every forecast run published in that window; each run is hourly and covers about 3 days ahead. |

Base URL: `https://data.elexon.co.uk/bmrs/api/v1`. No API key is required.

> **Important:** the FUELHH stream endpoint has no `from`/`to` parameters. It silently ignores unknown parameters and returns the latest day of data. This was the cause of an earlier bug where January requests returned October 2026 records (see [Changelog](#changelog-january-data-fix)).

## Methodology

1. **Delivery day.** A UK local calendar day (Europe/London), converted to a UTC interval. That interval is 24 hours on normal days, 23 on the spring DST day and 25 on the autumn DST day. In January, UK time is GMT, which equals UTC.
2. **Decision cutoff.** HH:MM UK local time on the day before each delivery date (default 11:00). Every delivery day in a monthly run has its own cutoff.
3. **Forecast vintage.** For each hourly period, Windwise uses the WINDFOR forecast with the **latest `publishTime` ≤ cutoff**. Forecasts published after the cutoff are excluded twice: once by the request window and again in code, independently of the upstream filter. With the default cutoff, January used the 09:30 UTC run from the previous day (lead times of 14.5–37.5 h).
4. **Matching.** WINDFOR is hourly and FUELHH is half-hourly. Each hour's actual is the mean of the two FUELHH half-hours starting in that hour, matched exactly on `startTime`. Hours with fewer than two half-hours are flagged `incomplete_actual` and excluded from metrics.
5. **Validation.** Unreadable timestamps or values, negative outturn and records outside the requested window are discarded with a warning. Duplicate FUELHH half-hours keep the most recently published value; duplicate WINDFOR records are collapsed. Missing data is reported, never imputed. An unexpected response shape is an error, not "no data".

### Metric definitions (error = forecast − actual, MW)

| Metric | Formula | Notes |
|---|---|---|
| MAE | mean(\|error\|) | Typical size of a miss |
| RMSE | √mean(error²) | Penalises large misses |
| Bias | mean(error) | **Positive = overforecast**, negative = underforecast |
| WAPE | Σ\|error\| ÷ Σ actual × 100 | Unavailable (null) if Σ actual = 0 |
| Coverage | matched periods ÷ periods with complete actual data × 100 | Null if there are no complete actual periods |

Metrics use only `matched` periods (complete actual and an eligible forecast).

## Results: January 2026 (live Elexon data, cutoff 11:00 UK)

Retrieved 25 Oct 2026 and recomputed independently from raw API responses by a separate script. Both produced identical values.

| Matched periods | Coverage | MAE | RMSE | Bias | WAPE |
|---|---|---|---|---|---|
| 744 / 744 | 100.0% | 2,122.81 MW | 2,616.69 MW | +1,840.30 MW | 18.33% |

Daily MAE ranged from 797 MW (9 Jan) to 5,375 MW (28 Jan). The largest single miss was +6,833 MW at 01:00 on 12 Jan. These values come from the public API and may change if Elexon revises historical data.

## Running locally

Prerequisites: Python 3.12, Node.js 22 (or 20+), and optionally Docker Desktop.

### Docker (recommended)

```bash
docker compose up --build
```

- Dashboard: http://localhost:5173
- API docs (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8000/health

The frontend image is a production build served by nginx. `VITE_API_URL` is a **build argument** (see `docker-compose.yml`) because Vite inlines it at build time.

### Without Docker

Backend:

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Frontend, in another terminal:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

### Configuration

The backend reads environment variables directly. It does not load a `.env` file; see `backend/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `ELEXON_BASE_URL` | `https://data.elexon.co.uk/bmrs/api/v1` | Elexon Insights API base |
| `FRONTEND_ORIGINS` | `http://localhost:5173` | Comma-separated CORS allow-list |
| `FRONTEND_ORIGIN_REGEX` | *(unset)* | Optional CORS regex, e.g. for Vercel preview URLs |
| `CACHE_TTL_SECONDS` | `900` | In-memory cache of Elexon responses (0 disables) |
| `ELEXON_TIMEOUT_SECONDS` | `45` | Upstream request timeout |
| `VITE_API_URL` (frontend, build time) | `http://localhost:8000` | Public backend URL |

## API

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness check and supported date window |
| `GET /api/analysis?date=2026-01-15&cutoff_hour=11&cutoff_minute=0` | One delivery day (must be in January 2026; otherwise 422) |
| `GET /api/summary?cutoff_hour=11&cutoff_minute=0` | All of January 2026, plus `daily` and `by_hour` breakdowns |

Example:

```bash
curl "http://localhost:8000/api/analysis?date=2026-01-15&cutoff_hour=11&cutoff_minute=0"
```

Response (abridged):

```json
{
  "date": "2026-01-15", "scope": "day",
  "metrics": {"mae_mw": 1110.25, "rmse_mw": 1212.22, "bias_mw": 1110.25, "wape_pct": 10.51, "n_periods": 24, "...": "..."},
  "coverage": {"matched": 24, "actual_periods": 24, "forecast_coverage_pct": 100.0, "expected_periods": 24,
               "missing_forecast": 0, "missing_actual": 0, "incomplete_actual": 0},
  "rows": [{"startTime": "2026-01-15T00:00:00Z", "deliveryDate": "2026-01-15", "actual": 14456.5, "forecast": 15828.0,
            "publishTime": "2026-01-14T09:30:00Z", "cutoffTime": "2026-01-14T11:00:00Z", "leadTimeHours": 14.5,
            "error": 1371.5, "absoluteError": 1371.5, "actualHalfHours": 2, "status": "matched"}],
  "warnings": [], "executive_summary": ["..."], "largest_errors": ["..."], "daily": ["..."], "by_hour": ["..."],
  "vintages_used": [{"publishTime": "2026-01-14T09:30:00Z", "periods": 24}],
  "methodology": {"...": "..."}, "period": {"...": "..."}, "source": {"retrieved_at": "..."}
}
```

Errors: `422` for invalid parameters or dates outside January 2026; `502` for an upstream HTTP error or unusable response; `504` for an upstream timeout. If Elexon returns no data, the API responds `200` with null metrics and explanatory `warnings`.

## Testing

```bash
python -m pytest                                        # backend, from repo root (mocked, deterministic)
WINDWISE_LIVE_TESTS=1 python -m pytest -m live          # optional: real Elexon API
cd frontend && npm test && npm run build                # frontend unit/component tests + production build
```

See [TESTING.md](TESTING.md) for coverage details and the manual smoke test.

## Deployment

### Live application

- **Dashboard:** https://wind-forecast-intelligence.vercel.app
- **Backend health:** https://windwise-api.onrender.com/health
- **API documentation:** https://windwise-api.onrender.com/docs

The recommended setup is the **backend on Render** (Docker, `render.yaml` blueprint) and the **frontend on Vercel** (`frontend/vercel.json`).

1. **Backend (Render):** New → Blueprint → select this repo. Render reads `render.yaml`, builds `backend/Dockerfile` and checks `/health`. Set `FRONTEND_ORIGINS` to the Vercel URL, e.g. `https://windwise.vercel.app`. The container listens on `$PORT`.
2. **Frontend (Vercel):** New Project → import the repo → **Root Directory `frontend`** (framework: Vite). Set the environment variable `VITE_API_URL=https://<your-render-service>.onrender.com` **before** the first build.
3. **Verify** from a private browser window:
   - `https://<backend>/health`
   - `https://<backend>/api/analysis?date=2026-01-15`
   - the dashboard loads 15 Jan and the Full-month view shows 744 periods.

Note: Render's free tier sleeps when idle, so the first request after a pause can take about 30–60 s.

## Limitations and assumptions

- Only national **wind** is analysed. Demand, other generation, interconnectors and prices are out of scope, so **forecast error alone is not evidence of balancing procurement cost, financial savings or market impact.**
- FUELHH is metered outturn and can be reduced by curtailment or constraint actions. Part of the observed positive bias may therefore reflect curtailment rather than forecast skill. Windwise does not separate the two.
- Hourly actuals are the mean of two half-hours; WINDFOR's exact hourly semantics are as published by Elexon.
- The 11:00 cutoff is a configurable analytical assumption, not a specific market rule. Confirm the operational cutoff with the client.
- Results depend on Elexon's historical data as currently served. Revisions upstream can change the figures.
- The cache is per-process and in-memory. This is a pilot, not an operational trading system.

## Changelog: January data fix

The original backend requested `FUELHH/stream?from=…&to=…`. Those parameters do not exist on that endpoint, so Elexon ignored them and returned its default window (the latest ~24 h, i.e. 24 Oct 2026). Every January request therefore received October data and matched zero periods. The original code also hard-coded "known" WINDFOR publication times (03:30, 05:30, 08:30, 10:30…) that do not match January's actual runs (02:30, 04:30, 07:30, 09:30, 11:30, 15:30, 18:30, 22:30 UTC).

The fix:

- Actuals are now queried by `settlementDateFrom/To` + `fuelType=WIND`.
- Forecasts come from the WINDFOR stream over a publish-time window, so all real vintages are available.
- Records outside the requested window are rejected with a warning, so this class of bug can no longer pass silently.

## AI assistance disclosure

AI tools were used in building this project. The initial scaffolding and documentation were AI-assisted. The January data-pipeline diagnosis and fix, the backend refactor, the dashboard features, the tests, the Docker and deployment configuration and this documentation were produced with **Claude Code (Anthropic)** working under the author's direction.

Verification was done by:

- checking Elexon's published OpenAPI specification and probing the live API;
- running the automated test suites;
- recomputing the January metrics with an independent script.

The author is responsible for reviewing and understanding all submitted code.
