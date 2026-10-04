"""Forecast contract shared by the HTTP producer and simulated controller."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

UTC = timezone.utc
EASTERN = ZoneInfo('America/New_York')
LOCATION = 'villanova_main_campus'


def instant(value):
    if not isinstance(value, str):
        raise ValueError('Forecast timestamps must be ISO 8601 strings with an offset.')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid forecast timestamp.') from exc
    if result.tzinfo is None:
        raise ValueError('Forecast timestamps require a UTC offset.')
    return result.astimezone(UTC)


def validate_forecast(data, now=None):
    """Reject ambiguous units/timing; never stamp old data with a new generation time."""
    if not isinstance(data, dict) or data.get('status') != 'success':
        raise ValueError('Forecast status must be success.')
    if data.get('location_id') != LOCATION or data.get('unit') != 'kW':
        raise ValueError('Expected villanova_main_campus hourly demand in kW.')
    if data.get('timezone') != 'America/New_York':
        raise ValueError('Expected America/New_York timezone.')
    if data.get('forecast_type') not in ('temporary_proxy', 'trained_model'):
        raise ValueError('Forecast must identify temporary_proxy or trained_model.')
    generated = instant(data.get('generated_at'))
    if 'generated_at_utc' in data and instant(data['generated_at_utc']) != generated:
        raise ValueError('Generation timestamps disagree.')
    if generated > (now or datetime.now(UTC)) + timedelta(minutes=5):
        raise ValueError('Forecast generation time is in the future.')
    valid_until = instant(data.get('valid_until'))
    rows = data.get('predictions')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 25:
        raise ValueError('Expected 1 to 25 explicit hourly intervals.')
    previous = None
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid prediction row.')
        start, end = instant(row.get('interval_start')), instant(row.get('interval_end'))
        value = row.get('value')
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value) or value < 0:
            raise ValueError('Forecast values must be finite, nonnegative kW numbers.')
        if end - start != timedelta(hours=1) or (previous is not None and start != previous):
            raise ValueError('Forecast intervals must be consecutive, non-overlapping hours.')
        # Enforce real Eastern offsets, including the repeated autumn hour.
        for field, dt in [('interval_start', start), ('interval_end', end)]:
            supplied = datetime.fromisoformat(row[field].replace('Z', '+00:00'))
            if supplied.utcoffset() != dt.astimezone(EASTERN).utcoffset():
                raise ValueError('Forecast interval offset does not match Eastern Time.')
        previous = end
    if valid_until != previous or generated >= valid_until:
        raise ValueError('valid_until must be the final interval end, after generation.')
    return data
