import datetime

from kivy.clock import Clock
from kivy.uix.screenmanager import Screen

from app.services.data_service import DataService


_STATUS_MAP = {
    "active":  ([0.024, 0.839, 0.627, 1], "ACTIVE"),
    "standby": ([1.000, 0.718, 0.012, 1], "STANDBY"),
    "idle":    ([0.300, 0.300, 0.350, 1], "IDLE"),
    "error":   ([0.937, 0.137, 0.235, 1], "ERROR"),
}


class DashboardScreen(Screen):
    """Main overview screen: sensor cards + system status."""

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def on_enter(self):
        # Defer until the next frame so self.ids is fully populated.
        Clock.schedule_once(self._init, 0)

    def _init(self, _dt):
        ds = DataService.get()
        self._refresh_all(ds)
        ds.bind(
            temperature_outside=self._on_temp,
            snow_depth_cm=self._on_snow,
            wind_speed_ms=self._on_wind,
            power_watts=self._on_power,
            active_devices=self._on_devices,
            system_status=self._on_status,
            last_event=self._on_event,
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            gps_float_rtk=self._on_connections,
            gps_status_text=self._on_connections,
            imu_ok=self._on_connections,
        )
        Clock.schedule_interval(self._tick_clock, 1)

    def on_leave(self):
        DataService.get().unbind(
            temperature_outside=self._on_temp,
            snow_depth_cm=self._on_snow,
            wind_speed_ms=self._on_wind,
            power_watts=self._on_power,
            active_devices=self._on_devices,
            system_status=self._on_status,
            last_event=self._on_event,
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            gps_float_rtk=self._on_connections,
            gps_status_text=self._on_connections,
            imu_ok=self._on_connections,
        )
        Clock.unschedule(self._tick_clock)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _refresh_all(self, ds):
        ids = self.ids
        ids.card_temp.value    = f"{ds.temperature_outside:+.1f}"
        ids.card_snow.value    = f"{ds.snow_depth_cm:.1f}"
        ids.card_wind.value    = f"{ds.wind_speed_ms:.1f}"
        ids.card_power.value   = str(int(ds.power_watts))
        ids.card_devices.value = str(ds.active_devices)
        self._on_status(ds, ds.system_status)
        ids.lbl_event.text = ds.last_event
        self._on_connections()
        self._tick_clock(0)

    def _tick_clock(self, _dt):
        now = datetime.datetime.now()
        self.ids.lbl_time.text = now.strftime("%H:%M:%S")
        self.ids.lbl_date.text = now.strftime("%A, %d %b %Y")

    # ── DataService bindings ──────────────────────────────────────────────

    def _on_temp(self, ds, v):    self.ids.card_temp.value    = f"{v:+.1f}"
    def _on_snow(self, ds, v):    self.ids.card_snow.value    = f"{v:.1f}"
    def _on_wind(self, ds, v):    self.ids.card_wind.value    = f"{v:.1f}"
    def _on_power(self, ds, v):   self.ids.card_power.value   = str(int(v))
    def _on_devices(self, ds, v): self.ids.card_devices.value = str(v)
    def _on_event(self, ds, v):   self.ids.lbl_event.text     = v

    def _on_status(self, ds, v):
        color, label = _STATUS_MAP.get(v, ([0.3, 0.3, 0.35, 1], v.upper()))
        self.ids.status_dot.color      = color
        self.ids.lbl_status_text.text  = label
        self.ids.lbl_status_text.color = color

    def _on_connections(self, *_):
        ds = DataService.get()
        self.ids.ind_5g.is_ok       = ds.cellular_ok
        self.ids.ind_gps.is_ok      = ds.gps_ok
        self.ids.ind_gps.is_warning = ds.gps_float_rtk
        self.ids.ind_imu.is_ok      = ds.imu_ok
        lbl = self.ids.lbl_gps_status
        lbl.text = ds.gps_status_text
        if ds.gps_ok:
            lbl.color = (0.000, 0.824, 0.549, 1.0)
        elif ds.gps_float_rtk:
            lbl.color = (1.000, 0.502, 0.000, 1.0)
        else:
            lbl.color = (0.937, 0.137, 0.235, 1.0)
