"""
SnowLink – main entry point.

Window/display configuration must happen before any other Kivy imports,
so the Config.set() calls are placed at the very top.
"""
import os
import sys

# ── Window configuration (before any other Kivy import) ──────────────────
os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")
if sys.platform.startswith("linux"):
    os.environ["GDK_BACKEND"] = "x11"
    os.environ["SDL_VIDEODRIVER"] = "x11"

from kivy.config import Config  # noqa: E402 – must come before kivy.app

# Target display: 10" 1280×800 touch screen.
# Change fullscreen to '1' (or 'auto') on the Raspberry Pi.
_fullscreen = "auto" if os.environ.get("SNOWLINK_FULLSCREEN") else "0"
Config.set("graphics", "width",       "1280")
Config.set("graphics", "height",      "800")
Config.set("graphics", "fullscreen",  _fullscreen)
Config.set("graphics", "resizable",   "0")
Config.set("input",    "mouse",       "mouse,disable_multitouch")

# ── Standard Kivy imports ─────────────────────────────────────────────────
from kivy.app import App            # noqa: E402
from kivy.lang import Builder       # noqa: E402
from kivy.uix.boxlayout import BoxLayout  # noqa: E402

# ── Register all custom widget / screen classes ───────────────────────────
# Importing the modules registers the Python classes so that KV rules can
# reference them by name.
from app.widgets.status_card   import StatusCard    # noqa: F401
from app.widgets.device_toggle import DeviceToggle  # noqa: F401
from app.widgets.nav_bar       import NavBar, NavButton, ExitButton  # noqa: F401
from app.widgets.conn_indicator import ConnIndicator  # noqa: F401
from app.screens.dashboard     import DashboardScreen  # noqa: F401
from app.screens.map_screen    import MapScreen         # noqa: F401
from app.screens.control       import ControlScreen    # noqa: F401
from app.screens.settings      import SettingsScreen   # noqa: F401

# ── KV files (order matters: widgets before screens, screens before root) ─
_KV_DIR = os.path.join(os.path.dirname(__file__), "kv")
_KV_FILES = [
    "status_card.kv",
    "device_toggle.kv",
    "conn_indicator.kv",
    "nav_bar.kv",
    "map.kv",
    "dashboard.kv",
    "control.kv",
    "settings.kv",
    "root.kv",
]


class RootWidget(BoxLayout):
    """Application root – holds the ScreenManager and the NavBar."""

    def on_kv_post(self, base_widget):
        from app.services.data_service import DataService
        ds = DataService.get()
        self._imu_was_ok = ds.imu_ok
        ds.bind(imu_ok=self._on_imu_changed)

    def _on_imu_changed(self, _, is_ok):
        if not is_ok and self._imu_was_ok:
            self._show_imu_popup()
        self._imu_was_ok = is_ok

    def _show_imu_popup(self):
        from kivy.uix.popup import Popup
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        from kivy.metrics import dp

        content = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(16))

        msg = Label(
            text='The WheelTech N100 IMU has lost its\nUSB connection. Check the cable.',
            font_size='15sp',
            color=(0.910, 0.910, 0.910, 1),
            halign='center',
            valign='middle',
        )
        msg.bind(size=lambda s, v: setattr(s, 'text_size', v))

        btn = Button(
            text='Dismiss',
            size_hint=(0.5, None),
            pos_hint={'center_x': 0.5},
            height=dp(44),
            background_normal='',
            background_color=(0.937, 0.137, 0.235, 1),
            color=(1, 1, 1, 1),
            font_size='15sp',
            bold=True,
        )

        content.add_widget(msg)
        content.add_widget(btn)

        popup = Popup(
            title='⚠  IMU Disconnected',
            content=content,
            size_hint=(None, None),
            size=(dp(440), dp(240)),
            background_color=(0.086, 0.129, 0.243, 0.97),
            separator_color=(0.937, 0.137, 0.235, 0.5),
            title_color=(0.937, 0.137, 0.235, 1),
            title_size='17sp',
        )
        btn.bind(on_release=popup.dismiss)
        popup.open()


class SnowLinkApp(App):
    title = "SnowLink"

    def build(self):
        for kv_file in _KV_FILES:
            Builder.load_file(os.path.join(_KV_DIR, kv_file))
        return RootWidget()



if __name__ == "__main__":
    SnowLinkApp().run()
