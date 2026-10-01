import copy
from datetime import datetime, timedelta
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from forecast_contract import UTC, EASTERN, LOCATION, instant, validate_forecast
from forecast_service import ForecastService, load_weather
from load_control import SimulatedLoad, ControlError
from server import create_app


class Clock:
    def __init__(self): self.now = datetime(2026, 10, 1, 14, tzinfo=UTC)
    def __call__(self): return self.now
    def advance(self, seconds): self.now += timedelta(seconds=seconds)


def forecast(clock, values=(260, 240, 220), offset=0):
    start = clock().replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)
    rows = []
    for i, value in enumerate(values):
        a, b = start + timedelta(hours=i), start + timedelta(hours=i+1)
        rows.append({'interval_start': a.astimezone(EASTERN).isoformat(), 'interval_end': b.astimezone(EASTERN).isoformat(), 'value': value})
    return {'status':'success','forecast_type':'temporary_proxy','location_id':LOCATION,'unit':'kW','timezone':'America/New_York','generated_at':clock().isoformat(),'valid_until':rows[-1]['interval_end'],'predictions':rows}


class ControllerTests(unittest.TestCase):
    def setUp(self): self.clock = Clock(); self.c = SimulatedLoad(self.clock)
    def auto(self): self.c.command({'action':'mode','mode':'automatic'})

    def test_off_hold_recovery(self):
        self.c.ingest(forecast(self.clock)); self.auto()
        self.assertEqual(self.c.state, 'OFF')
        self.clock.advance(3600); self.c.tick(); self.assertEqual(self.c.state, 'OFF')
        self.clock.advance(3600); self.c.tick(); self.assertEqual(self.c.state, 'ON')

    def test_threshold_boundaries(self):
        self.c.ingest(forecast(self.clock, (250, 230))); self.auto()
        self.assertEqual(self.c.state,'OFF')
        self.clock.advance(3600); self.c.tick(); self.assertEqual(self.c.state,'ON')

    def test_minimum_time_on_revision(self):
        self.c.ingest(forecast(self.clock)); self.auto()
        self.clock.advance(10); self.c.ingest(forecast(self.clock,(220,))); self.c.tick()
        self.assertEqual(self.c.state,'OFF')
        self.clock.advance(50); self.c.tick(); self.assertEqual(self.c.state,'ON')

    def test_tomorrow_does_not_switch_today(self):
        self.c.ingest(forecast(self.clock,(300,),offset=24)); self.auto()
        self.assertEqual(self.c.state,'ON')
        self.assertEqual(self.c.status()['forecast_status'],'waiting_for_interval')

    def test_current_cache_survives_next_day_forecast(self):
        self.c.ingest(forecast(self.clock)); self.c.ingest(forecast(self.clock,(100,),offset=24)); self.auto()
        self.assertEqual(self.c.state,'OFF')

    def test_failure_uses_cache_until_expiry(self):
        self.c.ingest(forecast(self.clock,(260,))); self.auto()
        self.c.fetch_failed('HTTP 500'); self.c.tick()
        self.assertTrue(self.c.status()['using_cached_forecast'])
        self.clock.advance(3600); self.c.tick()
        self.assertEqual(self.c.status()['forecast_status'],'stale')
        self.assertEqual(self.c.state,'OFF')
        self.assertFalse(self.c.status()['using_cached_forecast'])

    def test_manual_override_and_disconnect(self):
        self.c.ingest(forecast(self.clock)); self.auto()
        self.c.command({'action':'set_load','state':'ON'}); self.c.tick()
        self.assertEqual((self.c.state,self.c.mode),('ON','manual'))
        self.c.command({'action':'connection','connected':False})
        with self.assertRaises(ControlError): self.auto()
        self.assertIsNone(self.c.status()['simulated_state'])
        self.c.command({'action':'connection','connected':True})
        self.assertEqual(self.c.mode,'manual')

    def test_replay_and_return_to_wall_clock(self):
        self.c.ingest(forecast(self.clock,(260,220),offset=24))
        self.c.command({'action':'mode','mode':'replay'})
        self.clock.advance(1); self.c.tick(); self.assertEqual(self.c.state,'OFF')
        self.clock.advance(30); self.c.tick(); self.assertEqual(self.c.state,'ON')
        self.clock.advance(30); self.c.tick(); self.assertIn('Replay complete',self.c.reason)
        self.c.command({'action':'mode','mode':'automatic'})
        self.assertEqual(self.c.status()['clock'],'real_time')
        self.assertEqual(self.c.status()['forecast_status'],'waiting_for_interval')

    def test_invalid_rules_and_forecasts(self):
        for x in [float('nan'), float('inf'), -1, True, '260']:
            with self.assertRaises(ValueError): self.c.ingest(forecast(self.clock,(x,)))
        for field, value in [('unit','MW'),('location_id','building'),('generated_at',None),('status','temporary_proxy')]:
            data=forecast(self.clock); data[field]=value
            with self.assertRaises(ValueError): self.c.ingest(data)
        data=forecast(self.clock); data['predictions'][1]['interval_start']=data['predictions'][0]['interval_start']
        with self.assertRaises(ValueError): self.c.ingest(data)
        with self.assertRaises(ControlError): self.c.command({'action':'configure','off_kw':200,'on_kw':250,'minimum_seconds':60})
        self.assertEqual(self.c.off_kw,250)

    def test_disk_cache_restart_manual(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'forecast.json'; c=SimulatedLoad(self.clock,path)
            c.ingest(forecast(self.clock)); c.command({'action':'mode','mode':'automatic'})
            restarted=SimulatedLoad(self.clock,path)
            self.assertEqual(restarted.mode,'manual'); self.assertEqual(len(restarted.forecasts),1)
            self.clock.advance(4*3600)
            self.assertEqual(SimulatedLoad(self.clock,path).forecasts,[])

    def test_repeated_fetch_cannot_reset_dwell(self):
        data=forecast(self.clock); self.c.ingest(data); self.auto(); switched=self.c.last_switch
        self.clock.advance(5); self.c.ingest(data); self.c.tick()
        self.assertEqual(self.c.last_switch,switched)


class ForecastTests(unittest.TestCase):
    def test_contract_calendar_days_energy_and_cache(self):
        for today, count in [('2026-10-01',24),('2026-03-07',23),('2026-10-31',25)]:
            now=datetime.fromisoformat(today+'T12:00:00+00:00'); service=ForecastService()
            data=service.get(now); validate_forecast(data,now)
            self.assertEqual(len(data['predictions']),count)
            self.assertEqual(data['daily_energy_kwh'],5500)
            self.assertEqual(instant(data['valid_until']).astimezone(EASTERN).hour,0)
            self.assertEqual(service.get(now+timedelta(minutes=10))['generated_at'],data['generated_at'])
            self.assertEqual(len(set(x['interval_start'] for x in data['predictions'])),count)

    def test_weather_failure_does_not_fail_forecast(self):
        def fail(_): raise RuntimeError('offline')
        data=ForecastService(weather_loader=fail).get()
        self.assertEqual(data['status'],'success'); self.assertEqual(data['weather']['status'],'unavailable')

    def test_weather_mapping(self):
        import pandas as pd
        from types import SimpleNamespace
        frame=pd.DataFrame({'temp_f':[50,70],'humidity_pct':[40,60],'precipitation_in':[0.1,0.2]},index=pd.date_range('2026-10-02',periods=2,freq='h',tz=EASTERN))
        result=SimpleNamespace(hourly=frame,provider='test',retrieved_at_utc='2026-10-01T12:00:00+00:00')
        with patch('weather_service.fetch_forecast_weather',return_value=result):
            weather=load_weather('2026-10-02')
        self.assertEqual(weather['temperature_low_f'],50)
        self.assertAlmostEqual(weather['precipitation_total_in'],0.3)


class APITests(unittest.TestCase):
    def setUp(self):
        self.c=SimulatedLoad(); self.app=create_app(self.c,ForecastService()); self.client=self.app.test_client()

    def test_full_api_path(self):
        payload=self.client.get('/api/forecast').get_json(); self.c.ingest(payload)
        response=self.client.post('/api/command',json={'action':'mode','mode':'replay'})
        self.assertEqual(response.status_code,200); self.assertEqual(response.get_json()['mode'],'replay')
        self.assertTrue(self.client.get('/api/status').get_json()['simulation'])
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual(self.client.post('/api/command',json={'action':'set_load','state':'OFF'}).get_json()['simulated_state'],'OFF')

    def test_invalid_and_cross_origin_commands(self):
        for body in [None,[],{'action':'wrong'},{'action':'set_load','state':'BOGUS'}]:
            self.assertEqual(self.client.post('/api/command',data=json.dumps(body),content_type='application/json').status_code,400)
        self.assertEqual(self.client.post('/api/command',data='x').status_code,415)
        self.assertEqual(self.client.post('/api/command',json={'action':'mode','mode':'automatic'},headers={'Origin':'https://example.com'}).status_code,403)

    def test_site_and_no_source_exposure(self):
        page=self.client.get('/load-forecasting').get_data(as_text=True)
        self.assertIn('name="avatar-control-url" content=""',page)
        self.assertIn('name="avatar-api-url" content=""',page)
        self.assertIn('id="load-control"',page)
        for path in ['/server.py','/instance/forecast-cache.json','/.env','/../server.py']:
            self.assertEqual(self.client.get(path).status_code,404)
        with self.client.get('/static/load-control.js') as response:
            self.assertEqual(response.status_code,200)


class WorkerTests(unittest.TestCase):
    def test_control_runs_without_browser(self):
        from threading import Event, Thread
        from werkzeug.serving import make_server
        from control_api import ControlWorker
        clock=Clock(); c=SimulatedLoad(clock); switched=Event()
        original=c._switch
        def observe(desired, now):
            original(desired,now); switched.set()
        c._switch=observe
        class Source:
            def get(self): return forecast(clock,(260,))
        app=create_app(c,Source()); server=make_server('127.0.0.1',0,app,threaded=True)
        thread=Thread(target=server.serve_forever,daemon=True); thread.start()
        worker=ControlWorker(c,f'http://127.0.0.1:{server.server_port}/api/forecast',poll_seconds=1)
        try:
            c.command({'action':'mode','mode':'automatic'})
            worker.start()
            self.assertTrue(switched.wait(5),'Background worker failed to switch load')
            self.assertEqual(c.state,'OFF')
            self.assertIsNotNone(c.last_fetch)
        finally:
            worker.stop(); server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__=='__main__': unittest.main()
