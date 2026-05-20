"""
Central application state for SnowLink web backend.

AppState is a dataclass holding all live sensor readings, device states,
and connection status.  StateManager wraps it with a threading lock so
any service thread can update it safely, and exposes get_snapshot() for
REST / WebSocket broadcast.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class AppState:
    # ── Sensor readings ───────────────────────────────────────────────────
    temperature_outside: float = -3.5
    temperature_roof: float = -1.2
    snow_depth_cm: float = 14.0
    humidity_pct: float = 82.0
    wind_speed_ms: float = 4.2
    power_watts: float = 0.0

    # ── System status ─────────────────────────────────────────────────────
    system_status: str = "idle"       # idle | standby | active | error
    last_event: str = "System started"
    active_devices: int = 0

    # ── Device states ─────────────────────────────────────────────────────
    heat_roof_on: bool = False
    heat_gutter_on: bool = False
    pump_on: bool = False
    sensor_light_on: bool = True

    # ── GPS / RTK ─────────────────────────────────────────────────────────
    cellular_ok: bool = False
    cellular_detected: bool = False
    cellular_internet_ok: bool = False
    cellular_iface: str = ""
    cellular_ipv4: str = ""
    cellular_status_text: str = "No modem"
    cellular_is_huawei: bool = False
    cellular_product: str = ""
    cellular_bytes_received: int = 0
    cellular_bytes_sent: int = 0
    cellular_bytes_total: int = 0
    cellular_usage_reset_at_ms: int = 0
    cellular_usage_updated_at_ms: int = 0
    cellular_last_checked_at_ms: int = 0
    cellular_signal_quality_pct: int = 0
    cellular_signal_quality_text: str = "No signal"
    cellular_speed_test_running: bool = False
    cellular_speed_test_status: str = "Never run"
    cellular_speed_test_download_mbps: float = 0.0
    cellular_speed_test_upload_mbps: float = 0.0
    cellular_speed_test_latency_ms: float = 0.0
    cellular_speed_test_last_run_at_ms: int = 0
    cellular_speed_test_error: str = ""
    gps_ok: bool = False
    gps_float_rtk: bool = False
    gps_status_text: str = "No Fix"
    gps_lat: float = 60.0137563
    gps_lon: float = 11.0196226
    gps_altitude_m: float = 0.0
    gps_fix_quality: int = 0
    gps_satellites: int = 0
    gps_hdop: float = 0.0

    # ── IMU ───────────────────────────────────────────────────────────────
    imu_ok: bool = False
    imu_yaw_deg: float = 0.0
    imu_yaw_valid: bool = False
    imu_heading_deg: float = 0.0
    imu_heading_calibrated: bool = False

    # ── NTRIP ─────────────────────────────────────────────────────────────
    ntrip_ok: bool = False
    ntrip_status_text: str = "Disabled"

    # ── Map settings (mirrored from settings.json for WS broadcast) ───────
    marker_style: str = "snowcat"       # 'snowcat' | 'dot'
    imu_heading_enabled: bool = False

    # ── LiDAR placeholder ─────────────────────────────────────────────────
    lidar_available: bool = False
    lidar_point_count: int = 0


_DEVICE_LABELS = {
    "heat_roof_on": "Roof heater",
    "heat_gutter_on": "Gutter heater",
    "pump_on": "Water pump",
    "sensor_light_on": "Sensor light",
}
VALID_DEVICE_KEYS = frozenset(_DEVICE_LABELS.keys())


class StateManager:
    """Thread-safe wrapper around AppState."""

    def __init__(self) -> None:
        self._state = AppState()
        self._lock = threading.Lock()

    # ── Read ──────────────────────────────────────────────────────────────

    def get_snapshot(self) -> dict:
        with self._lock:
            snapshot = asdict(self._state)
            snapshot["snapshot_generated_at_ms"] = int(time.time() * 1000)
        return snapshot

    # ── Write ─────────────────────────────────────────────────────────────

    def update(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
            self._recompute_derived()

    def toggle_device(self, device_key: str) -> bool:
        if device_key not in VALID_DEVICE_KEYS:
            return False
        with self._lock:
            current = getattr(self._state, device_key)
            setattr(self._state, device_key, not current)
            label = "ON" if not current else "OFF"
            self._state.last_event = f"{_DEVICE_LABELS[device_key]} turned {label}"
            self._recompute_derived()
        return True

    # ── Internal ──────────────────────────────────────────────────────────

    def _recompute_derived(self) -> None:
        s = self._state
        active = sum([s.heat_roof_on, s.heat_gutter_on, s.pump_on, s.sensor_light_on])
        s.active_devices = active

        watts = 0.0
        if s.heat_roof_on:    watts += 1200
        if s.heat_gutter_on:  watts += 800
        if s.pump_on:         watts += 250
        if s.sensor_light_on: watts += 15
        s.power_watts = watts

        if active == 0:
            s.system_status = "idle"
        elif any([s.heat_roof_on, s.heat_gutter_on, s.pump_on]):
            s.system_status = "active"
        else:
            s.system_status = "standby"


# Module-level singleton — imported by services and API routes.
state_manager = StateManager()
