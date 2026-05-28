"""
GPS service – u-blox ZED-F9P RTK module.

Same two-path detection as ImuService:
  - Reader thread (fast): calls set_gps(disconnected) the moment readline
    raises an error.
  - Main loop (3 s guarantee): scans USB via gps_selftest and force-closes
    the port if the device has left the bus.
"""
from __future__ import annotations

import logging
import threading
import time

from backend.services.selftest import gps_selftest

log = logging.getLogger("snowlink.gps")

SELFTEST_INTERVAL = 3.0

_BAUDRATE = 9600

_FIX_TABLE: dict[int, tuple[bool, bool, str]] = {
    0: (False, False, "No Fix"),
    1: (False, True,  "GPS Fix"),
    2: (False, True,  "DGPS Fix"),
    3: (False, True,  "PPS Fix"),
    4: (True,  False, "RTK Fixed"),
    5: (False, True,  "RTK Float"),
    6: (False, False, "Dead Reckon."),
}


class GpsService:

    def __init__(self, data_service) -> None:
        self._ds     = data_service
        self._lock   = threading.Lock()
        self._serial = None
        self._active = True
        threading.Thread(target=self._loop, daemon=True, name="gps-loop").start()

    # ── Main loop ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._active:
            try:
                self._tick()
            except Exception:
                log.exception("GPS loop tick failed")
            time.sleep(SELFTEST_INTERVAL)

    def _tick(self) -> None:
        port = gps_selftest.find_port()

        with self._lock:
            ser     = self._serial
            is_open = ser is not None and ser.is_open

        if port is None:
            if is_open:
                try:
                    ser.close()
                except Exception:
                    pass
                with self._lock:
                    if self._serial is ser:
                        self._serial = None
            self._ds.set_gps(False, False, "Disconnected")

        elif not is_open:
            self._connect(port)

    # ── Serial connection ─────────────────────────────────────────────────

    def _connect(self, port: str) -> None:
        try:
            import serial
            ser = serial.Serial(port, baudrate=_BAUDRATE, timeout=1)
        except Exception:
            log.exception("GPS connect failed on %s", port)
            self._ds.set_gps(False, False, "Error")
            return
        with self._lock:
            self._serial = ser
        log.debug("GPS connected on %s", port)
        threading.Thread(
            target=self._reader, args=(ser,), daemon=True, name="gps-reader"
        ).start()

    # ── Reader thread ─────────────────────────────────────────────────────

    def _reader(self, ser) -> None:
        try:
            while self._active and ser.is_open:
                try:
                    raw = ser.readline()
                except Exception:
                    break
                try:
                    line = raw.decode("ascii", errors="ignore").strip()
                except Exception:
                    continue
                if len(line) > 6 and line[3:6] == "GGA":
                    self._parse_gga(line)
        finally:
            try:
                ser.close()
            except Exception:
                pass
            with self._lock:
                if self._serial is ser:
                    self._serial = None
            if self._active:
                self._ds.set_gps(False, False, "Disconnected")

    def _parse_gga(self, sentence: str) -> None:
        parts = sentence.split(",")
        if len(parts) < 10 or parts[6] == "":
            return
        try:
            fq = int(parts[6])
        except ValueError:
            return
        ok, warn, text = _FIX_TABLE.get(fq, (False, False, "Unknown"))

        satellites = 0
        hdop = 0.0
        lat = 0.0
        lon = 0.0
        altitude_m = 0.0

        try:
            if parts[7]:
                satellites = int(parts[7])
        except (ValueError, IndexError):
            pass
        try:
            if parts[8]:
                hdop = float(parts[8])
        except (ValueError, IndexError):
            pass
        try:
            if parts[9]:
                altitude_m = float(parts[9])
        except (ValueError, IndexError):
            pass
        try:
            if parts[2] and parts[3]:
                raw = float(parts[2])
                deg = int(raw / 100)
                lat = deg + (raw - deg * 100) / 60.0
                if parts[3] == 'S':
                    lat = -lat
        except (ValueError, IndexError):
            pass
        try:
            if parts[4] and parts[5]:
                raw = float(parts[4])
                deg = int(raw / 100)
                lon = deg + (raw - deg * 100) / 60.0
                if parts[5] == 'W':
                    lon = -lon
        except (ValueError, IndexError):
            pass

        self._ds.set_gps(
            ok, warn, text,
            satellites=satellites,
            hdop=hdop,
            lat=lat,
            lon=lon,
            altitude_m=altitude_m,
            fix_quality=fq,
        )

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
