"""
Raw IMU measurement type and FDFC binary frame parser for WHEELTEC N100.

IMURawMeasurement is the canonical sample type for high-rate logging and
for the future ESKF GNSS/IMU fusion pipeline.

The module-level imu_raw_queue is the shared channel between ImuService
(producer) and RecordingService (consumer).

═══ FDFC Frame Layout (WHEELTEC N100) ════════════════════════════════════════

Type 0x40 – 64 bytes total (confirmed ~107 Hz on test hardware):
  [0:2]   FD FC header
  [2]     0x40
  [3]     0x38 = 56 (payload length)
  [4:60]  payload (56 bytes) — confirmed from hardware hex dump:
    [0:4]   uint32_t  packet counter
    [4:16]  float32*3 gyro_x, gyro_y, gyro_z   (rad/s)
    [16:28] float32*3 accel_x, accel_y, accel_z (m/s²)  |a|=9.87 stationary
    [28:40] float32*3 Euler roll, pitch, yaw     (degrees)
    [40:44] float32   temperature                (°C)
    [44:48] float32   pressure                  (Pa, ~101325 at sea level)
    [48:56] reserved / near-zero
  [60:64] 4-byte CRC (not validated)

Type 0x41 – 56 bytes total (confirmed ~107 Hz, Euler/orientation output):
  [0:2]   FD FC header
  [2]     0x41
  [3]     0x30 = 48 (payload length)
  [4:52]  payload (48 bytes):
    [0:4]   uint32_t  packet counter
    [4:24]  various fields (near-zero in stationary test)
    [24:36] float32*3 yaw, pitch, roll (radians) — confirmed by heading parser
    [36:48] near-zero in stationary test
  [52:56] 4-byte CRC (not validated)

Any other frame type will appear in the diagnostics report.

Call reset_diagnostics() at recording start to clear session counts.
Call build_frame_type_report() / build_frame_hex_report() at stop to log.
"""
from __future__ import annotations

import logging
import math
import queue
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("snowlink.imu_raw")

_FDFC_HEADER          = b"\xFD\xFC"
_FDFC_IMU_FRAME_LEN   = 64   # type 0x40
_FDFC_EULER_FRAME_LEN = 56   # type 0x41

# ── Type-0x40 offsets (within 56-byte payload) ────────────────────────────────
# Confirmed from hardware hex dump (test2_2026-05-31):
#   [0:4]   uint32 counter
#   [4:16]  float32*3 gyro_x, gyro_y, gyro_z  (rad/s)
#   [16:28] float32*3 accel_x, accel_y, accel_z (m/s²)  |a| ≈ 9.87 stationary ✓
#   [28:40] float32*3 Euler roll, pitch, yaw (degrees)
#   [40:44] float32   temperature (°C)
#   [44:48] float32   pressure (Pa)
#   [48:56] reserved
_IMU_GYRO_OFFSET  = 4
_IMU_ACCEL_OFFSET = 16

# ── Type-0x41 confirmed offset ────────────────────────────────────────────────
_EULER_YPR_OFFSET = 24          # float32*3: yaw, pitch, roll (rad)

# ── Type-0x41 candidate raw-IMU offsets ──────────────────────────────────────
# Pairs of (gyro_offset, accel_offset) within the 48-byte Euler payload.
# The two 12-byte windows flanking the confirmed Euler triplet at [24:36].
_EULER_CANDIDATES: List[Tuple[int, int]] = [
    (8,  36),   # gyro=[8:20],  accel=[36:48]
    (36, 8),    # gyro=[36:48], accel=[8:20]
]

# Sanity / validation bounds
_MAX_ACCEL_MS2  = 160.0   # ~16 g
_MAX_GYRO_RADS  = 35.0    # ~2000 deg/s
_GRAVITY_MIN    = 5.0     # m/s²  — minimum plausible gravity magnitude
_GRAVITY_MAX    = 20.0    # m/s²  — maximum plausible gravity magnitude

_QUEUE_MAXSIZE = 10_000

# Shared channel between ImuService (producer) and RecordingService (consumer).
imu_raw_queue: queue.Queue["IMURawMeasurement"] = queue.Queue(maxsize=_QUEUE_MAXSIZE)


# ── Diagnostics (module-level, reset at each recording session) ───────────────
# Keys: "type0xTT_len0xLL". Updated from imu_service reader thread.
# GIL makes simple int increments safe without an explicit lock.

_frame_type_counts: Dict[str, int] = {}
_first_frame_bytes: Dict[str, bytes] = {}   # first full frame per type

# Auto-detected layout for raw IMU within 0x41 payload.
# None  = not yet probed
# (-1,-1) = confirmed absent (no gravity-magnitude match found)
# (g,a) = confirmed (gyro_offset, accel_offset)
_euler_raw_offsets: Optional[Tuple[int, int]] = None
_euler_detect_attempts: int = 0
_EULER_DETECT_MAX = 200     # ~20 s at 10 Hz before giving up


def reset_diagnostics() -> None:
    """Reset per-session diagnostics. Call from RecordingService.start()."""
    global _euler_raw_offsets, _euler_detect_attempts
    _frame_type_counts.clear()
    _first_frame_bytes.clear()
    _euler_raw_offsets = None
    _euler_detect_attempts = 0


def build_frame_type_report() -> str:
    """One-line summary of frame type counts for system_status.csv."""
    if not _frame_type_counts:
        return "no_fdfc_frames_observed"
    return " ".join(f"{k}={v}" for k, v in sorted(_frame_type_counts.items()))


def build_frame_hex_report() -> str:
    """Hex dump of first occurrence of each frame type (for layout inspection)."""
    if not _first_frame_bytes:
        return "no_frames"
    parts = []
    for k, raw in sorted(_first_frame_bytes.items()):
        parts.append(f"{k}=[{raw.hex()}]")
    return "  ".join(parts)


# ── Dataclass ──────────────────────────────────────────────────────────────────

@dataclass
class IMURawMeasurement:
    """
    One IMU sample.  accel_*: m/s²  gyro_*: rad/s  roll/pitch/yaw: rad (optional).
    """
    timestamp_monotonic: float
    timestamp_unix: float
    accel_x: float
    accel_y: float
    accel_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float
    roll:  Optional[float] = None
    pitch: Optional[float] = None
    yaw:   Optional[float] = None


# ── Frame type scanner ────────────────────────────────────────────────────────

def scan_and_count_fdfc_frames(buffer: bytes) -> None:
    """
    Walk all FDFC frames in buffer; update _frame_type_counts and
    capture the first occurrence of each unique (type, length) combo.
    For diagnostics only — does not validate CRC.
    """
    cursor = 0
    while cursor < len(buffer):
        start = buffer.find(_FDFC_HEADER, cursor)
        if start < 0 or len(buffer) - start < 4:
            break
        msg_type = buffer[start + 2]
        msg_len  = buffer[start + 3]
        key = f"type0x{msg_type:02X}_len0x{msg_len:02X}"
        _frame_type_counts[key] = _frame_type_counts.get(key, 0) + 1
        frame_end = start + 4 + msg_len + 4
        if key not in _first_frame_bytes:
            _first_frame_bytes[key] = buffer[start : min(frame_end, len(buffer))]
        # Advance: use declared length for known-reasonable sizes; else step 1
        if 4 <= msg_len <= 256:
            cursor = frame_end
        else:
            cursor = start + 1


# ── Main extractor ─────────────────────────────────────────────────────────────

def extract_fdfc_imu_raw(
    buffer: bytes,
    t_mono: float,
    t_unix: float,
) -> Tuple[List[IMURawMeasurement], bytes]:
    """
    Extract raw IMU measurements from FDFC frames.

    Processes type-0x40 frames (dedicated raw IMU) when present, and
    attempts to extract accel/gyro from type-0x41 Euler frames using
    auto-detected offsets.  The cursor advances by the full frame length
    for both 0x40 and 0x41, matching extract_fdfc_yaws() exactly so
    callers can share a single fdfc_buf.

    Returns (measurements, remaining_buffer).
    """
    measurements: List[IMURawMeasurement] = []
    cursor = 0

    while cursor < len(buffer):
        start = buffer.find(_FDFC_HEADER, cursor)
        if start < 0:
            return measurements, b""
        if len(buffer) - start < 4:
            return measurements, buffer[start:]

        message_type = buffer[start + 2]
        message_len  = buffer[start + 3]

        if message_type == 0x40 and message_len == 0x38:
            # Dedicated raw IMU frame
            if len(buffer) - start < _FDFC_IMU_FRAME_LEN:
                return measurements, buffer[start:]
            payload = buffer[start + 4 : start + 4 + message_len]
            m = _parse_0x40_payload(payload, t_mono, t_unix)
            if m is not None:
                measurements.append(m)
            cursor = start + _FDFC_IMU_FRAME_LEN

        elif message_type == 0x41 and message_len == 0x30:
            # Euler frame — also try to extract embedded raw IMU
            if len(buffer) - start < _FDFC_EULER_FRAME_LEN:
                return measurements, buffer[start:]
            payload = buffer[start + 4 : start + 4 + message_len]
            m = _parse_0x41_for_imu(payload, t_mono, t_unix)
            if m is not None:
                measurements.append(m)
            cursor = start + _FDFC_EULER_FRAME_LEN

        else:
            cursor = start + 1

    return measurements, b""


# ── Per-frame parsers ──────────────────────────────────────────────────────────

def _parse_0x40_payload(
    payload: bytes,
    t_mono: float,
    t_unix: float,
) -> Optional[IMURawMeasurement]:
    if len(payload) < _IMU_ACCEL_OFFSET + 12:
        return None
    try:
        gx, gy, gz = struct.unpack_from("<fff", payload, _IMU_GYRO_OFFSET)
        ax, ay, az = struct.unpack_from("<fff", payload, _IMU_ACCEL_OFFSET)
    except struct.error:
        return None
    if not all(math.isfinite(v) for v in (gx, gy, gz, ax, ay, az)):
        return None
    if max(abs(ax), abs(ay), abs(az)) > _MAX_ACCEL_MS2:
        return None
    if max(abs(gx), abs(gy), abs(gz)) > _MAX_GYRO_RADS:
        return None
    return IMURawMeasurement(
        timestamp_monotonic=t_mono, timestamp_unix=t_unix,
        accel_x=ax, accel_y=ay, accel_z=az,
        gyro_x=gx,  gyro_y=gy,  gyro_z=gz,
    )


def _parse_0x41_for_imu(
    payload: bytes,
    t_mono: float,
    t_unix: float,
) -> Optional[IMURawMeasurement]:
    """
    Try to extract raw accel/gyro from within the 0x41 Euler frame payload.

    The 48-byte payload has confirmed Euler angles at [24:36] (yaw, pitch, roll).
    The 12-byte windows at [8:20] and [36:48] are candidates for accel/gyro.
    Auto-detection picks the candidate where |accel| ≈ 9.81 m/s² (gravity).
    """
    global _euler_raw_offsets, _euler_detect_attempts

    if len(payload) < 48:
        return None

    # Already confirmed absent — skip
    if _euler_raw_offsets == (-1, -1):
        return None

    # If 0x40 raw-IMU frames are present, skip 0x41 extraction entirely —
    # 0x40 is the authoritative source and extracting from both would duplicate data.
    if _frame_type_counts.get("type0x40_len0x38", 0) > 0:
        return None

    # Try to detect layout if not yet confirmed
    if _euler_raw_offsets is None:
        if _euler_detect_attempts >= _EULER_DETECT_MAX:
            # Gave up — raw IMU not embedded in 0x41 on this device
            _euler_raw_offsets = (-1, -1)
            log.debug(
                "N100: no raw IMU found in 0x41 Euler frames after %d attempts "
                "(0x40 frames not present either — device may need configuration)",
                _euler_detect_attempts,
            )
            return None
        _euler_detect_attempts += 1
        detected = _detect_euler_imu_layout(payload)
        if detected is None:
            return None   # inconclusive — try next frame
        _euler_raw_offsets = detected
        if detected == (-1, -1):
            return None

    gyro_off, accel_off = _euler_raw_offsets

    try:
        gx, gy, gz = struct.unpack_from("<fff", payload, gyro_off)
        ax, ay, az = struct.unpack_from("<fff", payload, accel_off)
    except struct.error:
        return None

    if not all(math.isfinite(v) for v in (gx, gy, gz, ax, ay, az)):
        return None
    if max(abs(ax), abs(ay), abs(az)) > _MAX_ACCEL_MS2:
        return None
    if max(abs(gx), abs(gy), abs(gz)) > _MAX_GYRO_RADS:
        return None

    # Extract Euler angles to attach alongside raw data
    roll_r = pitch_r = yaw_r = None
    try:
        yaw_r, pitch_r, roll_r = struct.unpack_from("<fff", payload, _EULER_YPR_OFFSET)
        if not all(math.isfinite(v) for v in (yaw_r, pitch_r, roll_r)):
            roll_r = pitch_r = yaw_r = None
    except struct.error:
        pass

    return IMURawMeasurement(
        timestamp_monotonic=t_mono, timestamp_unix=t_unix,
        accel_x=ax, accel_y=ay, accel_z=az,
        gyro_x=gx,  gyro_y=gy,  gyro_z=gz,
        roll=roll_r, pitch=pitch_r, yaw=yaw_r,
    )


def _detect_euler_imu_layout(
    payload: bytes,
) -> Optional[Tuple[int, int]]:
    """
    Probe _EULER_CANDIDATES against a single Euler frame payload.

    Returns:
      (gyro_off, accel_off)  — confident gravity-magnitude match
      (-1, -1)               — nonzero values seen but no gravity match
      None                   — all candidate bytes are near-zero (inconclusive)
    """
    has_nonzero = False

    for gyro_off, accel_off in _EULER_CANDIDATES:
        if len(payload) < max(gyro_off, accel_off) + 12:
            continue
        try:
            ax, ay, az = struct.unpack_from("<fff", payload, accel_off)
        except struct.error:
            continue
        if not all(math.isfinite(v) for v in (ax, ay, az)):
            continue
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        if max(abs(ax), abs(ay), abs(az)) > 0.01:
            has_nonzero = True
        if _GRAVITY_MIN < mag < _GRAVITY_MAX:
            log.info(
                "N100: raw IMU detected in 0x41 frame — "
                "gyro_off=%d accel_off=%d |accel|=%.3f m/s²",
                gyro_off, accel_off, mag,
            )
            return (gyro_off, accel_off)

    if not has_nonzero:
        # All bytes near zero — device may still be initialising; retry
        return None
    return (-1, -1)
