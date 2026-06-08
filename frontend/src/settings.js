/**
 * Settings screen – NTRIP configuration, map marker style, IMU heading
 * calibration, and app version display.
 *
 * Settings are loaded from the REST API on first entry and persisted via
 * POST /api/settings.  IMU status is kept live from the WebSocket.
 */
const Settings = (() => {
  let _ntripEnabled      = false;
  let _markerStyle       = 'snowcat';
  let _imuHeadingEnabled = false;
  let _gnssRateHz        = 5;
  let _loaded            = false;
  let _speedTestRunning  = false;
  const STATE_RENDER_SETTLE_MS = 400;

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
      _gnssRateHz        = data.gnss?.update_rate_hz ?? 5;

      document.getElementById('inp-host').value      = data.ntrip?.host       ?? '';
      document.getElementById('inp-port').value      = data.ntrip?.port       ?? '2101';
      document.getElementById('inp-mountpoint').value = data.ntrip?.mountpoint ?? '';
      document.getElementById('inp-username').value  = data.ntrip?.username   ?? '';
      document.getElementById('inp-password').value  = data.ntrip?.password   ?? '';

      _renderNtripBtn();
      _renderMarkerBtn();
      _renderImuHeadingBtn();
      _renderGnssRateBtn();
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

  // ── GNSS update rate ───────────────────────────────────────────────
  function setGnssRate(hz) {
    _gnssRateHz = hz;
    _renderGnssRateBtn();
    API.postSettings({ gnss: { update_rate_hz: hz } })
      .catch(e => console.error('[Settings] setGnssRate error', e));
  }

  function _renderGnssRateBtn() {
    for (const hz of [1, 5, 10]) {
      const btn = document.getElementById(`btn-gnss-rate-${hz}`);
      if (!btn) continue;
      const active = hz === _gnssRateHz;
      if (!active) {
        btn.className = 'pill-btn inactive';
      } else if (hz === 1) {
        btn.className = 'pill-btn warning';
      } else if (hz === 5) {
        btn.className = 'pill-btn success';
      } else {
        btn.className = 'pill-btn danger';
      }
    }
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

  // ── Cellular data counter ─────────────────────────────────────────
  function _formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = bytes;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }
    const decimals = unitIndex === 0 || size >= 100 ? 0 : 1;
    return `${size.toFixed(decimals)} ${units[unitIndex]}`;
  }

  function _formatMbps(value) {
    const mbps = Number(value);
    if (!Number.isFinite(mbps) || mbps <= 0) return '0.0 Mbps';
    return `${mbps.toFixed(mbps >= 100 ? 0 : 1)} Mbps`;
  }

  function _formatMs(value) {
    const ms = Number(value);
    if (!Number.isFinite(ms) || ms <= 0) return '0 ms';
    return `${ms.toFixed(ms >= 100 ? 0 : 1)} ms`;
  }

  function _formatAge(ms) {
    const timestamp = Number(ms);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return 'Never';
    const elapsed = Math.max(0, Date.now() - timestamp);
    const minutes = Math.floor(elapsed / 60000);
    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} h ${minutes % 60} min ago`;
    const days = Math.floor(hours / 24);
    return `${days} d ${hours % 24} h ago`;
  }

  function _formatDateTime(ms) {
    const timestamp = Number(ms);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return 'Never';
    return new Date(timestamp).toLocaleString('en-GB', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    });
  }

  function _renderCellular(s) {
    const status = document.getElementById('cellular-status');
    const detail = document.getElementById('cellular-detail');
    const rx = document.getElementById('cellular-rx');
    const tx = document.getElementById('cellular-tx');
    const total = document.getElementById('cellular-total');
    const resetTime = document.getElementById('cellular-reset-time');
    const speedStatus = document.getElementById('cellular-speed-status');
    const download = document.getElementById('cellular-speed-download');
    const upload = document.getElementById('cellular-speed-upload');
    const latency = document.getElementById('cellular-speed-latency');
    const speedBtn = document.getElementById('btn-cellular-speed-test');

    if (status) {
      status.textContent = s.cellular_status_text ?? 'No modem';
      status.className = `settings-row-desc cellular-status ${
        s.cellular_ok ? 'ok' : (s.cellular_detected ? 'warning' : 'danger')
      }`;
    }
    if (detail) {
      const product = s.cellular_product || (s.cellular_is_huawei ? 'Huawei modem' : 'Cellular modem');
      const iface = s.cellular_iface ? `${s.cellular_iface}` : 'No interface';
      const ip = s.cellular_ipv4 ? ` · ${s.cellular_ipv4}` : '';
      detail.textContent = s.cellular_detected ? `${product} · ${iface}${ip}` : 'Waiting for USB dongle';
    }
    if (rx) rx.textContent = _formatBytes(s.cellular_bytes_received);
    if (tx) tx.textContent = _formatBytes(s.cellular_bytes_sent);
    if (total) total.textContent = _formatBytes(s.cellular_bytes_total);
    if (resetTime) {
      resetTime.textContent = `Last reset: ${_formatDateTime(s.cellular_usage_reset_at_ms)} · ${_formatAge(s.cellular_usage_reset_at_ms)}`;
    }

    if (speedStatus) {
      const statusText = s.cellular_speed_test_running
        ? 'Testing…'
        : (s.cellular_speed_test_error || s.cellular_speed_test_status || 'Never run');
      speedStatus.textContent = statusText;
      speedStatus.className = `settings-row-desc cellular-status ${
        s.cellular_speed_test_running ? 'warning' : (s.cellular_speed_test_error ? 'danger' : 'ok')
      }`;
    }
    if (download) download.textContent = _formatMbps(s.cellular_speed_test_download_mbps);
    if (upload) upload.textContent = _formatMbps(s.cellular_speed_test_upload_mbps);
    if (latency) latency.textContent = _formatMs(s.cellular_speed_test_latency_ms);
    if (speedBtn) {
      const running = _speedTestRunning || !!s.cellular_speed_test_running;
      speedBtn.disabled = running || !s.cellular_ipv4;
      speedBtn.textContent = running ? 'Testing…' : 'Test';
      speedBtn.className = `pill-btn ${running ? 'warning' : (s.cellular_ipv4 ? 'accent' : 'inactive')}`;
    }
  }

  async function runCellularSpeedTest() {
    const currentState = AppState.get();
    if (_speedTestRunning || currentState.cellular_speed_test_running) return;
    _speedTestRunning = true;
    const btn = document.getElementById('btn-cellular-speed-test');
    const status = document.getElementById('cellular-speed-status');
    if (btn) { btn.textContent = 'Testing…'; btn.disabled = true; btn.className = 'pill-btn warning'; }
    if (status) { status.textContent = 'Testing…'; status.className = 'settings-row-desc cellular-status warning'; }
    try {
      const result = await API.runCellularSpeedTest();
      if (result.state) AppState.update(result.state);
      if (btn) btn.textContent = result.ok ? 'Test' : 'Retry';
    } catch (e) {
      console.error('[Settings] runCellularSpeedTest error', e);
      if (status) { status.textContent = 'Speed test failed'; status.className = 'settings-row-desc cellular-status danger'; }
      if (btn) btn.textContent = 'Retry';
    } finally {
      _speedTestRunning = false;
      setTimeout(() => {
        const s = AppState.get();
        if (s && Object.keys(s).length) _renderCellular(s);
      }, STATE_RENDER_SETTLE_MS);
    }
  }

  async function resetCellularUsage() {
    const btn = document.getElementById('btn-reset-cellular-usage');
    if (btn) { btn.textContent = 'Resetting…'; btn.disabled = true; }
    try {
      const result = await API.resetCellularUsage();
      if (result.state) AppState.update(result.state);
      if (btn) btn.textContent = 'Reset';
    } catch (e) {
      console.error('[Settings] resetCellularUsage error', e);
      if (btn) btn.textContent = 'Error';
    } finally {
      setTimeout(() => {
        if (btn) {
          btn.textContent = 'Reset';
          btn.disabled = false;
        }
      }, 1200);
    }
  }

  // ── Local IP ───────────────────────────────────────────────────────
  function _renderLocalIp(s) {
    const el = document.getElementById('set-local-ip');
    if (!el) return;
    el.textContent = s.local_ip || '—';
  }

  // ── Connection indicator helper ────────────────────────────────────
  function _setConn(id, ok, warn = false) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('ok',      ok && !warn);
    el.classList.toggle('warning', !ok && warn);
  }

  // ── IMU status + header indicators (live from WebSocket) ───────────
  function _renderState(s) {
    // GPS badge
    const gpsTxt = document.getElementById('set-gps-text');
    if (gpsTxt) {
      gpsTxt.textContent = s.gps_status_text ?? 'No Fix';
      const cls = s.gps_ok ? 'ok' : (s.gps_float_rtk ? 'warning' : 'danger');
      gpsTxt.className = `badge-value ${cls}`;
    }

    // Connection indicators
    _setConn('ind-set-5g',  s.cellular_ok, s.cellular_detected && !s.cellular_ok);
    _setConn('ind-set-gps', s.gps_ok, s.gps_float_rtk);
    _setConn('ind-set-imu', s.imu_ok);

    _renderCellular(s);
    _renderLocalIp(s);

    // IMU calibration row
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
      _renderState(s);
    }
  });

  document.addEventListener('screenchange', async (e) => {
    if (e.detail === 'settings') {
      startClock();
      if (!_loaded) await loadSettings();
      const s = AppState.get();
      if (s && Object.keys(s).length) _renderState(s);

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
    setGnssRate,
    resetCellularUsage,
    runCellularSpeedTest,
  };
})();
