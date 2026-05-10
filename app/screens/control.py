import datetime

from kivy.clock import Clock
from kivy.uix.screenmanager import Screen

from app.services.data_service import DataService


class ControlScreen(Screen):
    """Device control screen: on/off toggles for heating elements and pump."""

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def on_enter(self):
        Clock.schedule_once(self._init, 0)

    def _init(self, _dt):
        ds = DataService.get()
        self._sync_toggles(ds)
        ds.bind(
            heat_roof_on=self._sync_toggles,
            heat_gutter_on=self._sync_toggles,
            pump_on=self._sync_toggles,
            sensor_light_on=self._sync_toggles,
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            gps_float_rtk=self._on_connections,
            gps_status_text=self._on_connections,
            imu_ok=self._on_connections,
        )
        self._on_connections()
        self._clock_event = Clock.schedule_interval(self._tick_clock, 1)
        self._tick_clock(0)

    def on_leave(self):
        if hasattr(self, '_clock_event'):
            self._clock_event.cancel()
        ds = DataService.get()
        ds.unbind(
            heat_roof_on=self._sync_toggles,
            heat_gutter_on=self._sync_toggles,
            pump_on=self._sync_toggles,
            sensor_light_on=self._sync_toggles,
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            gps_float_rtk=self._on_connections,
            gps_status_text=self._on_connections,
            imu_ok=self._on_connections,
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _tick_clock(self, _dt):
        now = datetime.datetime.now()
        self.ids.lbl_time_ctrl.text = now.strftime("%H:%M:%S")
        self.ids.lbl_date_ctrl.text = now.strftime("%A, %d %b %Y")

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

    def _sync_toggles(self, *_args):
        ds = DataService.get()
        self.ids.toggle_roof.is_on         = ds.heat_roof_on
        self.ids.toggle_gutter.is_on       = ds.heat_gutter_on
        self.ids.toggle_pump.is_on         = ds.pump_on
        self.ids.toggle_sensor_light.is_on = ds.sensor_light_on
