/**
 * Application entry point.
 *
 * Starts the WebSocket and loads the initial state from the REST API.
 * All screen modules are already loaded by <script> tags above this file.
 */
(async () => {
  WS.connect();

  try {
    const snapshot = await API.getState();
    AppState.update(snapshot);
  } catch (e) {
    console.warn('[app] initial state fetch failed', e);
  }
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
  ];

  function _setConn(id, ok, warn) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('ok',      !!ok && !warn);
    el.classList.toggle('warning', !ok && !!warn);
  }

  AppState.subscribe(function (s) {
    for (const sc of SCREENS) {
      _setConn('ind-' + sc.key + '-5g',  s.cellular_ok);
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
