"""
IMU service – reads yaw from WheelTech N100 and updates DataService.

The main loop runs every 3 seconds and is the sole authority for
connection state.  It calls imu_selftest.find_port() on every tick:

  - device gone  → close serial port, mark disconnected
  - device present, not connected → open serial port, start reader thread
  - device present, connected → nothing (reader thread handles data)

The reader thread only parses incoming bytes and reports stale data; it
does not manage connection state itself.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from backend.services.imu_heading import (
    extract_ascii_yaws,
    extract_fdfc_yaws,
    extract_wit_yaws,
)
from backend.services.selftest import imu_selftest

SELFTEST_INTERVAL = 3.0   # seconds between USB presence checks

_BAUDRATES = (921600, 115200, 9600)
_READ_SIZE = 1024
_STALE_YAW_SECONDS = 2.5
_INITIAL_YAW_TIMEOUT = 4.0


class ImuService:
    """Manages the IMU serial connection and yaw data pipeline."""

    def __init__(self, data_service) -> None:
        self._ds = data_service
        self._lock = threading.Lock()
        self._serial = None
        self._active = True
        self._baud_index = 0
        self._last_good_baud: Optional[int] = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="imu-loop")
        self._thread.start()

    # ── Main loop (runs every 3 s) ────────────────────────────────────────

    def _loop(self) -> None:
        while self._active:
            port = imu_selftest.find_port()

            with self._lock:
                ser = self._serial
                is_open = ser is not None and ser.is_open

            if port is None:
                # Device not present – close any open connection immediately.
                if is_open:
                    try:
                        ser.close()
                    except Exception:
                        pass
                self._ds.set_imu_connection(False)

            elif not is_open:
                # Device present but not connected – open the serial port.
                self._connect(port)

            time.sleep(SELFTEST_INTERVAL)

    # ── Serial connection ─────────────────────────────────────────────────

    def _connect(self, port: str) -> None:
        if self._last_good_baud is not None:
            baudrate = self._last_good_baud
        else:
            baudrate = _BAUDRATES[self._baud_index]
            self._baud_index = (self._baud_index + 1) % len(_BAUDRATES)

        ser = None
        try:
            import serial
            ser = serial.Serial(port, baudrate=baudrate, timeout=0.25)
            with self._lock:
                self._serial = ser
            self._ds.set_imu_connection(True)
            threading.Thread(
                target=self._reader, args=(ser,), daemon=True, name="imu-reader"
            ).start()
        except Exception:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            self._ds.set_imu_connection(False)

    # ── Reader thread ─────────────────────────────────────────────────────

    def _reader(self, ser) -> None:
        """Parse incoming bytes; exit silently on any error or stale data."""
        wit_buf = b""
        fdfc_buf = b""
        text_buf = ""
        connected_at = time.monotonic()
        last_yaw_at = 0.0
        yaw_stale = True

        try:
            while self._active and ser.is_open:
                try:
                    chunk = ser.read(_READ_SIZE)
                except Exception:
                    break

                if not chunk:
                    if not yaw_stale and time.monotonic() - last_yaw_at > _STALE_YAW_SECONDS:
                        self._ds.clear_imu_yaw()
                        yaw_stale = True
                    if yaw_stale and time.monotonic() - connected_at > _INITIAL_YAW_TIMEOUT:
                        # No data at this baud rate – mark it bad so the loop tries next.
                        self._last_good_baud = None
                        break
                    continue

                latest_yaw = None

                yaws, wit_buf = extract_wit_yaws(wit_buf + chunk)
                if yaws:
                    latest_yaw = yaws[-1]

                yaws, fdfc_buf = extract_fdfc_yaws(fdfc_buf + chunk)
                if yaws:
                    latest_yaw = yaws[-1]

                yaws, text_buf = extract_ascii_yaws(text_buf, chunk.decode("ascii", errors="ignore"))
                if yaws:
                    latest_yaw = yaws[-1]

                if latest_yaw is not None:
                    if self._last_good_baud != ser.baudrate:
                        self._last_good_baud = ser.baudrate
                    last_yaw_at = time.monotonic()
                    yaw_stale = False
                    self._ds.set_imu_yaw(latest_yaw)
                elif yaw_stale and time.monotonic() - connected_at > _INITIAL_YAW_TIMEOUT:
                    self._last_good_baud = None
                    break

        finally:
            try:
                ser.close()
            except Exception:
                pass
            with self._lock:
                if self._serial is ser:
                    self._serial = None

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
