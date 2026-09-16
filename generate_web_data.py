"""Generate static website data from the AVATAR Python models.

Run this file whenever the load-forecasting assumptions or model change. The
generated JSON is committed with the website so a static host such as Netlify
can display the forecast without running Python for every visitor.
"""

from __future__ import annotations

import json
from pathlib import Path

from avatar_load_forecasting import create_proxy_forecast


ROOT = Path(__file__).resolve().parent
FORECAST_OUTPUT = ROOT / "static" / "data" / "load_forecast.json"


def build_forecast_payload() -> dict[str, object]:
    result = create_proxy_forecast(
        daily_energy_mwh=5.5,
        # The temporary proxy shape is independent of date. A fixed date keeps
        # the committed JSON reproducible while the webpage labels it as a
        # baseline profile rather than pretending it is a live forecast.
        forecast_date="2026-01-01",
    )
    hourly = [
        {
            "hour": int(timestamp.hour),
            "label": timestamp.strftime("%I %p").lstrip("0"),
            "forecast_kw": round(float(row["forecast_kw"]), 2),
        }
        for timestamp, row in result.forecast.iterrows()
    ]
    peak = max(hourly, key=lambda item: item["forecast_kw"])
    return {
        "status": "temporary_proxy",
        "method": result.summary["method"],
        "daily_energy_mwh": result.summary["forecast_energy_mwh"],
        "daily_energy_kwh": result.summary["forecast_energy_kwh"],
        "average_load_kw": result.summary["average_load_kw"],
        "peak_load_kw": result.summary["peak_load_kw"],
        "peak_hour": peak["hour"],
        "peak_label": peak["label"],
        "warning": result.summary["warning"],
        "hourly": hourly,
    }


def main() -> None:
    FORECAST_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    FORECAST_OUTPUT.write_text(
        json.dumps(build_forecast_payload(), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {FORECAST_OUTPUT}")


if __name__ == "__main__":
    main()
