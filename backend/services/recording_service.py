"""
Recording service — logs selected sensor streams to CSV files.

Each session creates exactly one timestamped folder under logs/ and writes:
  gnss_raw.csv      lat, lon, alt, fix, RTK, sats, HDOP         ~1 Hz
  imu_heading.csv   yaw, heading, calibrated flag                ~10 Hz
  imu_raw.csv       accel, gyro + optional roll/pitch/yaw        ~95-100 Hz
  system_status.csv events + diagnostics from all sensors

GNSS logging is event-driven: the GPS reader pushes GNSSMeasurement objects
to gnss_queue; the recording loop drains it completely each tick.

IMU raw logging is event-driven: the IMU reader pushes IMURawMeasurement
objects to imu_raw_queue; a dedicated drain thread writes them to CSV.

Both queues are drained of stale data at the start of each session so
timestamp ranges are always consistent within a session folder.
"""
from __future__ import annotations

import csv
import logging
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.state import state_manager

log = logging.getLogger("snowlink.recording")

_LOGS_DIR = Path(__file__).parent.parent.parent / "logs"

_POLL_INTERVAL         = 0.1   # 10 Hz main loop tick
_STATE_UPDATE_INTERVAL = 1.0   # How often to push live counts to state_manager


def _list_serial_devices() -> str:
    import glob
    lines = []
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    if by_id:
        lines.append("by-id: " + ", ".join(by_id))
    tty = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    if tty:
        lines.append("tty: " + ", ".join(tty))
    return "; ".join(lines) if lines else "none found"


def _drain_queue(q: queue.Queue) -> None:
    """Discard all items currently in q without blocking."""
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


class RecordingService:
    _instance: Optional["RecordingService"] = None

    @classmethod
    def get(cls) -> "RecordingService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._active  = False
        self._streams: set[str] = set()

        self._session_dir: Optional[Path] = None
        self._session_start_t: float = 0.0

        self._gnss_file      = self._gnss_writer      = None
        self._imu_file       = self._imu_writer        = None
        self._imu_raw_file   = self._imu_raw_writer    = None
        self._sys_file       = self._sys_writer        = None

        self._gnss_count          = 0
        self._imu_count           = 0
        self._imu_raw_count       = 0
        self._imu_raw_flush_count = 0
        self._event_count         = 0
        self._gap_warnings        = 0

        # Always-running drain thread writes imu_raw_queue to CSV while recording.
        threading.Thread(
            target=self._raw_imu_loop, daemon=True, name="imu-raw-drain"
        ).start()

    # ── Public API ────────────────────────────────────────────────────────

    def start(self, session_name: str, streams: list[str]) -> dict:
        from backend.services.config_service import ConfigService
        from backend.services.imu_raw import reset_diagnostics, imu_raw_queue
        from backend.services.gps_service import reset_gnss_diagnostics, gnss_queue

        cfg_imu  = ConfigService.get_imu()
        cfg_gnss = ConfigService.get_gnss()
        reset_diagnostics()
        reset_gnss_diagnostics()

        # Drain stale sensor data from previous sessions so all CSV timestamps
        # in this session start from the same point in time.
        _drain_queue(gnss_queue)
        _drain_queue(imu_raw_queue)

        with self._lock:
            if self._active:
                return {"ok": False, "error": "Already recording"}

            safe = re.sub(r"[^\w-]", "_", session_name.strip()) if session_name.strip() else "session"
            dir_name = f"{safe}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            session_dir = _LOGS_DIR / dir_name
            session_dir.mkdir(parents=True, exist_ok=True)

            self._streams         = set(streams)
            self._session_dir     = session_dir
            self._session_start_t = time.monotonic()
            self._gnss_count      = 0
            self._imu_count       = 0
            self._imu_raw_count   = 0
            self._event_count     = 0
            self._gap_warnings    = 0

            if "gnss" in self._streams:
                self._gnss_file   = open(session_dir / "gnss_raw.csv", "w", newline="")
                self._gnss_writer = csv.writer(self._gnss_file)
                self._gnss_writer.writerow([
                    "timestamp_monotonic", "timestamp_unix",
                    "lat", "lon", "altitude_m",
                    "fix_quality", "rtk_status",
                    "h_acc_m", "v_acc_m",   # blank — not in NMEA GGA
                    "num_satellites", "hdop",
                ])

            if "imu" in self._streams:
                self._imu_file   = open(session_dir / "imu_heading.csv", "w", newline="")
                self._imu_writer = csv.writer(self._imu_file)
                self._imu_writer.writerow([
                    "timestamp_monotonic", "timestamp_unix",
                    "yaw_deg", "heading_deg", "calibrated",
                ])

                if cfg_imu.get("raw_log_enabled", True):
                    self._imu_raw_file   = open(session_dir / "imu_raw.csv", "w", newline="")
                    self._imu_raw_writer = csv.writer(self._imu_raw_file)
                    self._imu_raw_writer.writerow([
                        "timestamp_monotonic", "timestamp_unix",
                        "accel_x", "accel_y", "accel_z",
                        "gyro_x", "gyro_y", "gyro_z",
                        "roll", "pitch", "yaw",
                    ])

            if "system" in self._streams:
                self._sys_file   = open(session_dir / "system_status.csv", "w", newline="")
                self._sys_writer = csv.writer(self._sys_file)
                self._sys_writer.writerow([
                    "timestamp_monotonic", "timestamp_unix",
                    "level", "message",
                ])

            self._active = True

        # ── Startup diagnostics ───────────────────────────────────────────
        if "system" in streams:
            self._write_sys_event("INFO", f"SESSION: {dir_name}")
            self._write_sys_event("INFO", f"SERIAL DEVICES: {_list_serial_devices()}")

            imu_port_cfg  = cfg_imu.get("port",  "").strip() or "auto-detect"
            gnss_port_cfg = cfg_gnss.get("port", "").strip() or "auto-detect"

            # Warn on port collision
            if (cfg_imu.get("port", "").strip()
                    and cfg_gnss.get("port", "").strip()
                    and cfg_imu["port"].strip() == cfg_gnss["port"].strip()):
                self._write_sys_event(
                    "WARN",
                    f"PORT CONFLICT: IMU and GNSS both configured to {cfg_imu['port'].strip()}",
                )

            if "imu" in streams:
                raw_enabled = cfg_imu.get("raw_log_enabled", True)
                raw_rate    = cfg_imu.get("raw_log_rate_hz", 100)
                self._write_sys_event(
                    "INFO",
                    f"IMU CONFIG: port={imu_port_cfg} raw_enabled={raw_enabled} "
                    f"raw_rate_hz={raw_rate} (baud auto-detected via rotation)",
                )

            if "gnss" in streams:
                self._write_sys_event(
                    "INFO",
                    f"GNSS CONFIG: port={gnss_port_cfg} "
                    f"baud={cfg_gnss.get('baudrate', 115200)} "
                    f"log_rate_hz={cfg_gnss.get('log_rate_hz', 1)} "
                    "(ZED-F9P output rate configured externally in u-center2)",
                )

        state_manager.update(
            recording_active=True,
            recording_session=dir_name,
            recording_started_at_ms=int(time.time() * 1000),
            recording_gnss_count=0,
            recording_imu_count=0,
            recording_imu_raw_count=0,
            recording_event_count=0,
            recording_log_gnss="gnss" in streams,
            recording_log_imu="imu" in streams,
            recording_log_system="system" in streams,
        )

        threading.Thread(
            target=self._loop, daemon=True, name="recording-loop"
        ).start()
        log.info("Recording started: %s (streams: %s)", dir_name, streams)
        return {"ok": True, "session": dir_name}

    def stop(self) -> dict:
        from backend.services.imu_raw import imu_raw_queue as _irq

        # Phase 1: stop the main recording loop; start the shutdown drain window.
        # The raw IMU drain thread is NOT gated on _active — it writes while
        # self._imu_raw_writer is not None, so it keeps running here.
        with self._lock:
            if not self._active:
                return {"ok": False, "error": "Not recording"}
            self._active = False
            session  = self._session_dir.name if self._session_dir else ""
            elapsed  = time.monotonic() - self._session_start_t
            gnss_count  = self._gnss_count
            imu_count   = self._imu_count
            event_count = self._event_count

        # Phase 2: wait for the drain thread to flush all remaining raw IMU frames.
        drain_deadline = time.monotonic() + 3.0
        while time.monotonic() < drain_deadline:
            if _irq.empty():
                break
            time.sleep(0.005)
        raw_queue_remaining = _irq.qsize()

        # Phase 3: read final IMU raw counters (drain thread may have written more
        # rows after Phase 1), then do a final flush before reading imu_raw_count.
        with self._lock:
            if self._imu_raw_file:
                try:
                    self._imu_raw_file.flush()
                except Exception:
                    pass
            imu_raw_count       = self._imu_raw_count
            imu_raw_flush_count = self._imu_raw_flush_count
            gap_warnings        = self._gap_warnings

        raw_hz  = imu_raw_count / elapsed if elapsed > 0 else 0.0
        head_hz = imu_count     / elapsed if elapsed > 0 else 0.0
        gnss_hz = gnss_count    / elapsed if elapsed > 0 else 0.0

        from backend.services.imu_raw import (
            build_frame_type_report, build_frame_hex_report, build_raw_queue_stats,
        )
        from backend.services.imu_service import connected_port, connected_baud
        from backend.services.gps_service import build_gnss_diagnostic_report

        frame_type_report = build_frame_type_report()
        frame_hex_report  = build_frame_hex_report()
        gnss_diag         = build_gnss_diagnostic_report(elapsed)
        queue_stats       = build_raw_queue_stats()

        raw_recv_hz = queue_stats["extracted"] / elapsed if elapsed > 0 else 0.0

        # ── IMU diagnostics ───────────────────────────────────────────────
        self._write_sys_event(
            "INFO",
            f"IMU CONNECTED: port={connected_port!r} baud={connected_baud}",
        )
        self._write_sys_event("INFO", f"IMU FRAME TYPES: {frame_type_report}")
        self._write_sys_event("INFO", f"IMU FRAME HEX: {frame_hex_report}")
        self._write_sys_event(
            "INFO",
            f"IMU RAW STATS: "
            f"raw_frames_extracted={queue_stats['extracted']} "
            f"raw_frames_enqueued={queue_stats['enqueued']} "
            f"raw_frames_written={imu_raw_count} "
            f"raw_queue_remaining_at_stop={raw_queue_remaining} "
            f"raw_writer_flush_count={imu_raw_flush_count}",
        )
        self._write_sys_event(
            "INFO",
            f"IMU SUMMARY: "
            f"raw_received={queue_stats['extracted']} (~{raw_recv_hz:.1f}Hz) "
            f"raw_written={imu_raw_count} (~{raw_hz:.1f}Hz) "
            f"heading={imu_count} (~{head_hz:.1f}Hz) "
            f"timestamp_gaps={gap_warnings}",
        )

        if imu_raw_count == 0 and imu_count > 0:
            self._write_sys_event(
                "WARN",
                "IMU raw frames missing but heading frames present. "
                "Check N100 output configuration — device may not be sending 0x40 raw frames. "
                "Verify with: screen /dev/ttyUSB0 921600",
            )
        elif imu_raw_count == 0 and imu_count == 0:
            self._write_sys_event(
                "WARN",
                "IMU: no data logged. Baud rotation may still be searching. "
                "Check cable, port path, and that the N100 is powered.",
            )
        elif raw_hz < 80.0:
            self._write_sys_event(
                "WARN",
                f"IMU raw rate low: {raw_hz:.1f}Hz (expected ~95-100Hz). "
                "Check USB bandwidth and queue size.",
            )

        # ── GNSS diagnostics ──────────────────────────────────────────────
        self._write_sys_event("INFO", f"GNSS SUMMARY: {gnss_diag}")
        self._write_sys_event(
            "INFO",
            f"GNSS LOGGED: {gnss_count} rows (~{gnss_hz:.2f}Hz) over {elapsed:.1f}s",
        )

        expected_gnss = elapsed * 1.0
        if gnss_count < expected_gnss * 0.8:
            self._write_sys_event(
                "WARN",
                f"GNSS row count low ({gnss_count} vs ~{expected_gnss:.0f} expected). "
                "Possible causes: (1) ZED-F9P output rate < 1Hz — check u-center2 GGA settings; "
                "(2) Wrong baud rate — GNSS SUMMARY shows actual bytes received; "
                "(3) GGA messages not enabled on this port.",
            )

        # Phase 4: close all files (sets _imu_raw_writer to None, which stops
        # the drain thread from writing any further stale frames).
        with self._lock:
            self._close_files()

        state_manager.update(recording_active=False, recording_session="")

        summary = {
            "session":           session,
            "elapsed_s":         round(elapsed, 1),
            "imu_raw_count":     imu_raw_count,
            "imu_raw_hz":        round(raw_hz, 1),
            "imu_heading_count": imu_count,
            "imu_heading_hz":    round(head_hz, 1),
            "gnss_count":        gnss_count,
            "gnss_hz":           round(gnss_hz, 2),
            "gap_warnings":      gap_warnings,
        }

        # Print a human-readable summary to the application log
        warnings = []
        if imu_raw_count == 0:
            warnings.append("imu_raw=0")
        if gnss_count < expected_gnss * 0.8:
            warnings.append(f"gnss_low({gnss_count})")
        warn_str = ", ".join(warnings) if warnings else "none"

        log.info(
            "\nRecording summary:\n"
            "  session:     %s\n"
            "  imu_raw:     %d samples, %.1f Hz\n"
            "  imu_heading: %d samples, %.1f Hz\n"
            "  gnss_raw:    %d samples, %.2f Hz\n"
            "  warnings:    %s",
            session,
            imu_raw_count, raw_hz,
            imu_count, head_hz,
            gnss_count, gnss_hz,
            warn_str,
        )

        from backend.services.sftp_service import SftpService
        SftpService.get().upload_session(session)

        return {"ok": True, "session": session, "summary": summary}

    def list_sessions(self) -> list[dict]:
        if not _LOGS_DIR.exists():
            return []
        sessions = []
        for d in sorted(_LOGS_DIR.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            files: dict[str, int] = {}
            total = 0
            for f in sorted(d.iterdir()):
                if f.suffix in (".csv", ".json", ".md", ".yaml"):
                    size = f.stat().st_size
                    files[f.name] = size
                    total += size
            sessions.append({
                "name": d.name,
                "files": files,
                "total_size_bytes": total,
            })
        return sessions

    # ── High-rate IMU raw drain (always running) ──────────────────────────

    def _raw_imu_loop(self) -> None:
        """
        Drains imu_raw_queue continuously; writes every frame to imu_raw.csv.
        Write gate is self._imu_raw_writer (not self._active), so the drain
        continues through the shutdown window after stop() sets _active=False,
        letting stop() wait for the queue to empty before closing the file.
        No downsampling — every enqueued raw IMU frame is written.
        """
        from backend.services.imu_raw import imu_raw_queue

        prev_t_mono: Optional[float] = None
        was_active  = False

        raw_written  = 0
        flush_count  = 0
        gap_warnings = 0

        window_samples  = 0
        window_start    = time.monotonic()
        next_freq_check = window_start + 5.0
        next_state_push = time.time() + 1.0

        while True:
            try:
                m = imu_raw_queue.get(timeout=0.05)
            except Exception:
                continue

            with self._lock:
                active = self._active
                writer = self._imu_raw_writer

            # Reset per-session counters when a new recording starts
            if active and not was_active:
                raw_written     = 0
                flush_count     = 0
                gap_warnings    = 0
                prev_t_mono     = None
                window_samples  = 0
                window_start    = time.monotonic()
                next_freq_check = window_start + 5.0
            was_active = active

            # Discard when no writer (IMU raw not requested, or files already closed)
            if writer is None:
                continue

            # Timing gap detection — only flag genuine stalls (> 1 s), not
            # normal chunk-level grouping where many frames share one t_mono.
            if prev_t_mono is not None and m.timestamp_monotonic - prev_t_mono > 1.0:
                gap_warnings += 1
            prev_t_mono = m.timestamp_monotonic

            # Write every frame — no downsampling
            roll_s  = f"{m.roll:.6f}"  if m.roll  is not None else ""
            pitch_s = f"{m.pitch:.6f}" if m.pitch is not None else ""
            yaw_s   = f"{m.yaw:.6f}"   if m.yaw   is not None else ""

            with self._lock:
                if self._imu_raw_writer:
                    self._imu_raw_writer.writerow([
                        f"{m.timestamp_monotonic:.6f}", f"{m.timestamp_unix:.6f}",
                        f"{m.accel_x:.6f}", f"{m.accel_y:.6f}", f"{m.accel_z:.6f}",
                        f"{m.gyro_x:.6f}",  f"{m.gyro_y:.6f}",  f"{m.gyro_z:.6f}",
                        roll_s, pitch_s, yaw_s,
                    ])
                    raw_written += 1
                    # Flush every 100 rows; stop() does a final flush before close.
                    if raw_written % 100 == 0:
                        self._imu_raw_file.flush()
                        flush_count += 1
                        self._imu_raw_flush_count = flush_count
                    self._imu_raw_count = raw_written
                    self._gap_warnings  = gap_warnings
            window_samples += 1

            # Frequency check every 5 s — uses written count, no undefined vars
            now_m = time.monotonic()
            if now_m >= next_freq_check:
                elapsed_w = now_m - window_start
                meas_hz   = window_samples / elapsed_w if elapsed_w > 0 else 0.0
                if meas_hz < 80.0:
                    self._write_sys_event(
                        "WARN",
                        f"IMU raw write rate low: {meas_hz:.1f}Hz (expected ~100Hz)",
                    )
                window_samples  = 0
                window_start    = now_m
                next_freq_check = now_m + 5.0

            # Push live count to state (1 Hz)
            now_t = time.time()
            if now_t >= next_state_push:
                state_manager.update(recording_imu_raw_count=raw_written)
                next_state_push = now_t + 1.0

    # ── Main poll loop: heading, GNSS queue drain, system events (10 Hz) ──

    def _loop(self) -> None:
        from backend.services.gps_service import gnss_queue

        # Initialise last_event to the current state so we only log events
        # that happen AFTER this recording starts (avoids logging stale boot
        # messages like "Not found: GPS" that were set before the session).
        s = state_manager.get_snapshot()
        last_yaw   = None
        last_event = s.get("last_event", "")
        gnss_count = imu_count = event_count = 0
        next_state_push = time.time() + _STATE_UPDATE_INTERVAL

        while True:
            with self._lock:
                if not self._active:
                    break
                streams = self._streams

            try:
                s      = state_manager.get_snapshot()
                t_mono = time.monotonic()
                t_unix = time.time()

                # ── GNSS: drain queue fully each tick (event-driven, no poll lag)
                # No downsampling — every valid GGA is logged.
                # The ZED-F9P naturally limits output to ~1 Hz externally.
                if "gnss" in streams:
                    while True:
                        try:
                            m = gnss_queue.get_nowait()
                        except queue.Empty:
                            break
                        with self._lock:
                            if self._gnss_writer:
                                self._gnss_writer.writerow([
                                    f"{m.timestamp_monotonic:.6f}",
                                    f"{m.timestamp_unix:.6f}",
                                    f"{m.lat:.9f}",
                                    f"{m.lon:.9f}",
                                    f"{m.altitude_m:.3f}",
                                    m.fix_quality,
                                    m.fix_type_text,
                                    "",   # h_acc_m — not in NMEA GGA
                                    "",   # v_acc_m — not in NMEA GGA
                                    m.satellites,
                                    f"{m.hdop:.2f}",
                                ])
                                self._gnss_file.flush()
                        gnss_count += 1

                # ── IMU heading (~10 Hz via state change detection) ───────
                if "imu" in streams and s["imu_yaw_valid"]:
                    cur_yaw = s["imu_yaw_deg"]
                    if cur_yaw != last_yaw:
                        with self._lock:
                            if self._imu_writer:
                                self._imu_writer.writerow([
                                    f"{t_mono:.6f}", f"{t_unix:.6f}",
                                    round(s["imu_yaw_deg"], 4),
                                    round(s["imu_heading_deg"], 4),
                                    int(s["imu_heading_calibrated"]),
                                ])
                                self._imu_file.flush()
                        last_yaw = cur_yaw
                        imu_count += 1

                # ── System events (state changes since session start) ──────
                if "system" in streams:
                    event = s.get("last_event", "")
                    if event and event != last_event:
                        with self._lock:
                            if self._sys_writer:
                                self._sys_writer.writerow([
                                    f"{t_mono:.6f}", f"{t_unix:.6f}",
                                    "INFO", event,
                                ])
                                self._sys_file.flush()
                        last_event = event
                        event_count += 1

                now = time.time()
                if now >= next_state_push:
                    state_manager.update(
                        recording_gnss_count=gnss_count,
                        recording_imu_count=imu_count,
                        recording_event_count=event_count,
                    )
                    with self._lock:
                        self._gnss_count  = gnss_count
                        self._imu_count   = imu_count
                        self._event_count = event_count
                    next_state_push = now + _STATE_UPDATE_INTERVAL

            except Exception:
                log.exception("Recording loop error")

            time.sleep(_POLL_INTERVAL)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _write_sys_event(self, level: str, message: str) -> None:
        t_mono = time.monotonic()
        t_unix = time.time()
        with self._lock:
            if self._sys_writer:
                self._sys_writer.writerow([
                    f"{t_mono:.6f}", f"{t_unix:.6f}", level, message,
                ])
                self._sys_file.flush()

    def _close_files(self) -> None:
        for f in (self._gnss_file, self._imu_file, self._imu_raw_file, self._sys_file):
            if f:
                try:
                    f.close()
                except Exception:
                    pass
        self._gnss_file    = self._gnss_writer    = None
        self._imu_file     = self._imu_writer      = None
        self._imu_raw_file = self._imu_raw_writer  = None
        self._sys_file     = self._sys_writer      = None
