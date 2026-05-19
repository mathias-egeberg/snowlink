/**
 * Settings screen – NTRIP configuration, map marker style, IMU heading
 * calibration, and app version display.
 *
 * Settings are loaded from the REST API on first entry and persisted via
 * POST /api/settings.  IMU status is kept live from the WebSocket.
 */
const Settings = (() => {
  let _ntripEnabled     = false;
  let _markerStyle      = 'snowcat';
  let _imuHeadingEnabled = false;
  let _loaded            = false;

  // ── Clock ──────────────────────────────────────────────────────────
  let _clockTimer = null;
  function startClock() {
    function tick() {
      const now = new Date();
      const t = now.toLocaleTimeString('en-GB', { hour12: false });
      const d = now.toLocaleDateString('en-GB', {
        weekday: 'long', day: '2-digit', month: 'short', year: 'numeric',
      });
      const te = document.getElementById('set-time');
      const de = document.getElementById('set-date');
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

  // ── Load settings from backend ─────────────────────────────────────
  async function loadSettings() {
    try {
      const data = await API.getSettings();
      _ntripEnabled      = data.ntrip?.enabled ?? false;
      _markerStyle       = data.map?.marker_style ?? 'snowcat';
      _imuHeadingEnabled = data.map?.imu_heading_enabled ?? false;

      document.getElementById('inp-host').value      = data.ntrip?.host       ?? '';
      document.getElementById('inp-port').value      = data.ntrip?.port       ?? '2101';
      document.getElementById('inp-mountpoint').value = data.ntrip?.mountpoint ?? '';
      document.getElementById('inp-username').value  = data.ntrip?.username   ?? '';
      document.getElementById('inp-password').value  = data.ntrip?.password   ?? '';

      _renderNtripBtn();
      _renderMarkerBtn();
      _renderImuHeadingBtn();
      _loaded = true;
    } catch (e) {
      console.error('[Settings] load error', e);
    }
  }

  // ── NTRIP ──────────────────────────────────────────────────────────
  function toggleNtrip() {
    _ntripEnabled = !_ntripEnabled;
    _renderNtripBtn();
  }

  function _renderNtripBtn() {
    const btn = document.getElementById('btn-ntrip-toggle');
    if (!btn) return;
    btn.textContent = _ntripEnabled ? 'Enabled' : 'Disabled';
    btn.className   = `pill-btn ${_ntripEnabled ? 'success' : 'danger'}`;
  }

  async function saveNtrip() {
    const btn = document.getElementById('btn-save-ntrip');
    if (btn) { btn.textContent = 'Saving…'; btn.disabled = true; }
    try {
      await API.postSettings({
        ntrip: {
          enabled:    _ntripEnabled,
          host:       document.getElementById('inp-host').value.trim(),
          port:       document.getElementById('inp-port').value.trim(),
          mountpoint: document.getElementById('inp-mountpoint').value.trim(),
          username:   document.getElementById('inp-username').value.trim(),
          password:   document.getElementById('inp-password').value,
        },
      });
      if (btn) btn.textContent = 'Saved';
      setTimeout(() => { if (btn) { btn.textContent = 'Save'; btn.disabled = false; } }, 1500);
    } catch (e) {
      console.error('[Settings] saveNtrip error', e);
      if (btn) { btn.textContent = 'Error'; btn.disabled = false; }
    }
  }

  // ── Marker style ───────────────────────────────────────────────────
  async function toggleMarkerStyle() {
    _markerStyle = _markerStyle === 'snowcat' ? 'dot' : 'snowcat';
    _renderMarkerBtn();
    try {
      await API.postSettings({ map: { marker_style: _markerStyle } });
    } catch (e) {
      console.error('[Settings] toggleMarkerStyle error', e);
    }
  }

  function _renderMarkerBtn() {
    const btn = document.getElementById('btn-marker-style');
    if (!btn) return;
    btn.textContent = _markerStyle === 'snowcat' ? 'Snowcat Model' : 'GPS Dot';
    btn.className   = `pill-btn ${_markerStyle === 'snowcat' ? 'accent' : 'warning'}`;
  }

  // ── IMU heading ────────────────────────────────────────────────────
  async function toggleImuHeading() {
    _imuHeadingEnabled = !_imuHeadingEnabled;
    _renderImuHeadingBtn();
    try {
      await API.postSettings({ map: { imu_heading_enabled: _imuHeadingEnabled } });
    } catch (e) {
      console.error('[Settings] toggleImuHeading error', e);
    }
  }

  function _renderImuHeadingBtn() {
    const btn = document.getElementById('btn-imu-heading');
    if (!btn) return;
    btn.textContent = _imuHeadingEnabled ? 'Enabled' : 'Disabled';
    btn.className   = `pill-btn ${_imuHeadingEnabled ? 'success' : 'danger'}`;
  }

  async function calibrateImu() {
    const btn = document.getElementById('btn-calibrate-imu');
    if (btn) { btn.textContent = 'Calibrating…'; btn.disabled = true; }
    try {
      await API.calibrateImu();
      if (btn) btn.textContent = 'Calibrated ✓';
    } catch (e) {
      console.error('[Settings] calibrateImu error', e);
      if (btn) btn.textContent = 'No Yaw';
    } finally {
      setTimeout(() => {
        if (btn) {
          btn.textContent = 'Calibrate';
          // Re-enable only if IMU is still ready.
          const s = AppState.get();
          btn.disabled = !(s.imu_ok && s.imu_yaw_valid);
        }
      }, 1500);
    }
  }

  // ── IMU status (live from WebSocket) ───────────────────────────────
  function _renderImuStatus(s) {
    const status = document.getElementById('imu-heading-status');
    const btn    = document.getElementById('btn-calibrate-imu');
    const can    = s.imu_ok && s.imu_yaw_valid;

    if (status) {
      if (!s.imu_ok) {
        status.textContent = 'IMU disconnected';
      } else if (!s.imu_yaw_valid) {
        status.textContent = 'Waiting for yaw data';
      } else if (!s.imu_heading_calibrated) {
        status.textContent = `Raw yaw ${s.imu_yaw_deg.toFixed(1)}° – not calibrated`;
      } else {
        status.textContent =
          `Heading ${s.imu_heading_deg.toFixed(1)}° · raw ${s.imu_yaw_deg.toFixed(1)}°`;
      }
    }

    if (btn) {
      btn.disabled = !can;
      btn.className = `pill-btn ${can ? 'accent' : 'inactive'}`;
    }
  }

  // ── Subscriptions ──────────────────────────────────────────────────
  AppState.subscribe((s) => {
    if (document.getElementById('screen-settings')?.classList.contains('active')) {
      _renderImuStatus(s);
    }
  });

  document.addEventListener('screenchange', async (e) => {
    if (e.detail === 'settings') {
      startClock();
      if (!_loaded) await loadSettings();
      const s = AppState.get();
      if (s && Object.keys(s).length) _renderImuStatus(s);

      // App version from state (state includes version via API in future;
      // for now display the static string embedded in the page).
      const ver = document.getElementById('app-version');
      if (ver && !ver.dataset.set) {
        ver.dataset.set = '1';
        ver.textContent = '1.0.0';
      }
    } else {
      stopClock();
    }
  });

  return {
    toggleNtrip,
    saveNtrip,
    toggleMarkerStyle,
    toggleImuHeading,
    calibrateImu,
  };
})();
