from kivy.uix.boxlayout import BoxLayout
from kivy.properties import BooleanProperty, StringProperty

from app.services.data_service import DataService


class DeviceToggle(BoxLayout):
    """Large touch-friendly card for toggling a device on/off."""

    device_label = StringProperty("")
    device_key   = StringProperty("")
    is_on        = BooleanProperty(False)

    def toggle(self) -> None:
        DataService.get().toggle_device(self.device_key)
