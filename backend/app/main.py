"""Windwise backend.

Compares Elexon BMRS WINDFOR wind-generation forecasts with FUELHH wind
outturn for January 2026, using only forecasts published at or before a
day-ahead decision cutoff.
"""

import asyncio
import contextvars
from datetime import date, datetime, time, timedelta, timezone
import faulthandler
import importlib.util
import itertools
import logging
import os
import platform
from time import monotonic, perf_counter
from typing import Any
from zoneinfo import ZoneInfo

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

REQUEST_TIMEOUT_SECONDS = float(os.getenv("ELEXON_TIMEOUT_SECONDS", "45"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "900"))

# Reject Elexon responses larger than this before JSON-parsing them. An
# unexpectedly large payload would otherwise cause Python's C JSON decoder to
# allocate several hundred MB of objects at once, which on memory-constrained
# hosts (e.g. Render free tier, 512 MB) triggers a SIGSEGV via the Linux
# overcommit path rather than a clean MemoryError. 50 MB is well above the
# largest legitimate WINDFOR response for a 33-day publish window (~3-5 MB
# for ~15 000 records) while still safely below the 512 MB Render limit.
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", str(50 * 1024 * 1024)))

# Delivery dates and decision cutoffs are GB (UK local) days. Settlement
# dates in Elexon datasets follow the same convention.
UK_TZ = ZoneInfo("Europe/London")

ASSIGNMENT_FIRST_DAY = date(2026, 1, 1)
ASSIGNMENT_LAST_DAY = date(2026, 1, 31)

# How far before the earliest cutoff to look for forecast vintages. The
# latest eligible vintage is normally published hours before the cutoff;
# the extra margin lets the selection fall back to an older vintage if a
# publication run is missing, rather than leaving periods unmatched.
FORECAST_LOOKBACK = timedelta(hours=48)

ACTUALS_SOURCE = "Elexon BMRS FUELHH (half-hourly generation outturn, fuelType=WIND)"
FORECAST_SOURCE = "Elexon BMRS WINDFOR (hourly wind generation forecast)"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("windwise")

# Exit status 139 is SIGSEGV, a crash in native code that Python cannot
# catch. faulthandler writes the Python stack of every thread to stderr
# when that happens, so the platform logs show where the process died.
faulthandler.enable()


# ---------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_request_counter = itertools.count(1)


def _memory_mb() -> tuple[float | None, float | None]:
    """Current and peak resident memory in MB (Linux only, else None)."""

    try:
        with open("/proc/self/status", encoding="ascii") as status:
            fields = dict(line.split(":", 1) for line in status if ":" in line)
        return (
            int(fields["VmRSS"].split()[0]) / 1024,
            int(fields["VmHWM"].split()[0]) / 1024,
        )
    except (OSError, KeyError, ValueError):
        return None, None


def log_stage(stage: str, **details: Any) -> None:
    """Log a processing stage with the request id and process memory.

    If the process dies, the last stage logged for a request identifies
    the step that was running.
    """

    rss, peak = _memory_mb()
    logger.info(
        "stage=%s request=%s pid=%s rss_mb=%s peak_rss_mb=%s %s",
        stage,
        _request_id.get(),
        os.getpid(),
        "n/a" if rss is None else f"{rss:.1f}",
        "n/a" if peak is None else f"{peak:.1f}",
        " ".join(f"{key}={value}" for key, value in details.items()),
    )


logger.info(
    "Windwise process start: pid=%s python=%s platform=%s pandas=%s numpy=%s "
    "httpx=%s uvloop_installed=%s",
    os.getpid(),
    platform.python_version(),
    platform.platform(),
    pd.__version__,
    np.__version__,
    httpx.__version__,
    importlib.util.find_spec("uvloop") is not None,
)

app = FastAPI(
    title="Windwise API",
    description=(
        "Day-ahead wind forecast accuracy for GB national wind generation, "
        "January 2026, using Elexon BMRS FUELHH and WINDFOR."
    ),
    version="1.0.0",
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
    # Optional, e.g. r"https://windwise-.*\.vercel\.app" for preview deploys.
    allow_origin_regex=os.getenv("FRONTEND_ORIGIN_REGEX") or None,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_request_lifecycle(request, call_next):
    """Tag each request with an id and log when it starts and finishes."""

    if request.url.path == "/health":
        # Platform health checks run every few seconds; keep them quiet.
        return await call_next(request)

    _request_id.set(f"r{next(_request_counter)}")
    started = perf_counter()
    log_stage("request_start", method=request.method, path=request.url.path, query=request.url.query or "-")
    try:
        response = await call_next(request)
    except Exception as exc:
        log_stage("request_error", path=request.url.path, error=exc.__class__.__name__)
        raise
    log_stage(
        "request_end",
        path=request.url.path,
        status=response.status_code,
        ms=round((perf_counter() - started) * 1000),
    )
    return response


class UpstreamDataError(ValueError):
    """Elexon returned data that cannot be used safely."""


# ---------------------------------------------------------
# Time helpers
# ---------------------------------------------------------

def _iso_utc(dt: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC timestamp."""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _iso_or_none(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return _iso_utc(pd.Timestamp(value).to_pydatetime())


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return the UTC [start, end) interval of a UK local calendar day.

    Normal days are 24 hours long; the spring DST transition day is 23
    hours and the autumn transition day is 25 hours.
    """

    start = datetime.combine(day, time.min, tzinfo=UK_TZ)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UK_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def delivery_day_of(timestamp: pd.Timestamp) -> date:
    """UK local delivery date of a UTC period start."""

    return pd.Timestamp(timestamp).tz_convert(UK_TZ).date()


def decision_cutoff(
    delivery_day: date,
    cutoff_hour: int,
    cutoff_minute: int,
) -> pd.Timestamp:
    """Return the decision cutoff (UTC) for a UK local delivery date.

    The cutoff is HH:MM UK local time on the day before delivery. In
    January UK local time is GMT, so the cutoff equals HH:MM UTC.
    """

    local_cutoff = datetime.combine(
        delivery_day - timedelta(days=1),
        time(cutoff_hour, cutoff_minute),
        tzinfo=UK_TZ,
    )
    return pd.Timestamp(local_cutoff.astimezone(timezone.utc))


def _span_days(start: datetime, end: datetime) -> tuple[date, date]:
    """First and last UK local dates touched by a UTC [start, end) interval."""

    first = start.astimezone(UK_TZ).date()
    last = (end - timedelta(microseconds=1)).astimezone(UK_TZ).date()
    return first, last


# ---------------------------------------------------------
# Elexon API helpers
# ---------------------------------------------------------

_cache: dict[tuple, tuple[float, list[dict]]] = {}


def _rows(payload: Any) -> list[dict]:
    """Extract records from an Elexon response.

    Stream endpoints return a JSON array; other endpoints wrap records in
    ``{"data": [...]}``. Anything else is treated as an upstream error so
    that an unexpected shape is never mistaken for "no data".
    """

    if isinstance(payload, dict):
        for key in ("data", "results", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break

    if not isinstance(payload, list):
        raise UpstreamDataError(
            f"Unexpected Elexon response shape: {type(payload).__name__}"
        )

    return [item for item in payload if isinstance(item, dict)]


async def _fetch(path: str, params: dict) -> list[dict]:
    """Fetch records from the Elexon Insights API (with a short TTL cache).

    A response body larger than ``MAX_RESPONSE_BYTES`` is rejected before
    JSON-parsing.  Attempting to parse a multi-hundred-MB payload would cause
    Python's C JSON decoder to allocate large contiguous objects; on
    memory-constrained hosts that run with Linux overcommit the allocation
    *succeeds* but the subsequent memory access raises SIGSEGV (exit 139)
    rather than a catchable MemoryError.  Raising ``UpstreamDataError`` here
    keeps the crash in Python-land and returns HTTP 502 to the caller.
    """

    key = (path, tuple(sorted(params.items())))
    cached = _cache.get(key)
    if cached and cached[0] > monotonic():
        logger.info("Elexon cache hit: %s params=%s", path, params)
        return cached[1]

    url = f"{BASE_URL}/{path.lstrip('/')}"
    logger.info("Elexon request: %s params=%s", url, params)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(
            url,
            params=params,
            headers={"Accept": "application/json"},
        )

    body_bytes = len(response.content)
    log_stage(
        "elexon_response",
        endpoint=path,
        status=response.status_code,
        bytes=body_bytes,
    )
    response.raise_for_status()

    if body_bytes > MAX_RESPONSE_BYTES:
        raise UpstreamDataError(
            f"{path} response is {body_bytes:,} bytes, which exceeds the "
            f"{MAX_RESPONSE_BYTES:,}-byte safety limit. This prevents "
            "unbounded memory allocation that causes SIGSEGV on memory-"
            "constrained hosts. Raise MAX_RESPONSE_BYTES if this endpoint "
            "legitimately returns more data."
        )

    log_stage("elexon_json_parse_start", endpoint=path, bytes=body_bytes)
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamDataError(f"{path} did not return valid JSON") from exc

    records = _rows(payload)
    log_stage("elexon_parsed", endpoint=path, records=len(records))

    if CACHE_TTL_SECONDS > 0:
        _cache[key] = (monotonic() + CACHE_TTL_SECONDS, records)

    return records


def _require_fields(df: pd.DataFrame, required: set[str], dataset: str) -> None:
    missing = required.difference(df.columns)
    if missing:
        raise UpstreamDataError(
            f"{dataset} response is missing fields: {sorted(missing)}"
        )


# ---------------------------------------------------------
# Actual generation
# ---------------------------------------------------------

ACTUAL_COLUMNS = ["startTime", "actual", "halfHours"]


async def fetch_actuals(
    start: datetime,
    end: datetime,
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch WIND outturn for [start, end) and aggregate it to hourly means.

    FUELHH values are average MW over each 30-minute settlement period.
    WINDFOR is hourly, so each hour's actual is the mean of the two
    half-hours starting in that hour. ``halfHours`` records how many
    half-hours were available so incomplete hours can be excluded.

    Note: the FUELHH stream endpoint filters by ``settlementDateFrom`` /
    ``settlementDateTo`` (UK local dates) or by publish time. It does not
    support ``from`` / ``to``; unknown parameters are ignored and the
    endpoint silently returns the latest day of data instead.
    """

    first_day, last_day = _span_days(start, end)
    rows = await _fetch(
        "datasets/FUELHH/stream",
        {
            "settlementDateFrom": first_day.isoformat(),
            "settlementDateTo": last_day.isoformat(),
            "fuelType": "WIND",
        },
    )

    notes: list[str] = []
    empty = pd.DataFrame(columns=ACTUAL_COLUMNS)

    if not rows:
        notes.append(
            "Elexon FUELHH returned no wind generation records for "
            f"{first_day.isoformat()} to {last_day.isoformat()}."
        )
        return empty, notes

    df = pd.DataFrame(rows)
    log_stage("actuals_dataframe", rows=len(df))
    _require_fields(df, {"fuelType", "startTime", "generation"}, "FUELHH")

    df = df[df["fuelType"].astype(str).str.strip().str.upper().eq("WIND")].copy()
    df["startTime"] = pd.to_datetime(df["startTime"], utc=True, errors="coerce")
    df["actual"] = pd.to_numeric(df["generation"], errors="coerce")
    df["publishTime"] = pd.to_datetime(
        df["publishTime"] if "publishTime" in df else pd.NaT,
        utc=True,
        errors="coerce",
    )

    invalid = df["startTime"].isna() | df["actual"].isna()
    if invalid.any():
        notes.append(
            f"{int(invalid.sum())} FUELHH record(s) had an unreadable "
            "timestamp or generation value and were discarded."
        )
    df = df[~invalid]

    returned = len(df)
    df = df[(df["startTime"] >= pd.Timestamp(start)) & (df["startTime"] < pd.Timestamp(end))]
    if returned and df.empty:
        # This is exactly how the original ``from``/``to`` bug manifested.
        notes.append(
            "FUELHH returned records, but none fell inside the requested "
            "delivery window; check the request parameters."
        )
        return empty, notes

    negative = df["actual"] < 0
    if negative.any():
        notes.append(
            f"{int(negative.sum())} negative FUELHH wind value(s) were "
            "treated as invalid and discarded."
        )
        df = df[~negative]

    duplicated = df.duplicated("startTime", keep=False)
    if duplicated.any():
        conflicting = (
            df[duplicated].groupby("startTime")["actual"].nunique().gt(1).sum()
        )
        notes.append(
            f"{int(df[duplicated]['startTime'].nunique())} FUELHH half-hour(s) "
            f"appeared more than once ({int(conflicting)} with differing "
            "values); the most recently published value was kept."
        )
        df = (
            df.sort_values(["startTime", "publishTime"], na_position="first")
            .drop_duplicates("startTime", keep="last")
        )

    df["hour"] = df["startTime"].dt.floor("h")
    hourly = (
        df.groupby("hour", as_index=False)
        .agg(actual=("actual", "mean"), halfHours=("actual", "size"))
        .rename(columns={"hour": "startTime"})
        .sort_values("startTime")
        .reset_index(drop=True)
    )

    log_stage("actuals_hourly", periods=len(hourly))
    return hourly[ACTUAL_COLUMNS], notes


# ---------------------------------------------------------
# Historical wind forecasts
# ---------------------------------------------------------

FORECAST_COLUMNS = ["startTime", "publishTime", "forecast"]


async def fetch_forecasts(
    start: datetime,
    end: datetime,
    cutoff_hour: int,
    cutoff_minute: int,
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch every WINDFOR vintage that could be eligible for [start, end).

    The WINDFOR stream is filtered by publish time (bounds inclusive) and
    returns all forecast runs published in that window, each covering
    roughly the next three days hourly. Fetching from the earliest
    cutoff minus ``FORECAST_LOOKBACK`` to the latest cutoff guarantees
    the latest eligible vintage is included for every delivery period,
    whatever times Elexon actually published at.
    """

    first_day, last_day = _span_days(start, end)
    publish_from = decision_cutoff(first_day, cutoff_hour, cutoff_minute) - FORECAST_LOOKBACK
    publish_to = decision_cutoff(last_day, cutoff_hour, cutoff_minute)

    rows = await _fetch(
        "datasets/WINDFOR/stream",
        {
            "publishDateTimeFrom": _iso_utc(publish_from.to_pydatetime()),
            "publishDateTimeTo": _iso_utc(publish_to.to_pydatetime()),
        },
    )

    notes: list[str] = []
    empty = pd.DataFrame(columns=FORECAST_COLUMNS)

    if not rows:
        notes.append(
            "Elexon WINDFOR returned no forecasts published between "
            f"{_iso_utc(publish_from.to_pydatetime())} and "
            f"{_iso_utc(publish_to.to_pydatetime())}."
        )
        return empty, notes

    df = pd.DataFrame(rows)
    log_stage("forecasts_dataframe", rows=len(df))
    _require_fields(df, {"startTime", "publishTime", "generation"}, "WINDFOR")

    log_stage("forecasts_parse_timestamps", rows=len(df))
    df["startTime"] = pd.to_datetime(df["startTime"], utc=True, errors="coerce")
    df["publishTime"] = pd.to_datetime(df["publishTime"], utc=True, errors="coerce")
    df["forecast"] = pd.to_numeric(df["generation"], errors="coerce")

    invalid = df[FORECAST_COLUMNS].isna().any(axis=1)
    if invalid.any():
        notes.append(
            f"{int(invalid.sum())} WINDFOR record(s) had an unreadable "
            "timestamp or generation value and were discarded."
        )
    df = df[~invalid]

    # Never rely on the upstream filter alone for the no-look-ahead rule.
    df = df[df["publishTime"] <= publish_to]
    df = df[(df["startTime"] >= pd.Timestamp(start)) & (df["startTime"] < pd.Timestamp(end))]
    log_stage("forecasts_window_filtered", rows=len(df))

    duplicated = df.duplicated(["startTime", "publishTime"], keep=False)
    if duplicated.any():
        notes.append(
            f"{int(duplicated.sum())} duplicate WINDFOR record(s) for the "
            "same period and publish time were collapsed to one."
        )

    log_stage("forecasts_dedup_sort_start", rows=len(df))
    df = (
        df[FORECAST_COLUMNS]
        .drop_duplicates(subset=["startTime", "publishTime"], keep="last")
        .sort_values(["startTime", "publishTime"])
        .reset_index(drop=True)
    )

    log_stage(
        "forecasts_filtered",
        records=len(df),
        vintages=df["publishTime"].nunique(),
    )
    return df, notes


# ---------------------------------------------------------
# Metrics and analysis
# ---------------------------------------------------------

EMPTY_METRICS = {
    "mae_mw": None,
    "rmse_mw": None,
    "bias_mw": None,
    "wape_pct": None,
    "max_abs_error_mw": None,
    "mean_actual_mw": None,
    "mean_forecast_mw": None,
    "n_periods": 0,
}


def compute_metrics(forecast: Any, actual: Any) -> dict:
    """Accuracy metrics for paired forecast/actual values (MW).

    error = forecast - actual, so positive bias means overforecast.
    WAPE = sum(|error|) / sum(actual) * 100, unavailable if sum(actual) <= 0.
    """

    f = np.asarray(forecast, dtype=float)
    a = np.asarray(actual, dtype=float)
    if f.size == 0:
        return dict(EMPTY_METRICS)

    error = f - a
    absolute = np.abs(error)
    total_actual = float(a.sum())

    return {
        "mae_mw": round(float(absolute.mean()), 2),
        "rmse_mw": round(float(np.sqrt(np.mean(np.square(error)))), 2),
        "bias_mw": round(float(error.mean()), 2),
        "wape_pct": (
            round(float(absolute.sum() / total_actual * 100), 2)
            if total_actual > 0
            else None
        ),
        "max_abs_error_mw": round(float(absolute.max()), 2),
        "mean_actual_mw": round(float(a.mean()), 2),
        "mean_forecast_mw": round(float(f.mean()), 2),
        "n_periods": int(f.size),
    }


def _methodology(cutoff_hour: int, cutoff_minute: int) -> dict:
    return {
        "cutoff": (
            f"{cutoff_hour:02d}:{cutoff_minute:02d} UK local time "
            "(Europe/London) on the day before each delivery date"
        ),
        "selection": (
            "For each hourly delivery period, the WINDFOR forecast with the "
            "latest publishTime at or before the decision cutoff. Forecasts "
            "published after the cutoff are never used."
        ),
        "matching": (
            "Forecast startTime is matched exactly to the hourly mean of the "
            "two FUELHH half-hours starting in that hour. Hours with fewer "
            "than two half-hours of actual data are excluded from metrics."
        ),
        "timezone": (
            "Timestamps are UTC (ISO 8601). Delivery dates and cutoffs are UK "
            "local days; in January UK local time (GMT) equals UTC."
        ),
        "units": "MW (average generation over the period)",
        "metrics": {
            "mae": "mean(|forecast - actual|)",
            "rmse": "sqrt(mean((forecast - actual)^2))",
            "bias": "mean(forecast - actual); positive = overforecast, negative = underforecast",
            "wape": "sum(|forecast - actual|) / sum(actual) x 100; unavailable when sum(actual) is 0",
            "coverage": "matched periods / periods with complete actual data x 100",
        },
        "sources": {"actuals": ACTUALS_SOURCE, "forecasts": FORECAST_SOURCE},
    }


def _normalise_actuals(actuals: pd.DataFrame) -> pd.DataFrame:
    df = actuals.copy()
    if "halfHours" not in df:
        # Pre-aggregated hourly input (e.g. tests) is treated as complete.
        df["halfHours"] = 2
    if df.empty:
        return pd.DataFrame(columns=ACTUAL_COLUMNS).astype(
            {"startTime": "datetime64[ns, UTC]", "actual": float, "halfHours": float}
        )
    df["startTime"] = pd.to_datetime(df["startTime"], utc=True, errors="coerce")
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    df["halfHours"] = pd.to_numeric(df["halfHours"], errors="coerce")
    return (
        df.dropna(subset=["startTime", "actual"])
        .sort_values("startTime")
        .drop_duplicates(subset=["startTime"], keep="last")[ACTUAL_COLUMNS]
    )


def _normalise_forecasts(forecasts: pd.DataFrame) -> pd.DataFrame:
    if forecasts.empty:
        return pd.DataFrame(columns=FORECAST_COLUMNS).astype(
            {
                "startTime": "datetime64[ns, UTC]",
                "publishTime": "datetime64[ns, UTC]",
                "forecast": float,
            }
        )
    df = forecasts.copy()
    df["startTime"] = pd.to_datetime(df["startTime"], utc=True, errors="coerce")
    df["publishTime"] = pd.to_datetime(df["publishTime"], utc=True, errors="coerce")
    df["forecast"] = pd.to_numeric(df["forecast"], errors="coerce")
    return df.dropna(subset=FORECAST_COLUMNS)[FORECAST_COLUMNS]


def _fmt_mw(value: float) -> str:
    return f"{value:,.0f} MW"


def executive_summary(
    metrics: dict,
    coverage: dict,
    largest: list[dict],
    daily: list[dict],
    by_hour: list[dict],
) -> list[str]:
    """Plain-language findings derived only from the calculated results."""

    points: list[str] = []
    if not metrics["n_periods"]:
        points.append(
            "No forecast/actual pairs could be matched, so accuracy metrics "
            "are unavailable. See the data-quality warnings for the reason."
        )
        return points

    wape = metrics["wape_pct"]
    points.append(
        f"Across {metrics['n_periods']} matched hourly periods, the day-ahead "
        f"forecast had an MAE of {_fmt_mw(metrics['mae_mw'])}"
        + (f" (WAPE {wape:.1f}%)" if wape is not None else "")
        + f" against mean actual generation of {_fmt_mw(metrics['mean_actual_mw'])}."
    )

    bias = metrics["bias_mw"]
    if abs(bias) < 0.02 * max(metrics["mean_actual_mw"], 1):
        points.append(
            f"Bias was small ({bias:+,.0f} MW): over- and underforecasts "
            "largely offset on average."
        )
    else:
        direction = "overforecast" if bias > 0 else "underforecast"
        points.append(
            f"The forecast tended to {direction} wind generation, by "
            f"{abs(bias):,.0f} MW on average (bias {bias:+,.0f} MW)."
        )

    pct = coverage["forecast_coverage_pct"]
    points.append(
        f"Forecast coverage was {pct:.1f}% ({coverage['matched']} of "
        f"{coverage['actual_periods']} periods with complete actual data)."
        if pct is not None
        else "Coverage is unavailable because no complete actual periods were found."
    )

    if largest:
        top = largest[0]
        local = pd.Timestamp(top["startTime"]).tz_convert(UK_TZ)
        points.append(
            f"Largest miss: {local:%d %b %H:%M} UK time, forecast "
            f"{_fmt_mw(top['forecast'])} vs actual {_fmt_mw(top['actual'])} "
            f"(error {top['error']:+,.0f} MW)."
        )

    scored_days = [d for d in daily if d["metrics"]["mae_mw"] is not None]
    if len(scored_days) > 1:
        worst = max(scored_days, key=lambda d: d["metrics"]["mae_mw"])
        best = min(scored_days, key=lambda d: d["metrics"]["mae_mw"])
        points.append(
            f"Daily MAE ranged from {_fmt_mw(best['metrics']['mae_mw'])} "
            f"({best['date']}) to {_fmt_mw(worst['metrics']['mae_mw'])} "
            f"({worst['date']})."
        )
        if len(by_hour) >= 12:
            peak = max(by_hour, key=lambda h: h["mae_mw"])
            points.append(
                f"Errors were largest around {peak['hour']:02d}:00 UK time "
                f"(MAE {_fmt_mw(peak['mae_mw'])} for that hour of day)."
            )

    return points


def build_analysis(
    actuals: pd.DataFrame,
    forecasts: pd.DataFrame,
    cutoff_hour: int,
    cutoff_minute: int,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Match each hourly delivery period with its latest eligible forecast.

    If ``period_start``/``period_end`` are given, every hour in that UTC
    window is reported, so periods with no actual data are visible rather
    than silently dropped. Otherwise only the supplied actual periods are
    analysed.
    """

    warnings = list(notes or [])
    actuals = _normalise_actuals(actuals)
    forecasts = _normalise_forecasts(forecasts)

    if period_start is not None and period_end is not None:
        start_ts, end_ts = pd.Timestamp(period_start), pd.Timestamp(period_end)
        grid = pd.DataFrame({
            "startTime": pd.date_range(start_ts, end_ts, freq="h", inclusive="left", tz="UTC")
        })
        actuals = actuals[(actuals["startTime"] >= start_ts) & (actuals["startTime"] < end_ts)]
        table = grid.merge(actuals, on="startTime", how="left")
    else:
        table = actuals.reset_index(drop=True)

    log_stage(
        "analysis_merge_start",
        actual_rows=len(actuals),
        forecast_rows=len(forecasts),
        table_rows=len(table),
    )
    table["deliveryDate"] = [delivery_day_of(ts) for ts in table["startTime"]]
    cutoffs = {
        day: decision_cutoff(day, cutoff_hour, cutoff_minute)
        for day in set(table["deliveryDate"])
    }
    table["cutoffTime"] = pd.to_datetime(
        table["deliveryDate"].map(cutoffs), utc=True
    )

    # Latest vintage at or before each period's cutoff.
    log_stage("analysis_candidates_merge_start", forecast_rows=len(forecasts), table_rows=len(table))
    candidates = forecasts.merge(table[["startTime", "cutoffTime"]], on="startTime")
    candidates = candidates[candidates["publishTime"] <= candidates["cutoffTime"]]
    log_stage("analysis_candidates_filtered", candidates=len(candidates))
    chosen = (
        candidates.sort_values("publishTime")
        .drop_duplicates("startTime", keep="last")[FORECAST_COLUMNS]
    )
    table = table.merge(chosen, on="startTime", how="left").sort_values("startTime")

    has_actual = table["actual"].notna()
    complete = has_actual & (table["halfHours"] >= 2)
    has_forecast = table["forecast"].notna()
    matched_mask = complete & has_forecast

    table["status"] = np.select(
        [matched_mask, complete & ~has_forecast, has_actual & ~complete],
        ["matched", "missing_forecast", "incomplete_actual"],
        default="missing_actual",
    )
    table["error"] = np.where(matched_mask, table["forecast"] - table["actual"], np.nan)
    table["absoluteError"] = table["error"].abs()
    table["leadTimeHours"] = (
        (table["startTime"] - table["publishTime"]).dt.total_seconds() / 3600
    )

    matched = table[matched_mask]
    metrics = compute_metrics(matched["forecast"], matched["actual"])

    eligible = int(complete.sum())
    matched_count = int(matched_mask.sum())
    coverage = {
        "matched": matched_count,
        "actual_periods": eligible,
        "forecast_coverage_pct": (
            round(matched_count / eligible * 100, 1) if eligible else None
        ),
        "expected_periods": len(table),
        "missing_forecast": int((table["status"] == "missing_forecast").sum()),
        "missing_actual": int((table["status"] == "missing_actual").sum()),
        "incomplete_actual": int((table["status"] == "incomplete_actual").sum()),
    }

    if not len(table):
        warnings.append("No delivery periods were available to analyse.")
    elif not eligible:
        warnings.append(
            "No complete actual wind generation periods were found, so "
            "metrics and coverage are unavailable."
        )
    if coverage["missing_forecast"]:
        warnings.append(
            f"{coverage['missing_forecast']} period(s) with actual data had no "
            "forecast published at or before the cutoff and are excluded "
            "from metrics."
        )
    if coverage["missing_actual"] and eligible:
        warnings.append(
            f"{coverage['missing_actual']} period(s) had no actual generation data."
        )
    if coverage["incomplete_actual"]:
        warnings.append(
            f"{coverage['incomplete_actual']} hour(s) had only one of two "
            "half-hours of actual data and are excluded from metrics."
        )

    rows = [
        {
            "startTime": _iso_or_none(r.startTime),
            "deliveryDate": r.deliveryDate.isoformat(),
            "actual": None if pd.isna(r.actual) else round(float(r.actual), 1),
            "forecast": None if pd.isna(r.forecast) else round(float(r.forecast), 1),
            "publishTime": _iso_or_none(r.publishTime),
            "cutoffTime": _iso_or_none(r.cutoffTime),
            "leadTimeHours": None if pd.isna(r.leadTimeHours) else round(float(r.leadTimeHours), 1),
            "error": None if pd.isna(r.error) else round(float(r.error), 2),
            "absoluteError": None if pd.isna(r.absoluteError) else round(float(r.absoluteError), 2),
            "actualHalfHours": None if pd.isna(r.halfHours) else int(r.halfHours),
            "status": r.status,
        }
        for r in table.itertuples(index=False)
    ]

    largest = sorted(
        (row for row in rows if row["status"] == "matched"),
        key=lambda row: row["absoluteError"],
        reverse=True,
    )[:5]

    daily = []
    for day, group in table.groupby("deliveryDate", sort=True):
        day_complete = int(((group["actual"].notna()) & (group["halfHours"] >= 2)).sum())
        day_matched = group[group["status"] == "matched"]
        daily.append({
            "date": day.isoformat(),
            "metrics": compute_metrics(day_matched["forecast"], day_matched["actual"]),
            "matched": len(day_matched),
            "actual_periods": day_complete,
            "forecast_coverage_pct": (
                round(len(day_matched) / day_complete * 100, 1) if day_complete else None
            ),
        })

    by_hour = []
    if not matched.empty:
        local_hour = matched["startTime"].dt.tz_convert(UK_TZ).dt.hour
        for hour, group in matched.groupby(local_hour):
            by_hour.append({
                "hour": int(hour),
                "mae_mw": round(float(group["absoluteError"].mean()), 2),
                "bias_mw": round(float(group["error"].mean()), 2),
                "n_periods": len(group),
            })

    vintages = (
        matched.groupby("publishTime").size().sort_index()
        if not matched.empty
        else pd.Series(dtype=int)
    )

    logger.info(
        "Analysis coverage: matched=%s eligible=%s expected=%s",
        matched_count,
        eligible,
        len(table),
    )

    return {
        "rows": rows,
        "metrics": metrics,
        "coverage": coverage,
        "methodology": _methodology(cutoff_hour, cutoff_minute),
        "warnings": warnings,
        "largest_errors": largest,
        "daily": daily,
        "by_hour": by_hour,
        "vintages_used": [
            {"publishTime": _iso_or_none(ts), "periods": int(n)}
            for ts, n in vintages.items()
        ],
        "executive_summary": executive_summary(metrics, coverage, largest, daily, by_hour),
    }


# ---------------------------------------------------------
# API routes
# ---------------------------------------------------------

def _require_assignment_date(value: date) -> None:
    if not ASSIGNMENT_FIRST_DAY <= value <= ASSIGNMENT_LAST_DAY:
        raise HTTPException(
            status_code=422,
            detail=(
                "Delivery date must be within January 2026 "
                f"({ASSIGNMENT_FIRST_DAY.isoformat()} to "
                f"{ASSIGNMENT_LAST_DAY.isoformat()})."
            ),
        )


async def _analyse_days(
    first_day: date,
    last_day: date,
    cutoff_hour: int,
    cutoff_minute: int,
) -> dict:
    start, _ = local_day_bounds(first_day)
    _, end = local_day_bounds(last_day)
    log_stage(
        "analysis_start",
        first_day=first_day.isoformat(),
        last_day=last_day.isoformat(),
        cutoff=f"{cutoff_hour:02d}:{cutoff_minute:02d}",
    )

    try:
        (actuals, actual_notes), (forecasts, forecast_notes) = await asyncio.gather(
            fetch_actuals(start, end),
            fetch_forecasts(start, end, cutoff_hour, cutoff_minute),
        )
    except httpx.TimeoutException as exc:
        logger.warning("Elexon API timed out: %s", exc)
        raise HTTPException(
            status_code=504,
            detail="Elexon API timed out. Please retry shortly.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("Elexon returned HTTP %s", exc.response.status_code)
        raise HTTPException(
            status_code=502,
            detail=(
                f"Elexon API returned HTTP {exc.response.status_code} "
                f"for {exc.request.url.path}"
            ),
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Elexon API request failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Elexon API request failed: {exc.__class__.__name__}",
        ) from exc
    except UpstreamDataError as exc:
        logger.warning("Unusable Elexon response: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not process Elexon response: {exc}",
        ) from exc

    log_stage("fetch_complete", actual_periods=len(actuals), forecast_records=len(forecasts))
    result = build_analysis(
        actuals,
        forecasts,
        cutoff_hour,
        cutoff_minute,
        period_start=start,
        period_end=end,
        notes=actual_notes + forecast_notes,
    )
    log_stage("analysis_built", rows=len(result["rows"]), matched=result["coverage"]["matched"])
    result["period"] = {
        "first_delivery_date": first_day.isoformat(),
        "last_delivery_date": last_day.isoformat(),
        "start_utc": _iso_utc(start),
        "end_utc": _iso_utc(end),
        "timezone": "Europe/London",
    }
    result["cutoff"] = {"hour": cutoff_hour, "minute": cutoff_minute}
    result["source"] = {
        "actuals": ACTUALS_SOURCE,
        "forecasts": FORECAST_SOURCE,
        "base_url": BASE_URL,
        "retrieved_at": _iso_utc(datetime.now(timezone.utc)),
    }
    return result


CutoffHour = Query(11, ge=0, le=23, description="Decision cutoff hour, UK local time, day before delivery")
CutoffMinute = Query(0, ge=0, le=59, description="Decision cutoff minute")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "windwise",
        "version": app.version,
        "analysis_window": {
            "first_delivery_date": ASSIGNMENT_FIRST_DAY.isoformat(),
            "last_delivery_date": ASSIGNMENT_LAST_DAY.isoformat(),
        },
    }


@app.get("/api/analysis")
async def analysis(
    date_value: date = Query(
        date(2026, 1, 15),
        alias="date",
        description="UK local delivery date in January 2026 (YYYY-MM-DD)",
    ),
    cutoff_hour: int = CutoffHour,
    cutoff_minute: int = CutoffMinute,
):
    """Hourly forecast-vs-actual analysis for one delivery date."""

    _require_assignment_date(date_value)
    result = await _analyse_days(date_value, date_value, cutoff_hour, cutoff_minute)
    result["date"] = date_value.isoformat()
    result["scope"] = "day"
    return result


@app.get("/api/summary")
async def summary(
    cutoff_hour: int = CutoffHour,
    cutoff_minute: int = CutoffMinute,
):
    """Analysis for all of January 2026, with daily and hour-of-day breakdowns."""

    result = await _analyse_days(
        ASSIGNMENT_FIRST_DAY,
        ASSIGNMENT_LAST_DAY,
        cutoff_hour,
        cutoff_minute,
    )
    result["date"] = "2026-01"
    result["scope"] = "month"
    return result
