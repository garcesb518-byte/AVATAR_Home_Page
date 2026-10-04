"""Local simulator HTTP API and independent control worker."""
import json
from threading import Event, Thread
from urllib.request import urlopen
from urllib.parse import urlsplit
from flask import Blueprint, jsonify, request
from load_control import ControlError


def register_control(app, controller, forecast_service):
    api = Blueprint('load_control', __name__)

    @api.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @api.get('/api/forecast')
    def forecast():
        try:
            return jsonify(forecast_service.get())
        except Exception:
            app.logger.exception('Forecast generation failed')
            return jsonify(status='error', message='Forecast generation failed.'), 500

    @api.get('/api/status')
    def status():
        return jsonify(controller.status())

    @api.post('/api/command')
    def command():
        # Local-only simulator: cross-origin web pages cannot issue commands.
        origin = request.headers.get('Origin')
        if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
            return jsonify(status='error', message='Use controls on this server origin.'), 403
        if not request.is_json:
            return jsonify(status='error', message='Send application/json.'), 415
        try:
            result = controller.command(request.get_json(silent=True))
            return jsonify(result)
        except ControlError as exc:
            return jsonify(status='error', message=str(exc)), 400

    app.register_blueprint(api)
    app.extensions['load_controller'] = controller


class ControlWorker:
    """Poll HTTP forecasts every 60 s; evaluate control each second without a tab."""
    def __init__(self, controller, forecast_url, poll_seconds=60):
        parsed = urlsplit(forecast_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Forecast URL must be an HTTP(S) endpoint.')
        self.controller, self.forecast_url = controller, forecast_url
        self.poll_seconds = poll_seconds
        self.stop_event = Event()
        self.threads = []

    def start(self):
        def fetch_loop():
            while not self.stop_event.is_set():
                try:
                    with urlopen(self.forecast_url, timeout=30) as response:
                        raw = response.read(1024 * 1024 + 1)
                        if len(raw) > 1024 * 1024:
                            raise ValueError('Forecast response too large.')
                        self.controller.ingest(json.loads(raw))
                except Exception as exc:
                    self.controller.fetch_failed(str(exc))
                self.stop_event.wait(self.poll_seconds)
        def control_loop():
            while not self.stop_event.is_set():
                self.controller.tick()
                self.stop_event.wait(1)
        for target in (fetch_loop, control_loop):
            thread = Thread(target=target, daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
