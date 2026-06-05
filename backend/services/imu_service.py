"""
IMU service – WheelTech N100 via CP2102 USB-serial adapter.

Two detection paths run in parallel:
  - Reader thread (fast): calls set_imu_connection(False) the moment
    ser.read() raises or yaw data goes stale, typically within 2-3 s.
  - Main loop (3 s guarantee): scans USB via imu_selftest every
    SELFTEST_INTERVAL seconds regardless of reader state.  If the device
    has gone from the USB bus it force-closes the port and marks
    disconnected even if the reader hasn't noticed yet.

Raw IMU data (accel + gyro at full device rate) is pushed into
imu_raw_queue (see imu_raw.py) for consumption by RecordingService.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from backend.services.imu_heading import (
    extract_ascii_yaws,
    extract_fdfc_yaws,
    extract_wit_yaws,
    parse_fdfc_euler_rpy,
)
from backend.services.imu_raw import (
    IMURawMeasurement,
    extract_fdfc_imu_raw,
    imu_raw_queue,
    scan_and_count_fdfc_frames,
)
from backend.services.selftest import imu_selftest

log = logging.getLogger("snowlink.imu")

SELFTEST_INTERVAL = 3.0

_BAUDRATES     = (921600, 115200, 9600)
_READ_SIZE     = 1024
_STALE_SECONDS = 2.5
_BAUD_TIMEOUT  = 4.0


class ImuService:

    def __init__(self, data_service) -> None:
        self._ds              = data_service
        self._lock            = threading.Lock()
        self._serial          = None
        self._active          = True
        self._baud_index      = 0
        self._last_good_baud: Optional[int] = None
        threading.Thread(target=self._loop, daemon=True, name="imu-loop").start()

    # ── Main loop ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._active:
            self._tick()
            time.sleep(SELFTEST_INTERVAL)

    def _tick(self) -> None:
        try:
            with self._lock:
                is_open = self._serial is not None and self._serial.is_open

            if is_open:
                return

            port = imu_selftest.find_port()
            if port is None:
                self._ds.set_imu_connection(False)
                return

            self._connect(port)
        except Exception:
            log.exception("IMU tick failed")
            self._ds.set_imu_connection(False)

    # ── Serial connection ─────────────────────────────────────────────────

    def _connect(self, port: str) -> None:
        baudrate = self._last_good_baud or _BAUDRATES[self._baud_index]
        if self._last_good_baud is None:
            self._baud_index = (self._baud_index + 1) % len(_BAUDRATES)

        ser = None
        try:
            import serial
            ser = serial.Serial(port, baudrate=baudrate, timeout=0.25)
            with self._lock:
                self._serial = ser
            self._ds.set_imu_connection(True, port=port, baudrate=baudrate)
            threading.Thread(
                target=self._reader, args=(ser, port, baudrate),
                daemon=True, name="imu-reader",
            ).start()
            log.debug("IMU connected on %s @ %d", port, baudrate)
        except Exception:
            log.exception("IMU connect failed on %s", port)
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            self._ds.set_imu_connection(False)

    # ── Reader thread ─────────────────────────────────────────────────────

    def _reader(self, ser, port: str = "", baudrate: int = 0) -> None:
        from backend.services.config_service import ConfigService
        cfg = ConfigService.get_imu()
        raw_log_enabled: bool = cfg.get("raw_log_enabled", True)

        wit_buf   = b""
        fdfc_buf  = b""
        text_buf  = ""

        # Latest Euler angles (rad) from type-0x41 frames; attached to raw measurements.
        latest_rpy: Optional[tuple[float, float, float]] = None  # (roll, pitch, yaw)

        connected_at   = time.monotonic()
        last_yaw_at    = 0.0
        last_raw_push  = 0.0   # throttle for set_imu_raw() state updates (10 Hz)
        yaw_stale      = True
        dropped        = 0

        try:
            while self._active and ser.is_open:
                try:
                    chunk = ser.read(_READ_SIZE)
                except Exception:
                    break

                if not chunk:
                    if not yaw_stale and time.monotonic() - last_yaw_at > _STALE_SECONDS:
                        self._ds.clear_imu_yaw()
                        yaw_stale = True
                    if yaw_stale and time.monotonic() - connected_at > _BAUD_TIMEOUT:
                        self._last_good_baud = None
                        break
                    continue

                t_mono = time.monotonic()
                t_unix = time.time()
                latest_yaw = None

                # ── WIT binary ──────────────────────────────────────────
                yaws, wit_buf = extract_wit_yaws(wit_buf + chunk)
                if yaws:
                    latest_yaw = yaws[-1]

                # ── FDFC binary ─────────────────────────────────────────
                combined_fdfc = fdfc_buf + chunk

                # Count every frame type seen for diagnostics.
                scan_and_count_fdfc_frames(combined_fdfc)

                # Heading (type 0x41): existing path unchanged
                yaws, fdfc_buf = extract_fdfc_yaws(combined_fdfc)
                if yaws:
                    latest_yaw = yaws[-1]

                # Raw IMU (type 0x40): always extract for live state + recording.
                raw_measurements, _ = extract_fdfc_imu_raw(combined_fdfc, t_mono, t_unix)
                if raw_measurements:
                    if latest_rpy is not None:
                        for m in raw_measurements:
                            m.roll, m.pitch, m.yaw = latest_rpy
                    # Each ser.read() call yields a batch of frames that all share
                    # the same t_mono.  Interpolate per-frame timestamps backwards
                    # from t_mono so the downsampling check in _raw_imu_loop sees
                    # distinct timestamps spaced at the configured period.
                    n = len(raw_measurements)
                    if n > 1:
                        period = 1.0 / float(cfg.get("raw_log_rate_hz", 100))
                        for i, m in enumerate(raw_measurements):
                            offset = (n - 1 - i) * period
                            m.timestamp_monotonic = t_mono - offset
                            m.timestamp_unix      = t_unix - offset
                    # Push latest sample to state at 10 Hz for live display.
                    if t_mono - last_raw_push >= 0.1:
                        self._ds.set_imu_raw(raw_measurements[-1])
                        last_raw_push = t_mono
                    # Enqueue for high-rate CSV recording.
                    if raw_log_enabled:
                        for m in raw_measurements:
                            try:
                                imu_raw_queue.put_nowait(m)
                            except Exception:
                                dropped += 1
                                if dropped % 100 == 1:
                                    log.warning(
                                        "imu_raw_queue full, dropping samples (total dropped: %d)",
                                        dropped,
                                    )

                # Update latest Euler angles for attaching to next raw batch
                for frame_start in _iter_fdfc_euler_frames(combined_fdfc):
                    rpy = parse_fdfc_euler_rpy(combined_fdfc[frame_start + 4:])
                    if rpy is not None:
                        latest_rpy = rpy

                # ── ASCII ───────────────────────────────────────────────
                yaws, text_buf = extract_ascii_yaws(
                    text_buf, chunk.decode("ascii", errors="ignore")
                )
                if yaws:
                    latest_yaw = yaws[-1]

                if latest_yaw is not None:
                    if self._last_good_baud != ser.baudrate:
                        self._last_good_baud = ser.baudrate
                    last_yaw_at = t_mono
                    yaw_stale   = False
                    self._ds.set_imu_yaw(latest_yaw)
                elif yaw_stale and time.monotonic() - connected_at > _BAUD_TIMEOUT:
                    self._last_good_baud = None
                    break

        finally:
            try:
                ser.close()
            except Exception:
                pass
            with self._lock:
                if self._serial is ser:
                    self._serial = None
            if self._active:
                self._ds.set_imu_connection(False)

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()


# ── Helper ────────────────────────────────────────────────────────────────

_FDFC_HEADER        = b"\xFD\xFC"
_FDFC_EULER_FRAME_LEN = 56


def _iter_fdfc_euler_frames(buffer: bytes):
    """Yield start indices of complete type-0x41 Euler frames in buffer."""
    cursor = 0
    while cursor < len(buffer):
        start = buffer.find(_FDFC_HEADER, cursor)
        if start < 0 or len(buffer) - start < 4:
            break
        if buffer[start + 2] == 0x41 and buffer[start + 3] == 0x30:
            if len(buffer) - start >= _FDFC_EULER_FRAME_LEN:
                yield start
                cursor = start + _FDFC_EULER_FRAME_LEN
                continue
        cursor = start + 1
