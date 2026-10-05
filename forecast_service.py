"""Day-ahead proxy API adapter; preserves hourly fields and adds control metadata."""
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Lock
from forecast_contract import UTC, EASTERN, LOCATION, validate_forecast


class ForecastService:
    def __init__(self, weather_loader=None):
        self._cache = None
        self._lock = Lock()
        self.weather_loader = weather_loader

    def get(self, now=None):
        now = (now or datetime.now(UTC)).astimezone(UTC)
        tomorrow = now.astimezone(EASTERN).date() + timedelta(days=1)
        with self._lock:
            if self._cache and self._cache['forecast_date'] == tomorrow.isoformat():
                return deepcopy(self._cache)
            # Existing forecasting code remains the single source of the proxy shape.
            from avatar_load_forecasting import create_proxy_forecast
            result = create_proxy_forecast(forecast_date=tomorrow.isoformat())
            hourly, predictions = [], []
            for ts, row in result.forecast.iterrows():
                start = ts.to_pydatetime()
                end = (start.astimezone(UTC) + timedelta(hours=1)).astimezone(EASTERN)
                value = float(row['forecast_kw'])
                label = start.strftime('%I %p %Z').lstrip('0')
                hourly.append({'timestamp': start.isoformat(), 'hour': start.hour, 'label': label, 'forecast_kw': value})
                predictions.append({'interval_start': start.isoformat(), 'interval_end': end.isoformat(), 'value': value})
            peak = max(hourly, key=lambda x: x['forecast_kw'])
            total = sum(x['forecast_kw'] for x in hourly)
            weather = {'status': 'unavailable', 'note': 'Weather is optional context; it does not change this proxy.'}
            if self.weather_loader:
                try:
                    weather = self.weather_loader(tomorrow.isoformat())
                except Exception:
                    pass  # A weather outage must not suppress the load forecast.
            data = {
                'status': 'success', 'forecast_type': 'temporary_proxy',
                'location_id': LOCATION, 'unit': 'kW', 'timezone': 'America/New_York',
                'generated_at': now.isoformat(), 'generated_at_utc': now.isoformat(),
                'valid_until': predictions[-1]['interval_end'], 'forecast_date': tomorrow.isoformat(),
                'predictions': predictions, 'hourly': hourly, 'weather': weather,
                'method': result.summary['method'], 'warning': result.summary['warning'],
                'daily_energy_kwh': round(total, 2), 'daily_energy_mwh': round(total / 1000, 3),
                'average_load_kw': total / len(hourly), 'peak_load_kw': peak['forecast_kw'],
                'peak_hour': peak['hour'], 'peak_label': peak['label'],
            }
            validate_forecast(data, now)
            self._cache = data
            return deepcopy(data)


def load_weather(forecast_date):
    from weather_service import fetch_forecast_weather, WeatherConfig
    result = fetch_forecast_weather(forecast_days=2, config=WeatherConfig.from_environment())
    frame = result.hourly.loc[forecast_date]
    return {
        'status': 'available', 'provider': result.provider, 'forecast_date': forecast_date,
        'retrieved_at_utc': result.retrieved_at_utc,
        'temperature_low_f': float(frame['temp_f'].min()),
        'temperature_high_f': float(frame['temp_f'].max()),
        'average_humidity_pct': float(frame['humidity_pct'].mean()),
        'precipitation_total_in': float(frame['precipitation_in'].sum()),
        'note': 'Weather is context only. The temporary proxy is not weather-adjusted.',
    }
