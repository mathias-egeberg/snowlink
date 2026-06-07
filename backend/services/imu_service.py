"""
IMU service – WheelTech N100 via CP2102 USB-serial adapter.

Two detection paths run in parallel:
  - Reader thread (fast): calls set_imu_connection(False) the moment
    ser.read() raises or yaw data goes stale, typically within 2-3 s.
  - Main loop (3 s guarantee): scans USB via imu_selftest every
    SELFTEST_INTERVAL seconds regardless of reader state.

Port resolution:
  1. imu.port from config.yaml  (explicit /dev/serial/by-id/ path)
  2. Auto-detection via imu_selftest (CP2102 VID/PID scan)

Baud rate is always auto-detected through rotation (921600 → 115200 → 9600)
because the N100 can be configured to different rates. The working baud is
cached in _last_good_baud and reused on subsequent connections.
The imu.baudrate field in config.yaml is informational only.

A single reader thread parses all N100 FDFC frames and:
  - Extracts yaw/heading     → updates data_service (for the map/UI)
  - Extracts raw accel/gyro  → pushes IMURawMeasurement to imu_raw_queue
    (consumed by RecordingService for imu_raw.csv ~95-100 Hz)
  - Counts frame types       → session diagnostics in system_status.csv
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

from backend.services.imu_heading import (
    extract_ascii_yaws,
    extract_fdfc_yaws,
    extract_wit_yaws,
)
import backend.services.imu_raw as _imu_raw
from backend.services.imu_raw import (
    extract_fdfc_imu_raw,
    imu_raw_queue,
    scan_and_count_fdfc_frames,
)
from backend.services.selftest import imu_selftest

log = logging.getLogger("snowlink.imu")

SELFTEST_INTERVAL = 3.0

_BAUDRATES     = (921600, 115200, 9600)
_READ_SIZE     = 1024
_STALE_SECONDS = 2.5
_BAUD_TIMEOUT  = 4.0

# ── Connection diagnostics (for RecordingService.stop()) ──────────────────────
# Set by _connect() / _reader(); read by recording service at session end.
connected_port: str = ""
connected_baud: int = 0


class ImuService:

    def __init__(self, data_service) -> None:
        self._ds              = data_service
        self._lock            = threading.Lock()
        self._serial          = None
        self._active          = True
        self._baud_index      = 0
        self._last_good_baud: Optional[int] = None
        threading.Thread(target=self._loop, daemon=True, name="imu-loop").start()

    # ── Main loop ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._active:
            self._tick()
            time.sleep(SELFTEST_INTERVAL)

    def _tick(self) -> None:
        try:
            with self._lock:
                is_open = self._serial is not None and self._serial.is_open

            if is_open:
                return  # Reader thread is active — let it manage the connection.

            port = self._resolve_port()
            if port is None:
                self._ds.set_imu_connection(False)
                return

            self._connect(port)
        except Exception:
            log.exception("IMU tick failed")
            self._ds.set_imu_connection(False)

    # ── Port resolution ───────────────────────────────────────────────────

    def _resolve_port(self) -> Optional[str]:
        """Return port from config (explicit) or auto-detection."""
        import os
        from backend.services.config_service import ConfigService
        configured = ConfigService.get_imu().get("port", "").strip()
        if configured:
            if os.path.exists(configured):
                return configured
            log.error(
                "IMU: configured port %s not found — "
                "run 'ls -l /dev/serial/by-id/' to find the correct path",
                configured,
            )
            return None
        return imu_selftest.find_port()

    # ── Serial connection ─────────────────────────────────────────────────

    def _connect(self, port: str) -> None:
        global connected_port, connected_baud
        # Baud rotation: use cached working baud, or cycle through candidates.
        baudrate = self._last_good_baud or _BAUDRATES[self._baud_index]
        if self._last_good_baud is None:
            self._baud_index = (self._baud_index + 1) % len(_BAUDRATES)

        ser = None
        try:
            import serial
            ser = serial.Serial(port, baudrate=baudrate, timeout=0.25)
            with self._lock:
                self._serial = ser
            self._ds.set_imu_connection(True)
            connected_port = port
            connected_baud = baudrate
            log.info("IMU connecting on %s @ %d baud (attempting)", port, baudrate)
            threading.Thread(
                target=self._reader, args=(ser,), daemon=True, name="imu-reader"
            ).start()
        except Exception:
            log.exception("IMU connect failed on %s @ %d", port, baudrate)
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            self._ds.set_imu_connection(False)

    # ── Reader thread ─────────────────────────────────────────────────────

    def _reader(self, ser, baudrate: Optional[int] = None) -> None:
        """
        Read from the IMU serial port continuously.
        baudrate arg is read from ser.baudrate when not passed explicitly
        (kept for backwards compat with unit tests that call _reader directly).
        """
        global connected_baud

        if baudrate is None:
            baudrate = getattr(ser, "baudrate", 115200)

        wit_buf     = b""
        fdfc_buf    = b""
        imu_raw_buf = b""
        text_buf    = ""
        connected_at = time.monotonic()
        last_yaw_at  = 0.0
        yaw_stale    = True
        yaw_confirmed = False  # True once we've logged a confirmed good baud

        try:
            while self._active and ser.is_open:
                try:
                    chunk = ser.read(_READ_SIZE)
                except Exception:
                    break

                if not chunk:
                    if not yaw_stale and time.monotonic() - last_yaw_at > _STALE_SECONDS:
                        self._ds.clear_imu_yaw()
                        yaw_stale = True
                    if yaw_stale and time.monotonic() - connected_at > _BAUD_TIMEOUT:
                        self._last_good_baud = None   # wrong baud — rotate on next connect
                        break
                    continue

                t_mono = time.monotonic()
                t_unix = time.time()

                latest_yaw = None

                # ── Diagnostics: count FDFC frame types in raw chunk
                scan_and_count_fdfc_frames(chunk)

                # ── Heading extraction (FDFC Euler, WIT binary, ASCII text)
                yaws, fdfc_buf = extract_fdfc_yaws(fdfc_buf + chunk)
                if yaws:
                    latest_yaw = yaws[-1]

                yaws, wit_buf = extract_wit_yaws(wit_buf + chunk)
                if yaws:
                    latest_yaw = yaws[-1]

                yaws, text_buf = extract_ascii_yaws(
                    text_buf, chunk.decode("ascii", errors="ignore")
                )
                if yaws:
                    latest_yaw = yaws[-1]

                # ── Raw IMU extraction (FDFC 0x40/0x41 → imu_raw_queue)
                raw_samples, imu_raw_buf = extract_fdfc_imu_raw(
                    imu_raw_buf + chunk, t_mono, t_unix
                )
                _imu_raw.stat_frames_extracted += len(raw_samples)
                for m in raw_samples:
                    try:
                        imu_raw_queue.put_nowait(m)
                        _imu_raw.stat_frames_enqueued += 1
                    except queue.Full:
                        _imu_raw.stat_frames_dropped_full += 1

                # ── Update data_service
                if latest_yaw is not None:
                    if self._last_good_baud != baudrate:
                        self._last_good_baud = baudrate
                        connected_baud = baudrate
                    if not yaw_confirmed:
                        log.info(
                            "IMU confirmed working on %s @ %d baud",
                            getattr(ser, "port", "?"), baudrate,
                        )
                        yaw_confirmed = True
                    last_yaw_at = t_mono
                    yaw_stale   = False
                    self._ds.set_imu_yaw(latest_yaw)
                elif yaw_stale and t_mono - connected_at > _BAUD_TIMEOUT:
                    self._last_good_baud = None   # wrong baud — try next
                    break

        finally:
            try:
                ser.close()
            except Exception:
                pass
            with self._lock:
                if self._serial is ser:
                    self._serial = None
            if self._active:
                self._ds.set_imu_connection(False)

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
