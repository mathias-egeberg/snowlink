"""Pure helpers for IMU yaw parsing and calibration."""
from __future__ import annotations

import json
import math
import re
import struct
from typing import Optional

_ASCII_YAW_RE = re.compile(
    r"\b(?:yaw|heading)\b\s*[:=, ]+\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_MAX_TEXT_BUFFER = 4096
_FDFC_HEADER = b"\xFD\xFC"
_FDFC_IMU_FRAME_LEN = 64
_FDFC_EULER_FRAME_LEN = 56
_FDFC_EULER_PAYLOAD_OFFSET = 24


def normalize_yaw(degrees: float) -> float:
    """Return a yaw angle normalized to [0, 360)."""
    return degrees % 360.0


def apply_yaw_calibration(yaw_deg: float, zero_deg: float) -> float:
    """Return yaw relative to the calibrated boot/map direction."""
    return normalize_yaw(yaw_deg - zero_deg)


def shortest_yaw_delta(first_deg: float, second_deg: float) -> float:
    """Return the smallest absolute angular delta between two headings."""
    return abs((first_deg - second_deg + 180.0) % 360.0 - 180.0)


def parse_wit_angle_packet(packet: bytes) -> Optional[float]:
    """Parse yaw from a WIT-style 0x55 0x53 angle packet."""
    if len(packet) != 11 or packet[0] != 0x55 or packet[1] != 0x53:
        return None
    checksum = sum(packet[:10]) & 0xFF
    if checksum != packet[10]:
        return None
    yaw_raw = int.from_bytes(packet[6:8], byteorder="little", signed=True)
    return normalize_yaw(yaw_raw / 32768.0 * 180.0)


def extract_wit_yaws(buffer: bytes) -> tuple[tuple[float, ...], bytes]:
    """Extract all complete WIT yaw packets and return remaining bytes."""
    yaws: list[float] = []
    cursor = 0
    while cursor < len(buffer):
        start = buffer.find(b"\x55", cursor)
        if start < 0:
            return tuple(yaws), b""
        if len(buffer) - start < 11:
            return tuple(yaws), buffer[start:]

        packet = buffer[start:start + 11]
        yaw = parse_wit_angle_packet(packet)
        if yaw is None:
            cursor = start + 1
            continue

        yaws.append(yaw)
        cursor = start + 11

    return tuple(yaws), b""


def parse_ascii_yaw(line: str) -> Optional[float]:
    """Parse a yaw/heading value from a labelled ASCII telemetry line."""
    match = _ASCII_YAW_RE.search(line)
    if match:
        return normalize_yaw(float(match.group(1)))

    normalized = line.strip().lower()
    if normalized.startswith("{") and normalized.endswith("}"):
        return _parse_json_yaw(line)
    if normalized.startswith("$"):
        nmea_yaw = _parse_nmea_yaw(line)
        if nmea_yaw is not None:
            return nmea_yaw

    values = [float(value) for value in _NUMBER_RE.findall(line)]
    if not values:
        return None
    if normalized.startswith("$vnymr"):
        return normalize_yaw(values[0])
    csv_yaw = _parse_csv_yaw(line)
    if csv_yaw is not None:
        return csv_yaw
    if "angle" in normalized and len(values) >= 3:
        return normalize_yaw(values[2])
    return None


def extract_fdfc_yaws(buffer: bytes) -> tuple[tuple[float, ...], bytes]:
    """Extract yaw from WheelTech/N100 FD FC Euler frames."""
    yaws: list[float] = []
    cursor = 0
    while cursor < len(buffer):
        start = buffer.find(_FDFC_HEADER, cursor)
        if start < 0:
            return tuple(yaws), b""
        if len(buffer) - start < 4:
            return tuple(yaws), buffer[start:]

        message_type = buffer[start + 2]
        message_len = buffer[start + 3]
        frame_len = _fdfc_frame_len(message_type, message_len)
        if frame_len is None:
            cursor = start + 1
            continue
        if len(buffer) - start < frame_len:
            return tuple(yaws), buffer[start:]

        frame = buffer[start:start + frame_len]
        if message_type == 0x41:
            yaw = _parse_fdfc_euler_yaw(frame[4:])
            if yaw is not None:
                yaws.append(yaw)
        cursor = start + frame_len

    return tuple(yaws), b""


def extract_ascii_yaws(
    pending_text: str,
    chunk_text: str,
) -> tuple[tuple[float, ...], str]:
    """Extract yaw values from newline-delimited ASCII telemetry."""
    combined = (pending_text + chunk_text)[-_MAX_TEXT_BUFFER:]
    lines = combined.splitlines(keepends=True)
    if not lines:
        return (), ""

    has_pending = not lines[-1].endswith(("\n", "\r"))
    pending = lines[-1] if has_pending else ""
    complete_lines = lines[:-1] if has_pending else lines

    yaws = tuple(
        yaw
        for yaw in (parse_ascii_yaw(line.strip()) for line in complete_lines)
        if yaw is not None
    )
    return yaws, pending


def _parse_json_yaw(line: str) -> Optional[float]:
    try:
        value = json.loads(line).get("yaw")
        return normalize_yaw(float(value))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _parse_csv_yaw(line: str) -> Optional[float]:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 12:
        return None
    try:
        values = [float(value) for value in parts[:12]]
    except ValueError:
        return None
    return normalize_yaw(values[11])


def _parse_nmea_yaw(line: str) -> Optional[float]:
    payload = line.strip().split("*", 1)[0]
    fields = payload.split(",")
    if len(fields) < 2:
        return None

    message = fields[0].upper()
    heading_index = None
    if message == "$PASHR" and len(fields) >= 6:
        heading_index = 2
    elif message in ("$GPCHC", "$GNCHC", "$GTIMU", "$PIMU") and len(fields) >= 12:
        heading_index = 3
    elif message == "$VNYMR" and len(fields) >= 2:
        heading_index = 1

    if heading_index is None:
        return None
    try:
        return normalize_yaw(float(fields[heading_index]))
    except (TypeError, ValueError):
        return None


def _fdfc_frame_len(message_type: int, message_len: int) -> Optional[int]:
    if message_type == 0x40 and message_len == 0x38:
        return _FDFC_IMU_FRAME_LEN
    if message_type == 0x41 and message_len == 0x30:
        return _FDFC_EULER_FRAME_LEN
    return None


def _parse_fdfc_euler_yaw(payload: bytes) -> Optional[float]:
    if len(payload) < _FDFC_EULER_PAYLOAD_OFFSET + 12:
        return None
    try:
        yaw_rad, _pitch_rad, _roll_rad = struct.unpack_from(
            "<fff",
            payload,
            _FDFC_EULER_PAYLOAD_OFFSET,
        )
    except struct.error:
        return None

    yaw_deg = math.degrees(yaw_rad)
    if not math.isfinite(yaw_deg) or abs(yaw_deg) > 720.0:
        return None
    return normalize_yaw(yaw_deg)