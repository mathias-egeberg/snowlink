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
        )

    def on_leave(self):
        ds = DataService.get()
        ds.unbind(
            heat_roof_on=self._sync_toggles,
            heat_gutter_on=self._sync_toggles,
            pump_on=self._sync_toggles,
            sensor_light_on=self._sync_toggles,
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _sync_toggles(self, *_args):
        ds = DataService.get()
        self.ids.toggle_roof.is_on         = ds.heat_roof_on
        self.ids.toggle_gutter.is_on       = ds.heat_gutter_on
        self.ids.toggle_pump.is_on         = ds.pump_on
        self.ids.toggle_sensor_light.is_on = ds.sensor_light_on
