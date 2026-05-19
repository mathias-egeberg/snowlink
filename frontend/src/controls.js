/**
 * Control screen – device toggle buttons.
 *
 * Each button calls API.toggleDevice(), then waits for the WebSocket
 * state update to reflect the new value (no optimistic UI needed because
 * the WS broadcast arrives within ~200 ms).
 */
const Controls = (() => {
  const DEVICE_MAP = {
    heat_roof_on:    'toggle-heat-roof',
    heat_gutter_on:  'toggle-heat-gutter',
    pump_on:         'toggle-pump',
    sensor_light_on: 'toggle-sensor-light',
  };

  // ── Clock ──────────────────────────────────────────────────────────
  let _clockTimer = null;
  function startClock() {
    function tick() {
      const now = new Date();
      const t = now.toLocaleTimeString('en-GB', { hour12: false });
      const d = now.toLocaleDateString('en-GB', {
        weekday: 'long', day: '2-digit', month: 'short', year: 'numeric',
      });
      const te = document.getElementById('ctrl-time');
      const de = document.getElementById('ctrl-date');
      if (te) te.textContent = t;
      if (de) de.textContent = d;
    }
    tick();
    _clockTimer = setInterval(tick, 1000);
  }
  function stopClock() {
    clearInterval(_clockTimer);
    _clockTimer = null;
  }

  // ── Render ─────────────────────────────────────────────────────────
  function render(s) {
    for (const [key, btnId] of Object.entries(DEVICE_MAP)) {
      const btn = document.getElementById(btnId);
      if (!btn) continue;
      const isOn = !!s[key];
      btn.textContent = isOn ? 'ON' : 'OFF';
      btn.className   = `toggle-btn ${isOn ? 'on' : 'off'}`;
    }

    // GPS badge
    const gpsTxt = document.getElementById('ctrl-gps-text');
    if (gpsTxt) {
      gpsTxt.textContent = s.gps_status_text;
      const cls = s.gps_ok ? 'ok' : (s.gps_float_rtk ? 'warning' : 'danger');
      gpsTxt.className = `badge-value ${cls}`;
    }

    // Connection indicators
    _setConn('ind-ctrl-5g',  s.cellular_ok);
    _setConn('ind-ctrl-gps', s.gps_ok, s.gps_float_rtk);
    _setConn('ind-ctrl-imu', s.imu_ok);
  }

  function _setConn(id, ok, warn = false) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('ok',      ok && !warn);
    el.classList.toggle('warning', !ok && warn);
  }

  // ── Toggle action ──────────────────────────────────────────────────
  async function toggle(btn) {
    const key = btn.dataset.device;
    btn.disabled = true;
    try {
      await API.toggleDevice(key);
    } catch (e) {
      console.error('[Controls] toggle error', e);
    } finally {
      btn.disabled = false;
    }
  }

  // ── Subscriptions ──────────────────────────────────────────────────
  AppState.subscribe((s) => {
    if (document.getElementById('screen-control')?.classList.contains('active')) {
      render(s);
    }
  });

  document.addEventListener('screenchange', (e) => {
    if (e.detail === 'control') {
      startClock();
      const s = AppState.get();
      if (s && Object.keys(s).length) render(s);
    } else {
      stopClock();
    }
  });

  return { toggle };
})();
