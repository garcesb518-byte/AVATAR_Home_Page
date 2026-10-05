"""Run the integrated AVATAR site and simulated load locally: python server.py."""
import os
from pathlib import Path
from flask import Flask, abort, send_from_directory
from werkzeug.serving import make_server
from control_api import ControlWorker, register_control
from forecast_service import ForecastService, load_weather
from load_control import SimulatedLoad

ROOT = Path(__file__).resolve().parent


def create_app(controller=None, forecast_service=None):
    app = Flask(__name__, static_folder=str(ROOT / 'static'))
    controller = controller or SimulatedLoad(cache_path=ROOT / 'instance' / 'forecast-cache.json')
    forecast_service = forecast_service or ForecastService(
        weather_loader=None if os.getenv('AVATAR_WEATHER_ENABLED', '1') == '0' else load_weather)
    register_control(app, controller, forecast_service)

    @app.get('/')
    def home():
        return send_from_directory(ROOT, 'index.html')

    @app.get('/load-forecasting')
    def forecasting_alias():
        return page('load-forecasting.html')

    @app.get('/<name>.html')
    def html_page(name):
        return page(name + '.html')

    def page(filename):
        if filename not in {p.name for p in ROOT.glob('*.html')}:
            abort(404)
        if filename == 'load-forecasting.html':
            html = (ROOT / filename).read_text(encoding='utf-8')
            # Local forecast and control use the same origin; other tools retain their configured API.
            html = html.replace('name="avatar-api-url" content="https://avatar-api-5i2l.onrender.com"',
                                'name="avatar-api-url" content=""')
            html = html.replace('name="avatar-control-url" content="disabled"',
                                'name="avatar-control-url" content=""')
            return html
        return send_from_directory(ROOT, filename)

    return app


def main():
    port = int(os.getenv('AVATAR_SIM_PORT', '8000'))
    app = create_app()
    # Bind before the worker makes its first HTTP request. One controller per process.
    server = make_server('127.0.0.1', port, app, threaded=True)
    worker = ControlWorker(app.extensions['load_controller'], f'http://127.0.0.1:{port}/api/forecast')
    worker.start()
    print(f'AVATAR simulation: open http://127.0.0.1:{port}/load-forecasting (Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        server.server_close()


if __name__ == '__main__':
    main()
