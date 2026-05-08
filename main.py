"""
SnowLink – main entry point.

Window/display configuration must happen before any other Kivy imports,
so the Config.set() calls are placed at the very top.
"""
import os

# ── Window configuration (before any other Kivy import) ──────────────────
os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")

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
from app.widgets.nav_bar       import NavBar, NavButton  # noqa: F401
from app.screens.dashboard     import DashboardScreen  # noqa: F401
from app.screens.control       import ControlScreen    # noqa: F401
from app.screens.settings      import SettingsScreen   # noqa: F401

# ── KV files (order matters: widgets before screens, screens before root) ─
_KV_DIR = os.path.join(os.path.dirname(__file__), "kv")
_KV_FILES = [
    "status_card.kv",
    "device_toggle.kv",
    "nav_bar.kv",
    "dashboard.kv",
    "control.kv",
    "settings.kv",
    "root.kv",
]


class RootWidget(BoxLayout):
    """Application root – holds the ScreenManager and the NavBar."""


class SnowLinkApp(App):
    title = "SnowLink"

    def build(self):
        for kv_file in _KV_FILES:
            Builder.load_file(os.path.join(_KV_DIR, kv_file))
        return RootWidget()


if __name__ == "__main__":
    SnowLinkApp().run()
