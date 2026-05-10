"""
USB IMU detection and yaw reading for WheelTech N100.

The WheelTech N100 uses a Silicon Labs CP2102 USB-to-UART chip:
  VID 0x10C4 / PID 0xEA60

Detection is by VID/PID so it works regardless of which /dev/ttyUSBx
port the OS assigns. A keyword fallback catches units that report a
different description string.
"""
from __future__ import annotations

import threading
import time

from kivy.clock import Clock

from app.services.imu_heading import (
    extract_ascii_yaws,
    extract_fdfc_yaws,
    extract_wit_yaws,
)

# Silicon Labs CP2102 — the USB chip inside the WheelTech N100.
_WHEELTECH_VID = 0x10C4
_WHEELTECH_PID = 0xEA60

# Keyword fallback — matched against port description + hardware ID string.
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
    except ImportError:
        return None
    except Exception:
        return None
    return None


def _detect_imu() -> bool:
    return _find_imu_port() is not None


class ImuService:
    """Reads IMU yaw and keeps DataService IMU properties current."""

    POLL_INTERVAL = 3.0
    BAUDRATES = (921600, 115200, 9600)
    READ_SIZE = 1024
    STALE_YAW_SECONDS = 2.5
    INITIAL_YAW_TIMEOUT = 4.0

    def __init__(self, data_service):
        self._ds = data_service
        self._lock = threading.Lock()
        self._serial = None
        self._active = True
        self._baud_index = 0
        self._reconnect_check(0)
        self._event = Clock.schedule_interval(
            self._reconnect_check,
            self.POLL_INTERVAL,
        )

    def _reconnect_check(self, _dt):
        try:
            if not self._is_active():
                return
            with self._lock:
                if self._serial is not None and self._serial.is_open:
                    return

            port = _find_imu_port()
            if port is None:
                self._apply_connection(False)
                return
            self._connect(port)
        except Exception:
            self._apply_connection(False)

    def _connect(self, port: str) -> None:
        import serial

        baudrate = self.BAUDRATES[self._baud_index]
        self._baud_index = (self._baud_index + 1) % len(self.BAUDRATES)
        ser = None
        try:
            ser = serial.Serial(port, baudrate=baudrate, timeout=0.25)
            with self._lock:
                self._serial = ser
            self._apply_connection(True)
            threading.Thread(target=self._read_loop, args=(ser,), daemon=True).start()
        except Exception:
            if ser is not None and ser.is_open:
                try:
                    ser.close()
                except Exception:
                    pass
            self._apply_connection(False)

    def _read_loop(self, ser) -> None:
        wit_buffer = b""
        fdfc_buffer = b""
        text_buffer = ""
        connected_at = time.monotonic()
        last_yaw_at = 0.0
        yaw_is_stale = True

        try:
            while self._is_active() and ser.is_open:
                try:
                    chunk = ser.read(self.READ_SIZE)
                except Exception:
                    break

                if not chunk:
                    yaw_age = time.monotonic() - last_yaw_at
                    if not yaw_is_stale and yaw_age > self.STALE_YAW_SECONDS:
                        self._apply_yaw(None)
                        yaw_is_stale = True
                    if yaw_is_stale and self._has_initial_yaw_timed_out(connected_at):
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
                    last_yaw_at = time.monotonic()
                    yaw_is_stale = False
                    self._apply_yaw(latest_yaw)
                elif self._has_initial_yaw_timed_out(connected_at):
                    break
        finally:
            try:
                ser.close()
            except Exception:
                pass
            with self._lock:
                if self._serial is ser:
                    self._serial = None
            if self._is_active():
                self._apply_connection(False)

    def _apply_connection(self, connected: bool) -> None:
        def _update(_dt):
            self._ds.imu_ok = connected
            if not connected:
                self._ds.clear_imu_yaw()
        Clock.schedule_once(_update, 0)

    def _has_initial_yaw_timed_out(self, connected_at: float) -> bool:
        return time.monotonic() - connected_at > self.INITIAL_YAW_TIMEOUT

    def _is_active(self) -> bool:
        with self._lock:
            return self._active

    def _apply_yaw(self, yaw_deg: float | None) -> None:
        def _update(_dt):
            if yaw_deg is None:
                self._ds.clear_imu_yaw()
                return
            self._ds.set_imu_yaw(yaw_deg)
        Clock.schedule_once(_update, 0)

    def stop(self):
        with self._lock:
            self._active = False
        if self._event:
            Clock.unschedule(self._event)
            self._event = None
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
