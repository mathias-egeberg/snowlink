from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout


class ConnIndicator(BoxLayout):
    """
    Small header status chip.

    Properties:
        label     – text shown below the icon (e.g. '5G', 'GPS', 'IMU')
        icon_type – drawing style: '5g' | 'gps' | 'imu'
        is_ok     – True = green (connected), False = grey (disconnected)
    """

    label      = StringProperty("")
    icon_type  = StringProperty("5g")
    is_ok      = BooleanProperty(False)
    is_warning = BooleanProperty(False)  # orange — connected but degraded

    _GREEN  = (0.000, 0.824, 0.549, 1.0)
    _ORANGE = (1.000, 0.502, 0.000, 1.0)
    _RED    = (0.937, 0.137, 0.235, 1.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.bind(pos=self._redraw, size=self._redraw,
                  is_ok=self._redraw, is_warning=self._redraw)

    # ── Redraw ────────────────────────────────────────────────────────────

    def _redraw(self, *_):
        self.canvas.before.clear()
        if self.is_ok:
            fg = self._GREEN
        elif self.is_warning:
            fg = self._ORANGE
        else:
            fg = self._RED
        bg = (fg[0], fg[1], fg[2], 0.18 if self.is_ok else 0.12)
        # Icon centre: upper portion of the widget (above the 16dp label)
        cx = self.center_x
        iy = self.y + dp(16) + (self.height - dp(16)) * 0.5

        with self.canvas.before:
            Color(rgba=bg)
            RoundedRectangle(
                pos=(self.x + dp(2), self.y + dp(2)),
                size=(self.width - dp(4), self.height - dp(4)),
                radius=[dp(5)],
            )
            Color(rgba=fg)
            if self.icon_type == "5g":
                self._draw_bars(cx, iy)
            elif self.icon_type == "gps":
                self._draw_gps(cx, iy)
            elif self.icon_type == "imu":
                self._draw_imu(cx, iy)

    # ── Icon primitives ───────────────────────────────────────────────────

    def _draw_bars(self, cx, cy):
        """4 ascending signal bars, bottom-aligned."""
        w   = dp(4)
        gap = dp(2.5)
        total = 4 * w + 3 * gap
        x0    = cx - total / 2
        base  = cy - dp(7)
        for i, h in enumerate([dp(4), dp(7), dp(10), dp(14)]):
            Rectangle(pos=(x0 + i * (w + gap), base), size=(w, h))

    def _draw_gps(self, cx, cy):
        """Circle crosshair with centre dot."""
        r = dp(7)
        Line(circle=(cx, cy, r), width=1.5)
        Line(points=[cx - dp(11), cy, cx + dp(11), cy], width=1.5)
        Line(points=[cx, cy - dp(11), cx, cy + dp(11)], width=1.5)
        Ellipse(pos=(cx - dp(2.5), cy - dp(2.5)), size=(dp(5), dp(5)))

    def _draw_imu(self, cx, cy):
        """3-axis (X/Y/Z) arrows."""
        ln = dp(10)
        # X → right
        Line(points=[cx, cy, cx + ln, cy], width=1.5)
        Line(points=[cx + ln - dp(4), cy - dp(3),
                     cx + ln,         cy,
                     cx + ln - dp(4), cy + dp(3)], width=1.5)
        # Y ↑ up
        Line(points=[cx, cy, cx, cy + ln], width=1.5)
        Line(points=[cx - dp(3), cy + ln - dp(4),
                     cx,         cy + ln,
                     cx + dp(3), cy + ln - dp(4)], width=1.5)
        # Z ↙ depth
        Line(points=[cx, cy, cx - dp(7), cy - dp(7)], width=1.5)
        Line(points=[cx - dp(3), cy - dp(7),
                     cx - dp(7), cy - dp(7),
                     cx - dp(7), cy - dp(3)], width=1.5)
