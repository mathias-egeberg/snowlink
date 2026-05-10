"""
GPS service for u-blox ZED-F9P RTK module.

Identifies the module by USB VID/PID so it works regardless of which
/dev/ttyACMx port the OS assigns. Reads NMEA GGA sentences in a background
thread and keeps DataService.gps_ok / gps_float_rtk / gps_status_text current.

GGA fix quality values:
  0 = No fix       → red,    "No Fix"
  1 = GPS fix      → orange, "GPS Fix"
  2 = DGPS fix     → orange, "DGPS Fix"
  3 = PPS fix      → orange, "PPS Fix"
  4 = RTK Fixed    → green,  "RTK Fixed"
  5 = RTK Float    → orange, "RTK Float"
  6 = Dead reckon  → red,    "Dead Reckon."
"""
import threading

from kivy.clock import Clock
from serial.tools import list_ports

_GPS_VID = 0x1546  # U-Blox AG
_GPS_PID = 0x01A9  # ZED-F9P

# fix_quality → (gps_ok, gps_float_rtk, status_text)
_FIX_TABLE = {
    0: (False, False, "No Fix"),
    1: (False, True,  "GPS Fix"),
    2: (False, True,  "DGPS Fix"),
    3: (False, True,  "PPS Fix"),
    4: (True,  False, "RTK Fixed"),
    5: (False, True,  "RTK Float"),
    6: (False, False, "Dead Reckon."),
}


def _find_gps_port():
    try:
        for p in list_ports.comports():
            if p.vid == _GPS_VID and p.pid == _GPS_PID:
                return p.device
    except Exception:
        pass
    return None


class GpsService:
    """Polls USB for the ZED-F9P, reads NMEA in background, updates DataService."""

    POLL_INTERVAL = 3.0

    def __init__(self, data_service):
        self._ds = data_service
        self._lock = threading.Lock()
        self._serial = None
        self._active = True
        self._reconnect_check(0)
        Clock.schedule_interval(self._reconnect_check, self.POLL_INTERVAL)

    # ── Connection management ─────────────────────────────────────────────

    def _reconnect_check(self, dt):
        with self._lock:
            if self._serial is not None and self._serial.is_open:
                return
        port = _find_gps_port()
        if port:
            self._connect(port)
        else:
            self._apply(False, False, "Disconnected")

    def _connect(self, port):
        import serial
        try:
            ser = serial.Serial(port, baudrate=9600, timeout=1)
        except Exception:
            self._apply(False, False, "Error")
            return
        with self._lock:
            self._serial = ser
        threading.Thread(target=self._read_loop, args=(ser,), daemon=True).start()

    # ── NMEA reading ──────────────────────────────────────────────────────

    def _read_loop(self, ser):
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
                self._apply(False, False, "Disconnected")

    def _parse_gga(self, sentence):
        # $xxGGA,time,lat,NS,lon,EW,fix_quality,sats,hdop,alt,M,...
        parts = sentence.split(",")
        if len(parts) < 7 or parts[6] == "":
            return
        try:
            fq = int(parts[6])
        except ValueError:
            return
        ok, warn, text = _FIX_TABLE.get(fq, (False, False, "Unknown"))
        self._apply(ok, warn, text)

    # ── DataService update (always on main thread via Clock) ──────────────

    def _apply(self, ok, warn, text):
        def _update(dt):
            self._ds.gps_ok         = ok
            self._ds.gps_float_rtk  = warn
            self._ds.gps_status_text = text
        Clock.schedule_once(_update, 0)

    def stop(self):
        self._active = False
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
