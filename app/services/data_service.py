"""
DataService – singleton that holds live sensor readings and device states.

On a real Raspberry Pi, replace the _fluctuate() method and the toggle
handlers with actual GPIO / sensor I/O calls (e.g., RPi.GPIO, smbus2).
"""
import random

from kivy.clock import Clock
from kivy.event import EventDispatcher
from kivy.properties import (
    BooleanProperty, NumericProperty, StringProperty
)


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
        # Simulate live sensor fluctuation every 5 seconds.
        # Replace with real hardware polling on the Pi.
        Clock.schedule_interval(self._fluctuate, 5)

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
