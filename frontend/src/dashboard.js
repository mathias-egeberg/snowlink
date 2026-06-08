/**
 * Dashboard screen – live sensor readings, system status, connection
 * indicators, and clock.
 */
const Dashboard = (() => {
  // ── Helpers ────────────────────────────────────────────────────────
  function el(id) { return document.getElementById(id); }

  function setConnIndicator(indicatorId, ok, warning = false) {
    const el = document.getElementById(indicatorId);
    if (!el) return;
    el.classList.toggle('ok',      ok && !warning);
    el.classList.toggle('warning', !ok && warning);
  }

  function gpsColor(ok, floatRtk) {
    if (ok)       return 'ok';
    if (floatRtk) return 'warning';
    return 'danger';
  }

  const STATUS_CLASS = {
    active:  'active',
    standby: 'standby',
    idle:    'inactive',
    error:   'error',
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
      const te = el('dash-time');
      const de = el('dash-date');
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

  // ── State renderer ─────────────────────────────────────────────────
  function render(s) {
    // Sensor values
    const temp = el('card-temp');
    const snow = el('card-snow');
    const wind = el('card-wind');
    const pwr  = el('card-power');
    const dev  = el('card-devices');
    const evt  = el('card-event');
    if (temp) temp.textContent = (s.temperature_outside >= 0 ? '+' : '') + s.temperature_outside.toFixed(1);
    if (snow) snow.textContent = s.snow_depth_cm.toFixed(1);
    if (wind) wind.textContent = s.wind_speed_ms.toFixed(1);
    if (pwr)  pwr.textContent  = Math.round(s.power_watts);
    if (dev)  dev.textContent  = s.active_devices;
    if (evt)  evt.textContent  = s.last_event;

    // System status dot + label
    const dot   = el('dash-status-dot');
    const label = el('dash-status-text');
    const cls   = STATUS_CLASS[s.system_status] || 'inactive';
    if (dot) {
      dot.className = `status-dot ${cls}`;
    }
    if (label) {
      label.className = `status-label ${cls}`;
      label.textContent = s.system_status.toUpperCase();
    }

    // GPS badge
    const gpsTxt = el('dash-gps-text');
    if (gpsTxt) {
      gpsTxt.textContent = s.gps_status_text;
      gpsTxt.className   = `badge-value ${gpsColor(s.gps_ok, s.gps_float_rtk)}`;
    }

    // Connection indicators
    setConnIndicator('ind-dash-5g',  s.cellular_ok);
    setConnIndicator('ind-dash-gps', s.gps_ok,  s.gps_float_rtk);
    setConnIndicator('ind-dash-imu', s.imu_ok);
  }

  // ── Init ───────────────────────────────────────────────────────────
  AppState.subscribe((s) => {
    if (document.getElementById('screen-dashboard')?.classList.contains('active')) {
      render(s);
    }
  });

  document.addEventListener('screenchange', (e) => {
    if (e.detail === 'dashboard') {
      startClock();
      const s = AppState.get();
      if (s && Object.keys(s).length) render(s);
    } else {
      stopClock();
    }
  });

  // Start clock immediately (dashboard is the initial screen).
  startClock();

  return {};
})();
