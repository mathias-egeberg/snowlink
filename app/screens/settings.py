from kivy.uix.screenmanager import Screen
from kivy.properties import NumericProperty, StringProperty

from app.theme import APP_VERSION


class SettingsScreen(Screen):
    """Settings screen: thresholds, system mode, and device info."""

    activation_threshold = NumericProperty(-1.0)
    brightness           = NumericProperty(0.8)   # 0.0–1.0
    system_mode          = StringProperty("Auto") # "Auto" | "Manual"
    app_version          = StringProperty(APP_VERSION)

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
