"""
GPS service for u-blox ZED-F9P RTK module (no Kivy dependency).

Identifies the module by USB VID/PID so it works regardless of which
/dev/ttyACMx or COMx port the OS assigns.  Reads NMEA GGA sentences in a
background thread and updates the DataService GPS state.

On Windows without hardware the service starts but immediately reports
"Disconnected" (no crash).
"""
from __future__ import annotations

import threading
import time

_GPS_VID = 0x1546   # U-Blox AG
_GPS_PID = 0x01A9   # ZED-F9P

# fix_quality → (gps_ok, gps_float_rtk, status_text)
_FIX_TABLE: dict[int, tuple[bool, bool, str]] = {
    0: (False, False, "No Fix"),
    1: (False, True,  "GPS Fix"),
    2: (False, True,  "DGPS Fix"),
    3: (False, True,  "PPS Fix"),
    4: (True,  False, "RTK Fixed"),
    5: (False, True,  "RTK Float"),
    6: (False, False, "Dead Reckon."),
}


def _find_gps_port() -> str | None:
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.vid == _GPS_VID and p.pid == _GPS_PID:
                return p.device
    except Exception:
        pass
    return None


class GpsService:
    """Polls USB for ZED-F9P, reads NMEA in background, updates DataService."""

    POLL_INTERVAL = 3.0

    def __init__(self, data_service) -> None:
        self._ds = data_service
        self._lock = threading.Lock()
        self._serial = None
        self._active = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    # ── Connection management ─────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._active:
            with self._lock:
                already_open = self._serial is not None and self._serial.is_open
            if already_open:
                time.sleep(self.POLL_INTERVAL)
                continue

            port = _find_gps_port()
            if port:
                self._connect(port)
            else:
                self._ds.set_gps(False, False, "Disconnected")
            time.sleep(self.POLL_INTERVAL)

    def _connect(self, port: str) -> None:
        try:
            import serial
            ser = serial.Serial(port, baudrate=9600, timeout=1)
        except Exception:
            self._ds.set_gps(False, False, "Error")
            return
        with self._lock:
            self._serial = ser
        threading.Thread(target=self._read_loop, args=(ser,), daemon=True).start()

    # ── NMEA reading ──────────────────────────────────────────────────────

    def _read_loop(self, ser) -> None:
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
                # Match any talker's GGA sentence ($GNGGA, $GPGGA, etc.)
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
