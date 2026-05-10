"""
DataService – singleton that holds live sensor readings and device states.

On a real Raspberry Pi, replace the _fluctuate() method and the toggle
handlers with actual GPIO / sensor I/O calls (e.g., RPi.GPIO, smbus2).
"""
import random
from typing import Optional

from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import (
    BooleanProperty, NumericProperty, StringProperty
)

from app.services.imu_heading import apply_yaw_calibration, normalize_yaw
from app.services.settings_service import SettingsService


class DataService(EventDispatcher):
    """Central data model. Bind to any property to receive live updates."""

    # ── Sensor readings ───────────────────────────────────────────────────
    temperature_outside = NumericProperty(-3.5)
    temperature_roof    = NumericProperty(-1.2)
    snow_depth_cm       = NumericProperty(14.0)
    humidity_pct        = NumericProperty(82.0)
    wind_speed_ms       = NumericProperty(4.2)
    power_watts         = NumericProperty(0.0)

    # ── System status ─────────────────────────────────────────────────────
    system_status  = StringProperty("idle")   # idle | standby | active | error
    last_event     = StringProperty("System started")
    active_devices = NumericProperty(0)

    # ── Device states ─────────────────────────────────────────────────────
    heat_roof_on       = BooleanProperty(False)
    heat_gutter_on     = BooleanProperty(False)
    pump_on            = BooleanProperty(False)
    sensor_light_on    = BooleanProperty(True)

    # ── Connection / peripheral status ───────────────────────────────────
    cellular_ok      = BooleanProperty(False)
    gps_ok           = BooleanProperty(False)   # True = RTK Fixed
    gps_float_rtk    = BooleanProperty(False)   # True = any fix but not RTK Fixed (orange)
    gps_status_text  = StringProperty("No Fix")
    imu_ok           = BooleanProperty(False)
    imu_yaw_deg      = NumericProperty(0.0)
    imu_yaw_valid    = BooleanProperty(False)
    imu_heading_deg  = NumericProperty(0.0)
    imu_heading_calibrated = BooleanProperty(False)

    _instance = None

    @classmethod
    def get(cls) -> "DataService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(
            heat_roof_on=self._update_derived,
            heat_gutter_on=self._update_derived,
            pump_on=self._update_derived,
            sensor_light_on=self._update_derived,
        )
        self._update_derived()
        self.refresh_imu_heading_calibration()
        # Simulate live sensor fluctuation every 5 seconds.
        # Replace with real hardware polling on the Pi.
        Clock.schedule_interval(self._fluctuate, 5)
        # USB IMU detection — runs in background, updates self.imu_ok.
        from app.services.imu_service import ImuService
        self._imu_service = ImuService(self)
        # USB GPS — reads NMEA from ZED-F9P, updates gps_ok / gps_float_rtk / gps_status_text.
        from app.services.gps_service import GpsService
        self._gps_service = GpsService(self)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _fluctuate(self, dt):
        """Simulate small sensor value changes (demo only)."""
        self.temperature_outside = round(
            self.temperature_outside + random.uniform(-0.3, 0.3), 1
        )
        self.temperature_roof = round(
            self.temperature_roof + random.uniform(-0.2, 0.2), 1
        )
        self.snow_depth_cm = round(
            max(0.0, self.snow_depth_cm + random.uniform(-0.5, 0.5)), 1
        )
        self.wind_speed_ms = round(
            max(0.0, self.wind_speed_ms + random.uniform(-0.5, 0.5)), 1
        )

    def _update_derived(self, *_args):
        """Recalculate power draw, active device count, and system status."""
        active = int(self.heat_roof_on) + int(self.heat_gutter_on) + \
                 int(self.pump_on) + int(self.sensor_light_on)
        self.active_devices = active

        watts = 0
        if self.heat_roof_on:    watts += 1200
        if self.heat_gutter_on:  watts += 800
        if self.pump_on:         watts += 250
        if self.sensor_light_on: watts += 15
        self.power_watts = float(watts)

        if active == 0:
            self.system_status = "idle"
        elif any([self.heat_roof_on, self.heat_gutter_on, self.pump_on]):
            self.system_status = "active"
        else:
            self.system_status = "standby"

    # ── Public API ────────────────────────────────────────────────────────

    def toggle_device(self, device_key: str) -> None:
        """Toggle a device on/off and log the event."""
        labels = {
            "heat_roof_on":    "Roof heater",
            "heat_gutter_on":  "Gutter heater",
            "pump_on":         "Water pump",
            "sensor_light_on": "Sensor light",
        }
        current = getattr(self, device_key)
        setattr(self, device_key, not current)
        state = "ON" if not current else "OFF"
        self.last_event = f"{labels.get(device_key, device_key)} turned {state}"

    def set_imu_yaw(self, yaw_deg: float) -> None:
        """Update raw IMU yaw and derived calibrated heading."""
        self.imu_yaw_deg = normalize_yaw(yaw_deg)
        self.imu_yaw_valid = True
        self._update_imu_heading()

    def clear_imu_yaw(self) -> None:
        """Mark IMU yaw as unavailable without clearing saved calibration."""
        self.imu_yaw_valid = False

    def refresh_imu_heading_calibration(self) -> None:
        """Reload persisted IMU calibration state and recalculate heading."""
        self._update_imu_heading()

    def calibrate_imu_heading_to_boot(self) -> bool:
        """Use the current IMU yaw as the boot/map-forward direction."""
        if not self.imu_yaw_valid:
            return False

        settings = SettingsService.load()
        settings['map']['imu_yaw_zero_deg'] = round(self.imu_yaw_deg, 4)
        SettingsService.save()
        self._update_imu_heading()
        self.last_event = "IMU heading calibrated"
        return True

    def _get_imu_yaw_zero(self) -> Optional[float]:
        zero = SettingsService.load()['map'].get('imu_yaw_zero_deg')
        if zero is None:
            return None
        try:
            return normalize_yaw(float(zero))
        except (TypeError, ValueError):
            return None

    def _update_imu_heading(self) -> None:
        zero = self._get_imu_yaw_zero()
        self.imu_heading_calibrated = zero is not None
        if zero is None:
            self.imu_heading_deg = normalize_yaw(self.imu_yaw_deg)
            return
        self.imu_heading_deg = apply_yaw_calibration(self.imu_yaw_deg, zero)
