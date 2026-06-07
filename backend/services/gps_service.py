"""
GPS service – u-blox ZED-F9P RTK module.

Same two-path detection as ImuService:
  - Reader thread (fast): calls set_gps(disconnected) the moment readline
    raises an error.
  - Main loop (3 s guarantee): scans USB via gps_selftest and force-closes
    the port if the device has left the bus.

Port resolution order:
  1. gnss.port from config.yaml  (explicit /dev/serial/by-id/ path)
  2. Auto-detection via gps_selftest (u-blox VID/PID scan)

Baud rate is always read from gnss.baudrate in config.yaml (default 115200).
The ZED-F9P must be pre-configured to the same baud rate using u-center2.

GNSS measurements are pushed to gnss_queue for event-driven recording.
Diagnostics are accumulated in module-level counters and written to
system_status.csv by RecordingService.stop().
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from backend.services.selftest import gps_selftest

log = logging.getLogger("snowlink.gps")

SELFTEST_INTERVAL = 3.0

_FIX_TABLE: dict[int, tuple[bool, bool, str]] = {
    0: (False, False, "No Fix"),
    1: (False, True,  "GPS Fix"),
    2: (False, True,  "DGPS Fix"),
    3: (False, True,  "PPS Fix"),
    4: (True,  False, "RTK Fixed"),
    5: (False, True,  "RTK Float"),
    6: (False, False, "Dead Reckon."),
}


# ── GNSS measurement type ────────────────────────────────────────────────────

@dataclass
class GNSSMeasurement:
    """One parsed GGA fix pushed to gnss_queue."""
    timestamp_monotonic: float
    timestamp_unix: float
    lat: float
    lon: float
    altitude_m: float
    fix_quality: int        # numeric GGA quality indicator
    fix_type_text: str      # human-readable e.g. "RTK Fixed"
    satellites: int
    hdop: float


# ── Shared queue (GPS reader → RecordingService) ──────────────────────────────

gnss_queue: queue.Queue[GNSSMeasurement] = queue.Queue(maxsize=1000)


# ── Diagnostics (module-level, reset at each recording session) ───────────────
# GIL makes simple dict/int operations safe without an explicit lock here.

_diag_port: str = ""
_diag_baudrate: int = 0
_diag_bytes_received: int = 0
_diag_sentences_by_type: Dict[str, int] = {}
_diag_valid_gga: int = 0
_diag_invalid_gga: int = 0
_diag_max_gap_s: float = 0.0
_diag_last_gga_t: float = 0.0   # monotonic time of last valid GGA


def reset_gnss_diagnostics() -> None:
    """Reset per-session counters. Call from RecordingService.start()."""
    global _diag_bytes_received, _diag_valid_gga, _diag_invalid_gga
    global _diag_max_gap_s, _diag_last_gga_t
    _diag_bytes_received = 0
    _diag_sentences_by_type.clear()
    _diag_valid_gga = 0
    _diag_invalid_gga = 0
    _diag_max_gap_s = 0.0
    _diag_last_gga_t = 0.0


def build_gnss_diagnostic_report(elapsed_s: float) -> str:
    """One-line GNSS diagnostics for system_status.csv."""
    rate_hz = _diag_valid_gga / elapsed_s if elapsed_s > 0 else 0.0
    types_str = " ".join(f"{k}={v}" for k, v in sorted(_diag_sentences_by_type.items()))
    return (
        f"port={_diag_port!r} baud={_diag_baudrate} "
        f"bytes_rx={_diag_bytes_received} "
        f"valid_gga={_diag_valid_gga} (~{rate_hz:.2f}Hz) "
        f"invalid_gga={_diag_invalid_gga} "
        f"max_gap={_diag_max_gap_s:.2f}s "
        f"nmea_types=[{types_str}]"
    )


# ── Service ───────────────────────────────────────────────────────────────────

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

    def _resolve_port(self) -> Optional[str]:
        """Return the port to use: config path first, then auto-detect."""
        from backend.services.config_service import ConfigService
        configured = ConfigService.get_gnss().get("port", "").strip()
        if configured:
            if os.path.exists(configured):
                return configured
            log.error(
                "GNSS: configured port %s not found — "
                "run 'ls -l /dev/serial/by-id/' to find the correct path",
                configured,
            )
            return None
        return gps_selftest.find_port()

    def _tick(self) -> None:
        port = self._resolve_port()

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
        global _diag_port, _diag_baudrate
        from backend.services.config_service import ConfigService
        baudrate = ConfigService.get_gnss().get("baudrate", 115200)
        try:
            import serial
            ser = serial.Serial(port, baudrate=baudrate, timeout=1)
        except Exception:
            log.exception("GNSS connect failed on %s @ %d baud", port, baudrate)
            self._ds.set_gps(False, False, "Error")
            return
        with self._lock:
            self._serial = ser
        _diag_port     = port
        _diag_baudrate = baudrate
        log.info("GNSS connected on %s @ %d baud", port, baudrate)
        threading.Thread(
            target=self._reader, args=(ser,), daemon=True, name="gps-reader"
        ).start()

    # ── Reader thread ─────────────────────────────────────────────────────

    def _reader(self, ser) -> None:
        global _diag_bytes_received, _diag_valid_gga, _diag_invalid_gga
        global _diag_max_gap_s, _diag_last_gga_t

        try:
            while self._active and ser.is_open:
                try:
                    raw = ser.readline()
                except Exception:
                    break

                if not raw:
                    continue

                _diag_bytes_received += len(raw)

                try:
                    line = raw.decode("ascii", errors="ignore").strip()
                except Exception:
                    continue

                if not line:
                    continue

                # Track NMEA sentence types for diagnostics
                if line.startswith("$") and len(line) >= 6:
                    msg_type = line[1:6]
                    _diag_sentences_by_type[msg_type] = (
                        _diag_sentences_by_type.get(msg_type, 0) + 1
                    )

                if len(line) > 6 and line[3:6] == "GGA":
                    m = self._parse_gga(line)
                    if m is not None:
                        # Track GGA arrival gap
                        now = time.monotonic()
                        if _diag_last_gga_t > 0.0:
                            gap = now - _diag_last_gga_t
                            if gap > _diag_max_gap_s:
                                _diag_max_gap_s = gap
                        _diag_last_gga_t = now

                        try:
                            gnss_queue.put_nowait(m)
                        except queue.Full:
                            pass  # recording consumer is too slow; drop oldest not supported

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

    def _parse_gga(self, sentence: str) -> Optional[GNSSMeasurement]:
        """Parse NMEA GGA, update data_service state, return measurement."""
        global _diag_valid_gga, _diag_invalid_gga
        parts = sentence.split(",")
        if len(parts) < 10 or parts[6] == "":
            _diag_invalid_gga += 1
            return None
        try:
            fq = int(parts[6])
        except ValueError:
            _diag_invalid_gga += 1
            return None

        ok, warn, text = _FIX_TABLE.get(fq, (False, False, "Unknown"))

        satellites = 0
        hdop       = 0.0
        lat        = 0.0
        lon        = 0.0
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
                if parts[3] == "S":
                    lat = -lat
        except (ValueError, IndexError):
            pass
        try:
            if parts[4] and parts[5]:
                raw = float(parts[4])
                deg = int(raw / 100)
                lon = deg + (raw - deg * 100) / 60.0
                if parts[5] == "W":
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

        _diag_valid_gga += 1

        return GNSSMeasurement(
            timestamp_monotonic=time.monotonic(),
            timestamp_unix=time.time(),
            lat=lat,
            lon=lon,
            altitude_m=altitude_m,
            fix_quality=fq,
            fix_type_text=text,
            satellites=satellites,
            hdop=hdop,
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
