"""Live checks against the real Elexon API.

Skipped by default; run with WINDWISE_LIVE_TESTS=1 python -m pytest -m live
"""

import os

import pytest
from fastapi.testclient import TestClient

from app import main

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("WINDWISE_LIVE_TESTS") != "1",
        reason="set WINDWISE_LIVE_TESTS=1 to call the real Elexon API",
    ),
]


def test_live_january_day_is_fully_matched():
    body = TestClient(main.app).get("/api/analysis", params={"date": "2026-01-15"}).json()
    assert body["rows"][0]["startTime"] == "2026-01-15T00:00:00Z"
    assert body["coverage"]["expected_periods"] == 24
    assert body["coverage"]["matched"] > 0
    for row in body["rows"]:
        if row["publishTime"]:
            assert row["publishTime"] <= row["cutoffTime"]


def test_live_january_month_summary():
    body = TestClient(main.app).get("/api/summary").json()
    assert body["coverage"]["expected_periods"] == 744
    assert body["metrics"]["n_periods"] == body["coverage"]["matched"]
    assert {row["deliveryDate"][:7] for row in body["rows"]} == {"2026-01"}
