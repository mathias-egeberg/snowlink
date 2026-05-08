from kivy.uix.boxlayout import BoxLayout
from kivy.properties import ListProperty, StringProperty


class StatusCard(BoxLayout):
    """Read-only display card showing a sensor label, live value, and unit."""

    label      = StringProperty("")
    value      = StringProperty("--")
    unit       = StringProperty("")
    card_color = ListProperty([0.059, 0.204, 0.376, 1])
