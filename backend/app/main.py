
from datetime import date, datetime, time, timedelta, timezone
import logging
import os
from typing import Any

import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

BASE_URL = os.getenv(
    "ELEXON_BASE_URL",
    "https://data.elexon.co.uk/bmrs/api/v1",
).rstrip("/")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("windwise")

app = FastAPI(
    title="Wind Forecast Intelligence API",
    version="0.4.0",
)

origins = [
    item.strip()
    for item in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173",
    ).split(",")
    if item.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# Elexon API helpers
# ---------------------------------------------------------

def _rows(payload: Any) -> list[dict]:
    """Normalize common Elexon API response shapes."""

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in ("data", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [
                    item for item in value
                    if isinstance(item, dict)
                ]

    return []


def _iso_utc(dt: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC timestamp."""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


async def _fetch(path: str, params: dict) -> list[dict]:
    """Fetch records from the Elexon Insights API."""

    url = f"{BASE_URL}/{path.lstrip('/')}"

    logger.info("Elexon request: %s params=%s", url, params)

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.get(url, params=params)

        logger.info(
            "Elexon response: endpoint=%s status=%s",
            path,
            response.status_code,
        )

        response.raise_for_status()
        payload = response.json()

    records = _rows(payload)

    logger.info(
        "Elexon response: endpoint=%s records=%s",
        path,
        len(records),
    )

    if records:
        logger.info(
            "Elexon sample record for %s: %s",
            path,
            records[0],
        )

    return records


# ---------------------------------------------------------
# Actual generation
# ---------------------------------------------------------

async def fetch_actuals(
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """
    Fetch WIND actual generation and aggregate it into hourly
    values so it can be compared with hourly wind forecasts.
    """

    rows = await _fetch(
        "datasets/FUELHH/stream",
        {
            "from": _iso_utc(start),
            "to": _iso_utc(end),
        },
    )

    columns = ["startTime", "actual"]

    if not rows:
        logger.warning("No actual generation records returned.")
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(rows)

    required = {"fuelType", "startTime", "generation"}
    missing = required.difference(df.columns)

    if missing:
        raise ValueError(
            f"FUELHH response is missing fields: {sorted(missing)}"
        )

    df = df[
        df["fuelType"]
        .astype(str)
        .str.strip()
        .str.upper()
        .eq("WIND")
    ].copy()

    df["startTime"] = pd.to_datetime(
        df["startTime"],
        utc=True,
        errors="coerce",
    )

    df["actual"] = pd.to_numeric(
        df["generation"],
        errors="coerce",
    )

    df = df.dropna(subset=["startTime", "actual"])

    # FUELHH may have half-hourly records; the forecast is hourly.
    # Use the mean of the available actual records within each hour.
    df["startTime"] = df["startTime"].dt.floor("h")

    df = (
        df.groupby("startTime", as_index=False)["actual"]
        .mean()
        .sort_values("startTime")
    )

    logger.info("Hourly actual WIND records: %s", len(df))

    return df[columns]


# ---------------------------------------------------------
# Historical wind forecasts
# ---------------------------------------------------------

def _candidate_publication_times(
    publication_day: date,
    cutoff_hour: int,
    cutoff_minute: int,
) -> list[datetime]:
    """Return known Elexon publication vintages at/before the cutoff.

    Do not query an arbitrary cutoff such as 11:00 when the known
    vintage is 10:30. The history endpoint is keyed by publication
    vintage, and a non-vintage timestamp can return an unexpected result.
    """
    known_times = [
        (3, 30),
        (5, 30),
        (8, 30),
        (9, 30),
        (10, 30),
        (12, 30),
        (16, 30),
        (19, 30),
        (22, 30),
        (23, 30),
    ]

    cutoff = datetime.combine(
        publication_day,
        time(cutoff_hour, cutoff_minute),
        tzinfo=timezone.utc,
    )

    candidates = [
        datetime.combine(
            publication_day,
            time(hour, minute),
            tzinfo=timezone.utc,
        )
        for hour, minute in known_times
    ]
    return sorted(
        (candidate for candidate in candidates if candidate <= cutoff),
        reverse=True,
    )


async def fetch_forecasts(
    start: datetime,
    end: datetime,
    cutoff_hour: int,
    cutoff_minute: int,
) -> pd.DataFrame:
    """Fetch historical wind forecast vintages for the requested range.

    For each delivery day, try known publication vintages newest-first.
    A response only counts as a match if it actually contains forecast
    records for that delivery day with a publishTime no later than the
    applicable cutoff. This avoids stopping on an unrelated/non-matching
    response from the history endpoint.
    """
    columns = ["startTime", "publishTime", "forecast"]
    all_rows: list[dict] = []

    start_utc = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end_utc = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)
    first_delivery_day = start_utc.date()
    # end is exclusive; don't fetch an extra delivery day at midnight.
    last_delivery_day = (end_utc - timedelta(microseconds=1)).date()

    delivery_day = first_delivery_day
    while delivery_day <= last_delivery_day:
        publication_day = delivery_day - timedelta(days=1)
        cutoff = datetime.combine(
            publication_day,
            time(cutoff_hour, cutoff_minute),
            tzinfo=timezone.utc,
        )
        found_for_day = False

        for requested_publish_time in _candidate_publication_times(
            publication_day, cutoff_hour, cutoff_minute
        ):
            try:
                rows = await _fetch(
                    "forecast/generation/wind/history",
                    {"publishTime": _iso_utc(requested_publish_time)},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.info("No forecast vintage for %s", requested_publish_time)
                    continue
                raise

            if not rows:
                continue

            # Validate the returned vintage and delivery horizon before
            # deciding that this candidate is usable.
            valid_rows = []
            for row in rows:
                raw_start = row.get("startTime")
                raw_publish = row.get("publishTime")
                raw_generation = row.get("generation")
                if raw_start is None or raw_publish is None or raw_generation is None:
                    continue
                parsed_start = pd.to_datetime(raw_start, utc=True, errors="coerce")
                parsed_publish = pd.to_datetime(raw_publish, utc=True, errors="coerce")
                parsed_generation = pd.to_numeric(raw_generation, errors="coerce")
                if pd.isna(parsed_start) or pd.isna(parsed_publish) or pd.isna(parsed_generation):
                    continue
                if parsed_start.date() != delivery_day or parsed_publish > pd.Timestamp(cutoff):
                    continue
                valid_rows.append(row)

            if valid_rows:
                all_rows.extend(valid_rows)
                found_for_day = True
                logger.info(
                    "Forecast vintage selected: requested=%s delivery_day=%s "
                    "eligible_records=%s returned_publish_times=%s",
                    requested_publish_time,
                    delivery_day,
                    len(valid_rows),
                    sorted({str(row.get('publishTime')) for row in valid_rows}),
                )
                break

            logger.info(
                "Vintage %s returned %s records but none eligible for delivery day %s",
                requested_publish_time,
                len(rows),
                delivery_day,
            )

        if not found_for_day:
            logger.warning(
                "No eligible forecast found for delivery day %s before cutoff %s",
                delivery_day,
                _iso_utc(cutoff),
            )

        delivery_day += timedelta(days=1)

    if not all_rows:
        logger.warning("No eligible historical forecast records returned.")
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(all_rows)
    required = {"startTime", "publishTime", "generation"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"WINDFOR response is missing fields: {sorted(missing)}")

    df["startTime"] = pd.to_datetime(df["startTime"], utc=True, errors="coerce")
    df["publishTime"] = pd.to_datetime(df["publishTime"], utc=True, errors="coerce")
    df["forecast"] = pd.to_numeric(df["generation"], errors="coerce")
    df = df.dropna(subset=["startTime", "publishTime", "forecast"])

    start_ts = pd.Timestamp(start_utc)
    end_ts = pd.Timestamp(end_utc)
    df = df[(df["startTime"] >= start_ts) & (df["startTime"] < end_ts)].copy()
    df = (
        df[columns]
        .drop_duplicates(subset=["startTime", "publishTime"], keep="last")
        .sort_values(["startTime", "publishTime"])
        .reset_index(drop=True)
    )

    logger.info("Historical forecast records parsed: %s", len(df))
    if not df.empty:
        logger.info("Forecast publication range: %s to %s", df["publishTime"].min(), df["publishTime"].max())
        logger.info("Forecast delivery range: %s to %s", df["startTime"].min(), df["startTime"].max())
    return df


# ---------------------------------------------------------
# Decision cutoff and metrics
# ---------------------------------------------------------

def decision_cutoff(
    delivery_start: pd.Timestamp,
    cutoff_hour: int,
    cutoff_minute: int,
) -> pd.Timestamp:
    """Return the cutoff on the UTC day before delivery."""

    previous_day = delivery_start.date() - timedelta(days=1)

    return pd.Timestamp(
        datetime.combine(
            previous_day,
            time(cutoff_hour, cutoff_minute),
            tzinfo=timezone.utc,
        )
    )


def _empty_analysis(
    cutoff_hour: int,
    cutoff_minute: int,
) -> dict:
    """Return a consistent empty analysis response."""

    return {
        "rows": [],
        "metrics": {
            "mae_mw": None,
            "rmse_mw": None,
            "bias_mw": None,
            "wape_pct": None,
        },
        "coverage": {
            "matched": 0,
            "actual_periods": 0,
            "forecast_coverage_pct": 0,
        },
        "methodology": {
            "cutoff": (
                f"{cutoff_hour:02d}:{cutoff_minute:02d} UTC "
                "on the day before delivery"
            ),
            "selection": (
                "Latest forecast publishTime at or before cutoff "
                "for each hourly delivery timestamp"
            ),
            "timezone": "UTC",
        },
    }


def build_analysis(
    actuals: pd.DataFrame,
    forecasts: pd.DataFrame,
    cutoff_hour: int,
    cutoff_minute: int,
) -> dict:
    """Match each hourly actual with the latest eligible forecast."""

    if actuals.empty:
        return _empty_analysis(cutoff_hour, cutoff_minute)

    actuals = actuals.copy()
    forecasts = forecasts.copy()

    actuals["startTime"] = pd.to_datetime(
        actuals["startTime"],
        utc=True,
        errors="coerce",
    )
    actuals["actual"] = pd.to_numeric(
        actuals["actual"],
        errors="coerce",
    )

    actuals = (
        actuals.dropna(subset=["startTime", "actual"])
        .sort_values("startTime")
        .drop_duplicates(subset=["startTime"], keep="last")
    )

    if not forecasts.empty:
        forecasts["startTime"] = pd.to_datetime(
            forecasts["startTime"],
            utc=True,
            errors="coerce",
        )
        forecasts["publishTime"] = pd.to_datetime(
            forecasts["publishTime"],
            utc=True,
            errors="coerce",
        )
        forecasts["forecast"] = pd.to_numeric(
            forecasts["forecast"],
            errors="coerce",
        )

        forecasts = forecasts.dropna(
            subset=["startTime", "publishTime", "forecast"]
        )

    chosen = []

    for _, actual in actuals.iterrows():
        target = actual["startTime"]

        cutoff = decision_cutoff(
            target,
            cutoff_hour,
            cutoff_minute,
        )

        if forecasts.empty:
            eligible = forecasts
        else:
            eligible = forecasts[
                (forecasts["startTime"] == target)
                & (forecasts["publishTime"] <= cutoff)
            ]

        if eligible.empty:
            chosen.append({
                "startTime": target,
                "actual": float(actual["actual"]),
                "forecast": None,
                "publishTime": None,
                "cutoffTime": cutoff,
            })
        else:
            # Select the most recent forecast vintage available
            # before the decision cutoff.
            forecast_row = (
                eligible.sort_values("publishTime").iloc[-1]
            )

            chosen.append({
                "startTime": target,
                "actual": float(actual["actual"]),
                "forecast": float(forecast_row["forecast"]),
                "publishTime": forecast_row["publishTime"],
                "cutoffTime": cutoff,
            })

    result = pd.DataFrame(chosen)

    matched = result.dropna(
        subset=["forecast", "actual"]
    ).copy()

    if not matched.empty:
        # Positive error means forecast exceeded actual generation.
        error = matched["forecast"] - matched["actual"]
        absolute_error = error.abs()

        actual_total = float(matched["actual"].abs().sum())

        metrics = {
            "mae_mw": round(float(absolute_error.mean()), 2),
            "rmse_mw": round(
                float(np.sqrt(np.mean(np.square(error)))),
                2,
            ),
            "bias_mw": round(float(error.mean()), 2),
            "wape_pct": (
                round(
                    float(absolute_error.sum() / actual_total * 100),
                    2,
                )
                if actual_total
                else None
            ),
        }
    else:
        metrics = {
            "mae_mw": None,
            "rmse_mw": None,
            "bias_mw": None,
            "wape_pct": None,
        }

    # Convert timestamps into JSON-safe UTC strings.
    result["startTime"] = result["startTime"].map(
        lambda value: value.isoformat().replace("+00:00", "Z")
    )

    result["cutoffTime"] = result["cutoffTime"].map(
        lambda value: value.isoformat().replace("+00:00", "Z")
    )

    result["publishTime"] = result["publishTime"].map(
        lambda value: (
            value.isoformat().replace("+00:00", "Z")
            if pd.notna(value)
            else None
        )
    )

    result["error"] = result.apply(
        lambda row: (
            round(row["forecast"] - row["actual"], 2)
            if pd.notna(row["forecast"])
            else None
        ),
        axis=1,
    )

    result["absoluteError"] = result["error"].map(
        lambda value: (
            round(abs(value), 2)
            if pd.notna(value)
            else None
        )
    )

    actual_count = len(result)
    matched_count = int(result["forecast"].notna().sum())

    logger.info(
        "Analysis coverage: matched=%s actual_periods=%s",
        matched_count,
        actual_count,
    )

    return {
        "rows": result.replace({np.nan: None}).to_dict(
            orient="records"
        ),
        "metrics": metrics,
        "coverage": {
            "matched": matched_count,
            "actual_periods": actual_count,
            "forecast_coverage_pct": (
                round(matched_count / actual_count * 100, 1)
                if actual_count
                else 0
            ),
        },
        "methodology": {
            "cutoff": (
                f"{cutoff_hour:02d}:{cutoff_minute:02d} UTC "
                "on the day before delivery"
            ),
            "selection": (
                "Latest forecast publishTime at or before cutoff "
                "for each hourly delivery timestamp"
            ),
            "timezone": "UTC",
        },
    }


# ---------------------------------------------------------
# API routes
# ---------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "wind-forecast-intelligence",
    }


@app.get("/api/analysis")
async def analysis(
    date_value: date = Query(
        date(2026, 1, 15),
        alias="date",
    ),
    cutoff_hour: int = Query(
        11,
        ge=0,
        le=23,
    ),
    cutoff_minute: int = Query(
        0,
        ge=0,
        le=59,
    ),
):
    # Allow any historical delivery date for debugging and analysis.
    # The monthly summary endpoint remains scoped to January 2026.

    start = datetime.combine(
        date_value,
        time.min,
        tzinfo=timezone.utc,
    )
    end = start + timedelta(days=1)

    # Include earlier delivery dates when retrieving historical
    # vintages; the analysis itself uses the selected delivery day.
    fetch_start = start - timedelta(days=2)

    try:
        actuals = await fetch_actuals(start, end)

        forecasts = await fetch_forecasts(
            fetch_start,
            end,
            cutoff_hour,
            cutoff_minute,
        )

        result = build_analysis(
            actuals,
            forecasts,
            cutoff_hour,
            cutoff_minute,
        )

        result["date"] = date_value.isoformat()
        return result

    except httpx.HTTPStatusError as exc:
        logger.exception("Elexon returned an HTTP error")
        raise HTTPException(
            status_code=502,
            detail=(
                f"Elexon API returned HTTP {exc.response.status_code} "
                f"for {exc.request.url.path}"
            ),
        ) from exc

    except httpx.HTTPError as exc:
        logger.exception("Elexon API request failed")
        raise HTTPException(
            status_code=502,
            detail=f"Elexon API request failed: {exc}",
        ) from exc

    except (ValueError, KeyError) as exc:
        logger.exception("Could not process Elexon response")
        raise HTTPException(
            status_code=502,
            detail=f"Could not process Elexon response: {exc}",
        ) from exc


@app.get("/api/summary")
async def summary(
    cutoff_hour: int = Query(
        11,
        ge=0,
        le=23,
    ),
    cutoff_minute: int = Query(
        0,
        ge=0,
        le=59,
    ),
):
    """Return the January 2026 monthly analysis."""

    start = datetime(
        2026, 1, 1,
        tzinfo=timezone.utc,
    )
    end = datetime(
        2026, 2, 1,
        tzinfo=timezone.utc,
    )

    try:
        actuals = await fetch_actuals(start, end)

        forecasts = await fetch_forecasts(
            start - timedelta(days=2),
            end,
            cutoff_hour,
            cutoff_minute,
        )

        result = build_analysis(
            actuals,
            forecasts,
            cutoff_hour,
            cutoff_minute,
        )

        result["date"] = "2026-01"
        return result

    except httpx.HTTPStatusError as exc:
        logger.exception("Elexon returned an HTTP error")
        raise HTTPException(
            status_code=502,
            detail=(
                f"Elexon API returned HTTP {exc.response.status_code} "
                f"for {exc.request.url.path}"
            ),
        ) from exc

    except httpx.HTTPError as exc:
        logger.exception("Elexon API request failed")
        raise HTTPException(
            status_code=502,
            detail=f"Elexon API request failed: {exc}",
        ) from exc

    except (ValueError, KeyError) as exc:
        logger.exception("Could not process Elexon response")
        raise HTTPException(
            status_code=502,
            detail=f"Could not process Elexon response: {exc}",
        ) from exc
