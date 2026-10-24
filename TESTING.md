# Testing

## 1. Backend unit and API tests (deterministic)

From the repository root:

```bash
python -m pip install -r backend/requirements.txt
python -m pytest
```

Every Elexon call is mocked, so the suite needs no network. Expected result: `36 passed, 2 skipped` (the two skipped tests are the opt-in live tests).

| Area | What is tested |
|---|---|
| Ingestion | FUELHH queried with `settlementDateFrom/To` + `fuelType=WIND` (never `from`/`to`); non-WIND rows ignored; half-hours averaged to hourly |
| Regression | October records returned for a January request are rejected, with a warning |
| Duplicates / invalid data | Revised FUELHH values keep the latest publish; WINDFOR duplicates collapsed; null and negative values discarded with notes; missing fields are an error |
| Cutoff | Forecast at exactly the cutoff is eligible; one second later is not; upstream rows after the cutoff are dropped in code |
| Vintage selection | Latest eligible vintage chosen; each delivery day uses its own cutoff; lead time computed |
| Matching | A forecast never matches a different delivery period; missing and incomplete actuals are flagged |
| Time zones / DST | January = UTC; 29 Mar 2026 = 23 h; 25 Oct 2026 = 25 h; summer cutoff 11:00 BST = 10:00 UTC |
| Metrics | MAE, RMSE, bias sign, WAPE, zero-denominator WAPE → null, empty → null |
| API | `/health`; daily and monthly shape; 744-period month; zero-coverage cutoff; 422 for non-January and invalid parameters; 502 for upstream HTTP/malformed errors; 504 for timeouts; empty upstream → warnings, not fake metrics |
| Cache | Repeated identical upstream request is served from cache |

## 2. Live Elexon checks (optional, needs internet)

```bash
WINDWISE_LIVE_TESTS=1 python -m pytest -m live          # macOS/Linux/Git Bash
$env:WINDWISE_LIVE_TESTS="1"; python -m pytest -m live  # PowerShell
```

These check that 15 Jan 2026 returns 24 January periods, that every forecast used was published at or before its cutoff, and that the monthly view has 744 January-only periods.

## 3. Frontend tests and production build

```bash
cd frontend
npm ci
npm test          # Vitest + Testing Library (jsdom)
npm run build
```

Expected result: `17 passed`, and a build with no warnings.

| Area | What is tested |
|---|---|
| Formatting | Unavailable values render as `N/A`; UK-time conversion, including BST |
| Date rules | Only 2026-01-01…2026-01-31 accepted; picker has `min`/`max`; invalid date disables Run and shows a message; no API call for invalid dates |
| API integration | Correct URLs for day and month; backend `detail` shown; FastAPI validation errors joined; network failure explained |
| UI states | Loading state on first render; KPIs, warnings and table rendered from the response; error state with no stale data; empty result shows N/A KPIs and a disabled export |
| Controls | Date, cutoff and month scope produce the correct requests |
| CSV vs table | CSV rows equal the table rows (same order and values); header names carry units; metrics and coverage in the metadata block; `NA` for unavailable metrics; quoting |

## 4. Docker smoke test

```bash
docker compose up --build -d
docker compose ps        # backend should be "healthy"
```

Then check:

- [ ] http://localhost:8000/health returns `{"status":"ok",...}`
- [ ] http://localhost:8000/api/analysis?date=2026-01-15 returns 24 rows, with `coverage.matched` > 0
- [ ] http://localhost:8000/api/analysis?date=2026-02-01 returns 422
- [ ] http://localhost:5173 loads 15 Jan 2026 automatically; charts, KPIs, summary and table render
- [ ] **Full month → Run analysis** shows 744 periods and the daily MAE chart
- [ ] **Export CSV** downloads a file whose rows match the table
- [ ] Stop the backend (`docker compose stop backend`) and click Run: the dashboard shows a clear error, not stale or fake data

Finish with `docker compose down`.

## 5. Independent metric check

The January figures in the README were reproduced by a standalone script. It reads the raw FUELHH and WINDFOR responses with only the Python standard library, applies the same cutoff rule, and recomputes the metrics. It shares no code with the app, and its results matched to the cent: n = 744, MAE 2122.81, RMSE 2616.69, bias 1840.30, WAPE 18.33.

## Before deployment

1. Deploy the backend (Render blueprint `render.yaml`) and set `FRONTEND_ORIGINS` to the exact frontend origin.
2. Deploy `frontend/` to Vercel with `VITE_API_URL` set to the public backend URL **before** building.
3. Repeat the checklist in section 4 against the deployed URLs from a private browser window.
