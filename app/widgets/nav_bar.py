import sys

from kivy.app import App
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.properties import BooleanProperty, StringProperty


class NavButton(ButtonBehavior, BoxLayout):
    """Single tab button in the bottom navigation bar."""

    text      = StringProperty("")
    is_active = BooleanProperty(False)


class ExitButton(ButtonBehavior, BoxLayout):
    """Red exit button on the far right of the nav bar."""

    def on_release(self) -> None:
        App.get_running_app().stop()


class NavBar(BoxLayout):
    """Bottom navigation bar shared across all screens."""

    active_tab = StringProperty("dashboard")

    def go_to(self, screen_name: str) -> None:
        self.active_tab = screen_name
        sm = App.get_running_app().root.ids.screen_manager
        sm.current = screen_name
