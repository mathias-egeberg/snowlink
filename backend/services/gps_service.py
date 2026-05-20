"""
GPS service – reads NMEA GGA from u-blox ZED-F9P and updates DataService.

The main loop runs every 3 seconds and is the sole authority for
connection state.  It calls gps_selftest.find_port() on every tick:

  - device gone  → close serial port, mark disconnected
  - device present, not connected → open serial port, start reader thread
  - device present, connected → nothing (reader thread handles data)
"""
from __future__ import annotations

import threading
import time

from backend.services.selftest import gps_selftest

SELFTEST_INTERVAL = 3.0   # seconds between USB presence checks

_BAUDRATE = 9600

# GGA fix_quality → (gps_ok, float_rtk, status_text)
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
    """Manages the GPS serial connection and NMEA data pipeline."""

    def __init__(self, data_service) -> None:
        self._ds = data_service
        self._lock = threading.Lock()
        self._serial = None
        self._active = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gps-loop")
        self._thread.start()

    # ── Main loop (runs every 3 s) ────────────────────────────────────────

    def _loop(self) -> None:
        while self._active:
            port = gps_selftest.find_port()

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
                self._ds.set_gps(False, False, "Disconnected")

            elif not is_open:
                # Device present but not connected – open the serial port.
                self._connect(port)

            time.sleep(SELFTEST_INTERVAL)

    # ── Serial connection ─────────────────────────────────────────────────

    def _connect(self, port: str) -> None:
        try:
            import serial
            ser = serial.Serial(port, baudrate=_BAUDRATE, timeout=1)
        except Exception:
            self._ds.set_gps(False, False, "Error")
            return
        with self._lock:
            self._serial = ser
        threading.Thread(
            target=self._reader, args=(ser,), daemon=True, name="gps-reader"
        ).start()

    # ── Reader thread ─────────────────────────────────────────────────────

    def _reader(self, ser) -> None:
        """Parse NMEA GGA lines; exit silently on any error."""
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

    def _parse_gga(self, sentence: str) -> None:
        # $xxGGA,time,lat,NS,lon,EW,fix_quality,sats,hdop,alt,M,...
        parts = sentence.split(",")
        if len(parts) < 7 or parts[6] == "":
            return
        try:
            fq = int(parts[6])
        except ValueError:
            return
        ok, warn, text = _FIX_TABLE.get(fq, (False, False, "Unknown"))
        self._ds.set_gps(ok, warn, text)

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
