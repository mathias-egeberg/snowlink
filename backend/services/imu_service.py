"""
USB IMU detection and yaw reading for WheelTech N100 (no Kivy dependency).

Detection is by VID/PID (Silicon Labs CP2102: 0x10C4/0xEA60) with a
keyword fallback for units that report a different hardware-ID string.

On Windows without hardware the service starts but immediately reports
disconnected (no crash).
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

_WHEELTECH_VID = 0x10C4
_WHEELTECH_PID = 0xEA60
_MATCH_KEYWORDS = ("wheeltech", "n100", "cp2102")


def _is_imu_port(port) -> bool:
    if port.vid == _WHEELTECH_VID and port.pid == _WHEELTECH_PID:
        return True
    combined = f"{port.description} {port.hwid}".lower()
    return any(kw in combined for kw in _MATCH_KEYWORDS)


def _find_imu_port() -> str | None:
    try:
        from serial.tools import list_ports
        for port in list_ports.comports():
            if _is_imu_port(port):
                return port.device
    except Exception:
        return None
    return None


class ImuService:
    """Reads IMU yaw and keeps DataService IMU properties current."""

    SELFTEST_INTERVAL = 3.0  # seconds between every USB presence check
    POLL_INTERVAL = SELFTEST_INTERVAL  # alias used by DataService boot check
    BAUDRATES = (921600, 115200, 9600)
    READ_SIZE = 1024
    STALE_YAW_SECONDS = 2.5
    INITIAL_YAW_TIMEOUT = 4.0

    def __init__(self, data_service) -> None:
        self._ds = data_service
        self._lock = threading.Lock()
        self._serial = None
        self._active = True
        self._baud_index = 0
        # Remembered baud rate from last successful yaw read – tried first on reconnect
        # so we don't cycle through wrong rates after a brief unplug.
        self._last_good_baud: Optional[int] = None
        # Set by _read_loop when it exits so _poll_loop wakes immediately.
        self._disconnected = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    # ── Connection management ─────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._active:
            with self._lock:
                already_open = self._serial is not None and self._serial.is_open

            if already_open:
                # Wait for the read loop to signal a disconnect, or run the
                # active selftest every SELFTEST_INTERVAL seconds.
                self._disconnected.wait(timeout=self.SELFTEST_INTERVAL)
                self._disconnected.clear()

                # Active USB selftest: verify the device is still physically
                # present even if the serial fd hasn't raised an error yet.
                if _find_imu_port() is None:
                    with self._lock:
                        ser = self._serial
                    if ser and ser.is_open:
                        try:
                            ser.close()
                        except Exception:
                            pass
                continue

            port = _find_imu_port()
            if port is None:
                self._ds.set_imu_connection(False)
            else:
                self._connect(port)
            time.sleep(self.SELFTEST_INTERVAL)

    def _connect(self, port: str) -> None:
        # Try the last known-good baud rate first; fall back to rotation.
        if self._last_good_baud is not None:
            baudrate = self._last_good_baud
        else:
            baudrate = self.BAUDRATES[self._baud_index]
            self._baud_index = (self._baud_index + 1) % len(self.BAUDRATES)
        ser = None
        try:
            import serial
            ser = serial.Serial(port, baudrate=baudrate, timeout=0.25)
            with self._lock:
                self._serial = ser
            self._ds.set_imu_connection(True)
            threading.Thread(
                target=self._read_loop, args=(ser,), daemon=True
            ).start()
        except Exception:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            self._ds.set_imu_connection(False)

    # ── Read loop ─────────────────────────────────────────────────────────

    def _read_loop(self, ser) -> None:
        wit_buffer = b""
        fdfc_buffer = b""
        text_buffer = ""
        connected_at = time.monotonic()
        last_yaw_at = 0.0
        yaw_is_stale = True

        try:
            while self._active and ser.is_open:
                try:
                    chunk = ser.read(self.READ_SIZE)
                except Exception:
                    break

                if not chunk:
                    yaw_age = time.monotonic() - last_yaw_at
                    if not yaw_is_stale and yaw_age > self.STALE_YAW_SECONDS:
                        self._ds.clear_imu_yaw()
                        yaw_is_stale = True
                    if yaw_is_stale and (
                        time.monotonic() - connected_at > self.INITIAL_YAW_TIMEOUT
                    ):
                        break
                    continue

                latest_yaw = None

                wit_yaws, wit_buffer = extract_wit_yaws(wit_buffer + chunk)
                if wit_yaws:
                    latest_yaw = wit_yaws[-1]

                fdfc_yaws, fdfc_buffer = extract_fdfc_yaws(fdfc_buffer + chunk)
                if fdfc_yaws:
                    latest_yaw = fdfc_yaws[-1]

                text = chunk.decode("ascii", errors="ignore")
                ascii_yaws, text_buffer = extract_ascii_yaws(text_buffer, text)
                if ascii_yaws:
                    latest_yaw = ascii_yaws[-1]

                if latest_yaw is not None:
                    # Remember the baud rate that gave us valid data.
                    if self._last_good_baud != ser.baudrate:
                        self._last_good_baud = ser.baudrate
                    last_yaw_at = time.monotonic()
                    yaw_is_stale = False
                    self._ds.set_imu_yaw(latest_yaw)
                elif yaw_is_stale and (
                    time.monotonic() - connected_at > self.INITIAL_YAW_TIMEOUT
                ):
                    # Wrong baud rate or unresponsive device – try next baud.
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
            if self._active:
                self._ds.set_imu_connection(False)
            # Wake the poll loop so it rescans without waiting out the full interval.
            self._disconnected.set()

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        with self._lock:
            self._active = False
            if self._serial and self._serial.is_open:
                self._serial.close()
        self._disconnected.set()
