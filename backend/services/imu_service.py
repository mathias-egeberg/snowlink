"""
IMU service – WheelTech N100 via CP2102 USB-serial adapter.

Two detection paths run in parallel:
  - Reader thread (fast): calls set_imu_connection(False) the moment
    ser.read() raises or yaw data goes stale, typically within 2-3 s.
  - Main loop (3 s guarantee): scans USB via imu_selftest every
    SELFTEST_INTERVAL seconds regardless of reader state.  If the device
    has gone from the USB bus it force-closes the port and marks
    disconnected even if the reader hasn't noticed yet.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from backend.services.imu_heading import (
    extract_ascii_yaws,
    extract_fdfc_yaws,
    extract_wit_yaws,
)
from backend.services.selftest import imu_selftest

log = logging.getLogger("snowlink.imu")

SELFTEST_INTERVAL = 3.0

_BAUDRATES     = (921600, 115200, 9600)
_READ_SIZE     = 1024
_STALE_SECONDS = 2.5
_BAUD_TIMEOUT  = 4.0     # give up on a baud rate if no yaw arrives within this


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
            try:
                self._tick()
            except Exception:
                log.exception("IMU loop tick failed")
            time.sleep(SELFTEST_INTERVAL)

    def _tick(self) -> None:
        port = imu_selftest.find_port()

        with self._lock:
            ser    = self._serial
            is_open = ser is not None and ser.is_open

        if port is None:
            # Device gone from USB – force-close if still open, mark disconnected.
            if is_open:
                try:
                    ser.close()
                except Exception:
                    pass
            self._ds.set_imu_connection(False)

        elif not is_open:
            # Device present but no open port – connect.
            self._connect(port)

    # ── Serial connection ─────────────────────────────────────────────────

    def _connect(self, port: str) -> None:
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
            threading.Thread(
                target=self._reader, args=(ser,), daemon=True, name="imu-reader"
            ).start()
            log.debug("IMU connected on %s @ %d", port, baudrate)
        except Exception:
            log.exception("IMU connect failed on %s", port)
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            self._ds.set_imu_connection(False)

    # ── Reader thread ─────────────────────────────────────────────────────

    def _reader(self, ser) -> None:
        wit_buf      = b""
        fdfc_buf     = b""
        text_buf     = ""
        connected_at = time.monotonic()
        last_yaw_at  = 0.0
        yaw_stale    = True

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
                        self._last_good_baud = None   # wrong baud – rotate on next connect
                        break
                    continue

                latest_yaw = None

                yaws, wit_buf  = extract_wit_yaws(wit_buf + chunk)
                if yaws:
                    latest_yaw = yaws[-1]

                yaws, fdfc_buf = extract_fdfc_yaws(fdfc_buf + chunk)
                if yaws:
                    latest_yaw = yaws[-1]

                yaws, text_buf = extract_ascii_yaws(
                    text_buf, chunk.decode("ascii", errors="ignore")
                )
                if yaws:
                    latest_yaw = yaws[-1]

                if latest_yaw is not None:
                    if self._last_good_baud != ser.baudrate:
                        self._last_good_baud = ser.baudrate
                    last_yaw_at = time.monotonic()
                    yaw_stale   = False
                    self._ds.set_imu_yaw(latest_yaw)
                elif yaw_stale and time.monotonic() - connected_at > _BAUD_TIMEOUT:
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
            # Fast-path disconnect notification – the loop will confirm within 3 s.
            if self._active:
                self._ds.set_imu_connection(False)

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
