// Small API response used by the frontend tests (shape mirrors the backend).
export function analysisResponse(overrides = {}) {
  const rows = [
    { startTime: '2026-01-15T00:00:00Z', deliveryDate: '2026-01-15', actual: 1000, forecast: 1100, publishTime: '2026-01-14T09:30:00Z', cutoffTime: '2026-01-14T11:00:00Z', leadTimeHours: 14.5, error: 100, absoluteError: 100, actualHalfHours: 2, status: 'matched' },
    { startTime: '2026-01-15T01:00:00Z', deliveryDate: '2026-01-15', actual: 2000, forecast: 1800, publishTime: '2026-01-14T09:30:00Z', cutoffTime: '2026-01-14T11:00:00Z', leadTimeHours: 15.5, error: -200, absoluteError: 200, actualHalfHours: 2, status: 'matched' },
    { startTime: '2026-01-15T02:00:00Z', deliveryDate: '2026-01-15', actual: 1500, forecast: null, publishTime: null, cutoffTime: '2026-01-14T11:00:00Z', leadTimeHours: null, error: null, absoluteError: null, actualHalfHours: 2, status: 'missing_forecast' },
  ];
  return {
    date: '2026-01-15', scope: 'day', rows,
    metrics: { mae_mw: 150, rmse_mw: 158.11, bias_mw: -50, wape_pct: 10, max_abs_error_mw: 200, mean_actual_mw: 1500, mean_forecast_mw: 1450, n_periods: 2 },
    coverage: { matched: 2, actual_periods: 3, forecast_coverage_pct: 66.7, expected_periods: 3, missing_forecast: 1, missing_actual: 0, incomplete_actual: 0 },
    methodology: { cutoff: '11:00 UK local time (Europe/London) on the day before each delivery date', selection: 'Latest publishTime at or before cutoff', matching: 'Exact startTime', timezone: 'UTC', units: 'MW' },
    warnings: ['1 period(s) with actual data had no forecast published at or before the cutoff and are excluded from metrics.'],
    largest_errors: [rows[1]], daily: [], by_hour: [],
    vintages_used: [{ publishTime: '2026-01-14T09:30:00Z', periods: 2 }],
    executive_summary: ['Across 2 matched hourly periods, the day-ahead forecast had an MAE of 150 MW.'],
    source: { actuals: 'FUELHH', forecasts: 'WINDFOR', retrieved_at: '2026-10-25T10:00:00Z' },
    ...overrides,
  };
}
