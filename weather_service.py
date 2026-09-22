"""Weather data access for the AVATAR campus load forecast.

This module keeps the weather provider separate from the forecasting model.  It
can retrieve hourly forecast or historical weather from Open-Meteo, but the
rest of AVATAR only sees a small, consistent pandas DataFrame.  A later provider
or a Facilities-supplied CSV can therefore be substituted without rewriting the
load model.

Run this file directly for a safe local connection test::

    python weather_service.py
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


DEFAULT_LATITUDE = 40.0379
DEFAULT_LONGITUDE = -75.3433
DEFAULT_TIMEZONE = "America/New_York"

FORECAST_API_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_API_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
)


class WeatherServiceError(RuntimeError):
    """Raised when valid weather data cannot be retrieved or parsed."""


@dataclass(frozen=True)
class WeatherConfig:
    """Location and connection settings used by the weather provider."""

    latitude: float = DEFAULT_LATITUDE
    longitude: float = DEFAULT_LONGITUDE
    timezone_name: str = DEFAULT_TIMEZONE
    timeout_seconds: int = 20
    cache_hours: int = 3

    @classmethod
    def from_environment(cls) -> "WeatherConfig":
        """Allow deployment settings to override defaults without code edits."""
        return cls(
            latitude=float(os.getenv("AVATAR_LATITUDE", str(DEFAULT_LATITUDE))),
            longitude=float(os.getenv("AVATAR_LONGITUDE", str(DEFAULT_LONGITUDE))),
            timezone_name=os.getenv("AVATAR_TIMEZONE", DEFAULT_TIMEZONE),
            timeout_seconds=int(os.getenv("AVATAR_WEATHER_TIMEOUT", "20")),
            cache_hours=int(os.getenv("AVATAR_WEATHER_CACHE_HOURS", "3")),
        )


@dataclass
class WeatherResult:
    """Normalized hourly weather plus provenance for the website/API."""

    hourly: pd.DataFrame
    provider: str
    dataset: str
    retrieved_at_utc: str
    from_cache: bool


def _cache_directory() -> Path:
    configured = os.getenv("AVATAR_WEATHER_CACHE_DIR")
    root = Path(configured) if configured else Path(tempfile.gettempdir())
    path = root / "avatar_weather_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return _cache_directory() / f"{digest}.json"


def _read_cached_payload(url: str, maximum_age: timedelta | None) -> dict[str, Any] | None:
    path = _cache_path(url)
    if not path.exists():
        return None
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(wrapper["saved_at_utc"])
        if maximum_age is not None:
            age = datetime.now(timezone.utc) - saved_at
            if age > maximum_age:
                return None
        payload = wrapper["payload"]
        return payload if isinstance(payload, dict) else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return None


def _write_cached_payload(url: str, payload: dict[str, Any]) -> None:
    wrapper = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    try:
        _cache_path(url).write_text(
            json.dumps(wrapper, separators=(",", ":")), encoding="utf-8"
        )
    except OSError:
        # A read-only or ephemeral host should not make the live request fail.
        pass


def _request_payload(
    base_url: str,
    parameters: dict[str, object],
    config: WeatherConfig,
) -> tuple[dict[str, Any], bool]:
    url = f"{base_url}?{urlencode(parameters)}"
    fresh_cache = _read_cached_payload(
        url, timedelta(hours=max(0, config.cache_hours))
    )
    if fresh_cache is not None:
        return fresh_cache, True

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AVATAR-Villanova-Energy-Research/1.0",
        },
    )
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise WeatherServiceError("The weather provider returned an invalid response.")
        if payload.get("error"):
            raise WeatherServiceError(str(payload.get("reason", "Weather request failed.")))
        _write_cached_payload(url, payload)
        return payload, False
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        # If a previously successful result exists, prefer it to breaking AVATAR.
        stale_cache = _read_cached_payload(url, maximum_age=None)
        if stale_cache is not None:
            return stale_cache, True
        raise WeatherServiceError(
            "Weather data could not be reached and no cached result is available."
        ) from exc


def _parse_hourly_payload(
    payload: dict[str, Any],
    config: WeatherConfig,
    *,
    dataset: str,
    from_cache: bool,
) -> WeatherResult:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise WeatherServiceError("The weather response did not contain hourly data.")

    timestamps = pd.to_datetime(hourly["time"], errors="coerce")
    if timestamps.isna().any():
        raise WeatherServiceError("The weather response contained invalid timestamps.")
    if timestamps.tz is None:
        timestamps = timestamps.tz_localize(
            config.timezone_name, ambiguous="NaT", nonexistent="shift_forward"
        )
    else:
        timestamps = timestamps.tz_convert(config.timezone_name)

    source_columns = {
        "temperature_2m": "temp_f",
        "relative_humidity_2m": "humidity_pct",
        "apparent_temperature": "apparent_temp_f",
        "precipitation": "precipitation_in",
    }
    frame = pd.DataFrame(index=timestamps)
    for source_name, output_name in source_columns.items():
        values = hourly.get(source_name)
        if not isinstance(values, list) or len(values) != len(frame.index):
            raise WeatherServiceError(
                f"The weather response is missing a complete {source_name} series."
            )
        frame[output_name] = pd.to_numeric(values, errors="coerce")

    frame = frame[~frame.index.isna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if frame.empty or frame.isna().all(axis=None):
        raise WeatherServiceError("No usable hourly weather values were returned.")
    frame.index.name = "timestamp"

    return WeatherResult(
        hourly=frame,
        provider="Open-Meteo",
        dataset=dataset,
        retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
        from_cache=from_cache,
    )


def _base_parameters(config: WeatherConfig) -> dict[str, object]:
    return {
        "latitude": config.latitude,
        "longitude": config.longitude,
        "hourly": ",".join(HOURLY_VARIABLES),
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "timezone": config.timezone_name,
    }


def fetch_forecast_weather(
    forecast_days: int = 2,
    config: WeatherConfig | None = None,
) -> WeatherResult:
    """Retrieve hourly weather for the next one to sixteen days."""
    if not 1 <= forecast_days <= 16:
        raise ValueError("forecast_days must be between 1 and 16.")
    config = config or WeatherConfig.from_environment()
    parameters = _base_parameters(config)
    parameters["forecast_days"] = forecast_days
    payload, from_cache = _request_payload(FORECAST_API_URL, parameters, config)
    return _parse_hourly_payload(
        payload, config, dataset="forecast", from_cache=from_cache
    )


def fetch_historical_weather(
    start_date: str | date,
    end_date: str | date,
    config: WeatherConfig | None = None,
) -> WeatherResult:
    """Retrieve hourly historical weather for model training."""
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    config = config or WeatherConfig.from_environment()
    parameters = _base_parameters(config)
    parameters.update({"start_date": start.isoformat(), "end_date": end.isoformat()})
    payload, from_cache = _request_payload(HISTORICAL_API_URL, parameters, config)
    return _parse_hourly_payload(
        payload, config, dataset="historical", from_cache=from_cache
    )


def main() -> None:
    """Print a compact connection test without modifying project files."""
    result = fetch_forecast_weather(forecast_days=2)
    next_24 = result.hourly.head(24)
    print("AVATAR weather connection successful")
    print(f"Provider: {result.provider}")
    print(f"Rows received: {len(result.hourly)}")
    print(f"Using cached response: {'yes' if result.from_cache else 'no'}")
    print("\nNext 24 hourly values:")
    print(next_24.to_string())


if __name__ == "__main__":
    main()
