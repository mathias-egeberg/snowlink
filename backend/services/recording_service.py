"""
Recording service — logs selected sensor streams to CSV files.

Each session gets its own timestamped directory under logs/.
Supported streams:
  gnss    → gnss_raw.csv    (lat, lon, alt, fix, RTK, sats, HDOP)
  imu     → imu_heading.csv (yaw, heading, calibrated flag)
  system  → system_status.csv (events from last_event)

Only new samples are written (deduplication by value change), keeping
file sizes small even during long idle sessions.
"""
from __future__ import annotations

import csv
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.state import state_manager

log = logging.getLogger("snowlink.recording")

_LOGS_DIR = Path(__file__).parent.parent.parent / "logs"

_POLL_INTERVAL = 0.1          # 10 Hz polling; most sensors update at ≤1 Hz
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
        self._gnss_file = self._gnss_writer = None
        self._imu_file  = self._imu_writer  = None
        self._sys_file  = self._sys_writer  = None

        self._gnss_count  = 0
        self._imu_count   = 0
        self._event_count = 0

    # ── Public API ────────────────────────────────────────────────────────

    def start(self, session_name: str, streams: list[str]) -> dict:
        with self._lock:
            if self._active:
                return {"ok": False, "error": "Already recording"}

            safe = re.sub(r"[^\w-]", "_", session_name.strip()) if session_name.strip() else "session"
            dir_name = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{safe}"
            session_dir = _LOGS_DIR / dir_name
            session_dir.mkdir(parents=True, exist_ok=True)

            self._streams     = set(streams)
            self._session_dir = session_dir
            self._gnss_count  = 0
            self._imu_count   = 0
            self._event_count = 0

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

            if "system" in self._streams:
                self._sys_file   = open(session_dir / "system_status.csv", "w", newline="")
                self._sys_writer = csv.writer(self._sys_file)
                self._sys_writer.writerow([
                    "timestamp_monotonic", "timestamp_unix",
                    "level", "message",
                ])

            self._active = True

        state_manager.update(
            recording_active=True,
            recording_session=dir_name,
            recording_started_at_ms=int(time.time() * 1000),
            recording_gnss_count=0,
            recording_imu_count=0,
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
            session = self._session_dir.name if self._session_dir else ""
            self._close_files()

        state_manager.update(recording_active=False, recording_session="")
        log.info("Recording stopped: %s", session)
        return {"ok": True, "session": session}

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

    # ── Background loop ───────────────────────────────────────────────────

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

    # ── Internal ──────────────────────────────────────────────────────────

    def _close_files(self) -> None:
        for f in (self._gnss_file, self._imu_file, self._sys_file):
            if f:
                try:
                    f.close()
                except Exception:
                    pass
        self._gnss_file = self._gnss_writer = None
        self._imu_file  = self._imu_writer  = None
        self._sys_file  = self._sys_writer  = None
