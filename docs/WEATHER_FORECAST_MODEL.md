# AVATAR Weather-Responsive Load Forecast

## Purpose

AVATAR treats 104 MWh as Villanova's average daily campus consumption, not a
fixed total. A two-stage Bartley proxy uses tomorrow's weather to estimate:

1. the daily-energy multiplier above or below the 104 MWh average day; and
2. the distribution of that energy across the local calendar day's hourly
   intervals.

The Load Forecasting tab and What-If calculator both call the shared
`forecast_pipeline.py` path. The calculator therefore evaluates the same
weather-adjusted baseline shown on the forecast page.

## Training data and privacy

The committed model artifact contains trained statistical relationships and
metadata only. Raw Facilities meter readings remain outside the repository.
The current artifact was trained from 278 complete Bartley Hall days covering
2025-12-25 through 2026-10-22 and matching Open-Meteo historical weather.

## Inputs

- temperature and apparent temperature;
- heating and cooling degree values;
- humidity;
- precipitation and snowfall;
- wind speed;
- hour, weekday/weekend, month, and annual seasonality.

The daily model and hourly-shape model are scikit-learn
`HistGradientBoostingRegressor` models. The final hourly shares are normalized
before being multiplied by the forecast daily energy.

## Fallback

If live weather, required weather fields, or the trained artifact are
unavailable, AVATAR automatically returns the static Bartley weekday/weekend
profile scaled to 104 MWh. The API reports `fallback_used`, `fallback_reason`,
and `weather_applied_to_demand` so the interface can identify the active path.

## Retraining

Keep the raw CSV outside Git, then run:

```bash
python tools/train_bartley_weather_model.py "Bartley SEL Present Demand.csv"
```

This replaces `models/bartley_weather_models.joblib`. Review the printed
chronological validation metrics before publishing the new artifact.
