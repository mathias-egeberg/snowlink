/**
 * Record screen — start/stop sensor logging sessions.
 *
 * Streams available: gnss, imu, system
 * Data is written to timestamped CSV files on the Pi under logs/.
 */
const Record = (() => {
  let _clockTimer     = null;
  let _durationTimer  = null;
  let _startedAt      = 0;    // ms epoch when recording started (from state)
  let _sessionsLoaded = false;

  // ── Clock ──────────────────────────────────────────────────────────

  function _startClock() {
    function tick() {
      const now = new Date();
      const t = now.toLocaleTimeString('en-GB', { hour12: false });
      const d = now.toLocaleDateString('en-GB', {
        weekday: 'long', day: '2-digit', month: 'short', year: 'numeric',
      });
      const te = document.getElementById('rec-time');
      const de = document.getElementById('rec-date');
      if (te) te.textContent = t;
      if (de) de.textContent = d;
    }
    tick();
    _clockTimer = setInterval(tick, 1000);
  }

  function _stopClock() {
    if (_clockTimer) { clearInterval(_clockTimer); _clockTimer = null; }
  }

  // ── Duration ticker ────────────────────────────────────────────────

  function _startDuration(startedAtMs) {
    _startedAt = startedAtMs;
    function tick() {
      const elapsed = Math.max(0, Date.now() - _startedAt);
      const s = Math.floor(elapsed / 1000);
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const ss = s % 60;
      const el = document.getElementById('rec-duration');
      if (el) el.textContent =
        `${h}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
    }
    tick();
    _durationTimer = setInterval(tick, 1000);
  }

  function _stopDuration() {
    if (_durationTimer) { clearInterval(_durationTimer); _durationTimer = null; }
  }

  // ── Screen lifecycle ───────────────────────────────────────────────

  function onEnter() {
    _startClock();
    if (!_sessionsLoaded) loadSessions();
    const s = AppState.get();
    if (s) _render(s);
  }

  function onLeave() {
    _stopClock();
    _stopDuration();
  }

  // ── State rendering ────────────────────────────────────────────────

  function _render(s) {
    // Header indicators
    _syncIndicator('ind-rec-5g',  s.cellular_ok);
    _syncIndicator('ind-rec-gps', s.gps_ok || s.gps_float_rtk);
    _syncIndicator('ind-rec-imu', s.imu_ok);

    const gpsEl = document.getElementById('rec-gps-text');
    if (gpsEl) {
      gpsEl.textContent = s.gps_status_text ?? 'No Fix';
      gpsEl.className = `badge-value ${s.gps_ok ? 'ok' : s.gps_float_rtk ? 'warning' : 'danger'}`;
    }

    const active = s.recording_active;

    // Show/hide config vs active panel
    const cfg    = document.getElementById('rec-config-panel');
    const actpnl = document.getElementById('rec-active-panel');
    if (cfg)    cfg.classList.toggle('hidden', active);
    if (actpnl) actpnl.classList.toggle('hidden', !active);

    // Nav button pulsing
    const navBtn = document.querySelector('.nav-btn[data-screen="record"]');
    if (navBtn) navBtn.classList.toggle('recording', active);

    if (active) {
      // Live counters
      _setText('rec-gnss-count',  s.recording_gnss_count);
      _setText('rec-imu-count',   s.recording_imu_count);
      _setText('rec-event-count', s.recording_event_count);
      _setText('rec-session-display', s.recording_session ?? '—');

      // Grey-out inactive stream cells
      _dimCell('rec-gnss-cell', !s.recording_log_gnss);
      _dimCell('rec-imu-cell',  !s.recording_log_imu);
      _dimCell('rec-sys-cell',  !s.recording_log_system);

      // Start duration ticker if not running
      if (!_durationTimer && s.recording_started_at_ms) {
        _startDuration(s.recording_started_at_ms);
      }
    } else {
      _stopDuration();
      const dur = document.getElementById('rec-duration');
      if (dur) dur.textContent = '0:00:00';
    }
  }

  function _syncIndicator(id, active) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('active', !!active);
  }

  function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val ?? '0';
  }

  function _dimCell(id, dim) {
    const el = document.getElementById(id);
    if (el) el.style.opacity = dim ? '0.35' : '1';
  }

  // ── Actions ────────────────────────────────────────────────────────

  async function start() {
    const nameEl = document.getElementById('rec-session-name');
    const name   = nameEl?.value.trim() ?? '';

    const streams = [];
    if (document.getElementById('rec-stream-gnss')?.checked)   streams.push('gnss');
    if (document.getElementById('rec-stream-imu')?.checked)    streams.push('imu');
    if (document.getElementById('rec-stream-system')?.checked) streams.push('system');

    if (streams.length === 0) {
      alert('Select at least one stream to record.');
      return;
    }

    const btn = document.getElementById('rec-start-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }

    try {
      const res = await fetch('/api/recording/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_name: name, streams }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error ?? 'Failed');
      if (nameEl) nameEl.value = '';
    } catch (err) {
      alert(`Could not start recording: ${err.message}`);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '⏺ Start Recording'; }
    }
  }

  async function stop() {
    try {
      const res = await fetch('/api/recording/stop', { method: 'POST' });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error ?? 'Failed');
      _stopDuration();
      loadSessions();
    } catch (err) {
      alert(`Could not stop recording: ${err.message}`);
    }
  }

  async function loadSessions() {
    _sessionsLoaded = true;
    const list = document.getElementById('rec-sessions-list');
    if (!list) return;
    try {
      const res  = await fetch('/api/recording/sessions');
      const data = await res.json();
      _renderSessions(data.sessions ?? []);
    } catch {
      if (list) list.innerHTML = '<span class="rec-no-sessions">Could not load sessions</span>';
    }
  }

  function _renderSessions(sessions) {
    const list = document.getElementById('rec-sessions-list');
    if (!list) return;
    if (sessions.length === 0) {
      list.innerHTML = '<span class="rec-no-sessions">No sessions yet</span>';
      return;
    }
    list.innerHTML = sessions.map(s => {
      const size = _fmtBytes(s.total_size_bytes);
      const files = Object.keys(s.files ?? {}).join(', ') || 'empty';
      return `
        <div class="rec-session-item">
          <div class="rec-session-name-text">${s.name}</div>
          <div class="rec-session-meta">${files} &nbsp;·&nbsp; ${size}</div>
        </div>
        <div class="settings-divider"></div>`;
    }).join('');
    // Remove trailing divider
    list.querySelectorAll('.settings-divider:last-child').forEach(el => el.remove());
  }

  function _fmtBytes(b) {
    if (b < 1024) return `${b} B`;
    if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1048576).toFixed(2)} MB`;
  }

  // ── Live state subscription ────────────────────────────────────────

  AppState.subscribe((s) => {
    if (Nav.current() === 'record') _render(s);
    // Keep nav button pulsing accurate regardless of current screen
    const navBtn = document.querySelector('.nav-btn[data-screen="record"]');
    if (navBtn) navBtn.classList.toggle('recording', !!s.recording_active);
  });

  // ── Screen change hook ─────────────────────────────────────────────

  document.addEventListener('screenchange', (e) => {
    if (e.detail === 'record') onEnter();
    else onLeave();
  });

  return { start, stop, loadSessions };
})();
