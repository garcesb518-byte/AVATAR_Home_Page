/* Simulated load controls. All decisions and state live in the Python backend. */
(() => {
  const panel = document.querySelector('#load-control');
  if (!panel) return;
  const meta = document.querySelector('meta[name="avatar-control-url"]');
  const base = meta?.content?.replace(/\/$/, '');
  const message = document.querySelector('#control-message');
  const buttons = [...panel.querySelectorAll('button')];
  let busy = false;
  let backendFailed = false;
  const text = (id, value) => { document.getElementById(id).textContent = value; };
  const eastern = value => new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'
  }).format(new Date(value));

  if (base === undefined || base === 'disabled') {
    document.getElementById('control-local-setup').hidden = false;
    text('control-connection', 'Local backend required');
    text('control-mode', 'LOCAL DEMONSTRATION');
    message.textContent = 'Simulator is not connected here. Run python server.py and open the local Load Forecasting page to use these controls.';
    buttons.forEach(button => button.disabled = true);
    return;
  }

  async function api(path, body) {
    const response = await fetch(base + path, {
      method: body ? 'POST' : 'GET', cache: 'no-store', signal: AbortSignal.timeout(12000),
      ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})
    });
    const data = await response.json();
    if (!response.ok || data.status === 'error') throw new Error(data.message || 'Backend request failed.');
    return data;
  }

  function show(data) {
    panel.dataset.state = data.simulated_state || 'UNKNOWN';
    text('control-state', data.simulated_state || 'Unknown');
    text('control-mode', data.mode === 'replay' ? 'ACCELERATED REPLAY · 120×' : data.mode.toUpperCase());
    text('control-connection', data.connected ? 'Simulator connected' : 'Simulator disconnected');
    text('control-commanded', data.commanded_state);
    text('control-clock', eastern(data.effective_time));
    text('control-decision', data.reason);
    text('control-forecast-status', data.forecast_status.replaceAll('_', ' '));
    text('control-value', data.active_interval ? `${data.active_interval.value.toFixed(1)} kW` : 'No active interval');
    text('control-interval', data.active_interval ? `${eastern(data.active_interval.interval_start)} – ${eastern(data.active_interval.interval_end)}` : 'Tomorrow’s forecast waits until its interval starts in automatic mode.');
    text('control-source', data.forecast_type === 'temporary_proxy' ? 'Temporary proxy · not trained on campus measurements' : data.forecast_type || 'Awaiting applicable forecast');
    text('control-rules', `OFF at ≥ ${data.rules.off_kw} kW; ON at ≤ ${data.rules.on_kw} kW; otherwise hold. Minimum ${data.rules.minimum_seconds} seconds between automatic switches (${data.mode === 'replay' ? 'forecast time' : 'real time'}).`);
    text('control-fetch', data.last_fetch ? `Last successful forecast fetch: ${eastern(data.last_fetch)}` : 'Waiting for the first forecast fetch.');
    text('control-warning', data.source_error ? `${data.using_cached_forecast ? 'Using a still-valid cached interval. ' : ''}Forecast fetch failed: ${data.source_error}` : '');
    for (const [id, key] of [['rule-off','off_kw'],['rule-on','on_kw'],['rule-minimum','minimum_seconds']]) {
      const input = document.getElementById(id);
      if (!input.dataset.edited && document.activeElement !== input) input.value = data.rules[key];
    }
    buttons.forEach(button => {
      button.disabled = busy || (!data.connected && button.dataset.action !== 'reconnect') ||
        (button.dataset.action === 'replay' && !data.cached_forecast_count) ||
        (button.dataset.action === 'configure' && data.mode !== 'manual');
    });
    panel.querySelectorAll('[data-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === data.mode)));
    const log = document.querySelector('#control-events');
    log.replaceChildren(...data.events.slice().reverse().map(event => {
      const li = document.createElement('li');
      li.textContent = `${eastern(event.timestamp)} · ${event.message}`;
      return li;
    }));
  }
  async function poll() {
    try {
      show(await api('/api/status'));
      if (backendFailed) message.textContent = 'Backend reconnected.';
      backendFailed = false;
    }
    catch (error) {
      backendFailed = true;
      text('control-connection', 'Backend unavailable');
      text('control-state', 'Unknown');
      panel.dataset.state = 'UNKNOWN';
      buttons.forEach(button => button.disabled = true);
      message.textContent = 'Cannot reach the backend. Start python server.py to reconnect. Last displayed values may be outdated.';
    }
  }
  panel.querySelectorAll('input').forEach(input => input.addEventListener('input', () => { input.dataset.edited = 'true'; }));
  panel.addEventListener('click', async event => {
    const button = event.target.closest('button[data-action]');
    if (!button || busy || button.disabled) return;
    const action = button.dataset.action;
    let body;
    if (['manual','automatic','replay'].includes(action)) body = {action:'mode',mode:action};
    if (['on','off'].includes(action)) body = {action:'set_load',state:action.toUpperCase()};
    if (['disconnect','reconnect'].includes(action)) body = {action:'connection',connected:action === 'reconnect'};
    if (action === 'configure') {
      const inputs = ['rule-off','rule-on','rule-minimum'].map(id => document.getElementById(id));
      if (!inputs.every(input => input.reportValidity())) return;
      body = {action:'configure',off_kw:Number(inputs[0].value),on_kw:Number(inputs[1].value),minimum_seconds:Number(inputs[2].value)};
    }
    busy = true;
    buttons.forEach(item => item.disabled = true);
    try {
      const data = await api('/api/command', body);
      if (action === 'configure') panel.querySelectorAll('input').forEach(input => delete input.dataset.edited);
      message.textContent = 'Simulated controller accepted the request.';
      busy = false;
      show(data);
    } catch (error) { busy = false; message.textContent = error.message; await poll(); }
  });
  // Sequential polling avoids overlapping requests when the server is slow.
  async function loop() { if (!busy) await poll(); setTimeout(loop, 2000); }
  loop();
})();
