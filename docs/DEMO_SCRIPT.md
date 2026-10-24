# Windwise demo script (target 8–9 minutes, maximum 10)

Before recording:

- Run `docker compose up --build -d`.
- Open http://localhost:5173 and http://localhost:8000/docs in separate tabs.
- Open a terminal in the repo root.
- Click Run once so the Elexon responses are cached.

## 1. Problem and objective (≈1 min)

> "Windwise answers one question: how good was the GB wind forecast *that was actually available* when a day-ahead decision had to be made? Comparing outturn with the newest forecast flatters accuracy, because later forecasts did not exist at decision time. Windwise enforces a decision cutoff, by default 11:00 UK time the day before delivery, and measures the error of the forecast you could really have used, across January 2026."

## 2. Architecture (≈1 min)

Show the README architecture diagram.

> "A React and Vite dashboard calls a FastAPI backend. The backend pulls two public Elexon BMRS datasets, validates them, selects forecast vintages, computes metrics and writes a plain-language summary. Everything runs in Docker Compose. The frontend is a production nginx build, and CI runs both test suites."

## 3. Data source and methodology (≈1.5 min)

Show the Methodology panel in the dashboard.

- Actuals come from FUELHH (half-hourly, `fuelType=WIND`, queried by settlement date). Forecasts come from WINDFOR (hourly), queried over a publish-time window so every forecast run is available.
- For each hour, the forecast with the latest publish time at or before the cutoff wins. For 11:00 that is the previous day's 09:30 run, with lead times of 14.5 to 37.5 hours.
- Half-hours are averaged to hourly and matched exactly on start time. Delivery days are UK local days, so the code handles 23- and 25-hour DST days.
- Briefly mention the bug fix: "the original code sent `from`/`to` parameters that Elexon ignores, so January requests silently received October data. Now we query by settlement date, and any out-of-window data triggers a warning."

## 4. Dashboard demonstration (≈2.5 min)

1. Default view, 15 Jan 2026. Walk through the KPI cards, the actual vs forecast chart (hover the tooltip) and the hourly error chart.
2. Scroll the table: publish time, lead time and status per hour. Click **Export CSV** and open it to show the same rows with UTC timestamps and the metrics header.
3. Change the cutoff to **09:00** and run again. The 09:30 run is now too late, so an older vintage is used: lead times grow and you can compare the error.
4. Switch to **Full month** and run. Show 744 matched periods, the daily MAE chart and the executive summary.

## 5. Metrics and interpretation (≈1.5 min)

Use the full month, 11:00 cutoff:

> "MAE was about 2,120 MW against mean generation of about 11,600 MW, which is a WAPE of about 18%. Bias is +1,840 MW, so the forecast systematically overestimated wind. Daily MAE ranged from about 800 MW on 9 January to about 5,400 MW on 28 January, and the largest single miss was +6,800 MW overnight on 12 January. Coverage was 100%: every hour had complete actuals and an eligible forecast."

> "Positive bias means that, other things equal, more replacement generation would have been needed than planned."

## 6. Limitations (≈1 min)

- Forecast error alone is not procurement cost, savings or market impact. Demand, other generation and prices are out of scope.
- FUELHH is metered outturn after curtailment, so part of the overforecast may be curtailment rather than forecast skill.
- The 11:00 cutoff is an assumption to confirm with the client. Results reflect Elexon data as served today.

## 7. Testing and deployment (≈1 min)

- Terminal: run `python -m pytest` (36 passed) and `cd frontend && npm test` (17 passed).
- Mention the opt-in live tests and the independent recomputation that matched the dashboard exactly.
- Deployment: backend on Render via `render.yaml`, frontend on Vercel with `VITE_API_URL`. Show the live URLs if deployed.
- Close by noting that AI assistance (Claude Code) was used and is disclosed in the README.
