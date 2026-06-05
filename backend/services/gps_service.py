"""
GPS service – u-blox ZED-F9P RTK module.

Same two-path detection as ImuService:
  - Reader thread (fast): calls set_gps(disconnected) the moment readline
    raises an error.
  - Main loop (3 s guarantee): scans USB via gps_selftest and force-closes
    the port if the device has left the bus.

Baud rate: tries 115200 → 38400 → 9600 in sequence until valid GGA is
received. The last working baud is remembered across reconnects.

ZED-F9P rate configuration: after the first valid GGA sentence, sends a
UBX-CFG-RATE message to request 20 Hz measurement output (50 ms period).
Only sent when baud ≥ 38400 to avoid buffer overflow on slow ports. The
F9P must be pre-configured (via u-center) to output GGA at ≥ 38400 baud;
changing the baud rate itself requires a separate UBX-CFG-PRT sequence
that is outside the scope of this auto-configuration.
"""
from __future__ import annotations

import logging
import struct
import threading
import time
from typing import Optional

from backend.services.selftest import gps_selftest

log = logging.getLogger("snowlink.gps")

SELFTEST_INTERVAL = 3.0

_BAUDRATES   = (115200, 38400, 9600)
_BAUD_TIMEOUT = 5.0   # seconds to wait for valid GGA before trying next baud

_FIX_TABLE: dict[int, tuple[bool, bool, str]] = {
    0: (False, False, "No Fix"),
    1: (False, True,  "GPS Fix"),
    2: (False, True,  "DGPS Fix"),
    3: (False, True,  "PPS Fix"),
    4: (True,  False, "RTK Fixed"),
    5: (False, True,  "RTK Float"),
    6: (False, False, "Dead Reckon."),
}

# UBX-CFG-RATE: measRate=50 ms (20 Hz), navRate=1, timeRef=UTC.
# Pre-computed; checksum is Fletcher-8 over class+id+len+payload.
_UBX_CFG_RATE_20HZ = bytes.fromhex("b5620608060032000100000047e4")


def _ubx_cfg_rate(meas_rate_ms: int) -> bytes:
    """Build a UBX-CFG-RATE message for the given measurement period in ms."""
    payload = struct.pack("<HHH", meas_rate_ms, 1, 0)
    msg = bytes([0x06, 0x08]) + struct.pack("<H", len(payload)) + payload
    ck_a = ck_b = 0
    for b in msg:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return b"\xB5\x62" + msg + bytes([ck_a, ck_b])


class GpsService:

    def __init__(self, data_service) -> None:
        self._ds              = data_service
        self._lock            = threading.Lock()
        self._serial          = None
        self._active          = True
        self._baud_index      = 0
        self._last_good_baud: Optional[int] = None
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
            self._ds.set_gps(False, False, "Disconnected", connected=False)

        elif not is_open:
            self._connect(port)

    # ── Serial connection ─────────────────────────────────────────────────

    def _connect(self, port: str) -> None:
        baudrate = self._last_good_baud or _BAUDRATES[self._baud_index]
        if self._last_good_baud is None:
            self._baud_index = (self._baud_index + 1) % len(_BAUDRATES)

        try:
            import serial
            ser = serial.Serial(port, baudrate=baudrate, timeout=1)
        except Exception:
            log.exception("GPS connect failed on %s", port)
            self._ds.set_gps(False, False, "Error", connected=False)
            return
        with self._lock:
            self._serial = ser
        self._ds.set_gps(False, False, "No Fix", connected=True)
        log.debug("GPS connecting on %s @ %d", port, baudrate)
        threading.Thread(
            target=self._reader, args=(ser,), daemon=True, name="gps-reader"
        ).start()

    # ── Reader thread ─────────────────────────────────────────────────────

    def _reader(self, ser) -> None:
        connected_at   = time.monotonic()
        gga_count      = 0
        rate_configured = False

        try:
            while self._active and ser.is_open:
                try:
                    raw = ser.readline()
                except Exception:
                    break

                # Baud-rate timeout: check on every iteration so that garbage
                # data from a wrong baud rate doesn't prevent cycling to the
                # next baud (non-empty garbage keeps raw non-empty, but
                # still won't contain a valid GGA sentence).
                if gga_count == 0 and time.monotonic() - connected_at > _BAUD_TIMEOUT:
                    log.debug("GPS: no valid GGA at %d baud, retrying", ser.baudrate)
                    self._last_good_baud = None
                    break

                if not raw:
                    continue

                try:
                    line = raw.decode("ascii", errors="ignore").strip()
                except Exception:
                    continue

                if len(line) > 6 and line[3:6] == "GGA":
                    if gga_count == 0:
                        # First valid GGA — record working baud.
                        self._last_good_baud = ser.baudrate
                        log.info("GPS: valid GGA on %s @ %d", ser.device, ser.baudrate)
                        # Configure 20 Hz output if the port can sustain it.
                        if not rate_configured and ser.baudrate >= 38400:
                            try:
                                ser.write(_UBX_CFG_RATE_20HZ)
                                rate_configured = True
                                log.debug("GPS: sent UBX-CFG-RATE 20 Hz on %s", ser.device)
                            except Exception:
                                log.debug("GPS: UBX-CFG-RATE send failed")
                    gga_count += 1
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
                self._ds.set_gps(False, False, "Disconnected", connected=False)

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

    # ── RTCM injection (called by NtripService) ───────────────────────────

    def send_rtcm(self, data: bytes) -> None:
        with self._lock:
            ser = self._serial
        if ser is not None and ser.is_open:
            try:
                ser.write(data)
            except Exception:
                log.debug("RTCM write to GPS failed")

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
