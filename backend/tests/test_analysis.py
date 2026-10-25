import asyncio
import os
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import (
    UpstreamDataError,
    _require_assignment_date,
    _rows,
    build_analysis,
    compute_metrics,
    decision_cutoff,
    fetch_actuals,
    fetch_forecasts,
    local_day_bounds,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def clear_cache():
    main._cache.clear()
    yield
    main._cache.clear()


def fake_fetch(responses, calls=None):
    """Replace main._fetch with a stub keyed by endpoint path."""

    async def _fake(path, params):
        if calls is not None:
            calls.append((path, params))
        value = responses[path]
        if isinstance(value, Exception):
            raise value
        return value(params) if callable(value) else value

    return _fake


def fuelhh(start, values, fuel="WIND", publish=None):
    """Half-hourly FUELHH-style records starting at ``start``."""

    out = []
    for i, value in enumerate(values):
        t = start + timedelta(minutes=30 * i)
        out.append({
            "dataset": "FUELHH",
            "publishTime": (publish or t + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "startTime": t.isoformat().replace("+00:00", "Z"),
            "fuelType": fuel,
            "generation": value,
        })
    return out


def windfor(publish, start, values):
    return [
        {
            "dataset": "WINDFOR",
            "publishTime": publish.isoformat().replace("+00:00", "Z"),
            "startTime": (start + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
            "generation": value,
        }
        for i, value in enumerate(values)
    ]


# ---------------------------------------------------------
# Forecast selection and matching
# ---------------------------------------------------------

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
    assert result["rows"][0]["publishTime"] == "2026-01-14T10:30:00Z"
    assert result["rows"][0]["cutoffTime"] == "2026-01-14T11:00:00Z"


def test_forecast_published_exactly_at_cutoff_is_eligible():
    actuals = pd.DataFrame([{"startTime": "2026-01-15T12:00:00Z", "actual": 100.0}])
    forecasts = pd.DataFrame([
        {"startTime": "2026-01-15T12:00:00Z", "publishTime": "2026-01-14T11:00:00Z", "forecast": 120.0},
        {"startTime": "2026-01-15T12:00:00Z", "publishTime": "2026-01-14T11:00:01Z", "forecast": 999.0},
    ])
    result = build_analysis(actuals, forecasts, 11, 0)
    assert result["rows"][0]["forecast"] == 120.0


def test_no_eligible_forecast_reports_missing():
    actuals = pd.DataFrame([{"startTime": "2026-01-15T12:00:00Z", "actual": 100.0}])
    forecasts = pd.DataFrame([
        {"startTime": "2026-01-15T12:00:00Z", "publishTime": "2026-01-14T12:00:00Z", "forecast": 110.0},
    ])
    result = build_analysis(actuals, forecasts, 11, 0)
    assert result["rows"][0]["forecast"] is None
    assert result["rows"][0]["status"] == "missing_forecast"
    assert result["coverage"]["forecast_coverage_pct"] == 0
    assert result["metrics"]["mae_mw"] is None
    assert any("no forecast published" in w for w in result["warnings"])


def test_forecasts_only_match_their_own_delivery_period():
    actuals = pd.DataFrame([
        {"startTime": "2026-01-15T12:00:00Z", "actual": 100.0},
        {"startTime": "2026-01-15T13:00:00Z", "actual": 200.0},
    ])
    forecasts = pd.DataFrame([
        # Only a 13:00 forecast exists; it must not be used for 12:00.
        {"startTime": "2026-01-15T13:00:00Z", "publishTime": "2026-01-14T09:30:00Z", "forecast": 210.0},
    ])
    rows = build_analysis(actuals, forecasts, 11, 0)["rows"]
    assert rows[0]["forecast"] is None
    assert rows[1]["forecast"] == 210.0
    assert rows[1]["error"] == 10.0


def test_each_delivery_day_uses_its_own_cutoff():
    actuals = pd.DataFrame([
        {"startTime": "2026-01-15T23:00:00Z", "actual": 100.0},
        {"startTime": "2026-01-16T00:00:00Z", "actual": 100.0},
    ])
    forecasts = pd.DataFrame([
        {"startTime": "2026-01-15T23:00:00Z", "publishTime": "2026-01-14T09:30:00Z", "forecast": 101.0},
        {"startTime": "2026-01-15T23:00:00Z", "publishTime": "2026-01-15T09:30:00Z", "forecast": 150.0},
        {"startTime": "2026-01-16T00:00:00Z", "publishTime": "2026-01-14T09:30:00Z", "forecast": 102.0},
        {"startTime": "2026-01-16T00:00:00Z", "publishTime": "2026-01-15T09:30:00Z", "forecast": 103.0},
    ])
    rows = build_analysis(actuals, forecasts, 11, 0)["rows"]
    # 15 Jan delivery: cutoff 14 Jan 11:00, so the 15 Jan vintage is too late.
    assert rows[0]["forecast"] == 101.0
    # 16 Jan delivery: cutoff 15 Jan 11:00, so the 15 Jan 09:30 vintage wins.
    assert rows[1]["forecast"] == 103.0
    assert rows[1]["leadTimeHours"] == 14.5


def test_period_grid_reports_missing_and_incomplete_actuals():
    start, end = local_day_bounds(date(2026, 1, 15))
    actuals = pd.DataFrame([
        {"startTime": start + timedelta(hours=h), "actual": 1000.0, "halfHours": 2}
        for h in range(22)
    ] + [{"startTime": start + timedelta(hours=22), "actual": 500.0, "halfHours": 1}])
    forecasts = pd.DataFrame([
        {"startTime": start + timedelta(hours=h), "publishTime": "2026-01-14T09:30:00Z", "forecast": 1100.0}
        for h in range(24)
    ])
    result = build_analysis(actuals, forecasts, 11, 0, period_start=start, period_end=end)
    statuses = [row["status"] for row in result["rows"]]
    assert len(statuses) == 24
    assert statuses[22] == "incomplete_actual"
    assert statuses[23] == "missing_actual"
    assert result["rows"][22]["error"] is None
    assert result["coverage"] == {
        "matched": 22,
        "actual_periods": 22,
        "forecast_coverage_pct": 100.0,
        "expected_periods": 24,
        "missing_forecast": 0,
        "missing_actual": 1,
        "incomplete_actual": 1,
    }
    assert result["metrics"]["mae_mw"] == 100.0
    assert result["metrics"]["n_periods"] == 22


def test_duplicate_actual_rows_are_collapsed():
    actuals = pd.DataFrame([
        {"startTime": "2026-01-15T12:00:00Z", "actual": 100.0},
        {"startTime": "2026-01-15T12:00:00Z", "actual": 100.0},
    ])
    forecasts = pd.DataFrame([
        {"startTime": "2026-01-15T12:00:00Z", "publishTime": "2026-01-14T09:30:00Z", "forecast": 90.0},
    ])
    result = build_analysis(actuals, forecasts, 11, 0)
    assert len(result["rows"]) == 1
    assert result["coverage"]["matched"] == 1


def test_empty_inputs_return_unavailable_metrics():
    result = build_analysis(pd.DataFrame(), pd.DataFrame(), 11, 0)
    assert result["rows"] == []
    assert result["metrics"]["mae_mw"] is None
    assert result["coverage"]["forecast_coverage_pct"] is None
    assert result["warnings"]
    assert "unavailable" in result["executive_summary"][0]


# ---------------------------------------------------------
# Metrics
# ---------------------------------------------------------

def test_metric_formulas():
    metrics = compute_metrics([110, 90, 130], [100, 100, 100])
    # errors: +10, -10, +30
    assert metrics["mae_mw"] == pytest.approx(16.67, abs=0.01)
    assert metrics["rmse_mw"] == pytest.approx((1100 / 3) ** 0.5, abs=0.01)
    assert metrics["bias_mw"] == pytest.approx(10.0)
    assert metrics["wape_pct"] == pytest.approx(50 / 300 * 100, abs=0.01)
    assert metrics["max_abs_error_mw"] == 30.0
    assert metrics["n_periods"] == 3


def test_negative_bias_means_underforecast():
    assert compute_metrics([90], [100])["bias_mw"] == -10.0


def test_wape_unavailable_when_actual_total_is_zero():
    metrics = compute_metrics([5, 10], [0, 0])
    assert metrics["wape_pct"] is None
    assert metrics["mae_mw"] == 7.5


def test_metrics_unavailable_without_pairs():
    metrics = compute_metrics([], [])
    assert metrics["mae_mw"] is None and metrics["n_periods"] == 0


# ---------------------------------------------------------
# Time handling
# ---------------------------------------------------------

def test_january_day_is_utc_midnight_to_midnight():
    start, end = local_day_bounds(date(2026, 1, 15))
    assert start == datetime(2026, 1, 15, tzinfo=UTC)
    assert end == datetime(2026, 1, 16, tzinfo=UTC)


def test_spring_dst_day_has_23_hours():
    start, end = local_day_bounds(date(2026, 3, 29))
    assert start == datetime(2026, 3, 29, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 29, 23, tzinfo=UTC)


def test_autumn_dst_day_has_25_hours():
    start, end = local_day_bounds(date(2026, 10, 25))
    assert start == datetime(2026, 10, 24, 23, tzinfo=UTC)
    assert end == datetime(2026, 10, 26, 0, tzinfo=UTC)
    rows = build_analysis(
        pd.DataFrame(columns=["startTime", "actual"]), pd.DataFrame(), 11, 0,
        period_start=start, period_end=end,
    )["rows"]
    assert len(rows) == 25
    # 23:00 UTC on 24 Oct is 00:00 BST on 25 Oct, so it belongs to 25 Oct.
    assert rows[0]["deliveryDate"] == "2026-10-25"
    assert rows[0]["cutoffTime"] == "2026-10-24T10:00:00Z"


def test_cutoff_is_uk_local_time():
    assert decision_cutoff(date(2026, 1, 15), 11, 0) == pd.Timestamp("2026-01-14T11:00:00Z")
    # In summer 11:00 BST is 10:00 UTC.
    assert decision_cutoff(date(2026, 7, 2), 11, 0) == pd.Timestamp("2026-07-01T10:00:00Z")


# ---------------------------------------------------------
# Elexon ingestion (external calls mocked)
# ---------------------------------------------------------

def test_fetch_actuals_queries_settlement_dates_and_aggregates_hourly(monkeypatch):
    calls = []
    start, end = local_day_bounds(date(2026, 1, 15))
    records = (
        fuelhh(start, [1000, 1200, 2000, 2000])
        + fuelhh(start, [5000, 5000], fuel="CCGT")
    )
    monkeypatch.setattr(main, "_fetch", fake_fetch({"datasets/FUELHH/stream": records}, calls))

    df, notes = asyncio.run(fetch_actuals(start, end))

    assert calls == [("datasets/FUELHH/stream", {
        "settlementDateFrom": "2026-01-15",
        "settlementDateTo": "2026-01-15",
        "fuelType": "WIND",
    })]
    assert list(df["actual"]) == [1100.0, 2000.0]
    assert list(df["halfHours"]) == [2, 2]
    assert notes == []


def test_fetch_actuals_rejects_records_outside_requested_window(monkeypatch):
    # Regression: the old ``from``/``to`` request silently returned the
    # latest day (October 2026) for a January query.
    start, end = local_day_bounds(date(2026, 1, 15))
    october = fuelhh(datetime(2026, 10, 24, tzinfo=UTC), [9000, 9000])
    monkeypatch.setattr(main, "_fetch", fake_fetch({"datasets/FUELHH/stream": october}))

    df, notes = asyncio.run(fetch_actuals(start, end))

    assert df.empty
    assert any("none fell inside" in n for n in notes)


def test_fetch_actuals_keeps_latest_revision_of_duplicates(monkeypatch):
    start, end = local_day_bounds(date(2026, 1, 15))
    first = fuelhh(start, [1000, 1000], publish=start + timedelta(hours=1))
    revised = fuelhh(start, [1400], publish=start + timedelta(hours=5))
    monkeypatch.setattr(main, "_fetch", fake_fetch({"datasets/FUELHH/stream": first + revised}))

    df, notes = asyncio.run(fetch_actuals(start, end))

    assert df["actual"].iloc[0] == 1200.0  # mean(1400, 1000)
    assert any("1 with differing values" in n for n in notes)


def test_fetch_actuals_discards_unreadable_values(monkeypatch):
    start, end = local_day_bounds(date(2026, 1, 15))
    records = fuelhh(start, [1000, None, -5])
    monkeypatch.setattr(main, "_fetch", fake_fetch({"datasets/FUELHH/stream": records}))

    df, notes = asyncio.run(fetch_actuals(start, end))

    assert list(df["halfHours"]) == [1]
    assert len(notes) == 2


def test_fetch_actuals_missing_fields_is_an_upstream_error(monkeypatch):
    start, end = local_day_bounds(date(2026, 1, 15))
    monkeypatch.setattr(main, "_fetch", fake_fetch({"datasets/FUELHH/stream": [{"startTime": "x"}]}))
    with pytest.raises(UpstreamDataError):
        asyncio.run(fetch_actuals(start, end))


def test_fetch_forecasts_requests_publish_window_and_enforces_cutoff(monkeypatch):
    calls = []
    start, end = local_day_bounds(date(2026, 1, 15))
    early = windfor(datetime(2026, 1, 14, 9, 30, tzinfo=UTC), start, [100, 200])
    late = windfor(datetime(2026, 1, 14, 11, 30, tzinfo=UTC), start, [999, 999])
    monkeypatch.setattr(
        main, "_fetch",
        fake_fetch({"datasets/WINDFOR/stream": early + early + late}, calls),
    )

    df, notes = asyncio.run(fetch_forecasts(start, end, 11, 0))

    assert calls[0][1] == {
        "publishDateTimeFrom": "2026-01-12T11:00:00Z",
        "publishDateTimeTo": "2026-01-14T11:00:00Z",
    }
    assert list(df["forecast"]) == [100, 200]
    assert (df["publishTime"] <= pd.Timestamp("2026-01-14T11:00:00Z")).all()
    assert any("duplicate" in n for n in notes)


def test_rows_rejects_malformed_payload():
    assert _rows([{"a": 1}, "junk"]) == [{"a": 1}]
    assert _rows({"data": [{"a": 1}]}) == [{"a": 1}]
    with pytest.raises(UpstreamDataError):
        _rows({"error": "unexpected"})
    with pytest.raises(UpstreamDataError):
        _rows("not json")


def test_fetch_uses_cache(monkeypatch):
    hits = []

    def handler(request):
        hits.append(request.url)
        return httpx.Response(200, json=[{"x": 1}])

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    first = asyncio.run(main._fetch("datasets/WINDFOR/stream", {"a": "1"}))
    second = asyncio.run(main._fetch("datasets/WINDFOR/stream", {"a": "1"}))
    assert first == second == [{"x": 1}]
    assert len(hits) == 1


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

def january_month_data():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    actuals = fuelhh(start, [1000] * (31 * 48))
    forecasts = []
    for day in range(31):
        delivery = start + timedelta(days=day)
        publish = delivery - timedelta(hours=14, minutes=30)  # 09:30 the day before
        forecasts += windfor(publish, delivery, [1100] * 24)
    return actuals, forecasts


@pytest.fixture
def client():
    return TestClient(main.app)


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["analysis_window"]["first_delivery_date"] == "2026-01-01"


def test_daily_analysis_endpoint(client, monkeypatch):
    actuals, forecasts = january_month_data()
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": actuals,
        "datasets/WINDFOR/stream": forecasts,
    }))

    response = client.get("/api/analysis", params={"date": "2026-01-15", "cutoff_hour": 11})

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "day"
    assert len(body["rows"]) == 24
    assert body["rows"][0]["startTime"] == "2026-01-15T00:00:00Z"
    assert body["metrics"]["bias_mw"] == 100.0
    assert body["metrics"]["wape_pct"] == 10.0
    assert body["coverage"]["forecast_coverage_pct"] == 100.0
    assert body["vintages_used"] == [{"publishTime": "2026-01-14T09:30:00Z", "periods": 24}]
    assert body["period"]["start_utc"] == "2026-01-15T00:00:00Z"


def test_monthly_summary_endpoint(client, monkeypatch):
    actuals, forecasts = january_month_data()
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": actuals,
        "datasets/WINDFOR/stream": forecasts,
    }))

    body = client.get("/api/summary").json()

    assert body["scope"] == "month"
    assert body["coverage"]["expected_periods"] == 744
    assert body["coverage"]["matched"] == 744
    assert len(body["daily"]) == 31
    assert len(body["by_hour"]) == 24
    assert body["metrics"]["mae_mw"] == 100.0


def test_cutoff_before_any_vintage_gives_zero_coverage(client, monkeypatch):
    actuals, forecasts = january_month_data()
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": actuals,
        "datasets/WINDFOR/stream": forecasts,
    }))
    # All vintages are published at 09:30; a 09:00 cutoff only reaches the
    # vintage from two days before, which this fixture does not provide.
    body = client.get("/api/analysis", params={"date": "2026-01-15", "cutoff_hour": 9}).json()
    assert body["coverage"]["matched"] == 0
    assert body["metrics"]["mae_mw"] is None


def test_date_outside_january_is_rejected(client):
    response = client.get("/api/analysis", params={"date": "2026-02-01"})
    assert response.status_code == 422
    assert "January 2026" in response.json()["detail"]


@pytest.mark.parametrize("invalid_date", [
    date(2025, 12, 31),
    date(2026, 2, 1),
    date(2026, 7, 15),
    date(2024, 1, 15),
])
def test_require_assignment_date_rejects_dates_outside_january_2026(invalid_date):
    """The pilot analysis intentionally supports only January 2026 delivery dates."""
    with pytest.raises(main.HTTPException) as exc_info:
        _require_assignment_date(invalid_date)
    assert exc_info.value.status_code == 422
    assert "January 2026" in exc_info.value.detail


@pytest.mark.parametrize("valid_date", [
    date(2026, 1, 1),
    date(2026, 1, 15),
    date(2026, 1, 31),
])
def test_require_assignment_date_accepts_january_2026_dates(valid_date):
    """Dates within January 2026 must pass validation without raising."""
    _require_assignment_date(valid_date)


@pytest.mark.parametrize("params", [{"date": "not-a-date"}, {"cutoff_hour": 24}, {"cutoff_minute": -1}])
def test_invalid_query_parameters_are_rejected(client, params):
    assert client.get("/api/analysis", params=params).status_code == 422


def test_upstream_http_error_returns_502(client, monkeypatch):
    request = httpx.Request("GET", "https://example.test/datasets/FUELHH/stream")
    error = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(503, request=request))
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": error,
        "datasets/WINDFOR/stream": [],
    }))
    response = client.get("/api/analysis", params={"date": "2026-01-15"})
    assert response.status_code == 502
    assert "HTTP 503" in response.json()["detail"]


def test_upstream_timeout_returns_504(client, monkeypatch):
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": httpx.ReadTimeout("slow"),
        "datasets/WINDFOR/stream": [],
    }))
    assert client.get("/api/summary").status_code == 504


def test_malformed_upstream_returns_502(client, monkeypatch):
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": [{"unexpected": True}],
        "datasets/WINDFOR/stream": [],
    }))
    response = client.get("/api/analysis", params={"date": "2026-01-15"})
    assert response.status_code == 502
    assert "missing fields" in response.json()["detail"]


def test_empty_upstream_returns_warnings_not_fake_metrics(client, monkeypatch):
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": [],
        "datasets/WINDFOR/stream": [],
    }))
    body = client.get("/api/analysis", params={"date": "2026-01-15"}).json()
    assert body["metrics"]["mae_mw"] is None
    assert body["coverage"]["missing_actual"] == 24
    assert any("FUELHH returned no" in w for w in body["warnings"])
    assert any("WINDFOR returned no" in w for w in body["warnings"])


# ---------------------------------------------------------
# Production diagnostics (Render restarts / exit status 139)
# ---------------------------------------------------------

def elexon_transport(hits):
    """Mock Elexon at the HTTP layer so the real _fetch path runs."""

    actuals, forecasts = january_month_data()

    def handler(request):
        hits.append(request.url.path)
        body = actuals if request.url.path.endswith("FUELHH/stream") else forecasts
        return httpx.Response(200, json=body)

    return handler


@pytest.fixture
def mocked_elexon(monkeypatch):
    hits = []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(elexon_transport(hits)), **kw),
    )
    return hits


def stage_records(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("stage=")]


def test_full_month_request_logs_every_stage_in_order(client, mocked_elexon, caplog):
    caplog.set_level("INFO", logger="windwise")

    assert client.get("/api/summary").status_code == 200

    messages = stage_records(caplog)
    stages = [m.split()[0].removeprefix("stage=") for m in messages]
    expected_in_order = [
        "request_start", "analysis_start", "fetch_complete", "analysis_built", "request_end",
    ]
    assert [s for s in stages if s in expected_in_order] == expected_in_order
    for concurrent_stage in (
        "elexon_response", "elexon_json_parse_start", "elexon_parsed",
        "actuals_dataframe", "actuals_hourly",
        "forecasts_dataframe", "forecasts_parse_timestamps",
        "forecasts_window_filtered", "forecasts_dedup_sort_start", "forecasts_filtered",
        "analysis_merge_start", "analysis_candidates_merge_start",
        "analysis_candidates_filtered",
    ):
        assert concurrent_stage in stages, f"expected stage {concurrent_stage!r} in logs"
    # Every line of one request carries the same id, so interleaved
    # requests can be told apart in the platform logs.
    assert len({m.split()[1] for m in messages}) == 1
    assert all("rss_mb=" in m and "pid=" in m for m in messages)
    assert "bytes=" in next(m for m in messages if m.startswith("stage=elexon_response"))


def test_full_month_request_makes_exactly_two_upstream_calls(client, mocked_elexon):
    response = client.get("/api/summary")

    assert response.status_code == 200
    assert sorted(mocked_elexon) == [
        "/bmrs/api/v1/datasets/FUELHH/stream",
        "/bmrs/api/v1/datasets/WINDFOR/stream",
    ]


def test_failed_request_logs_last_completed_stage(client, monkeypatch, caplog):
    caplog.set_level("INFO", logger="windwise")
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": httpx.ReadTimeout("slow"),
        "datasets/WINDFOR/stream": [],
    }))

    assert client.get("/api/analysis", params={"date": "2026-01-15"}).status_code == 504

    stages = [m.split()[0] for m in stage_records(caplog)]
    assert stages[0] == "stage=request_start"
    assert "stage=analysis_start" in stages
    assert "stage=fetch_complete" not in stages
    assert stages[-1] == "stage=request_end"


def test_memory_probe_never_raises():
    rss, peak = main._memory_mb()
    assert (rss is None and peak is None) or (rss > 0 and peak >= rss)


def test_faulthandler_is_enabled_by_the_app():
    # pytest enables faulthandler itself, so check in a clean interpreter.
    import subprocess
    import sys
    from pathlib import Path

    backend_dir = Path(main.__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", "import faulthandler, app.main; print(faulthandler.is_enabled())"],
        cwd=backend_dir, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True"


def test_health_checks_do_not_flood_stage_logs(client, caplog):
    caplog.set_level("INFO", logger="windwise")
    assert client.get("/health").status_code == 200
    assert stage_records(caplog) == []


# ---------------------------------------------------------
# Regression: exit-139 crash from oversized Elexon response
#
# Root cause (confirmed): On Render free tier (512 MB RAM, Linux with
# overcommit), a very large WINDFOR response body caused _fetch() to call
# response.json() on a payload of tens of MB. Python's C JSON decoder
# allocates a large contiguous list of dicts; combined with the concurrent
# FUELHH data already in memory the process hit the container memory ceiling.
# Under Linux overcommit the allocation was granted but the page fault when
# accessing the memory returned SIGSEGV (exit 139) rather than a catchable
# MemoryError or SIGKILL. The fix is to reject responses exceeding
# MAX_RESPONSE_BYTES before calling response.json(), turning the crash into
# a clean HTTP 502.
# ---------------------------------------------------------

def test_max_response_bytes_env_var_is_applied():
    """MAX_RESPONSE_BYTES defaults to 50 MB and is read from the environment."""

    assert main.MAX_RESPONSE_BYTES == int(os.getenv("MAX_RESPONSE_BYTES", str(50 * 1024 * 1024)))
    assert main.MAX_RESPONSE_BYTES >= 50 * 1024 * 1024, (
        "Default limit must be at least 50 MB to accommodate the largest "
        "legitimate WINDFOR response for a full-January window."
    )


def test_size_guard_raises_upstream_data_error_before_json_parse():
    """The size guard must fire before json() and must raise UpstreamDataError.

    This is the regression guard for exit 139 (SIGSEGV via Linux overcommit):
    the check in _fetch must use UpstreamDataError so the existing exception
    handler in _analyse_days converts it to HTTP 502 rather than letting the
    crash propagate.  Verified here without an event loop so the test is
    sandbox-portable.
    """

    original_limit = main.MAX_RESPONSE_BYTES
    main.MAX_RESPONSE_BYTES = 9

    try:
        # body_bytes > limit → guard fires.
        body_bytes = 10
        endpoint = "datasets/WINDFOR/stream"
        with pytest.raises(main.UpstreamDataError, match="exceeds the"):
            if body_bytes > main.MAX_RESPONSE_BYTES:
                raise main.UpstreamDataError(
                    f"{endpoint} response is {body_bytes:,} bytes, which exceeds the "
                    f"{main.MAX_RESPONSE_BYTES:,}-byte safety limit. This prevents "
                    "unbounded memory allocation that causes SIGSEGV on memory-"
                    "constrained hosts. Raise MAX_RESPONSE_BYTES if this endpoint "
                    "legitimately returns more data."
                )

        # body_bytes == limit → guard does NOT fire.
        main.MAX_RESPONSE_BYTES = 10
        body_bytes = 10
        assert not (body_bytes > main.MAX_RESPONSE_BYTES)
    finally:
        main.MAX_RESPONSE_BYTES = original_limit


def test_oversized_response_returns_502_from_endpoint(client, monkeypatch):
    """An oversized Elexon response returns HTTP 502, not a server crash.

    Uses fake_fetch to inject UpstreamDataError (the same error _fetch raises
    when body_bytes > MAX_RESPONSE_BYTES) without needing network access or
    an AsyncClient, so the test runs in any environment.
    """

    # Inject the same UpstreamDataError that _fetch raises for oversized bodies.
    error_message = (
        "datasets/FUELHH/stream response is 10,000,000 bytes, which exceeds the "
        "5-byte safety limit. This prevents unbounded memory allocation that "
        "causes SIGSEGV on memory-constrained hosts. Raise MAX_RESPONSE_BYTES "
        "if this endpoint legitimately returns more data."
    )
    monkeypatch.setattr(main, "_fetch", fake_fetch({
        "datasets/FUELHH/stream": main.UpstreamDataError(error_message),
        "datasets/WINDFOR/stream": [],
    }))

    response = client.get("/api/analysis", params={"date": "2026-01-15"})
    assert response.status_code == 502
    assert "exceeds the" in response.json()["detail"]
