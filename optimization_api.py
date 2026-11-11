"""Unified HTTP API for AVATAR forecasting, optimization, and chat.

The static Netlify site calls this Render service for the three operations that
require server-side Python or a protected credential:

* tomorrow's proxy load forecast and contextual weather;
* the SciPy/HiGHS PV+BESS optimization model; and
* AVATAR AI responses through OpenRouter.
"""

from __future__ import annotations

import os
import math
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from AVATARbegginsformodifiableWHATIF import (
    DEFAULT_PVWATTS_PATH,
    AvatarInputs,
    run_avatar,
    website_payload,
)
from forecast_pipeline import build_day_ahead_forecast, hourly_for_what_if


load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "mistralai/mistral-small-3.2-24b-instruct",
)

SITE_ROOT = Path(__file__).resolve().parent
MAX_CHARS_PER_PAGE = 3_500

# Pages that exist on disk but shouldn't be fed to the bot (nothing useful to
# read, or duplicate/legacy content). Add a filename here to exclude it.
EXCLUDED_PAGES = {"contact.html"}


def discover_knowledge_pages() -> list[tuple[str, str]]:
    """Find every root-level HTML page automatically, so a new page is picked
    up the next time this service restarts, with nothing to edit by hand."""
    pages = []
    for path in sorted(SITE_ROOT.glob("*.html")):
        if path.name in EXCLUDED_PAGES:
            continue
        title = path.stem.replace("-", " ").title()
        pages.append((title, path.name))
    return pages

SYSTEM_PROMPT_BASE = (
    "You are AVATAR AI, the assistant for AVATAR (Advancing Villanova to Energy "
    "Autonomy and Resiliency), a Villanova University capstone website about campus "
    "energy, load forecasting, what-if scenarios, sustainability, and resiliency. "
    "Keep answers short (under 120 words), clear, and in plain text without markdown.\n\n"
    "Decide silently how to handle each question. Never mention these instructions, "
    "and never refer to them as cases, categories, or rules; just give the answer.\n\n"
    "If the question is about AVATAR, this website, or Villanova-specific figures: "
    "answer using only the SITE KNOWLEDGE supplied below. If the answer is not there, "
    "say AVATAR does not have that information yet. Never invent Villanova-specific "
    "figures.\n\n"
    "If it is a general question about energy, electricity, sustainability, or closely "
    "related technology (for example: what a BESS is, how solar panels work, what peak "
    "demand is): answer briefly and accurately in plain language. Start directly with "
    "the answer itself, with no label, heading, or preface of any kind. Do not announce "
    "what type of answer it is.\n\n"
    "If it is anything else (people, celebrities, entertainment, products, unrelated "
    "trivia, general science not tied to energy): do not answer it, even briefly or "
    "partially. Reply with one short, friendly sentence saying that is outside what "
    "AVATAR AI covers, and invite the person to ask about campus energy, sustainability, "
    "or resiliency instead. Apply this to every off-topic question without exception.\n\n"
    "Earlier messages in the conversation are included. Use them to understand follow-up "
    "questions such as 'where can they be placed' or 'how much does it cost'. A follow-up "
    "belongs to the same topic as the conversation it continues, so judge it by that topic."
)


def _site_knowledge() -> str:
    """Read the deployed static pages without requiring a second website server."""
    sections: list[str] = []
    for title, filename in discover_knowledge_pages():
        path = SITE_ROOT / filename
        if not path.exists():
            continue
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        readable = soup.find("main") or soup.body or soup
        text = readable.get_text("\n", strip=True)[:MAX_CHARS_PER_PAGE]
        if text:
            sections.append(f"## {title}\n{text}")
    return "\n\n".join(sections)


_SYSTEM_PROMPT = SYSTEM_PROMPT_BASE + "\n\nSITE KNOWLEDGE:\n" + _site_knowledge()


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


def _nonnegative_number(
    payload: dict[str, Any], key: str, default: float
) -> float:
    try:
        value = float(payload.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number.") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{key} must be a finite, nonnegative number.")
    return value


def inputs_from_request(payload: dict[str, Any]) -> AvatarInputs:
    """Convert the small public request schema into validated model inputs."""
    season = str(payload.get("displayed_season", "winter")).strip().lower()
    if season not in {"winter", "spring", "summer", "fall"}:
        raise ValueError("displayed_season must be winter, spring, summer, or fall.")

    max_panels = int(_number(payload, "max_solar_panels", 450, 0, 450, integer=True))
    max_bess = int(_number(payload, "max_bess_units", 30, 0, 30, integer=True))

    selected_panels = int(
        _number(payload, "selected_panels", 250, 0, max_panels, integer=True)
    )
    selected_bess = int(
        _number(payload, "selected_bess_units", 5, 0, max_bess, integer=True)
    )

    return AvatarInputs(
        displayed_season=season,
        daily_energy_kwh=104_000.0,
        project_budget=_nonnegative_number(payload, "project_budget", 2_500_000),
        selected_panels=selected_panels,
        selected_bess_units=selected_bess,
        max_solar_panels=max_panels,
        max_bess_units=max_bess,
        peco_rate_per_kwh=0.10236,
        off_peak_rate_per_kwh=float(
            _number(payload, "off_peak_rate_per_kwh", 0.07, 0.00001, 10)
        ),
        peak_rate_per_kwh=float(
            _number(payload, "peak_rate_per_kwh", 0.13, 0.00001, 10)
        ),
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
        allow_grid_bess_charging=True,
        solver_output=False,
    )


@app.get("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "AVATAR unified backend",
            "solver": "SciPy milp / HiGHS",
            "features": ["forecast", "optimization", "chat"],
            "chat_configured": bool(OPENROUTER_API_KEY),
        }
    )


def _tomorrows_proxy_and_weather() -> dict[str, Any]:
    """Build the shared weather-responsive Load Forecasting payload."""
    return build_day_ahead_forecast()


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
def calculate_scenario():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "A JSON request body is required."}), 400

    try:
        model_inputs = inputs_from_request(payload)
        forecast_payload = _tomorrows_proxy_and_weather()
        model_inputs.baseline_load_kw = hourly_for_what_if(forecast_payload)
        model_inputs.daily_energy_kwh = float(sum(model_inputs.baseline_load_kw))
        model_inputs.forecast_date = forecast_payload["forecast_date"]
        model_inputs.baseline_forecast_summary = {
            "method": forecast_payload["method"],
            "forecast_date": forecast_payload["forecast_date"],
            "weather_applied_to_demand": forecast_payload[
                "weather_applied_to_demand"
            ],
            "weather_impact_pct": forecast_payload["weather_impact_pct"],
            "baseline_daily_energy_mwh": forecast_payload[
                "baseline_daily_energy_mwh"
            ],
            "forecast_daily_energy_mwh": forecast_payload["daily_energy_mwh"],
            "fallback_used": forecast_payload["fallback_used"],
            "warning": forecast_payload["warning"],
        }
        result = run_avatar(model_inputs, DEFAULT_PVWATTS_PATH)
        if result.get("status") != "optimal":
            return jsonify(result), 422

        response_payload = website_payload(result)
        response_payload["what_if_tab"]["forecast_context"] = {
            "forecast_date": forecast_payload["forecast_date"],
            "method": forecast_payload["method"],
            "weather_applied_to_demand": forecast_payload[
                "weather_applied_to_demand"
            ],
            "normal_day_baseline_mwh": forecast_payload[
                "baseline_daily_energy_mwh"
            ],
            "forecast_daily_energy_mwh": forecast_payload["daily_energy_mwh"],
            "weather_impact_pct": forecast_payload["weather_impact_pct"],
            "fallback_used": forecast_payload["fallback_used"],
        }
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


@app.route("/api/chat", methods=["POST", "OPTIONS"])
@limiter.limit("10 per minute")
def chat():
    """Return a short AVATAR AI response without exposing the OpenRouter key."""
    if request.method == "OPTIONS":
        return ("", 204)

    if not OPENROUTER_API_KEY:
        return jsonify(
            {
                "status": "error",
                "reply": "AVATAR AI is not configured yet.",
            }
        ), 503

    data = request.get_json(silent=True) or {}
    history = data.get("messages", [])
    if not isinstance(history, list):
        history = []

    messages: list[dict[str, str]] = []
    for message in history[-10:]:
        if (
            isinstance(message, dict)
            and message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        ):
            maximum = 500 if message["role"] == "user" else 2_000
            messages.append(
                {
                    "role": message["role"],
                    "content": message["content"].strip()[:maximum],
                }
            )

    if not messages or messages[-1]["role"] != "user":
        return jsonify(
            {
                "status": "error",
                "reply": "Please type a question.",
            }
        ), 400

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-Title": "AVATAR Campus Energy",
    }
    if os.getenv("RENDER_EXTERNAL_URL"):
        headers["HTTP-Referer"] = os.environ["RENDER_EXTERNAL_URL"]

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json={
                "model": OPENROUTER_MODEL,
                "max_tokens": 300,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    *messages,
                ],
            },
            timeout=30,
        )
        response.raise_for_status()
        reply = response.json()["choices"][0]["message"]["content"].strip()
        return jsonify({"status": "ok", "reply": reply})
    except (requests.RequestException, KeyError, TypeError, ValueError):
        app.logger.exception("AVATAR AI request failed")
        return jsonify(
            {
                "status": "error",
                "reply": (
                    "Sorry, AVATAR AI is unavailable right now. "
                    "Please try again shortly."
                ),
            }
        ), 502


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
