"""Single source of truth for AVATAR's public day-ahead forecast payload."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from avatar_load_forecasting import create_proxy_forecast
from forecast_contract import EASTERN, LOCATION, UTC, validate_forecast
from weather_load_model import (
    WeatherLoadModelError,
    create_weather_adjusted_forecast,
    select_weather_day,
)
from weather_service import (
    WeatherConfig,
    WeatherResult,
    WeatherServiceError,
    fetch_forecast_weather,
)


BASELINE_DAILY_ENERGY_MWH = 104.0


def _weather_payload(
    result: WeatherResult,
    forecast_date: str,
    applied_to_demand: bool,
) -> dict[str, Any]:
    day = select_weather_day(result.hourly, forecast_date)
    return {
        "status": "available",
        "provider": result.provider,
        "dataset": result.dataset,
        "forecast_date": forecast_date,
        "retrieved_at_utc": result.retrieved_at_utc,
        "from_cache": result.from_cache,
        "applied_to_demand": applied_to_demand,
        "temperature_low_f": round(float(day["temp_f"].min()), 1),
        "temperature_high_f": round(float(day["temp_f"].max()), 1),
        "average_humidity_pct": round(float(day["humidity_pct"].mean()), 1),
        "precipitation_total_in": round(float(day["precipitation_in"].sum()), 3),
        "snowfall_total_in": round(float(day["snowfall_in"].sum()), 3),
        "average_wind_speed_mph": round(float(day["wind_speed_mph"].mean()), 1),
        "hourly": [
            {
                "timestamp": timestamp.isoformat(),
                "label": timestamp.strftime("%I %p").lstrip("0"),
                "temp_f": round(float(row["temp_f"]), 1),
                "humidity_pct": round(float(row["humidity_pct"]), 1),
                "apparent_temp_f": round(float(row["apparent_temp_f"]), 1),
                "precipitation_in": round(float(row["precipitation_in"]), 3),
                "snowfall_in": round(float(row["snowfall_in"]), 3),
                "wind_speed_mph": round(float(row["wind_speed_mph"]), 1),
            }
            for timestamp, row in day.iterrows()
        ],
        "note": (
            "Tomorrow's weather is applied to both the forecast daily energy and "
            "the hourly demand shape."
            if applied_to_demand
            else "Weather is available, but the static load fallback is currently in use."
        ),
    }


def build_day_ahead_forecast(
    *,
    now: datetime | None = None,
    forecast_date: str | None = None,
    baseline_daily_energy_mwh: float = BASELINE_DAILY_ENERGY_MWH,
    weather_result: WeatherResult | None = None,
) -> dict[str, Any]:
    """Build the shared Load Forecasting and What-If baseline payload."""
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    config = WeatherConfig.from_environment()
    if forecast_date is None:
        target = now_utc.astimezone(ZoneInfo(config.timezone_name)).date() + timedelta(days=1)
        forecast_date = target.isoformat()
    else:
        target = datetime.fromisoformat(forecast_date).date()

    fallback_used = False
    fallback_reason: str | None = None
    weather: dict[str, Any]
    try:
        result = weather_result or fetch_forecast_weather(
            forecast_days=3, config=config
        )
        try:
            forecast_result = create_weather_adjusted_forecast(
                result.hourly,
                forecast_date,
                baseline_daily_energy_mwh=baseline_daily_energy_mwh,
                timezone=config.timezone_name,
            )
            weather = _weather_payload(result, forecast_date, True)
        except (WeatherLoadModelError, ValueError, KeyError) as exc:
            fallback_used = True
            fallback_reason = f"weather_model_unavailable: {type(exc).__name__}"
            forecast_result = create_proxy_forecast(
                daily_energy_mwh=baseline_daily_energy_mwh,
                forecast_date=forecast_date,
                timezone=config.timezone_name,
            )
            weather = _weather_payload(result, forecast_date, False)
    except (WeatherServiceError, ValueError, KeyError) as exc:
        fallback_used = True
        fallback_reason = f"weather_unavailable: {type(exc).__name__}"
        forecast_result = create_proxy_forecast(
            daily_energy_mwh=baseline_daily_energy_mwh,
            forecast_date=forecast_date,
            timezone=config.timezone_name,
        )
        weather = {
            "status": "unavailable",
            "provider": "Open-Meteo",
            "forecast_date": forecast_date,
            "applied_to_demand": False,
            "hourly": [],
            "message": (
                "Tomorrow's weather is temporarily unavailable. The Bartley-based "
                "104 MWh static fallback remains available."
            ),
        }

    hourly: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for timestamp, row in forecast_result.forecast.iterrows():
        start = timestamp.to_pydatetime()
        end = (start.astimezone(timezone.utc) + timedelta(hours=1)).astimezone(EASTERN)
        value = float(row["forecast_kw"])
        label = start.strftime("%I %p").lstrip("0")
        hourly.append(
            {
                "timestamp": start.isoformat(),
                "hour": int(start.hour),
                "label": label,
                "forecast_kw": round(value, 2),
            }
        )
        predictions.append(
            {
                "interval_start": start.isoformat(),
                "interval_end": end.isoformat(),
                "value": round(value, 2),
            }
        )
    peak = max(hourly, key=lambda item: item["forecast_kw"])
    total = sum(item["forecast_kw"] for item in hourly)
    summary = forecast_result.summary
    applied = bool(weather.get("applied_to_demand"))
    payload = {
        "status": "success",
        "forecast_type": "trained_model" if applied else "temporary_proxy",
        "location_id": LOCATION,
        "unit": "kW",
        "timezone": config.timezone_name,
        "generated_at": now_utc.isoformat(),
        "generated_at_utc": now_utc.isoformat(),
        "valid_until": predictions[-1]["interval_end"],
        "forecast_date": target.isoformat(),
        "predictions": predictions,
        "hourly": hourly,
        "weather": weather,
        "weather_applied_to_demand": applied,
        "method": summary["method"],
        "warning": summary["warning"],
        "shape_source": summary["shape_source"],
        "shape_data_start": summary["shape_data_start"],
        "shape_data_end": summary["shape_data_end"],
        "shape_day_type": summary["shape_day_type"],
        "baseline_daily_energy_mwh": baseline_daily_energy_mwh,
        "weather_multiplier": summary.get("weather_multiplier", 1.0),
        "weather_impact_pct": summary.get("weather_impact_pct", 0.0),
        "daily_energy_kwh": round(total, 1),
        "daily_energy_mwh": round(total / 1000, 3),
        "average_load_kw": round(total / len(hourly), 1),
        "peak_load_kw": round(float(peak["forecast_kw"]), 1),
        "peak_hour": peak["hour"],
        "peak_label": peak["label"],
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
    }
    validate_forecast(payload, now_utc)
    return payload


def hourly_for_what_if(payload: dict[str, Any]) -> list[float]:
    """Convert a 23/24/25-hour forecast into the calculator's 24 civil hours."""
    rows = payload.get("hourly", [])
    grouped: dict[int, list[float]] = {hour: [] for hour in range(24)}
    for row in rows:
        hour = int(row["hour"])
        grouped[hour].append(float(row["forecast_kw"]))
    values = np.array(
        [np.mean(grouped[hour]) if grouped[hour] else np.nan for hour in range(24)],
        dtype=float,
    )
    if np.isnan(values).any():
        values = pd.Series(values).interpolate(limit_direction="both").to_numpy()
    target_energy = float(payload["daily_energy_kwh"])
    if values.sum() <= 0:
        raise ValueError("Forecast baseline has no usable energy.")
    values *= target_energy / values.sum()
    return [float(value) for value in values]
