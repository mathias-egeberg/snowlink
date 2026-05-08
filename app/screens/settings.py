import datetime

from kivy.clock import Clock
from kivy.uix.screenmanager import Screen
from kivy.properties import NumericProperty, StringProperty

from app.theme import APP_VERSION
from app.services.data_service import DataService


class SettingsScreen(Screen):
    """Settings screen: thresholds, system mode, and device info."""

    activation_threshold = NumericProperty(-1.0)
    brightness           = NumericProperty(0.8)   # 0.0–1.0
    system_mode          = StringProperty("Auto") # "Auto" | "Manual"
    app_version          = StringProperty(APP_VERSION)

    def on_enter(self):
        Clock.schedule_once(self._init, 0)

    def _init(self, _dt):
        ds = DataService.get()
        ds.bind(
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            imu_ok=self._on_connections,
        )
        self._on_connections()
        self._clock_event = Clock.schedule_interval(self._tick_clock, 1)
        self._tick_clock(0)

    def on_leave(self):
        if hasattr(self, '_clock_event'):
            self._clock_event.cancel()
        DataService.get().unbind(
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            imu_ok=self._on_connections,
        )

    def _tick_clock(self, _dt):
        now = datetime.datetime.now()
        self.ids.lbl_time_set.text = now.strftime("%H:%M:%S")
        self.ids.lbl_date_set.text = now.strftime("%A, %d %b %Y")

    def _on_connections(self, *_):
        ds = DataService.get()
        self.ids.ind_5g.is_ok  = ds.cellular_ok
        self.ids.ind_gps.is_ok = ds.gps_ok
        self.ids.ind_imu.is_ok = ds.imu_ok

    def increment_threshold(self) -> None:
        self.activation_threshold = round(
            min(5.0, self.activation_threshold + 0.5), 1
        )

    def decrement_threshold(self) -> None:
        self.activation_threshold = round(
            max(-10.0, self.activation_threshold - 0.5), 1
        )

    def toggle_mode(self) -> None:
        self.system_mode = "Manual" if self.system_mode == "Auto" else "Auto"
