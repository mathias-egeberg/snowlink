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

// ── Pointer-event scroll for overflow containers ──────────────────────────
// Uses PointerEvents + setPointerCapture so all move events are routed to
// the scroll element regardless of where the finger travels.  CSS sets
// touch-action:none on these containers so the browser does not interfere.
(function () {
  const SELECTORS = [
    '.settings-scroll',
    '.record-left',
    '.record-right',
    '.gps-overlay-content',
    '.rec-sessions-card',
  ];

  function _enable(el) {
    if (el._ptrScroll) return;
    el._ptrScroll = true;

    let ptId     = null;
    let startY   = 0;
    let startTop = 0;
    let lastY    = 0;
    let lastT    = 0;
    let vel      = 0;   // px / ms
    let rafId    = null;

    el.addEventListener('pointerdown', e => {
      if (e.pointerType === 'mouse') return;   // let wheel handle mouse
      if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
      ptId     = e.pointerId;
      startY   = lastY = e.clientY;
      startTop = el.scrollTop;
      lastT    = performance.now();
      vel      = 0;
      el.setPointerCapture(e.pointerId);
    });

    el.addEventListener('pointermove', e => {
      if (e.pointerId !== ptId) return;
      const now = performance.now();
      const dt  = now - lastT;
      if (dt > 0) vel = (lastY - e.clientY) / dt;
      lastY = e.clientY;
      lastT = now;
      el.scrollTop = startTop + (startY - e.clientY);
    });

    function _coast() {
      if (Math.abs(vel) < 0.05) { rafId = null; return; }
      el.scrollTop += vel * 16;   // ~60 fps frame
      vel *= 0.92;                // damping factor
      rafId = requestAnimationFrame(_coast);
    }

    el.addEventListener('pointerup', e => {
      if (e.pointerId !== ptId) return;
      ptId  = null;
      rafId = requestAnimationFrame(_coast);
    });

    el.addEventListener('pointercancel', e => {
      if (e.pointerId !== ptId) return;
      ptId = null; vel = 0;
    });

    el.addEventListener('lostpointercapture', e => {
      if (e.pointerId !== ptId) return;
      ptId = null;
    });
  }

  function _applyAll() {
    SELECTORS.forEach(sel => document.querySelectorAll(sel).forEach(_enable));
  }

  _applyAll();
  document.addEventListener('screenchange', _applyAll);
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
