"""Thread-safe simulated load. This module contains no physical relay driver."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from threading import RLock
from forecast_contract import UTC, EASTERN, instant, validate_forecast


class ControlError(ValueError):
    pass


class SimulatedLoad:
    def __init__(self, clock=None, cache_path=None):
        self.clock = clock or (lambda: datetime.now(UTC))
        self.lock = RLock()
        self.cache_path = Path(cache_path) if cache_path else None
        self.forecasts = []
        self.mode = 'manual'
        self.connected = True
        self.state = 'ON'
        self.off_kw, self.on_kw = 250.0, 230.0
        self.minimum_seconds = 60
        self.last_switch = None
        self.replay_wall = self.replay_start = self.replay_end = None
        self.replay_forecast = None
        self.replay_speed = 120  # one forecast hour = 30 real seconds
        self.source_error = None
        self.last_fetch = None
        self.reason = 'Manual mode; automatic control is paused.'
        self.events = []
        if self.cache_path and self.cache_path.exists():
            try:
                for data in json.loads(self.cache_path.read_text()):
                    validate_forecast(data, self.clock())
                    if instant(data['valid_until']) > self.clock():
                        self.forecasts.append(data)
            except (ValueError, OSError, TypeError):
                self.forecasts = []
        self._event('Started in manual mode; state is simulated only.')

    def _event(self, message):
        self.events.append({'timestamp': self.clock().isoformat(), 'message': message})
        self.events = self.events[-60:]

    def ingest(self, payload):
        data = deepcopy(validate_forecast(payload, self.clock()))
        with self.lock:
            now = self.clock()
            if instant(data['valid_until']) <= now:
                raise ValueError('Received forecast has expired.')
            key = data['predictions'][0]['interval_start']
            existing = next((f for f in self.forecasts if f['predictions'][0]['interval_start'] == key), None)
            if existing and instant(existing['generated_at']) > instant(data['generated_at']):
                raise ValueError('Received an older forecast revision.')
            self.forecasts = [f for f in self.forecasts if instant(f['valid_until']) > now and f['predictions'][0]['interval_start'] != key]
            self.forecasts.append(data)
            self.forecasts.sort(key=lambda f: instant(f['generated_at']), reverse=True)
            self.forecasts = self.forecasts[:4]
            self.last_fetch, self.source_error = now.isoformat(), None
            if self.cache_path:
                try:
                    self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                    temp = self.cache_path.with_suffix('.tmp')
                    temp.write_text(json.dumps(self.forecasts), encoding='utf-8')
                    temp.replace(self.cache_path)
                except OSError:
                    self._event('Forecast accepted, but disk cache could not be saved.')

    def fetch_failed(self, message):
        with self.lock:
            self.source_error = str(message)

    def _time(self):
        if self.mode == 'replay':
            return self.replay_start + (self.clock() - self.replay_wall) * self.replay_speed
        return self.clock()

    def _selection(self, now):
        forecasts = [self.replay_forecast] if self.mode == 'replay' else self.forecasts
        for data in forecasts:
            if instant(data['valid_until']) <= now:
                continue
            for row in data['predictions']:
                if instant(row['interval_start']) <= now < instant(row['interval_end']):
                    return data, row, 'valid'
        if not forecasts:
            return None, None, 'unavailable'
        if all(instant(f['valid_until']) <= now for f in forecasts):
            return None, None, 'stale'
        return None, None, 'waiting_for_interval'

    def _switch(self, desired, now):
        if desired == self.state:
            return
        self.state = desired
        self.last_switch = now
        self._event(f'{self.mode}: simulated load switched {desired}.')

    def tick(self):
        with self.lock:
            now = self._time()
            if not self.connected:
                self.reason = 'Simulator disconnected; commands are blocked.'
                return
            if self.mode == 'manual':
                self.reason = 'Manual override; automatic control is paused.'
                return
            _, row, quality = self._selection(now)
            if row is None:
                self.reason = {'stale': 'Forecast expired; holding the simulated state.', 'unavailable': 'No valid forecast; holding the simulated state.', 'waiting_for_interval': 'Waiting for a forecast interval covering the current time.'}[quality]
                if self.mode == 'replay' and now >= self.replay_end:
                    self.reason = 'Replay complete; holding the final simulated state. Choose Manual to exit.'
                return
            kw = row['value']
            desired = 'OFF' if kw >= self.off_kw else 'ON' if kw <= self.on_kw else self.state
            if desired == self.state:
                self.reason = f'{kw:.1f} kW: holding {self.state} under the threshold rules.'
            elif self.last_switch is not None and (now - self.last_switch).total_seconds() < self.minimum_seconds:
                self.reason = 'Minimum switching interval has not elapsed; holding state.'
            else:
                self._switch(desired, now)
                self.reason = f'{kw:.1f} kW: requested and simulated {desired}.'

    def command(self, body):
        if not isinstance(body, dict):
            raise ControlError('Expected a JSON object.')
        with self.lock:
            action = body.get('action')
            if action == 'connection':
                if type(body.get('connected')) is not bool:
                    raise ControlError('connected must be true or false.')
                self.connected = body['connected']
                self.mode = 'manual'  # reconnect never silently resumes automation
                self.last_switch = self.clock()
                self._event('Simulator reconnected; manual mode.' if self.connected else 'Simulator disconnected; automatic mode stopped.')
            elif action == 'configure':
                off, on, seconds = body.get('off_kw'), body.get('on_kw'), body.get('minimum_seconds')
                if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in (off, on, seconds)):
                    raise ControlError('Thresholds and minimum time must be finite numbers.')
                if not 0 <= on < off or not 1 <= seconds <= 3600:
                    raise ControlError('Require 0 <= ON threshold < OFF threshold and 1–3600 seconds.')
                if self.mode != 'manual':
                    raise ControlError('Switch to Manual before changing rules.')
                self.off_kw, self.on_kw, self.minimum_seconds = off, on, seconds
                self._event(f'Rules changed: OFF >= {off} kW, ON <= {on} kW, minimum {seconds} s.')
            elif action in ('mode', 'set_load'):
                if not self.connected:
                    raise ControlError('Simulator is disconnected. Reconnect first.')
                if action == 'set_load':
                    if body.get('state') not in ('ON', 'OFF'):
                        raise ControlError('state must be ON or OFF.')
                    if self.mode != 'manual':
                        self.last_switch = self.clock()  # reset clock domain
                    self.mode = 'manual'
                    self._switch(body['state'], self.clock())
                    self._event('Manual override selected.')
                else:
                    mode = body.get('mode')
                    if mode not in ('manual', 'automatic', 'replay'):
                        raise ControlError('mode must be manual, automatic, or replay.')
                    if mode == 'replay':
                        candidates = [f for f in self.forecasts if instant(f['valid_until']) > self.clock()]
                        if not candidates:
                            raise ControlError('Load a valid forecast before starting replay.')
                        self.replay_forecast = deepcopy(candidates[0])
                        self.replay_start = instant(self.replay_forecast['predictions'][0]['interval_start'])
                        self.replay_end = instant(self.replay_forecast['valid_until'])
                        self.replay_wall = self.clock()
                    previous_mode = self.mode
                    self.mode = mode
                    if mode == 'replay' or previous_mode == 'replay':
                        self.last_switch = self._time()
                    self._event(f'Mode selected: {mode}.')
            else:
                raise ControlError('Unknown command action.')
            self.tick()
            return self.status()

    def status(self):
        with self.lock:
            now = self._time()
            data, row, quality = self._selection(now)
            return {
                'status': 'success', 'simulation': True, 'physical_hardware_connected': False,
                'mode': self.mode, 'connected': self.connected, 'simulated_state': self.state if self.connected else None,
                'commanded_state': self.state, 'illustrative_load_w': 60,
                'reason': self.reason, 'forecast_status': quality,
                'forecast_type': data['forecast_type'] if data else None,
                'generated_at': data['generated_at'] if data else None,
                'active_interval': deepcopy(row), 'effective_time': now.astimezone(EASTERN).isoformat(),
                'clock': 'accelerated_replay' if self.mode == 'replay' else 'real_time',
                'replay_speed': self.replay_speed, 'last_fetch': self.last_fetch,
                'source_error': self.source_error, 'using_cached_forecast': bool(self.source_error and row),
                'cached_forecast_count': len(self.forecasts),
                'rules': {'off_kw': self.off_kw, 'on_kw': self.on_kw, 'minimum_seconds': self.minimum_seconds},
                'events': deepcopy(self.events[-15:]),
            }
