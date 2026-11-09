from datetime import datetime
import unittest
from unittest.mock import patch

import pandas as pd

from forecast_contract import UTC
from forecast_pipeline import build_day_ahead_forecast, hourly_for_what_if
from weather_load_model import create_weather_adjusted_forecast
from weather_service import WeatherResult, WeatherServiceError


def weather_day(date="2026-11-09"):
    index = pd.date_range(date, periods=24, freq="h", tz="America/New_York")
    return pd.DataFrame(
        {
            "temp_f": [43 + abs(12 - hour) * 0.8 for hour in range(24)],
            "humidity_pct": [65] * 24,
            "apparent_temp_f": [40 + abs(12 - hour) * 0.7 for hour in range(24)],
            "precipitation_in": [0.0] * 24,
            "snowfall_in": [0.0] * 24,
            "wind_speed_mph": [7.0] * 24,
        },
        index=index,
    )


class WeatherLoadModelTests(unittest.TestCase):
    def test_model_moves_total_away_from_average_day(self):
        frame = weather_day()
        result = create_weather_adjusted_forecast(frame, "2026-11-09")
        total = float(result.forecast["forecast_mw"].sum())
        self.assertEqual(len(result.forecast), 24)
        self.assertGreater(total, 0)
        self.assertNotAlmostEqual(total, 104.0, places=2)
        self.assertNotEqual(result.summary["weather_impact_pct"], 0)

    def test_shared_payload_applies_weather(self):
        result = WeatherResult(
            hourly=weather_day(),
            provider="test",
            dataset="forecast",
            retrieved_at_utc="2026-11-08T12:00:00+00:00",
            from_cache=False,
        )
        payload = build_day_ahead_forecast(
            now=datetime(2026, 11, 8, 12, tzinfo=UTC),
            forecast_date="2026-11-09",
            weather_result=result,
        )
        self.assertTrue(payload["weather_applied_to_demand"])
        self.assertFalse(payload["fallback_used"])
        self.assertEqual(payload["forecast_type"], "trained_model")
        self.assertEqual(len(hourly_for_what_if(payload)), 24)

    def test_weather_outage_keeps_static_fallback(self):
        with patch(
            "forecast_pipeline.fetch_forecast_weather",
            side_effect=WeatherServiceError("offline"),
        ):
            payload = build_day_ahead_forecast(
                now=datetime(2026, 11, 8, 12, tzinfo=UTC),
                forecast_date="2026-11-09",
            )
        self.assertTrue(payload["fallback_used"])
        self.assertFalse(payload["weather_applied_to_demand"])
        self.assertEqual(payload["forecast_type"], "temporary_proxy")
        self.assertAlmostEqual(payload["daily_energy_mwh"], 104.0, places=2)


if __name__ == "__main__":
    unittest.main()
