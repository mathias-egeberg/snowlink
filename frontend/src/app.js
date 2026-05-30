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

// ── Document-level touch scroll ───────────────────────────────────────────
// Mirrors the pattern used by the working keyboard handler: document-level
// touchstart/touchmove with e.target.closest() to find the scroll container.
// This is proven to fire on the Pi because the keyboard uses the same API.
(function () {
  const SEL = '.settings-scroll, .record-left, .record-right, .gps-overlay-content, .rec-sessions-card';

  let _el      = null;   // active scroll container
  let _startY  = 0;
  let _startT  = 0;
  let _lastY   = 0;
  let _lastT   = 0;
  let _vel     = 0;      // px / ms
  let _moved   = false;

  document.addEventListener('touchstart', e => {
    if (!e.touches.length) return;
    const el = e.target.closest(SEL);
    if (!el) { _el = null; return; }
    _el      = el;
    _startY  = _lastY = e.touches[0].clientY;
    _startT  = _lastT = performance.now();
    _vel     = 0;
    _moved   = false;
  }, { passive: true });

  document.addEventListener('touchmove', e => {
    if (!_el || !e.touches.length) return;
    const y   = e.touches[0].clientY;
    const now = performance.now();
    const dt  = now - _lastT;
    if (dt > 0) _vel = (_lastY - y) / dt;
    const dy = _lastY - y;
    _lastY   = y;
    _lastT   = now;
    _el.scrollTop += dy;
    _moved   = true;
  }, { passive: true });

  document.addEventListener('touchend', () => {
    if (!_el || !_moved) { _el = null; return; }
    // Capture scroll target in closure so it persists during coast.
    const el  = _el;
    let   vel = _vel * 16;   // scale to ~px/frame at 60 fps
    _el = null;

    (function coast() {
      if (Math.abs(vel) < 0.5) return;
      el.scrollTop += vel;
      vel *= 0.92;
      requestAnimationFrame(coast);
    })();
  }, { passive: true });

  document.addEventListener('touchcancel', () => { _el = null; }, { passive: true });
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

  async function confirm() {
    try {
      await fetch('/api/exit', { method: 'POST' });
    } catch (_) {
      // Backend shut down before the response arrived — that's fine.
    }
    window.close();
  }

  return { show, hide, confirm };
})();
