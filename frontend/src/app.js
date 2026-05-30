/**
 * Application entry point.
 *
 * Starts the WebSocket and polls the REST API for fresh state snapshots.
 * All screen modules are already loaded by <script> tags above this file.
 */
const STATE_REFRESH_MS = 500;
let _statePollTimer = null;

function startStatePolling() {
  if (_statePollTimer !== null) return;

  let inFlight = false;

  async function refreshState() {
    if (inFlight) return;
    inFlight = true;
    try {
      const snapshot = await API.getState();
      AppState.update(snapshot);
    } catch (e) {
      console.warn('[app] state refresh failed', e);
    } finally {
      inFlight = false;
    }
  }

  refreshState();
  _statePollTimer = setInterval(refreshState, STATE_REFRESH_MS);
}

function stopStatePolling() {
  if (_statePollTimer === null) return;
  clearInterval(_statePollTimer);
  _statePollTimer = null;
}

(() => {
  WS.connect();
  startStatePolling();
})();

window.addEventListener('beforeunload', stopStatePolling);

// ── Scroll handler (touch + mouse) ────────────────────────────────────────
// Pi touchscreens may send mouse events instead of touch events depending on
// the driver (USB HID displays, evdev emulation, etc.).  We handle both.
// _touchFired flag prevents double-scrolling when both APIs fire together.
(function () {
  const SEL = '.settings-scroll, .record-left, .record-right, .gps-overlay-content, .rec-sessions-card';
  const THRESHOLD = 5;   // px of movement before scroll activates

  let _el          = null;
  let _startY      = 0;
  let _lastY       = 0;
  let _lastT       = 0;
  let _vel         = 0;
  let _active      = false;  // threshold crossed?
  let _touchFired  = false;  // suppress mouse if touch already fired

  function _begin(el, y) {
    _el = el; _startY = _lastY = y;
    _lastT = performance.now(); _vel = 0; _active = false;
  }

  function _update(y) {
    if (!_el) return;
    const now = performance.now();
    const dy  = _lastY - y;
    const dt  = now - _lastT;
    if (!_active) {
      if (Math.abs(y - _startY) < THRESHOLD) { _lastY = y; _lastT = now; return; }
      _active = true;
    }
    if (dt > 0) _vel = dy / dt;
    _lastY = y; _lastT = now;
    _el.scrollTop += dy;
  }

  function _finish() {
    if (!_el || !_active) { _el = null; return; }
    const el = _el;
    let   v  = _vel * 16;   // scale px/ms → px/frame at ~60fps
    _el = null; _active = false;
    (function coast() {
      if (Math.abs(v) < 0.5) return;
      el.scrollTop += v;
      v *= 0.92;
      requestAnimationFrame(coast);
    })();
  }

  // ── Touch events ─────────────────────────────────────────────────────────
  document.addEventListener('touchstart', e => {
    _touchFired = true;
    setTimeout(() => { _touchFired = false; }, 500);
    const el = e.touches.length ? e.target.closest(SEL) : null;
    if (el) _begin(el, e.touches[0].clientY); else _el = null;
  }, { passive: true });

  document.addEventListener('touchmove', e => {
    if (e.touches.length) _update(e.touches[0].clientY);
  }, { passive: true });

  document.addEventListener('touchend',    _finish,                          { passive: true });
  document.addEventListener('touchcancel', () => { _el = null; },            { passive: true });

  // ── Mouse events (Pi displays that emulate mouse via USB HID / evdev) ────
  document.addEventListener('mousedown', e => {
    if (_touchFired || e.button !== 0) return;
    const el = e.target.closest(SEL);
    if (el) _begin(el, e.clientY); else _el = null;
  });

  document.addEventListener('mousemove', e => {
    if (_touchFired) return;
    if (!(e.buttons & 1)) { _el = null; return; }
    _update(e.clientY);
  });

  document.addEventListener('mouseup', () => {
    if (!_touchFired) _finish();
  });
})();

// ── Centralised header indicator updates ──────────────────────────────────
// Update every conn-indicator and GPS badge on every screen on every state
// tick, regardless of which screen is active.  Each screen module may also
// do its own rendering, but this ensures the icons are never stale.
(function () {
  const SCREENS = [
    { key: 'dash', gpsId: 'dash-gps-text' },
    { key: 'map',  gpsId: 'map-gps-text'  },
    { key: 'ctrl', gpsId: 'ctrl-gps-text' },
    { key: 'set',  gpsId: 'set-gps-text'  },
    { key: 'rec',  gpsId: 'rec-gps-text'  },
  ];

  function _signalLevel(s) {
    const quality = Number(s.cellular_signal_quality_pct);
    if (Number.isFinite(quality) && quality > 0) {
      if (quality >= 80) return 4;
      if (quality >= 60) return 3;
      if (quality >= 35) return 2;
      return 1;
    }
    if (s.cellular_ok) return 3;
    if (s.cellular_detected) return 1;
    return 0;
  }

  function _setSignalClasses(el, s) {
    for (let level = 0; level <= 4; level += 1) {
      el.classList.remove('signal-level-' + level);
    }
    el.classList.add('signal-level-' + _signalLevel(s));
    el.classList.toggle('speed-testing', !!s.cellular_speed_test_running);
    const status = s.cellular_status_text ?? 'No modem';
    const quality = s.cellular_signal_quality_text ?? 'No signal';
    const speed = s.cellular_speed_test_status ?? 'Never run';
    el.title = `Cellular: ${status} · ${quality} · Speed test: ${speed}`;
  }

  function _setConn(id, ok, warn, s) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('ok',      !!ok && !warn);
    el.classList.toggle('warning', !ok && !!warn);
    if (id.endsWith('-5g') && s) _setSignalClasses(el, s);
  }

  AppState.subscribe(function (s) {
    for (const sc of SCREENS) {
      _setConn('ind-' + sc.key + '-5g',  s.cellular_ok, s.cellular_detected && !s.cellular_ok, s);
      _setConn('ind-' + sc.key + '-gps', s.gps_ok, s.gps_float_rtk);
      _setConn('ind-' + sc.key + '-imu', s.imu_ok);

      const gpsTxt = document.getElementById(sc.gpsId);
      if (gpsTxt) {
        gpsTxt.textContent = s.gps_status_text ?? 'No Fix';
        gpsTxt.className   = 'badge-value ' +
          (s.gps_ok ? 'ok' : s.gps_float_rtk ? 'warning' : 'danger');
      }
    }
  });
}());

(function () {
  function runSpeedTestFromHeader(event) {
    event.preventDefault();
    if (typeof Settings !== 'undefined' && Settings.runCellularSpeedTest) {
      Settings.runCellularSpeedTest();
    }
  }

  for (const el of document.querySelectorAll('[id^="ind-"][id$="-5g"]')) {
    el.classList.add('cellular-action');
    el.setAttribute('role', 'button');
    el.setAttribute('tabindex', '0');
    el.setAttribute('aria-label', 'Run cellular speed test');
    el.addEventListener('click', runSpeedTestFromHeader);
    el.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') runSpeedTestFromHeader(event);
    });
  }
}());

const ExitDialog = (() => {
  const overlay = () => document.getElementById('exit-overlay');

  function show() { overlay().classList.remove('hidden'); }
  function hide() { overlay().classList.add('hidden'); }

  async function minimize() {
    hide();
    try {
      await fetch('/api/minimize', { method: 'POST' });
    } catch (_) { /* ignore */ }
  }

  async function confirm() {
    try {
      await fetch('/api/exit', { method: 'POST' });
    } catch (_) {
      // Backend shut down before the response arrived — that's fine.
    }
    window.close();
  }

  return { show, hide, minimize, confirm };
})();
