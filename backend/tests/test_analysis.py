import pandas as pd
from app.main import build_analysis

def test_latest_forecast_before_cutoff_is_selected():
    actuals = pd.DataFrame([{"startTime": "2026-01-15T12:00:00Z", "actual": 100.0}])
    forecasts = pd.DataFrame([
        {"startTime": "2026-01-15T12:00:00Z", "publishTime": "2026-01-14T08:00:00Z", "forecast": 90.0},
        {"startTime": "2026-01-15T12:00:00Z", "publishTime": "2026-01-14T10:30:00Z", "forecast": 95.0},
        {"startTime": "2026-01-15T12:00:00Z", "publishTime": "2026-01-14T12:00:00Z", "forecast": 110.0},
    ])
    result = build_analysis(actuals, forecasts, 11, 0)
    assert result["rows"][0]["forecast"] == 95.0
    assert result["rows"][0]["error"] == -5.0

def test_no_eligible_forecast_reports_missing():
    actuals = pd.DataFrame([{"startTime": "2026-01-15T12:00:00Z", "actual": 100.0}])
    forecasts = pd.DataFrame([
        {"startTime": "2026-01-15T12:00:00Z", "publishTime": "2026-01-14T12:00:00Z", "forecast": 110.0},
    ])
    result = build_analysis(actuals, forecasts, 11, 0)
    assert result["rows"][0]["forecast"] is None
    assert result["coverage"]["forecast_coverage_pct"] == 0
