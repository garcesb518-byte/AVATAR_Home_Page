"""HTTP API for the AVATAR SciPy/HiGHS optimization model.

The static Netlify site sends a JSON request to this service when a visitor
presses the Run Optimization button. The service validates the website inputs,
runs the existing engineering model, and returns a JSON-safe result.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

from AVATARbegginsformodifiableWHATIF import (
    DEFAULT_PVWATTS_PATH,
    AvatarInputs,
    run_avatar,
    website_payload,
)
from avatar_load_forecasting import create_proxy_forecast
from weather_service import (
    WeatherConfig,
    WeatherServiceError,
    fetch_forecast_weather,
)


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024


def _allowed_origins() -> set[str]:
    configured = os.getenv(
        "AVATAR_ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    )
    return {origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()}


@app.after_request
def add_cors_headers(response):
    """Allow the configured Netlify site (and local test site) to call the API."""
    origin = request.headers.get("Origin", "").rstrip("/")
    allowed = _allowed_origins()
    if "*" in allowed:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def _number(
    payload: dict[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
    *,
    integer: bool = False,
) -> float | int:
    raw_value = payload.get(key, default)
    try:
        value = int(raw_value) if integer else float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum:g} and {maximum:g}.")
    return value


def inputs_from_request(payload: dict[str, Any]) -> AvatarInputs:
    """Convert the small public request schema into validated model inputs."""
    mode = str(payload.get("mode", "optimize")).strip().lower()
    if mode not in {"optimize", "scenario"}:
        raise ValueError("mode must be optimize or scenario.")

    season = str(payload.get("displayed_season", "winter")).strip().lower()
    if season not in {"winter", "spring", "summer", "fall"}:
        raise ValueError("displayed_season must be winter, spring, summer, or fall.")

    max_panels = int(_number(payload, "max_solar_panels", 450, 0, 450, integer=True))
    max_bess = int(_number(payload, "max_bess_units", 30, 0, 30, integer=True))

    if mode == "scenario":
        selected_panels = int(
            _number(payload, "selected_panels", 250, 0, max_panels, integer=True)
        )
        selected_bess = int(
            _number(payload, "selected_bess_units", 5, 0, max_bess, integer=True)
        )
    else:
        # These fields are ignored by optimize mode, but zeroing them also keeps
        # the model-level selected-versus-maximum validation unambiguous.
        selected_panels = 0
        selected_bess = 0

    return AvatarInputs(
        mode=mode,
        displayed_season=season,
        daily_energy_kwh=5_500.0,
        project_budget=float(
            _number(payload, "project_budget", 2_500_000, 0, 2_500_000)
        ),
        selected_panels=selected_panels,
        selected_bess_units=selected_bess,
        max_solar_panels=max_panels,
        max_bess_units=max_bess,
        peco_rate_per_kwh=0.10236,
        energy_conservation_percent=float(
            _number(payload, "energy_conservation_percent", 0, 0, 100)
        ),
        occupancy_load_change_percent=float(
            _number(payload, "occupancy_load_change_percent", 0, -100, 100)
        ),
        temperature_load_change_percent=float(
            _number(payload, "temperature_load_change_percent", 0, -100, 100)
        ),
        allow_solar_curtailment=False,
        allow_grid_bess_charging=False,
        solver_output=False,
    )


@app.get("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "AVATAR SciPy optimization API",
            "solver": "SciPy milp / HiGHS",
        }
    )


def _tomorrows_proxy_and_weather() -> dict[str, Any]:
    """Build the Load Forecasting tab payload without overstating accuracy."""
    weather_config = WeatherConfig.from_environment()
    local_now = datetime.now(ZoneInfo(weather_config.timezone_name))
    forecast_date = local_now.date() + timedelta(days=1)

    proxy = create_proxy_forecast(
        daily_energy_mwh=5.5,
        forecast_date=forecast_date.isoformat(),
        timezone=weather_config.timezone_name,
    )
    hourly_load = [
        {
            "timestamp": timestamp.isoformat(),
            "hour": int(timestamp.hour),
            "label": timestamp.strftime("%I %p").lstrip("0"),
            "forecast_kw": round(float(row["forecast_kw"]), 2),
        }
        for timestamp, row in proxy.forecast.iterrows()
    ]
    peak = max(hourly_load, key=lambda item: item["forecast_kw"])

    weather_payload: dict[str, Any]
    try:
        weather_result = fetch_forecast_weather(
            forecast_days=3, config=weather_config
        )
        weather_day = weather_result.hourly[
            weather_result.hourly.index.date == forecast_date
        ]
        if weather_day.empty:
            raise WeatherServiceError(
                "Tomorrow's hourly weather was not present in the provider response."
            )

        weather_payload = {
            "status": "available",
            "provider": weather_result.provider,
            "dataset": weather_result.dataset,
            "forecast_date": forecast_date.isoformat(),
            "retrieved_at_utc": weather_result.retrieved_at_utc,
            "from_cache": weather_result.from_cache,
            "applied_to_demand": False,
            "temperature_low_f": round(float(weather_day["temp_f"].min()), 1),
            "temperature_high_f": round(float(weather_day["temp_f"].max()), 1),
            "average_humidity_pct": round(
                float(weather_day["humidity_pct"].mean()), 1
            ),
            "precipitation_total_in": round(
                float(weather_day["precipitation_in"].sum()), 3
            ),
            "hourly": [
                {
                    "timestamp": timestamp.isoformat(),
                    "label": timestamp.strftime("%I %p").lstrip("0"),
                    "temp_f": round(float(row["temp_f"]), 1),
                    "humidity_pct": round(float(row["humidity_pct"]), 1),
                    "apparent_temp_f": round(
                        float(row["apparent_temp_f"]), 1
                    ),
                    "precipitation_in": round(
                        float(row["precipitation_in"]), 3
                    ),
                }
                for timestamp, row in weather_day.iterrows()
            ],
            "note": (
                "Weather is displayed for context but is not yet applied to demand. "
                "Historical Villanova load data are required to learn that relationship."
            ),
        }
    except (WeatherServiceError, ValueError) as exc:
        app.logger.warning("AVATAR weather request unavailable: %s", exc)
        weather_payload = {
            "status": "unavailable",
            "provider": "Open-Meteo",
            "forecast_date": forecast_date.isoformat(),
            "applied_to_demand": False,
            "hourly": [],
            "message": (
                "Tomorrow's weather is temporarily unavailable. The baseline proxy "
                "load profile is still available."
            ),
        }

    return {
        "status": "temporary_proxy",
        "method": proxy.summary["method"],
        "forecast_date": forecast_date.isoformat(),
        "daily_energy_mwh": proxy.summary["forecast_energy_mwh"],
        "daily_energy_kwh": proxy.summary["forecast_energy_kwh"],
        "average_load_kw": proxy.summary["average_load_kw"],
        "peak_load_kw": proxy.summary["peak_load_kw"],
        "peak_hour": peak["hour"],
        "peak_label": peak["label"],
        "weather_applied_to_demand": False,
        "warning": proxy.summary["warning"],
        "hourly": hourly_load,
        "weather": weather_payload,
    }


@app.get("/api/forecast")
def forecast():
    """Return tomorrow's proxy load and contextual weather for the website."""
    try:
        return jsonify(_tomorrows_proxy_and_weather())
    except Exception:
        app.logger.exception("AVATAR forecast request failed")
        return jsonify(
            {
                "status": "error",
                "message": "The forecast service could not complete this request.",
            }
        ), 500


@app.route("/api/optimize", methods=["POST", "OPTIONS"])
def optimize():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "A JSON request body is required."}), 400

    try:
        model_inputs = inputs_from_request(payload)
        result = run_avatar(model_inputs, DEFAULT_PVWATTS_PATH)
        if result.get("status") != "optimal":
            return jsonify(result), 422

        response_payload = website_payload(result)
        response_payload["what_if_tab"]["load_comparison"] = [
            {
                "hour": hour,
                "baseline_kw": round(float(result["baseline_load_kw"][hour]), 3),
                "adjusted_kw": round(float(result["adjusted_load_kw"][hour]), 3),
            }
            for hour in range(24)
        ]
        return jsonify(response_payload)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception:
        app.logger.exception("AVATAR optimization request failed")
        return jsonify(
            {
                "status": "error",
                "message": "The optimization service could not complete this scenario.",
            }
        ), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
