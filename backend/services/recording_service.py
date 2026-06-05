"""
Recording service — logs selected sensor streams to CSV files.

Each session gets its own timestamped directory under logs/.
Supported streams:
  gnss    → gnss_raw.csv      (lat, lon, alt, fix, RTK, sats, HDOP)
  imu     → imu_heading.csv   (yaw, heading, calibrated flag)   ~10 Hz
            imu_raw.csv       (accel, gyro + optional rpy)       ~100 Hz
  system  → system_status.csv (events + IMU diagnostics)

imu_raw.csv is intended for future ESKF GNSS/IMU fusion.
imu_heading.csv remains the low-rate orientation/debug log.
"""
from __future__ import annotations

import csv
import logging
import math
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

_POLL_INTERVAL        = 0.1   # 10 Hz for heading / event polling
_STATE_UPDATE_INTERVAL = 1.0  # How often to push counts to state_manager


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

        self._gnss_file = self._gnss_writer = None
        self._imu_file  = self._imu_writer  = None
        self._imu_raw_file = self._imu_raw_writer = None
        self._sys_file  = self._sys_writer  = None

        self._gnss_count     = 0
        self._imu_count      = 0
        self._imu_raw_count  = 0
        self._event_count    = 0
        self._gap_warnings   = 0
        self._invalid_count  = 0

        # Always-running drain thread flushes imu_raw_queue whether or not recording.
        threading.Thread(
            target=self._raw_imu_loop, daemon=True, name="imu-raw-drain"
        ).start()

    # ── Public API ────────────────────────────────────────────────────────

    def start(self, session_name: str, streams: list[str]) -> dict:
        from backend.services.config_service import ConfigService
        from backend.services.imu_raw import reset_diagnostics
        cfg_imu = ConfigService.get_imu()
        reset_diagnostics()

        with self._lock:
            if self._active:
                return {"ok": False, "error": "Already recording"}

            safe = re.sub(r"[^\w-]", "_", session_name.strip()) if session_name.strip() else "session"
            dir_name = f"{safe}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            session_dir = _LOGS_DIR / dir_name
            session_dir.mkdir(parents=True, exist_ok=True)

            self._streams     = set(streams)
            self._session_dir = session_dir
            self._session_start_t = time.monotonic()
            self._gnss_count     = 0
            self._imu_count      = 0
            self._imu_raw_count  = 0
            self._event_count    = 0
            self._gap_warnings   = 0
            self._invalid_count  = 0

            if "gnss" in self._streams:
                self._gnss_file   = open(session_dir / "gnss_raw.csv", "w", newline="")
                self._gnss_writer = csv.writer(self._gnss_file)
                self._gnss_writer.writerow([
                    "timestamp_monotonic", "timestamp_unix",
                    "lat", "lon", "altitude_m",
                    "fix_quality", "fix_type", "satellites", "hdop",
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

        # Log IMU config to system_status at recording start
        if "system" in streams and "imu" in streams:
            s = state_manager.get_snapshot()
            port_str    = s.get("imu_port", "") or "auto"
            baud_str    = str(s.get("imu_baudrate", 0) or cfg_imu.get("baudrate", 115200))
            raw_enabled = cfg_imu.get("raw_log_enabled", True)
            raw_rate    = cfg_imu.get("raw_log_rate_hz", 100)
            self._write_sys_event(
                "INFO",
                f"IMU CONFIG: port={port_str} baud={baud_str} "
                f"raw_enabled={raw_enabled} raw_rate_hz={raw_rate}",
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
        with self._lock:
            if not self._active:
                return {"ok": False, "error": "Not recording"}
            self._active = False
            session       = self._session_dir.name if self._session_dir else ""
            elapsed       = time.monotonic() - self._session_start_t
            gnss_count    = self._gnss_count
            imu_count     = self._imu_count
            imu_raw_count = self._imu_raw_count
            event_count   = self._event_count
            gap_warnings  = self._gap_warnings
            invalid_count = self._invalid_count

        raw_hz  = imu_raw_count / elapsed if elapsed > 0 else 0.0
        head_hz = imu_count / elapsed if elapsed > 0 else 0.0

        from backend.services.imu_raw import (
            build_frame_type_report,
            build_frame_hex_report,
        )
        frame_type_report = build_frame_type_report()
        frame_hex_report  = build_frame_hex_report()

        # Write frame type diagnostics first — essential for debugging missing raw data
        self._write_sys_event("INFO", f"IMU FRAME TYPES: {frame_type_report}")
        self._write_sys_event("INFO", f"IMU FRAME HEX: {frame_hex_report}")

        # Write summary to system_status before closing files
        self._write_sys_event(
            "INFO",
            f"IMU SUMMARY: raw={imu_raw_count} (~{raw_hz:.1f}Hz) "
            f"heading={imu_count} (~{head_hz:.1f}Hz) "
            f"gaps={gap_warnings} invalid={invalid_count}",
        )

        with self._lock:
            self._close_files()

        state_manager.update(
            recording_active=False,
            recording_session="",
        )

        summary = {
            "imu_raw_count":      imu_raw_count,
            "imu_raw_hz":         round(raw_hz, 1),
            "imu_heading_count":  imu_count,
            "gnss_count":         gnss_count,
            "gap_warnings":       gap_warnings,
            "invalid_count":      invalid_count,
        }

        log.info(
            "Recording stopped: %s\n"
            "  IMU raw samples:     %d  (~%.1f Hz)\n"
            "  IMU heading samples: %d  (~%.1f Hz)\n"
            "  GNSS samples:        %d\n"
            "  System events:       %d\n"
            "  Timestamp warnings:  %d\n"
            "  Invalid IMU packets: %d",
            session,
            imu_raw_count, raw_hz,
            imu_count, head_hz,
            gnss_count,
            event_count,
            gap_warnings,
            invalid_count,
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

    # ── High-rate IMU raw loop (always running) ───────────────────────────

    def _raw_imu_loop(self) -> None:
        """
        Drains imu_raw_queue at whatever rate the IMU produces data.
        Writes to imu_raw.csv only when recording is active.
        Applies downsampling to honour raw_log_rate_hz from config.
        """
        from backend.services.config_service import ConfigService
        from backend.services.imu_raw import imu_raw_queue

        cfg           = ConfigService.get_imu()
        raw_rate_hz   = float(cfg.get("raw_log_rate_hz", 100))
        min_interval  = 1.0 / max(raw_rate_hz, 1.0)

        last_written_t  = 0.0
        prev_t_mono: Optional[float] = None
        was_active      = False

        # Per-session counters (reset when recording starts)
        raw_count    = 0
        gap_warnings = 0

        # Frequency measurement window
        window_samples = 0
        window_start   = time.monotonic()
        next_freq_check = window_start + 5.0
        next_state_push = time.time() + 1.0

        while True:
            try:
                m = imu_raw_queue.get(timeout=0.05)
            except Exception:
                continue

            with self._lock:
                active   = self._active
                has_imu  = "imu" in self._streams
                writer   = self._imu_raw_writer
                fp       = self._imu_raw_file

            # Reset counters when a new recording starts
            if active and not was_active:
                raw_count    = 0
                gap_warnings = 0
                last_written_t = 0.0
                prev_t_mono    = None
                window_samples = 0
                window_start   = time.monotonic()
                next_freq_check = window_start + 5.0
            was_active = active

            if not active or not has_imu or writer is None:
                continue  # discard without writing

            # ── Timing checks ────────────────────────────────────────────
            if prev_t_mono is not None:
                dt = m.timestamp_monotonic - prev_t_mono
                if dt < 0:
                    log.warning("IMU non-monotonic timestamp: dt=%.6f s", dt)
                    gap_warnings += 1
                elif dt > 0.05:  # >50 ms gap — 5× expected at 100 Hz
                    log.debug("IMU timestamp gap: dt=%.3f s", dt)
                    gap_warnings += 1
            prev_t_mono = m.timestamp_monotonic

            # ── Downsampling ─────────────────────────────────────────────
            # Use 99% of min_interval to avoid floating-point precision
            # causing on-rate samples to be wrongly dropped.
            if m.timestamp_monotonic - last_written_t < min_interval * 0.99:
                continue
            last_written_t = m.timestamp_monotonic

            # ── Write row ────────────────────────────────────────────────
            roll_s  = f"{m.roll:.6f}"  if m.roll  is not None else ""
            pitch_s = f"{m.pitch:.6f}" if m.pitch is not None else ""
            yaw_s   = f"{m.yaw:.6f}"   if m.yaw   is not None else ""

            with self._lock:
                if self._imu_raw_writer:
                    self._imu_raw_writer.writerow([
                        f"{m.timestamp_monotonic:.6f}",
                        f"{m.timestamp_unix:.6f}",
                        f"{m.accel_x:.6f}", f"{m.accel_y:.6f}", f"{m.accel_z:.6f}",
                        f"{m.gyro_x:.6f}",  f"{m.gyro_y:.6f}",  f"{m.gyro_z:.6f}",
                        roll_s, pitch_s, yaw_s,
                    ])
                    self._imu_raw_file.flush()
                    # Update counter under lock so stop() always gets an accurate count.
                    raw_count += 1
                    self._imu_raw_count = raw_count
                    self._gap_warnings  = gap_warnings
            window_samples += 1

            # ── Periodic frequency check (every 5 s) ─────────────────────
            now_m = time.monotonic()
            if now_m >= next_freq_check:
                elapsed   = now_m - window_start
                meas_hz   = window_samples / elapsed if elapsed > 0 else 0.0
                if meas_hz < raw_rate_hz * 0.8:
                    msg = (f"IMU raw rate low: {meas_hz:.1f}Hz "
                           f"(target {raw_rate_hz:.0f}Hz)")
                    log.warning(msg)
                    self._write_sys_event("WARN", msg)
                window_samples = 0
                window_start   = now_m
                next_freq_check = now_m + 5.0

            # ── Push counts to state (1 Hz) ───────────────────────────────
            now_t = time.time()
            if now_t >= next_state_push:
                state_manager.update(recording_imu_raw_count=raw_count)
                next_state_push = now_t + 1.0

    # ── Low-rate heading / event loop (10 Hz) ─────────────────────────────

    def _loop(self) -> None:
        last_lat   = None
        last_yaw   = None
        last_event = None
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

                if "gnss" in streams and s["gps_fix_quality"] > 0:
                    cur_lat = s["gps_lat"]
                    if cur_lat != last_lat:
                        with self._lock:
                            if self._gnss_writer:
                                self._gnss_writer.writerow([
                                    f"{t_mono:.6f}", f"{t_unix:.6f}",
                                    s["gps_lat"], s["gps_lon"], s["gps_altitude_m"],
                                    s["gps_fix_quality"], s["gps_status_text"],
                                    s["gps_satellites"], s["gps_hdop"],
                                ])
                                self._gnss_file.flush()
                        last_lat = cur_lat
                        gnss_count += 1

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
        """Write a message to system_status.csv (safe to call from any thread)."""
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
        self._gnss_file     = self._gnss_writer     = None
        self._imu_file      = self._imu_writer       = None
        self._imu_raw_file  = self._imu_raw_writer   = None
        self._sys_file      = self._sys_writer       = None
