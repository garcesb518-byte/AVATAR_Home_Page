

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_TARGET_METER = "Villanova Campus Load"

# Values shown in the Bryn Mawr town/utility-loop dashboard supplied on 2026-09-15.
# They must never be treated as Villanova consumption. They can validate a town
# export that is used solely to derive a normalized reference shape.
BRYN_MAWR_REFERENCE = {
    "start": "2026-07-01 00:00",
    "end": "2026-08-05 23:59:59",
    "meters": {
        "Bryan Mawr 133": {
            "usage_mwh": 4205.34,
            "peak_mw": 7.78,
            "peak_time": "2026-07-02 14:55",
        },
        "Bryan Mawr 148": {
            "usage_mwh": 553.60,
            "peak_mw": 3.16,
            "peak_time": "2026-08-03 15:10",
        },
        "Total Campus Loop": {
            "usage_mwh": 5023.92,
            "peak_mw": 8.14,
            "peak_time": "2026-07-15 15:15",
        },
    },
}

# Approximate hourly MW shape inferred from the supplied Bryn Mawr screenshot.
# Hours 00:00-15:00 follow the visible curve; 16:00-23:00 are explicitly
# speculative because the screenshot ends at about 3 PM. Only the ratios matter:
# create_proxy_forecast() normalizes these values to the campus daily-energy input.
BRYN_MAWR_PROXY_SHAPE = np.array(
    [
        5.65,
        5.56,
        5.53,
        5.52,
        5.54,
        5.57,
        5.64,
        5.82,
        6.08,
        6.35,
        6.62,
        6.88,
        7.10,
        7.32,
        7.50,
        7.42,
        7.28,
        7.12,
        6.95,
        6.76,
        6.52,
        6.26,
        6.00,
        5.80,
    ],
    dtype=float,
)


@dataclass
class ForecastResult:
    forecast: pd.DataFrame
    metrics: dict[str, float]
    summary: dict[str, object]
    model: HistGradientBoostingRegressor | None


def _normalized_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _find_column(columns: Iterable[object], aliases: Iterable[str]) -> object | None:
    lookup = {_normalized_name(column): column for column in columns}
    for alias in aliases:
        if _normalized_name(alias) in lookup:
            return lookup[_normalized_name(alias)]
    return None


def _read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        return pd.read_csv(path)
    raise ValueError("Input must be a CSV, TXT, XLS, or XLSX file.")


def _parse_timestamp(df: pd.DataFrame) -> pd.Series:
    timestamp_col = _find_column(
        df.columns,
        ["timestamp", "date time", "datetime", "interval end", "interval start"],
    )
    if timestamp_col is not None:
        return pd.to_datetime(df[timestamp_col], errors="coerce")

    date_col = _find_column(df.columns, ["date", "day"])
    time_col = _find_column(df.columns, ["time", "interval time"])
    if date_col is not None and time_col is not None:
        return pd.to_datetime(
            df[date_col].astype(str) + " " + df[time_col].astype(str),
            errors="coerce",
        )
    raise ValueError(
        "No timestamp was found. Supply a Timestamp/DateTime column, or separate "
        "Date and Time columns."
    )


def _apply_timezone(timestamps: pd.Series, timezone: str) -> pd.Series:
    valid = timestamps.dropna()
    if valid.empty:
        return timestamps
    if valid.dt.tz is None:
        # 'NaT' handles the repeated fall-DST hour without silently duplicating it.
        return timestamps.dt.tz_localize(
            timezone, ambiguous="NaT", nonexistent="shift_forward"
        )
    return timestamps.dt.tz_convert(timezone)


def _unit_multiplier_to_mw(unit: str) -> float:
    unit = str(unit).strip().lower().replace(" ", "")
    factors = {"w": 1e-6, "kw": 1e-3, "mw": 1.0}
    if unit not in factors:
        raise ValueError(
            f"Unsupported demand unit '{unit}'. Use W, kW, or MW. Energy units "
            "(Wh/kWh/MWh) should not be used for the forecast target export."
        )
    return factors[unit]


def normalize_load_export(
    path: str | Path,
    default_unit: str = "MW",
    timezone: str = DEFAULT_TIMEZONE,
) -> pd.DataFrame:
    """Return facilities data in long form: timestamp, meter, demand_mw.

    Supported layouts:
      1. Long: Timestamp, Meter, Value/Demand, Unit (Unit is optional)
      2. Wide: Timestamp followed by one column per meter
    """
    raw = _read_table(path)
    timestamps = _apply_timezone(_parse_timestamp(raw), timezone)
    meter_col = _find_column(raw.columns, ["meter", "meter name", "series"])
    value_col = _find_column(
        raw.columns, ["value", "demand", "demand mw", "reading", "usage"]
    )
    unit_col = _find_column(raw.columns, ["unit", "uom", "units"])

    if meter_col is not None and value_col is not None:
        units = (
            raw[unit_col].fillna(default_unit).astype(str)
            if unit_col is not None
            else pd.Series(default_unit, index=raw.index)
        )
        multipliers = units.map(_unit_multiplier_to_mw)
        result = pd.DataFrame(
            {
                "timestamp": timestamps,
                "meter": raw[meter_col].astype(str).str.strip(),
                "demand_mw": pd.to_numeric(raw[value_col], errors="coerce")
                * multipliers,
            }
        )
    else:
        excluded = {
            column
            for column in raw.columns
            if column
            in {
                _find_column(
                    raw.columns,
                    [
                        "timestamp",
                        "date time",
                        "datetime",
                        "interval end",
                        "interval start",
                    ],
                ),
                _find_column(raw.columns, ["date", "day"]),
                _find_column(raw.columns, ["time", "interval time"]),
                unit_col,
            }
        }
        value_columns = [column for column in raw.columns if column not in excluded]
        if not value_columns:
            raise ValueError("No meter-value columns were found in the export.")
        wide = raw[value_columns].apply(pd.to_numeric, errors="coerce")
        wide.insert(0, "timestamp", timestamps)
        result = wide.melt(
            id_vars="timestamp", var_name="meter", value_name="raw_demand"
        )

        parsed_units: list[str] = []
        clean_names: list[str] = []
        for name in result["meter"].astype(str):
            match = re.search(r"[\[(]\s*(MW|kW|W)\s*[\])]", name, re.I)
            parsed_units.append(match.group(1) if match else default_unit)
            clean_names.append(
                re.sub(r"\s*[\[(]\s*(MW|kW|W)\s*[\])]\s*", "", name, flags=re.I)
            )
        result["meter"] = clean_names
        result["demand_mw"] = pd.to_numeric(
            result["raw_demand"], errors="coerce"
        ) * pd.Series(parsed_units, index=result.index).map(_unit_multiplier_to_mw)
        result = result.drop(columns="raw_demand")

    result = result.dropna(subset=["timestamp", "meter", "demand_mw"])
    result = result[result["demand_mw"] >= 0].copy()
    result = (
        result.groupby(["timestamp", "meter"], as_index=False)["demand_mw"]
        .mean()
        .sort_values(["meter", "timestamp"])
    )
    if result.empty:
        raise ValueError("No valid nonnegative demand readings remained after cleaning.")
    return result


def select_hourly_target(load_long: pd.DataFrame, target_meter: str) -> pd.Series:
    meter_lookup = {
        _normalized_name(meter): meter for meter in load_long["meter"].unique()
    }
    key = _normalized_name(target_meter)
    if key not in meter_lookup:
        available = ", ".join(sorted(map(str, load_long["meter"].unique())))
        raise ValueError(
            f"Meter '{target_meter}' was not found. Available meters: {available}"
        )

    actual_name = meter_lookup[key]
    target = (
        load_long.loc[load_long["meter"] == actual_name]
        .set_index("timestamp")["demand_mw"]
        .sort_index()
    )
    hourly = target.resample("1h").mean()
    missing_before = int(hourly.isna().sum())
    hourly = hourly.interpolate(method="time", limit=2, limit_area="inside")
    if hourly.isna().any():
        longest_available = hourly.notna().groupby(hourly.isna().cumsum()).sum().max()
        raise ValueError(
            f"The target series has gaps longer than two hours. The longest usable "
            f"continuous portion is approximately {int(longest_available)} hours. "
            "Re-export the missing date range before training."
        )
    hourly.attrs["meter"] = actual_name
    hourly.attrs["missing_hours_before_interpolation"] = missing_before
    return hourly.dropna()


def calculate_interval_summary(
    load_long: pd.DataFrame, start: str, end: str
) -> pd.DataFrame:
    """Estimate usage by integrating MW readings over their native intervals."""
    timezone = load_long["timestamp"].dt.tz
    start_ts = pd.Timestamp(start, tz=timezone)
    end_ts = pd.Timestamp(end, tz=timezone)
    subset = load_long[load_long["timestamp"].between(start_ts, end_ts)].copy()
    rows: list[dict[str, object]] = []
    for meter, group in subset.groupby("meter"):
        group = group.sort_values("timestamp").copy()
        if len(group) < 2:
            continue
        step_hours = group["timestamp"].diff().dt.total_seconds().div(3600)
        typical_step = float(step_hours[step_hours > 0].median())
        # Treat each reading as the average demand for one native interval.
        energy_mwh = float(group["demand_mw"].sum() * typical_step)
        peak_row = group.loc[group["demand_mw"].idxmax()]
        rows.append(
            {
                "meter": meter,
                "usage_mwh": energy_mwh,
                "peak_mw": float(peak_row["demand_mw"]),
                "peak_time": peak_row["timestamp"],
                "native_interval_minutes": typical_step * 60,
            }
        )
    return pd.DataFrame(rows)


def compare_with_bryn_mawr_reference(load_long: pd.DataFrame) -> pd.DataFrame:
    actual = calculate_interval_summary(
        load_long, BRYN_MAWR_REFERENCE["start"], BRYN_MAWR_REFERENCE["end"]
    )
    if actual.empty:
        return actual
    reference = (
        pd.DataFrame.from_dict(BRYN_MAWR_REFERENCE["meters"], orient="index")
        .rename_axis("meter")
        .reset_index()
    )
    comparison = reference.merge(actual, on="meter", suffixes=("_reference", "_export"))
    comparison["usage_difference_pct"] = 100 * (
        comparison["usage_mwh_export"] - comparison["usage_mwh_reference"]
    ) / comparison["usage_mwh_reference"]
    comparison["peak_difference_pct"] = 100 * (
        comparison["peak_mw_export"] - comparison["peak_mw_reference"]
    ) / comparison["peak_mw_reference"]
    return comparison


def _read_external_features(
    weather_path: str | Path | None,
    calendar_path: str | Path | None,
    timezone: str,
) -> pd.DataFrame | None:
    frames: list[pd.DataFrame] = []

    if weather_path:
        weather = _read_table(weather_path)
        weather.index = _apply_timezone(_parse_timestamp(weather), timezone)
        selected: dict[str, pd.Series] = {}
        temp_col = _find_column(
            weather.columns, ["temp_f", "temperature_f", "temperature", "temp"]
        )
        humidity_col = _find_column(
            weather.columns, ["humidity_pct", "relative_humidity", "humidity"]
        )
        if temp_col is not None:
            selected["temp_f"] = pd.to_numeric(weather[temp_col], errors="coerce")
        if humidity_col is not None:
            selected["humidity_pct"] = pd.to_numeric(
                weather[humidity_col], errors="coerce"
            )
        if not selected:
            raise ValueError("Weather file needs a temperature and/or humidity column.")
        frame = pd.DataFrame(
            {name: values.to_numpy() for name, values in selected.items()},
            index=weather.index,
        ).resample("1h").mean()
        frames.append(frame)

    if calendar_path:
        calendar = _read_table(calendar_path)
        date_col = _find_column(calendar.columns, ["date", "timestamp", "day"])
        if date_col is None:
            raise ValueError("Academic calendar file needs a Date column.")
        dates = pd.to_datetime(calendar[date_col], errors="coerce")
        if dates.dt.tz is None:
            dates = dates.dt.tz_localize(timezone, ambiguous="NaT")
        selected = {}
        for output_name, aliases in {
            "semester_in_session": ["semester_in_session", "in_session", "semester"],
            "campus_holiday": ["campus_holiday", "holiday", "is_holiday"],
        }.items():
            column = _find_column(calendar.columns, aliases)
            if column is not None:
                selected[output_name] = pd.to_numeric(
                    calendar[column], errors="coerce"
                ).fillna(0)
        if not selected:
            raise ValueError(
                "Calendar file needs Semester_In_Session and/or Campus_Holiday."
            )
        daily = pd.DataFrame(
            {name: values.to_numpy() for name, values in selected.items()},
            index=dates,
        ).resample("1D").max().ffill()
        frames.append(daily.resample("1h").ffill())

    if not frames:
        return None
    combined = pd.concat(frames, axis=1).sort_index()
    return combined[~combined.index.duplicated(keep="last")]


def _calendar_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    hour = index.hour + index.minute / 60
    dow = index.dayofweek
    month = index.month
    return pd.DataFrame(
        {
            "hour_sin": np.sin(2 * np.pi * hour / 24),
            "hour_cos": np.cos(2 * np.pi * hour / 24),
            "dow_sin": np.sin(2 * np.pi * dow / 7),
            "dow_cos": np.cos(2 * np.pi * dow / 7),
            "month_sin": np.sin(2 * np.pi * (month - 1) / 12),
            "month_cos": np.cos(2 * np.pi * (month - 1) / 12),
            "is_weekend": (dow >= 5).astype(int),
        },
        index=index,
    )


def build_training_frame(
    load_mw: pd.Series, external: pd.DataFrame | None = None
) -> pd.DataFrame:
    frame = _calendar_features(load_mw.index)
    frame["lag_1h_mw"] = load_mw.shift(1)
    frame["lag_24h_mw"] = load_mw.shift(24)
    frame["lag_168h_mw"] = load_mw.shift(168)
    frame["rolling_24h_mean_mw"] = load_mw.shift(1).rolling(24).mean()
    frame["rolling_24h_std_mw"] = load_mw.shift(1).rolling(24).std()
    frame["rolling_168h_mean_mw"] = load_mw.shift(1).rolling(168).mean()
    if external is not None:
        frame = frame.join(external.reindex(frame.index).interpolate(limit_direction="both"))
    frame["target_mw"] = load_mw
    return frame.dropna()


def _external_row(
    timestamp: pd.Timestamp,
    external: pd.DataFrame | None,
    training_external: pd.DataFrame | None,
) -> dict[str, float]:
    if external is None:
        return {}
    if timestamp in external.index:
        row = external.loc[timestamp]
    else:
        row = pd.Series(index=external.columns, dtype=float)

    result: dict[str, float] = {}
    for column in external.columns:
        value = row.get(column, np.nan)
        if pd.isna(value) and training_external is not None:
            same_hour = training_external[
                training_external.index.hour == timestamp.hour
            ][column]
            value = same_hour.median()
        if pd.isna(value):
            value = 0.0
        result[column] = float(value)
    return result


def _one_feature_row(
    timestamp: pd.Timestamp,
    history: pd.Series,
    external: pd.DataFrame | None,
    training_external: pd.DataFrame | None,
) -> pd.DataFrame:
    calendar = _calendar_features(pd.DatetimeIndex([timestamp])).iloc[0].to_dict()
    before = history.loc[history.index < timestamp].sort_index()
    if len(before) < 168:
        raise ValueError("At least 168 hourly observations are required for forecasting.")

    def lag(hours: int) -> float:
        desired = timestamp - pd.Timedelta(hours=hours)
        if desired in history.index:
            return float(history.loc[desired])
        return float(before.iloc[-hours])

    row = {
        **calendar,
        "lag_1h_mw": float(before.iloc[-1]),
        "lag_24h_mw": lag(24),
        "lag_168h_mw": lag(168),
        "rolling_24h_mean_mw": float(before.tail(24).mean()),
        "rolling_24h_std_mw": float(before.tail(24).std()),
        "rolling_168h_mean_mw": float(before.tail(168).mean()),
        **_external_row(timestamp, external, training_external),
    }
    return pd.DataFrame([row], index=pd.DatetimeIndex([timestamp]))


def forecast_horizon(
    model: HistGradientBoostingRegressor,
    history: pd.Series,
    feature_columns: list[str],
    horizon_hours: int,
    external: pd.DataFrame | None = None,
    training_external: pd.DataFrame | None = None,
) -> pd.Series:
    working = history.copy().sort_index()
    start = working.index.max().floor("h") + pd.Timedelta(hours=1)
    predictions: list[float] = []
    timestamps = pd.date_range(start=start, periods=horizon_hours, freq="1h")
    for timestamp in timestamps:
        row = _one_feature_row(timestamp, working, external, training_external)
        row = row.reindex(columns=feature_columns)
        prediction = max(0.0, float(model.predict(row)[0]))
        predictions.append(prediction)
        working.loc[timestamp] = prediction
    return pd.Series(predictions, index=timestamps, name="forecast_mw")


def _new_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=350,
        max_leaf_nodes=31,
        min_samples_leaf=20,
        l2_regularization=0.2,
        random_state=42,
    )


def evaluate_rolling_day_ahead(
    load_mw: pd.Series,
    external: pd.DataFrame | None,
    validation_days: int,
) -> tuple[dict[str, float], pd.Timestamp]:
    last_timestamp = load_mw.index.max()
    # Use the final day only when it contains the full 00:00-23:00 range.
    validation_end = last_timestamp.floor("D")
    if last_timestamp.hour == 23:
        validation_end += pd.Timedelta(days=1)
    cutoff = validation_end - pd.Timedelta(days=validation_days)
    train_load = load_mw.loc[load_mw.index < cutoff]
    train_frame = build_training_frame(train_load, external)
    if len(train_frame) < 168:
        raise ValueError(
            "Not enough training rows after lag creation. Supply at least 30 days; "
            "90 days or more is strongly preferred."
        )
    feature_columns = [c for c in train_frame.columns if c != "target_mw"]
    model = _new_model()
    model.fit(train_frame[feature_columns], train_frame["target_mw"])

    predictions: list[pd.Series] = []
    truths: list[pd.Series] = []
    baselines: list[pd.Series] = []
    for day_start in pd.date_range(
        cutoff, validation_end - pd.Timedelta(days=1), freq="1D"
    ):
        history = load_mw.loc[load_mw.index < day_start]
        if len(history) < 168:
            continue
        predicted = forecast_horizon(
            model,
            history,
            feature_columns,
            24,
            external,
            external.loc[external.index < cutoff] if external is not None else None,
        )
        truth = load_mw.reindex(predicted.index).dropna()
        predicted = predicted.reindex(truth.index)
        if truth.empty:
            continue
        baseline = load_mw.shift(24).reindex(truth.index)
        predictions.append(predicted)
        truths.append(truth)
        baselines.append(baseline)

    if not predictions:
        return {}, cutoff
    y_pred = pd.concat(predictions)
    y_true = pd.concat(truths)
    y_base = pd.concat(baselines).reindex(y_true.index)
    valid_base = y_base.notna()
    metrics = {
        "mae_mw": float(mean_absolute_error(y_true, y_pred)),
        "rmse_mw": float(math.sqrt(mean_squared_error(y_true, y_pred))),
        "mape_percent": float(
            100 * np.mean(np.abs((y_true - y_pred) / y_true.clip(lower=0.05)))
        ),
        "persistence_mae_mw": float(
            mean_absolute_error(y_true[valid_base], y_base[valid_base])
        ),
    }
    return metrics, cutoff


def train_and_forecast(
    hourly_load_mw: pd.Series,
    external: pd.DataFrame | None = None,
    horizon_hours: int = 24,
    validation_days: int = 14,
) -> ForecastResult:
    if len(hourly_load_mw) < 24 * 30:
        raise ValueError(
            f"Only {len(hourly_load_mw)} hourly values were supplied. Version 1 "
            "requires at least 30 days; 6-12 months is recommended."
        )

    metrics, validation_cutoff = evaluate_rolling_day_ahead(
        hourly_load_mw, external, validation_days
    )
    frame = build_training_frame(hourly_load_mw, external)
    feature_columns = [c for c in frame.columns if c != "target_mw"]
    model = _new_model()
    model.fit(frame[feature_columns], frame["target_mw"])
    predictions = forecast_horizon(
        model,
        hourly_load_mw,
        feature_columns,
        horizon_hours,
        external,
        external.loc[external.index <= hourly_load_mw.index.max()]
        if external is not None
        else None,
    )

    forecast = predictions.to_frame()
    forecast["forecast_kw"] = forecast["forecast_mw"] * 1000
    forecast.index.name = "timestamp"
    peak_time = forecast["forecast_mw"].idxmax()
    summary: dict[str, object] = {
        "forecast_energy_mwh": round(float(forecast["forecast_mw"].sum()), 3),
        "forecast_energy_kwh": round(float(forecast["forecast_kw"].sum()), 1),
        "peak_load_mw": round(float(forecast["forecast_mw"].max()), 3),
        "peak_load_kw": round(float(forecast["forecast_kw"].max()), 1),
        "peak_time": peak_time.isoformat(),
        "forecast_start": forecast.index.min().isoformat(),
        "forecast_end": forecast.index.max().isoformat(),
        "validation_start": validation_cutoff.isoformat(),
        "what_if_input_mw": [round(float(x), 4) for x in forecast["forecast_mw"]],
        "what_if_input_kw": [round(float(x), 1) for x in forecast["forecast_kw"]],
    }
    return ForecastResult(forecast, metrics, summary, model)


def create_proxy_forecast(
    daily_energy_mwh: float = 5.5,
    forecast_date: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> ForecastResult:
    """Create a temporary campus forecast from a scaled town reference shape.

    The returned 24 hourly average-power values sum numerically to the requested
    daily MWh because each interval is exactly one hour.
    """
    if daily_energy_mwh <= 0:
        raise ValueError("daily_energy_mwh must be greater than zero.")
    if forecast_date:
        start = pd.Timestamp(forecast_date)
        if start.tzinfo is None:
            start = start.tz_localize(timezone)
        else:
            start = start.tz_convert(timezone)
        start = start.normalize()
    else:
        start = pd.Timestamp.now(tz=timezone).normalize() + pd.Timedelta(days=1)

    index = pd.date_range(start=start, periods=24, freq="1h")
    normalized_shape = BRYN_MAWR_PROXY_SHAPE / BRYN_MAWR_PROXY_SHAPE.sum()
    forecast_mw = daily_energy_mwh * normalized_shape
    forecast = pd.DataFrame(
        {
            "forecast_mw": forecast_mw,
            "forecast_kw": forecast_mw * 1000,
        },
        index=index,
    )
    forecast.index.name = "timestamp"
    peak_time = forecast["forecast_mw"].idxmax()
    summary: dict[str, object] = {
        "method": "scaled_bryn_mawr_reference_shape",
        "forecast_status": "temporary_proxy_not_trained_model",
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
        "warning": (
            "The magnitude is the user-supplied Villanova daily energy target. "
            "The shape is a temporary Bryn Mawr proxy, and hours 16-23 are inferred."
        ),
    }
    return ForecastResult(forecast, {}, summary, None)


def forecast_next_24_hours(
    data_path: str | Path,
    target_meter: str = DEFAULT_TARGET_METER,
    default_unit: str = "MW",
    timezone: str = DEFAULT_TIMEZONE,
    weather_path: str | Path | None = None,
    calendar_path: str | Path | None = None,
    validation_days: int = 14,
) -> ForecastResult:
    """Convenience function for direct use by the AVATAR what-if program."""
    load_long = normalize_load_export(data_path, default_unit, timezone)
    hourly = select_hourly_target(load_long, target_meter)
    external = _read_external_features(weather_path, calendar_path, timezone)
    return train_and_forecast(
        hourly,
        external=external,
        horizon_hours=24,
        validation_days=validation_days,
    )


def _make_plot(
    history: pd.Series | None, result: ForecastResult, output_path: Path, meter: str
) -> None:
    fig, ax = plt.subplots(figsize=(12, 5.5))
    if history is not None and not history.empty:
        history_hours = min(7 * 24, len(history))
        ax.plot(
            history.tail(history_hours).index,
            history.tail(history_hours),
            color="#7251a3",
            linewidth=1.7,
            label="Observed demand",
        )
    ax.plot(
        result.forecast.index,
        result.forecast["forecast_mw"],
        color="#e0bd45",
        linewidth=2.4,
        marker="o",
        markersize=3,
        label=(
            "24-hour trained forecast"
            if result.model is not None
            else "24-hour scaled proxy"
        ),
    )
    if history is not None and not history.empty:
        ax.axvline(result.forecast.index.min(), color="#5f6b70", linestyle="--")
    ax.set_title(f"AVATAR Day-Ahead Forecast — {meter}")
    ax.set_ylabel("Average Demand (MW)")
    ax.set_xlabel("Local Time")
    ax.grid(alpha=0.2)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def _demo_data(timezone: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(42)
    index = pd.date_range("2026-05-01", periods=120 * 24, freq="1h", tz=timezone)
    hour = index.hour.to_numpy()
    dow = index.dayofweek.to_numpy()
    temp = 68 + 13 * np.sin(2 * np.pi * (hour - 8) / 24) + rng.normal(0, 2, len(index))
    load = (
        0.17
        + 0.075 * np.clip(np.sin(np.pi * (hour - 6) / 14), 0, None)
        + 0.018 * (temp > 78) * (temp - 78) / 10
        - 0.018 * (dow >= 5)
        + rng.normal(0, 0.006, len(index))
    )
    load_long = pd.DataFrame(
        {"timestamp": index, "meter": DEFAULT_TARGET_METER, "demand_mw": load}
    )
    future_index = pd.date_range(index[-1] + pd.Timedelta(hours=1), periods=24, freq="1h")
    all_weather_index = index.append(future_index)
    all_hour = all_weather_index.hour.to_numpy()
    weather = pd.DataFrame(
        {
            "temp_f": 68 + 13 * np.sin(2 * np.pi * (all_hour - 8) / 24),
            "humidity_pct": 58 - 12 * np.sin(2 * np.pi * (all_hour - 8) / 24),
        },
        index=all_weather_index,
    )
    return load_long, weather


def _write_outputs(
    output_dir: Path,
    hourly: pd.Series | None,
    result: ForecastResult,
    meter: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    result.forecast.to_csv(output_dir / "next_24_hours_forecast.csv")
    with (output_dir / "forecast_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {"summary": result.summary, "validation_metrics": result.metrics},
            handle,
            indent=2,
        )
    _make_plot(hourly, result, output_dir / "forecast_plot.png", meter)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="Facilities CSV/XLSX interval export")
    parser.add_argument("--target-meter", default=DEFAULT_TARGET_METER)
    parser.add_argument("--default-unit", default="MW", choices=["W", "kW", "MW"])
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--weather", help="Optional historical + forecast weather CSV/XLSX")
    parser.add_argument("--calendar", help="Optional Villanova academic-calendar CSV/XLSX")
    parser.add_argument("--validation-days", type=int, default=14)
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--output-dir", default="forecast_outputs")
    parser.add_argument(
        "--check-bryn-mawr-summary",
        action="store_true",
        help="Validate a town-loop export against the supplied dashboard summary",
    )
    parser.add_argument("--demo", action="store_true")
    parser.add_argument(
        "--daily-energy-mwh",
        type=float,
        default=5.5,
        help="Villanova daily energy used by proxy mode (default: 5.5 MWh/day)",
    )
    parser.add_argument(
        "--forecast-date", help="Proxy forecast date in YYYY-MM-DD format"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.data and not args.demo:
        result = create_proxy_forecast(
            daily_energy_mwh=args.daily_energy_mwh,
            forecast_date=args.forecast_date,
            timezone=args.timezone,
        )
        output_dir = Path(args.output_dir).resolve()
        _write_outputs(
            output_dir, None, result, "Villanova Campus Load (Temporary Proxy)"
        )
        print("\nAVATAR proxy forecast complete")
        print(json.dumps({"summary": result.summary}, indent=2))
        print(f"\nOutputs: {output_dir}")
        return

    if args.demo:
        load_long, external = _demo_data(args.timezone)
    else:
        load_long = normalize_load_export(args.data, args.default_unit, args.timezone)
        external = _read_external_features(args.weather, args.calendar, args.timezone)

    if args.check_bryn_mawr_summary and not args.demo:
        comparison = compare_with_bryn_mawr_reference(load_long)
        if comparison.empty:
            print("No matching July 1-August 5, 2026 reference data were found.")
        else:
            print("\nBryn Mawr town-export check:\n")
            print(comparison.to_string(index=False))

    hourly = select_hourly_target(load_long, args.target_meter)
    result = train_and_forecast(
        hourly,
        external,
        horizon_hours=args.horizon_hours,
        validation_days=args.validation_days,
    )
    output_dir = Path(args.output_dir).resolve()
    _write_outputs(output_dir, hourly, result, args.target_meter)

    print("\nAVATAR forecast complete")
    print(json.dumps({"summary": result.summary, "metrics": result.metrics}, indent=2))
    print(f"\nOutputs: {output_dir}")


if __name__ == "__main__":
    main()
