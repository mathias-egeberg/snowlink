"""
GPS service – u-blox ZED-F9P RTK module.

Two detection paths run in parallel:
  - Reader thread (fast): detects port errors immediately.
  - Main loop (3 s guarantee): scans USB via gps_selftest every
    SELFTEST_INTERVAL seconds regardless of reader state.

Port resolution order:
  1. gnss.port from config.yaml  (explicit /dev/serial/by-id/ path)
  2. Auto-detection via gps_selftest (u-blox VID/PID scan)

Baud rate is always read from gnss.baudrate in config.yaml (default 115200).

Protocol (gnss.protocol in config.yaml):
  "auto"  – parse whatever arrives; prefer UBX-NAV-PVT when observed,
             fall back to NMEA GGA after _UBX_FALLBACK_TIMEOUT seconds.
  "nmea"  – NMEA GGA only; use fix-type fallback accuracy table.
  "ubx"   – send UBX-CFG-MSG commands on connect, expect UBX output.

Accuracy values in gnss_raw.csv:
  UBX-NAV-PVT:  hAcc / vAcc from receiver  → acc_source=receiver_reported
  NMEA GGA:     per-fix-type fallback table → acc_source=estimated_from_fix_type
  No fix:       blanks                      → acc_source=unavailable

GNSS measurements are pushed to gnss_queue for event-driven recording.
Diagnostics are accumulated in module-level counters and written to
system_status.csv by RecordingService.stop().
"""
from __future__ import annotations

import logging
import os
import queue
import struct
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from backend.services.selftest import gps_selftest

log = logging.getLogger("snowlink.gps")

SELFTEST_INTERVAL    = 3.0
_UBX_FALLBACK_TIMEOUT = 2.0   # s: if no NAV-PVT arrives, fall back to NMEA

_FIX_TABLE: Dict[int, Tuple[bool, bool, str]] = {
    0: (False, False, "No Fix"),
    1: (False, True,  "GPS Fix"),
    2: (False, True,  "DGPS Fix"),
    3: (False, True,  "PPS Fix"),
    4: (True,  False, "RTK Fixed"),
    5: (False, True,  "RTK Float"),
    6: (False, False, "Dead Reckon."),
}

# Fallback (h_acc_m, v_acc_m) when receiver does not report accuracy.
_ACC_FALLBACK: Dict[int, Tuple[float, float]] = {
    4: (0.02, 0.05),   # RTK Fixed
    5: (0.20, 0.50),   # RTK Float
    2: (1.50, 3.00),   # DGPS
    3: (1.50, 3.00),   # PPS
    1: (2.50, 5.00),   # GPS Fix
    6: (5.00, 10.0),   # Dead reckoning
}


# ── GNSS measurement type ─────────────────────────────────────────────────────

@dataclass
class GNSSMeasurement:
    """One GNSS fix pushed to gnss_queue."""
    timestamp_monotonic: float
    timestamp_unix:      float
    lat:                 float
    lon:                 float
    altitude_m:          float
    fix_quality:         int           # GGA quality indicator (0–6)
    fix_type_text:       str           # human-readable e.g. "RTK Fixed"
    satellites:          int
    hdop:                float
    h_acc_m:             Optional[float] = None  # horizontal accuracy 1σ (m)
    v_acc_m:             Optional[float] = None  # vertical accuracy 1σ (m)
    acc_source:          str = "unavailable"     # receiver_reported | estimated_from_fix_type | unavailable


# ── Shared queue (GPS reader → RecordingService) ──────────────────────────────

gnss_queue: queue.Queue[GNSSMeasurement] = queue.Queue(maxsize=1000)


# ── Diagnostics (module-level, reset each recording session) ──────────────────

_diag_port:              str = ""
_diag_baudrate:          int = 0
_diag_bytes_received:    int = 0
_diag_protocol_used:     str = "nmea"   # nmea | ubx | mixed
_diag_sentences_by_type: Dict[str, int] = {}   # NMEA sentence type counts
_diag_ubx_msg_counts:    Dict[str, int] = {}   # UBX "cls/id" counts
_diag_acc_source_counts: Dict[str, int] = {}   # acc_source value counts
_diag_valid_gga:         int = 0        # total measurements emitted
_diag_invalid_gga:       int = 0        # GGA sentences that failed parsing
_diag_rtk_fixed_count:   int = 0
_diag_rtk_float_count:   int = 0
_diag_other_fix_count:   int = 0
_diag_max_gap_s:         float = 0.0
_diag_last_emit_t:       float = 0.0


def reset_gnss_diagnostics() -> None:
    """Reset per-session counters. Call from RecordingService.start()."""
    global _diag_bytes_received, _diag_protocol_used
    global _diag_valid_gga, _diag_invalid_gga
    global _diag_rtk_fixed_count, _diag_rtk_float_count, _diag_other_fix_count
    global _diag_max_gap_s, _diag_last_emit_t
    _diag_bytes_received  = 0
    _diag_protocol_used   = "nmea"
    _diag_sentences_by_type.clear()
    _diag_ubx_msg_counts.clear()
    _diag_acc_source_counts.clear()
    _diag_valid_gga       = 0
    _diag_invalid_gga     = 0
    _diag_rtk_fixed_count = 0
    _diag_rtk_float_count = 0
    _diag_other_fix_count = 0
    _diag_max_gap_s       = 0.0
    _diag_last_emit_t     = 0.0


def build_gnss_diagnostic_report(elapsed_s: float) -> str:
    """Multi-field GNSS diagnostics string for system_status.csv."""
    rate_hz   = _diag_valid_gga / elapsed_s if elapsed_s > 0 else 0.0
    nmea_str  = " ".join(f"{k}={v}" for k, v in sorted(_diag_sentences_by_type.items()))
    ubx_str   = " ".join(f"{k}={v}" for k, v in sorted(_diag_ubx_msg_counts.items()))
    acc_str   = " ".join(f"{k}={v}" for k, v in sorted(_diag_acc_source_counts.items()))
    return (
        f"protocol={_diag_protocol_used} "
        f"port={_diag_port!r} baud={_diag_baudrate} "
        f"bytes_rx={_diag_bytes_received} "
        f"measurements={_diag_valid_gga} (~{rate_hz:.2f}Hz) "
        f"rtk_fixed={_diag_rtk_fixed_count} rtk_float={_diag_rtk_float_count} "
        f"other_fix={_diag_other_fix_count} "
        f"acc_source=[{acc_str}] "
        f"max_gap={_diag_max_gap_s:.2f}s "
        f"nmea_types=[{nmea_str}] "
        f"ubx_types=[{ubx_str}]"
    )


# ── UBX helpers ───────────────────────────────────────────────────────────────

def _ubx_ck(data: bytes) -> Tuple[int, int]:
    """Fletcher-8 checksum over class+id+length+payload bytes."""
    a = b = 0
    for byte in data:
        a = (a + byte) & 0xFF
        b = (b + a)   & 0xFF
    return a, b


def _build_ubx_cfg_msg(msg_class: int, msg_id: int, rate: int) -> bytes:
    """
    Build a UBX-CFG-MSG command (8-byte payload) that enables msg_class/msg_id
    on UART1 at the given rate.
    """
    payload = bytes([msg_class, msg_id, 0, rate, 0, 0, 0, 0])
    ck_data = bytes([0x06, 0x01, len(payload), 0]) + payload
    ck_a, ck_b = _ubx_ck(ck_data)
    return b"\xb5\x62" + ck_data + bytes([ck_a, ck_b])


def _parse_buf(buf: bytes) -> Tuple[List[Tuple[int, int, bytes]], List[str], bytes]:
    """
    Scan a byte buffer for complete UBX frames and NMEA sentences.

    UBX frame:   0xB5 0x62 cls id lenL lenH [payload] ckA ckB
    NMEA sentence: starts with '$', ends at LF.

    Returns:
      ubx_frames: [(msg_class, msg_id, payload), ...]
      nmea_lines: [str, ...]  (stripped)
      remaining:  unconsumed bytes (incomplete frame/line at buffer end)
    """
    ubx_frames: List[Tuple[int, int, bytes]] = []
    nmea_lines:  List[str] = []
    cursor = 0
    n = len(buf)

    while cursor < n:
        b = buf[cursor]

        if b == 0xB5 and cursor + 1 < n and buf[cursor + 1] == 0x62:
            if n - cursor < 6:
                break
            msg_cls  = buf[cursor + 2]
            msg_id   = buf[cursor + 3]
            msg_len  = int.from_bytes(buf[cursor + 4: cursor + 6], "little")
            frame_end = cursor + 6 + msg_len + 2
            if n < frame_end:
                break
            ck_data         = buf[cursor + 2: cursor + 6 + msg_len]
            calc_a, calc_b  = _ubx_ck(ck_data)
            if calc_a == buf[frame_end - 2] and calc_b == buf[frame_end - 1]:
                ubx_frames.append((msg_cls, msg_id, buf[cursor + 6: cursor + 6 + msg_len]))
            cursor = frame_end

        elif b == ord("$"):
            nl = buf.find(b"\n", cursor)
            if nl < 0:
                break
            line = buf[cursor: nl].decode("ascii", errors="ignore").strip()
            if line:
                nmea_lines.append(line)
            cursor = nl + 1

        else:
            cursor += 1

    return ubx_frames, nmea_lines, buf[cursor:]


# ── Service ───────────────────────────────────────────────────────────────────

class GpsService:

    def __init__(self, data_service) -> None:
        self._ds             = data_service
        self._lock           = threading.Lock()
        self._serial         = None
        self._active         = True
        self._last_ubx_pvt_t = 0.0   # monotonic time of last NAV-PVT emit
        self._last_hdop      = 0.0   # cached from NMEA GGA or NAV-DOP
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
        global _diag_bytes_received

        from backend.services.config_service import ConfigService
        protocol = ConfigService.get_gnss().get("protocol", "auto").lower()

        if protocol == "ubx":
            self._enable_ubx_output(ser)

        buf = b""

        try:
            while self._active and ser.is_open:
                try:
                    chunk = ser.read(4096)
                except Exception:
                    break

                if not chunk:
                    continue

                _diag_bytes_received += len(chunk)
                buf += chunk

                t_mono = time.monotonic()
                t_unix = time.time()

                ubx_frames, nmea_lines, buf = _parse_buf(buf)

                for msg_cls, msg_id, payload in ubx_frames:
                    key = f"{msg_cls:02x}/{msg_id:02x}"
                    _diag_ubx_msg_counts[key] = _diag_ubx_msg_counts.get(key, 0) + 1
                    self._handle_ubx(msg_cls, msg_id, payload, t_mono, t_unix, protocol)

                for line in nmea_lines:
                    if line.startswith("$") and len(line) >= 6:
                        _diag_sentences_by_type[line[1:6]] = (
                            _diag_sentences_by_type.get(line[1:6], 0) + 1
                        )
                    if len(line) > 6 and line[3:6] == "GGA":
                        self._handle_gga(line, t_mono, t_unix, protocol)

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

    # ── UBX output enable ─────────────────────────────────────────────────

    def _enable_ubx_output(self, ser) -> None:
        """Send UBX-CFG-MSG to enable NAV-PVT, NAV-DOP, NAV-HPPOSLLH on UART1."""
        for msg_class, msg_id, name in (
            (0x01, 0x07, "NAV-PVT"),
            (0x01, 0x04, "NAV-DOP"),
            (0x01, 0x14, "NAV-HPPOSLLH"),
        ):
            cmd = _build_ubx_cfg_msg(msg_class, msg_id, rate=1)
            try:
                ser.write(cmd)
                time.sleep(0.05)
                log.debug("GNSS: sent UBX-CFG-MSG to enable %s", name)
            except Exception:
                log.warning("GNSS: failed to enable %s via UBX-CFG-MSG", name)

    # ── UBX frame dispatcher ──────────────────────────────────────────────

    def _handle_ubx(
        self,
        msg_cls: int,
        msg_id: int,
        payload: bytes,
        t_mono: float,
        t_unix: float,
        protocol: str,
    ) -> None:
        if msg_cls == 0x01:
            if msg_id == 0x07:
                self._handle_ubx_nav_pvt(payload, t_mono, t_unix)
            elif msg_id == 0x04:
                self._handle_ubx_nav_dop(payload)
            elif msg_id == 0x14:
                self._handle_ubx_nav_hpposllh(payload)

    # ── UBX-NAV-PVT (class 0x01, id 0x07) ───────────────────────────────

    def _handle_ubx_nav_pvt(self, payload: bytes, t_mono: float, t_unix: float) -> None:
        """Parse NAV-PVT; emit GNSSMeasurement with receiver-reported accuracy."""
        global _diag_protocol_used
        if len(payload) < 84:
            return

        fix_type = payload[20]
        flags    = payload[21]
        num_sv   = payload[23]

        gnss_fix_ok = bool(flags & 0x01)
        diff_soln   = bool((flags >> 1) & 0x01)
        carr_soln   = (flags >> 6) & 0x03   # 0=none, 1=float, 2=fixed

        lon      = struct.unpack_from("<i", payload, 24)[0] * 1e-7   # deg
        lat      = struct.unpack_from("<i", payload, 28)[0] * 1e-7   # deg
        height_m = struct.unpack_from("<i", payload, 32)[0] * 1e-3   # mm → m
        h_acc_mm = struct.unpack_from("<I", payload, 40)[0]
        v_acc_mm = struct.unpack_from("<I", payload, 44)[0]

        # Map UBX fix to GGA-compatible quality code
        if fix_type == 0 or not gnss_fix_ok:
            fq, fq_text = 0, "No Fix"
        elif carr_soln == 2:
            fq, fq_text = 4, "RTK Fixed"
        elif carr_soln == 1:
            fq, fq_text = 5, "RTK Float"
        elif diff_soln:
            fq, fq_text = 2, "DGPS Fix"
        else:
            fq, fq_text = 1, "GPS Fix"

        ok   = (fq == 4)
        warn = fq in (1, 2, 3, 5)
        self._ds.set_gps(
            ok, warn, fq_text,
            satellites=num_sv, hdop=self._last_hdop,
            lat=lat, lon=lon, altitude_m=height_m, fix_quality=fq,
        )

        if fq > 0:
            h_acc      = h_acc_mm * 1e-3
            v_acc      = v_acc_mm * 1e-3
            acc_source = "receiver_reported"
        else:
            h_acc = v_acc = None
            acc_source    = "unavailable"

        m = GNSSMeasurement(
            timestamp_monotonic=t_mono,
            timestamp_unix=t_unix,
            lat=lat, lon=lon, altitude_m=height_m,
            fix_quality=fq, fix_type_text=fq_text,
            satellites=num_sv, hdop=self._last_hdop,
            h_acc_m=h_acc, v_acc_m=v_acc,
            acc_source=acc_source,
        )

        _diag_protocol_used  = "mixed" if _diag_sentences_by_type else "ubx"
        self._last_ubx_pvt_t = t_mono
        self._emit(m)

    # ── UBX-NAV-DOP (class 0x01, id 0x04) ───────────────────────────────

    def _handle_ubx_nav_dop(self, payload: bytes) -> None:
        """Cache hDOP from NAV-DOP for use in NAV-PVT measurements."""
        if len(payload) < 18:
            return
        self._last_hdop = struct.unpack_from("<H", payload, 12)[0] * 0.01

    # ── UBX-NAV-HPPOSLLH (class 0x01, id 0x14) ──────────────────────────

    def _handle_ubx_nav_hpposllh(self, payload: bytes) -> None:
        """
        Log sub-mm accuracy from NAV-HPPOSLLH. Position comes from NAV-PVT
        (which has fix flags/carr_soln); we only log accuracy here for debug.
        """
        if len(payload) < 36:
            return
        h_acc = struct.unpack_from("<I", payload, 28)[0] * 0.1e-3   # 0.1 mm → m
        v_acc = struct.unpack_from("<I", payload, 32)[0] * 0.1e-3
        log.debug("GNSS NAV-HPPOSLLH: h_acc=%.4f m  v_acc=%.4f m", h_acc, v_acc)

    # ── NMEA GGA handler ─────────────────────────────────────────────────

    def _handle_gga(self, sentence: str, t_mono: float, t_unix: float, protocol: str) -> None:
        """Parse GGA; emit only when UBX-NAV-PVT is not flowing."""
        global _diag_protocol_used

        m = self._parse_gga(sentence, t_mono, t_unix)
        if m is None:
            return

        # Cache HDOP regardless of whether we emit (UBX-NAV-PVT uses it)
        self._last_hdop = m.hdop

        if protocol != "nmea" and t_mono - self._last_ubx_pvt_t < _UBX_FALLBACK_TIMEOUT:
            # UBX-NAV-PVT is flowing — skip NMEA emit to avoid duplicates
            return

        _diag_protocol_used = "mixed" if _diag_ubx_msg_counts else "nmea"
        self._emit(m)

    # ── NMEA GGA parser ───────────────────────────────────────────────────

    def _parse_gga(self, sentence: str, t_mono: float, t_unix: float) -> Optional[GNSSMeasurement]:
        """Return GNSSMeasurement from a GGA sentence, or None on parse failure."""
        global _diag_invalid_gga
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
            satellites=satellites, hdop=hdop,
            lat=lat, lon=lon, altitude_m=altitude_m, fix_quality=fq,
        )

        if fq in _ACC_FALLBACK:
            h_acc, v_acc = _ACC_FALLBACK[fq]
            acc_source   = "estimated_from_fix_type"
        else:
            h_acc = v_acc = None
            acc_source    = "unavailable"

        return GNSSMeasurement(
            timestamp_monotonic=t_mono,
            timestamp_unix=t_unix,
            lat=lat, lon=lon, altitude_m=altitude_m,
            fix_quality=fq, fix_type_text=text,
            satellites=satellites, hdop=hdop,
            h_acc_m=h_acc, v_acc_m=v_acc,
            acc_source=acc_source,
        )

    # ── Emit helper ───────────────────────────────────────────────────────

    def _emit(self, m: GNSSMeasurement) -> None:
        """Push measurement to queue and update session diagnostics."""
        global _diag_valid_gga, _diag_max_gap_s, _diag_last_emit_t
        global _diag_rtk_fixed_count, _diag_rtk_float_count, _diag_other_fix_count

        t = m.timestamp_monotonic
        if _diag_last_emit_t > 0.0:
            gap = t - _diag_last_emit_t
            if gap > _diag_max_gap_s:
                _diag_max_gap_s = gap
        _diag_last_emit_t = t

        _diag_valid_gga += 1
        _diag_acc_source_counts[m.acc_source] = (
            _diag_acc_source_counts.get(m.acc_source, 0) + 1
        )
        if m.fix_quality == 4:
            _diag_rtk_fixed_count += 1
        elif m.fix_quality == 5:
            _diag_rtk_float_count += 1
        elif m.fix_quality > 0:
            _diag_other_fix_count += 1

        try:
            gnss_queue.put_nowait(m)
        except queue.Full:
            pass

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
