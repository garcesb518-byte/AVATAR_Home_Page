"""Train AVATAR's weather-responsive proxy without committing raw meter data.

Usage:
    python tools/train_bartley_weather_model.py "Bartley SEL Present Demand.csv"

The output is a compact model artifact containing normalized relationships,
metadata, and validation results. Raw Bartley demand values are not copied into
the repository.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_load_model import (  # noqa: E402
    MODEL_VERSION,
    daily_weather_features,
    hourly_weather_features,
)
from weather_service import WeatherConfig, fetch_historical_weather  # noqa: E402


def read_bartley(path: Path) -> tuple[pd.DataFrame, pd.Series]:
    raw = pd.read_csv(path, encoding="utf-8-sig")
    required = {"Timestamp", "Status", "Value (kW)"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Missing required SEL columns: {sorted(missing)}")
    timestamps = raw["Timestamp"].astype(str).str.replace(
        r"\s+(EST|EDT)$", "", regex=True
    )
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                timestamps,
                format="%d-%b-%y %I:%M:%S %p",
                errors="coerce",
            ).dt.round("15min"),
            "status": raw["Status"].astype(str),
            "kw": pd.to_numeric(raw["Value (kW)"], errors="coerce"),
        }
    )
    frame = frame[
        frame["timestamp"].notna()
        & frame["kw"].notna()
        & (frame["kw"] > 0)
        & (frame["status"] == "{ok}")
    ]
    demand = frame.groupby("timestamp")["kw"].mean().sort_index()
    interval_counts = demand.groupby(demand.index.date).count()
    complete_dates = set(interval_counts[interval_counts >= 92].index)
    demand = demand[pd.Index(demand.index.date).isin(complete_dates)]

    interval = demand.to_frame("kw")
    interval["date"] = interval.index.date
    interval["hour"] = interval.index.hour
    hourly = interval.groupby(["date", "hour"], as_index=False).agg(
        hourly_kwh=("kw", lambda values: float(values.sum()) * 0.25),
        interval_count=("kw", "count"),
    )
    full_dates = set(
        hourly.groupby("date")["hour"].nunique().loc[lambda x: x == 24].index
    )
    hourly = hourly[hourly["date"].isin(full_dates)].copy()
    daily_energy = hourly.groupby("date")["hourly_kwh"].sum().sort_index()
    hourly["daily_kwh"] = hourly["date"].map(daily_energy)
    hourly["share"] = hourly["hourly_kwh"] / hourly["daily_kwh"]
    return hourly, daily_energy


def _model(min_samples_leaf: int) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        learning_rate=0.045,
        max_iter=350,
        max_leaf_nodes=15,
        min_samples_leaf=min_samples_leaf,
        l2_regularization=0.8,
        random_state=42,
    )


def build_training_sets(
    hourly: pd.DataFrame,
    daily_energy: pd.Series,
    weather: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    normal_energy = float(daily_energy.mean())
    daily_rows: list[dict[str, float]] = []
    daily_targets: list[float] = []
    shape_rows: list[pd.DataFrame] = []
    shape_targets: list[float] = []
    for date_value, energy in daily_energy.items():
        day_weather = weather[weather.index.date == date_value]
        day_load = hourly[hourly["date"] == date_value].sort_values("hour")
        if len(day_weather) != 24 or len(day_load) != 24:
            continue
        features = daily_weather_features(day_weather, pd.Timestamp(date_value))
        daily_rows.append(features)
        daily_targets.append(float(energy / normal_energy))
        hour_frame = hourly_weather_features(
            day_weather, pd.Timestamp(date_value), features
        ).reset_index(drop=True)
        hour_frame["date"] = pd.Timestamp(date_value)
        shape_rows.append(hour_frame)
        shape_targets.extend(day_load["share"].astype(float).tolist())
    daily_x = pd.DataFrame(daily_rows)
    daily_y = pd.Series(daily_targets, name="daily_multiplier")
    shape_x = pd.concat(shape_rows, ignore_index=True)
    shape_y = pd.Series(shape_targets, name="hourly_share")
    if len(daily_x) < 120:
        raise ValueError(f"Only {len(daily_x)} complete matched days were available.")
    return daily_x, daily_y, shape_x, shape_y


def train(
    bartley_path: Path,
    output_path: Path,
    validation_days: int = 42,
) -> dict[str, object]:
    hourly, daily_energy = read_bartley(bartley_path)
    start = min(daily_energy.index).isoformat()
    end = max(daily_energy.index).isoformat()
    weather_result = fetch_historical_weather(
        start, end, WeatherConfig(cache_hours=24 * 365)
    )
    weather = weather_result.hourly.copy()
    weather.index = weather.index.tz_localize(None)
    daily_x, daily_y, shape_x, shape_y = build_training_sets(
        hourly, daily_energy, weather
    )

    ordered_dates = pd.Series(sorted(shape_x["date"].unique()))
    cutoff = pd.Timestamp(ordered_dates.iloc[-validation_days])
    daily_dates = pd.Series(sorted(shape_x["date"].unique()))
    daily_train_mask = daily_dates < cutoff
    # daily_x is constructed in the same sorted-date order as daily_energy.
    daily_model = _model(min_samples_leaf=10)
    daily_model.fit(daily_x.loc[daily_train_mask.to_numpy()], daily_y.loc[daily_train_mask.to_numpy()])
    daily_pred = daily_model.predict(daily_x.loc[~daily_train_mask.to_numpy()])
    daily_truth = daily_y.loc[~daily_train_mask.to_numpy()]

    shape_train = shape_x["date"] < cutoff
    shape_features = [column for column in shape_x.columns if column != "date"]
    shape_model = _model(min_samples_leaf=40)
    shape_model.fit(shape_x.loc[shape_train, shape_features], shape_y.loc[shape_train])
    raw_shape_pred = np.clip(
        shape_model.predict(shape_x.loc[~shape_train, shape_features]), 1e-8, None
    )
    shape_eval = shape_x.loc[~shape_train, ["date"]].copy()
    shape_eval["truth"] = shape_y.loc[~shape_train].to_numpy()
    shape_eval["prediction"] = raw_shape_pred
    shape_eval["prediction"] = shape_eval.groupby("date")["prediction"].transform(
        lambda values: values / values.sum()
    )

    validation = {
        "validation_start": cutoff.date().isoformat(),
        "validation_days": int((~daily_train_mask).sum()),
        "daily_multiplier_mae": round(
            float(mean_absolute_error(daily_truth, daily_pred)), 5
        ),
        "daily_multiplier_rmse": round(
            float(mean_squared_error(daily_truth, daily_pred) ** 0.5), 5
        ),
        "daily_baseline_mae": round(
            float(mean_absolute_error(daily_truth, np.ones(len(daily_truth)))), 5
        ),
        "hourly_share_mae": round(
            float(mean_absolute_error(shape_eval["truth"], shape_eval["prediction"])),
            7,
        ),
    }

    final_daily = _model(min_samples_leaf=10)
    final_daily.fit(daily_x, daily_y)
    final_shape = _model(min_samples_leaf=40)
    final_shape.fit(shape_x[shape_features], shape_y)
    lower = max(0.25, float(daily_y.quantile(0.01)) * 0.95)
    upper = float(daily_y.quantile(0.99)) * 1.05
    artifact = {
        "model_version": MODEL_VERSION,
        "sklearn_version": sklearn.__version__,
        "daily_model": final_daily,
        "shape_model": final_shape,
        "daily_features": list(daily_x.columns),
        "shape_features": shape_features,
        "daily_multiplier_bounds": [lower, upper],
        "metadata": {
            "load_source": "Bartley Hall SEL present-demand data",
            "load_data_start": start,
            "load_data_end": end,
            "matched_days": int(len(daily_x)),
            "normalization": "Bartley daily energy divided by Bartley mean daily energy",
            "campus_average_day_mwh": 104.0,
            "weather_provider": weather_result.provider,
            "validation": validation,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_path, compress=3)
    return artifact["metadata"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bartley_csv", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "models" / "bartley_weather_models.joblib",
    )
    parser.add_argument("--validation-days", type=int, default=42)
    args = parser.parse_args()
    metadata = train(args.bartley_csv, args.output, args.validation_days)
    print(f"Saved {args.output}")
    print(metadata)


if __name__ == "__main__":
    main()
