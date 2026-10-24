# Submission checklist

No separate assignment brief is stored in this repository. This checklist is based on the requirements captured in the README and the project brief. Confirm it against the official submission instructions.

## Code and repository

- [x] January 2026 actuals load (FUELHH by settlement date): 1,488 / 1,488 half-hours
- [x] Forecast vintages selected by decision cutoff (latest `publishTime` ≤ cutoff)
- [x] Forecasts matched to actuals by exact hourly delivery period
- [x] MAE, RMSE, bias, WAPE and coverage computed from validated data
- [x] Backend tests pass (`python -m pytest`)
- [x] Frontend tests pass and the production build succeeds (`npm test`, `npm run build`)
- [x] Docker Compose starts both services; backend healthcheck passes
- [x] README, TESTING.md and the demo script are complete
- [x] No secrets committed (`.env` ignored; Elexon needs no key)
- [ ] Project pushed to its **own** GitHub repository (it currently has a local repo only)
- [ ] Repository visibility set to **Public**, and checked from a logged-out browser
- [ ] GitHub Actions CI is green on the pushed commit

## Deployment

- [ ] Backend deployed (Render blueprint) with `FRONTEND_ORIGINS` set
- [ ] `https://<backend>/health` returns `ok`
- [ ] Frontend deployed (Vercel, root `frontend`) with `VITE_API_URL` set before the build
- [ ] Deployed dashboard loads 15 Jan 2026, and Full month shows 744 periods (checked in a private window)
- [ ] Deployed URLs added to the README

## Demo video (≤ 10 min)

- [ ] Recorded following `docs/DEMO_SCRIPT.md`
- [ ] Covers: problem, architecture, data and methodology, dashboard, metrics, limitations, testing and deployment
- [ ] Uploaded (e.g. Loom or unlisted YouTube); link opens without login
- [ ] Link added to the README and the submission form

## Submission form

- [ ] Public GitHub repository URL
- [ ] Deployed frontend URL (and backend URL if requested)
- [ ] Demo video URL
- [ ] AI-use disclosure, consistent with the README section "AI assistance disclosure"
- [ ] Any stated assumptions (11:00 UK cutoff, hourly averaging of FUELHH) mentioned where the form asks
