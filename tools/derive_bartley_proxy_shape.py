"""Derive publishable hourly timing ratios from a Bartley SEL demand export.

The raw Facilities file stays outside the repository. This script prints only
normalized weekday/weekend shapes, coverage, and quality counts; it does not
copy measured Bartley kW values into a project file.

Usage:
    python tools/derive_bartley_proxy_shape.py "Bartley SEL Present Demand.csv"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def derive(path: Path) -> dict[str, object]:
    raw = pd.read_csv(path, encoding="utf-8-sig")
    required = {"Timestamp", "Status", "Value (kW)"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Missing required SEL columns: {', '.join(sorted(missing))}")

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
    counts = demand.groupby(demand.index.date).count()
    complete_dates = set(counts[counts >= 92].index)
    demand = demand[pd.Index(demand.index.date).isin(complete_dates)]

    prepared = demand.to_frame()
    prepared["date"] = prepared.index.date
    prepared["hour"] = prepared.index.hour
    prepared["day_type"] = prepared.index.dayofweek.map(
        lambda value: "weekend" if value >= 5 else "weekday"
    )
    prepared["daily_kwh"] = prepared.groupby("date")["kw"].transform("sum") * 0.25
    prepared["share"] = prepared["kw"] * 0.25 / prepared["daily_kwh"]
    hourly = prepared.groupby(["date", "hour"], as_index=False).agg(
        share=("share", "sum"), day_type=("day_type", "first")
    )
    full_dates = set(
        hourly.groupby("date")["hour"].nunique().loc[lambda values: values == 24].index
    )
    hourly = hourly[hourly["date"].isin(full_dates)]

    profiles: dict[str, list[float]] = {}
    day_counts: dict[str, int] = {}
    for day_type in ("weekday", "weekend"):
        subset = hourly[hourly["day_type"] == day_type]
        shape = subset.groupby("hour")["share"].median().reindex(range(24))
        if shape.isna().any():
            raise ValueError(f"The {day_type} profile is missing one or more hours.")
        shape = shape / shape.sum()
        profiles[day_type] = [round(float(value), 8) for value in shape]
        day_counts[day_type] = int(subset["date"].nunique())

    return {
        "source": path.name,
        "coverage_start": demand.index.min().date().isoformat(),
        "coverage_end": demand.index.max().date().isoformat(),
        "usable_days": sum(day_counts.values()),
        "day_counts": day_counts,
        "profiles": profiles,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    print(json.dumps(derive(args.csv), indent=2))


if __name__ == "__main__":
    main()
