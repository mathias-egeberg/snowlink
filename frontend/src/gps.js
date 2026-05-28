/**
 * GPS Status sub-page.
 *
 * Shown as a full-screen overlay when the user taps any GPS indicator in
 * the header or the "GPS Status" button in Settings.  Back button returns
 * to whichever screen was active before.
 */
const GpsStatus = (() => {
  let _previousScreen = 'dashboard';

  // ── Open / close ───────────────────────────────────────────────────

  function open() {
    _previousScreen = Nav.current();
    document.getElementById('gps-overlay').classList.remove('hidden');
    const s = AppState.get();
    if (s && Object.keys(s).length) _render(s);
  }

  function close() {
    document.getElementById('gps-overlay').classList.add('hidden');
    Nav.go(_previousScreen);
  }

  // ── Rendering ──────────────────────────────────────────────────────

  function _fixClass(s) {
    if (s.gps_ok)        return 'ok';
    if (s.gps_float_rtk) return 'warning';
    return 'danger';
  }

  function _set(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function _fmt(val, decimals) {
    const n = Number(val);
    return Number.isFinite(n) ? n.toFixed(decimals) : '--';
  }

  function _render(s) {
    const overlay = document.getElementById('gps-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;

    const cls = _fixClass(s);

    // Banner
    const banner = document.getElementById('gps-banner-status');
    if (banner) {
      banner.textContent = s.gps_status_text ?? 'No Fix';
      banner.className = `gps-banner-status ${cls}`;
    }
    const icon = document.getElementById('gps-banner-icon');
    if (icon) icon.className = `gps-banner-icon ${cls}`;

    // Metrics
    _set('gps-satellites', s.gps_satellites ?? '--');
    _set('gps-hdop', _fmt(s.gps_hdop, 2));
    _set('gps-fix-quality', s.gps_fix_quality ?? '--');
    _set('gps-altitude', _fmt(s.gps_altitude_m, 1));

    // Coordinates — show 6 decimal places
    const lat = Number(s.gps_lat);
    const lon = Number(s.gps_lon);
    _set('gps-lat', Number.isFinite(lat) ? lat.toFixed(6) : '--');
    _set('gps-lon', Number.isFinite(lon) ? lon.toFixed(6) : '--');

    // NTRIP row
    const ntripStatus = document.getElementById('gps-ntrip-status');
    if (ntripStatus) {
      ntripStatus.textContent = s.ntrip_status_text ?? 'Disabled';
      ntripStatus.className = `settings-row-desc ${s.ntrip_ok ? 'ok' : ''}`
        .replace(/\s+ok$/, s.ntrip_ok ? ' cellular-status ok' : '');
    }

    // Device connection row
    _set('gps-device-status', s.gps_ok || s.gps_float_rtk
      ? 'Connected'
      : (s.gps_status_text === 'Disconnected' || s.gps_status_text === 'No Fix' ? s.gps_status_text : 'Disconnected'));
    const devStatus = document.getElementById('gps-device-status');
    if (devStatus) {
      devStatus.className = `settings-row-desc cellular-status ${
        s.gps_ok ? 'ok' : (s.gps_float_rtk ? 'warning' : 'danger')
      }`;
    }
  }

  // ── Live updates ───────────────────────────────────────────────────

  AppState.subscribe((s) => {
    const overlay = document.getElementById('gps-overlay');
    if (overlay && !overlay.classList.contains('hidden')) {
      _render(s);
    }
  });

  return { open, close };
})();
