"""Weather-responsive Bartley proxy for AVATAR's day-ahead campus forecast.

The trained artifact contains only statistical models and normalized Bartley
relationships. Raw Facilities meter readings are deliberately kept outside the
repository. The 104 MWh value is treated as Villanova's average-day anchor;
tomorrow's weather can move the forecast above or below that anchor.
"""

from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn

from avatar_load_forecasting import ForecastResult


MODEL_VERSION = 1
DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parent / "models" / "bartley_weather_models.joblib"
)


class WeatherLoadModelError(RuntimeError):
    """Raised when the weather model cannot safely produce a forecast."""


def _required_column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        raise WeatherLoadModelError(f"Weather data is missing '{name}'.")
    values = pd.to_numeric(frame[name], errors="coerce")
    if values.isna().all():
        raise WeatherLoadModelError(f"Weather data has no usable '{name}' values.")
    return values.interpolate(limit_direction="both")


def select_weather_day(
    weather_hourly: pd.DataFrame, forecast_date: str | pd.Timestamp
) -> pd.DataFrame:
    """Return one local calendar day, preserving 23/25-hour DST days."""
    if not isinstance(weather_hourly.index, pd.DatetimeIndex):
        raise WeatherLoadModelError("Weather data needs a DatetimeIndex.")
    target = pd.Timestamp(forecast_date).date()
    day = weather_hourly[weather_hourly.index.date == target].copy().sort_index()
    if len(day) not in {23, 24, 25}:
        raise WeatherLoadModelError(
            f"Expected 23, 24, or 25 weather hours for {target}; found {len(day)}."
        )
    return day


def daily_weather_features(
    weather_day: pd.DataFrame, forecast_date: str | pd.Timestamp
) -> dict[str, float]:
    """Create explainable daily weather and calendar features."""
    target = pd.Timestamp(forecast_date)
    temp = _required_column(weather_day, "temp_f")
    apparent = _required_column(weather_day, "apparent_temp_f")
    humidity = _required_column(weather_day, "humidity_pct")
    precipitation = _required_column(weather_day, "precipitation_in").clip(lower=0)
    snowfall = _required_column(weather_day, "snowfall_in").clip(lower=0)
    wind = _required_column(weather_day, "wind_speed_mph").clip(lower=0)
    day_of_week = target.dayofweek
    month = target.month
    day_of_year = target.dayofyear
    return {
        "temp_mean_f": float(temp.mean()),
        "temp_min_f": float(temp.min()),
        "temp_max_f": float(temp.max()),
        "apparent_mean_f": float(apparent.mean()),
        "apparent_min_f": float(apparent.min()),
        "apparent_max_f": float(apparent.max()),
        "heating_degree_f": float((65.0 - apparent).clip(lower=0).mean()),
        "cooling_degree_f": float((apparent - 65.0).clip(lower=0).mean()),
        "humidity_mean_pct": float(humidity.mean()),
        "precipitation_total_in": float(precipitation.sum()),
        "precipitation_hours": float((precipitation > 0).sum()),
        "snowfall_total_in": float(snowfall.sum()),
        "snow_hours": float((snowfall > 0).sum()),
        "wind_mean_mph": float(wind.mean()),
        "wind_max_mph": float(wind.max()),
        "dow_sin": math.sin(2 * math.pi * day_of_week / 7),
        "dow_cos": math.cos(2 * math.pi * day_of_week / 7),
        "is_weekend": float(day_of_week >= 5),
        "month_sin": math.sin(2 * math.pi * (month - 1) / 12),
        "month_cos": math.cos(2 * math.pi * (month - 1) / 12),
        "year_sin": math.sin(2 * math.pi * (day_of_year - 1) / 365.25),
        "year_cos": math.cos(2 * math.pi * (day_of_year - 1) / 365.25),
    }


def hourly_weather_features(
    weather_day: pd.DataFrame,
    forecast_date: str | pd.Timestamp,
    daily: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Build hourly features for the intraday-shape model."""
    daily = daily or daily_weather_features(weather_day, forecast_date)
    frame = pd.DataFrame(index=weather_day.index)
    hour = frame.index.hour + frame.index.minute / 60
    frame["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    frame["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    for name in (
        "temp_f",
        "apparent_temp_f",
        "humidity_pct",
        "precipitation_in",
        "snowfall_in",
        "wind_speed_mph",
    ):
        frame[name] = _required_column(weather_day, name).to_numpy()
    frame["temp_lag_1h_f"] = frame["temp_f"].shift(1).bfill()
    frame["temp_lag_3h_f"] = frame["temp_f"].shift(3).bfill()
    for name in (
        "heating_degree_f",
        "cooling_degree_f",
        "precipitation_total_in",
        "snowfall_total_in",
        "dow_sin",
        "dow_cos",
        "is_weekend",
        "month_sin",
        "month_cos",
        "year_sin",
        "year_cos",
    ):
        frame[name] = daily[name]
    return frame


@lru_cache(maxsize=2)
def load_model_artifact(path: str = str(DEFAULT_MODEL_PATH)) -> dict[str, Any]:
    model_path = Path(path)
    if not model_path.exists():
        raise WeatherLoadModelError(f"Weather model artifact not found: {model_path}")
    try:
        artifact = joblib.load(model_path)
    except Exception as exc:
        raise WeatherLoadModelError("Weather model artifact could not be loaded.") from exc
    if artifact.get("model_version") != MODEL_VERSION:
        raise WeatherLoadModelError("Weather model artifact version is incompatible.")
    trained_version = str(artifact.get("sklearn_version", ""))
    if trained_version.split(".")[:2] != sklearn.__version__.split(".")[:2]:
        raise WeatherLoadModelError(
            "Weather model artifact requires a different scikit-learn version."
        )
    return artifact


def create_weather_adjusted_forecast(
    weather_hourly: pd.DataFrame,
    forecast_date: str,
    baseline_daily_energy_mwh: float = 104.0,
    model_path: str | Path = DEFAULT_MODEL_PATH,
    timezone: str = "America/New_York",
) -> ForecastResult:
    """Forecast tomorrow's total energy and hourly demand from weather."""
    if baseline_daily_energy_mwh <= 0:
        raise ValueError("baseline_daily_energy_mwh must be greater than zero.")
    artifact = load_model_artifact(str(Path(model_path).resolve()))
    weather_day = select_weather_day(weather_hourly, forecast_date)
    daily = daily_weather_features(weather_day, forecast_date)
    daily_row = pd.DataFrame([daily]).reindex(columns=artifact["daily_features"])
    multiplier = float(artifact["daily_model"].predict(daily_row)[0])
    lower, upper = artifact["daily_multiplier_bounds"]
    multiplier = float(np.clip(multiplier, lower, upper))
    forecast_energy_mwh = baseline_daily_energy_mwh * multiplier

    hourly_frame = hourly_weather_features(weather_day, forecast_date, daily)
    raw_share = artifact["shape_model"].predict(
        hourly_frame.reindex(columns=artifact["shape_features"])
    )
    raw_share = np.clip(np.asarray(raw_share, dtype=float), 1e-8, None)
    normalized_share = raw_share / raw_share.sum()
    forecast_mw = forecast_energy_mwh * normalized_share
    forecast = pd.DataFrame(
        {"forecast_mw": forecast_mw, "forecast_kw": forecast_mw * 1000},
        index=weather_day.index,
    )
    forecast.index.name = "timestamp"
    peak_time = forecast["forecast_mw"].idxmax()
    metadata = artifact["metadata"]
    summary: dict[str, Any] = {
        "method": "weather_adjusted_bartley_proxy_v1",
        "forecast_status": "weather_adjusted_proxy_not_campus_trained_model",
        "shape_source": "Bartley Hall SEL present-demand data with Villanova weather",
        "shape_data_start": metadata["load_data_start"],
        "shape_data_end": metadata["load_data_end"],
        "shape_day_type": "weekend" if pd.Timestamp(forecast_date).dayofweek >= 5 else "weekday",
        "baseline_daily_energy_mwh": round(float(baseline_daily_energy_mwh), 3),
        "weather_multiplier": round(multiplier, 5),
        "weather_impact_pct": round((multiplier - 1.0) * 100, 2),
        "forecast_energy_mwh": round(float(forecast["forecast_mw"].sum()), 3),
        "forecast_energy_kwh": round(float(forecast["forecast_kw"].sum()), 1),
        "average_load_mw": round(float(forecast["forecast_mw"].mean()), 4),
        "average_load_kw": round(float(forecast["forecast_kw"].mean()), 1),
        "peak_load_mw": round(float(forecast["forecast_mw"].max()), 4),
        "peak_load_kw": round(float(forecast["forecast_kw"].max()), 1),
        "peak_time": peak_time.isoformat(),
        "forecast_start": forecast.index.min().isoformat(),
        "forecast_end": forecast.index.max().isoformat(),
        "what_if_input_mw": [round(float(x), 5) for x in forecast["forecast_mw"]],
        "what_if_input_kw": [round(float(x), 2) for x in forecast["forecast_kw"]],
        "validation": metadata["validation"],
        "warning": (
            "This weather-responsive profile uses all presently available campus "
            "load data. The observed weather relationship is transferred to "
            f"Villanova's {baseline_daily_energy_mwh:g} MWh average-day campus baseline."
        ),
    }
    return ForecastResult(forecast, metadata["validation"], summary, None)
