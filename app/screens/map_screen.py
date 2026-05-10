"""
MapScreen – 3D interactive map using MapLibre GL JS.

The map is rendered by a webkit2gtk WebView running in a GLib event loop
on a daemon thread.  The GTK window is frameless (override_redirect=True)
and positioned exactly over the Kivy placeholder, making it appear fully
embedded.  MapLibre GL JS delivers 3D terrain + full pan/tilt/zoom/rotate
via WebGL and multi-touch.

Lifecycle:
  on_enter → _WebkitHost.start()  – frameless GTK window created, WebView loaded
  on_leave → _WebkitHost.stop()   – window destroyed, GLib loop quit
"""
from __future__ import annotations

import base64
import datetime
import os
import threading
from typing import Optional

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.screenmanager import Screen

from app.services.data_service import DataService

# ── Coordinates ───────────────────────────────────────────────────────────────
BOOT_LAT = 60.0137563
BOOT_LON = 11.0196226

# ── Tile sources ──────────────────────────────────────────────────────────────
_TOPO_TILES = (
    "https://cache.kartverket.no/v1/wmts/1.0.0"
    "/topo/default/webmercator/{z}/{y}/{x}.png"
)
_AERIAL_TILES = (
    "https://services.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_DEM_TILES = (
    "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png"
)

# ── Snowcat marker image ──────────────────────────────────────────────────────
def _load_marker_data_url() -> str:
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "assets", "snowcat_marker.png")
    )
    try:
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return ""

_SNOWCAT_DATA_URL = _load_marker_data_url()

# ── MapLibre GL JS HTML ───────────────────────────────────────────────────────
_MAP_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="stylesheet"
  href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body,#map{width:100%;height:100%;margin:0;padding:0;overflow:hidden;
               background:#0d1829}
#basemap-toggle{
  position:absolute;top:10px;right:10px;z-index:10;
  display:flex;gap:6px
}
#basemap-toggle button{
  background:rgba(13,24,41,0.88);color:#e2e8f0;
  border:1.5px solid #00b4d8;border-radius:6px;
  padding:8px 18px;font-size:14px;font-family:sans-serif;
  cursor:pointer;touch-action:manipulation;user-select:none
}
#basemap-toggle button.active{
  background:#00b4d8;color:#0d1829;font-weight:700
}
.maplibregl-ctrl-logo{display:none!important}
.maplibregl-ctrl-attrib{font-size:9px;opacity:0.6}
</style>
</head>
<body>
<div id="map"></div>
<div id="basemap-toggle">
  <button id="btn-topo"   class="active" onclick="setBasemap('topo')">Topo</button>
  <button id="btn-aerial"              onclick="setBasemap('aerial')">Aerial</button>
</div>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
var bootLat = BOOT_LAT_PH;
var bootLon = BOOT_LON_PH;

var map = new maplibregl.Map({
  container: 'map',
  center: [bootLon, bootLat],
  zoom: 15,
  pitch: 55,
  bearing: 0,
  maxPitch: 85,
  touchZoomRotate: true,
  dragRotate: true,
  style: {
    version: 8,
    sources: {
      topo: {
        type: 'raster',
        tiles: ['TOPO_PH'],
        tileSize: 256, maxzoom: 18,
        attribution: '© Kartverket'
      },
      aerial: {
        type: 'raster',
        tiles: ['AERIAL_PH'],
        tileSize: 256, maxzoom: 18,
        attribution: '© Esri'
      },
      dem: {
        type: 'raster-dem',
        tiles: ['DEM_PH'],
        tileSize: 256, encoding: 'terrarium', maxzoom: 16
      }
    },
    layers: [
      {id:'topo-layer',   type:'raster', source:'topo',
       layout:{visibility:'visible'}},
      {id:'aerial-layer', type:'raster', source:'aerial',
       layout:{visibility:'none'}},
      {id:'hillshade',    type:'hillshade', source:'dem',
       paint:{
         'hillshade-shadow-color':'#1a2a3a',
         'hillshade-illumination-direction':335,
         'hillshade-exaggeration':0.4
       }
      }
    ],
    terrain: {source:'dem', exaggeration:1.5}
  }
});

map.addControl(
  new maplibregl.NavigationControl({visualizePitch: true}),
  'bottom-right'
);

map.on('load', function() {
  var el = document.createElement('div');
  el.style.cssText = 'position:relative;width:110px';

  var img = document.createElement('img');
  img.src = 'SNOWCAT_DATA_URL_PH';
  img.style.cssText = [
    'width:110px','display:block',
    'filter:drop-shadow(0 3px 10px rgba(0,0,0,0.85))'
  ].join(';');
  el.appendChild(img);

  // Small GPS dot below the centre of the machine
  var dot = document.createElement('div');
  dot.style.cssText = [
    'position:absolute','bottom:-6px','left:50%',
    'transform:translateX(-50%)',
    'width:10px','height:10px','border-radius:50%',
    'background:#00b4d8','border:2px solid #fff',
    'box-shadow:0 0 8px rgba(0,180,216,0.9)'
  ].join(';');
  el.appendChild(dot);

  window._snowcatMarker = new maplibregl.Marker({element: el, anchor: 'bottom'})
    .setLngLat([bootLon, bootLat])
    .setPopup(
      new maplibregl.Popup({offset: 14})
        .setHTML(
          '<div style="font-family:sans-serif;font-size:12px;line-height:1.8">' +
          '<strong>Snowcat position</strong><br/>' +
          bootLat.toFixed(7) + '° N<br/>' +
          bootLon.toFixed(7) + '° E</div>'
        )
    )
    .addTo(map);
});

function setBasemap(name) {
  map.setLayoutProperty('topo-layer',  'visibility',
    name==='topo'   ? 'visible':'none');
  map.setLayoutProperty('aerial-layer','visibility',
    name==='aerial' ? 'visible':'none');
  document.getElementById('btn-topo')
    .classList.toggle('active', name==='topo');
  document.getElementById('btn-aerial')
    .classList.toggle('active', name==='aerial');
}

window.moveMarker = function(lat, lon, bearing) {
  if (window._snowcatMarker) {
    window._snowcatMarker.setLngLat([lon, lat]);
  }
  map.easeTo({center:[lon, lat], bearing:bearing, duration:200});
};
</script>
</body>
</html>
"""


def _build_map_html(lat: float, lon: float) -> str:
    return (
        _MAP_HTML
        .replace("BOOT_LAT_PH", f"{lat:.8f}")
        .replace("BOOT_LON_PH", f"{lon:.8f}")
        .replace("TOPO_PH",   _TOPO_TILES)
        .replace("AERIAL_PH", _AERIAL_TILES)
        .replace("DEM_PH",    _DEM_TILES)
        .replace("SNOWCAT_DATA_URL_PH", _SNOWCAT_DATA_URL)
    )


# ── webkit2gtk host ───────────────────────────────────────────────────────────

class _WebkitHost:
    """
    Manages a frameless webkit2gtk overlay window.

    The GTK window uses override_redirect=True so the window manager adds
    no title bar and cannot move or resize it.  It is positioned and sized
    to cover the Kivy map placeholder exactly.  The GLib main loop runs on
    a daemon thread alongside Kivy's event loop.
    """

    def __init__(self):
        self._win: Optional[object] = None
        self._available: Optional[bool] = None  # None = untested

    def _gtk_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("WebKit2", "4.1")
            from gi.repository import GLib, Gtk, WebKit2  # noqa: F401
            self._available = True
        except (ImportError, ValueError):
            self._available = False
        return self._available

    def show(self, html: str, x: int, y: int, w: int, h: int) -> bool:
        """
        First call: create a frameless WebKit2 window on a GLib daemon thread.
        Subsequent calls: just move, resize, and show the existing window.
        Returns False if webkit2gtk is unavailable.
        """
        if not self._gtk_available():
            return False

        from gi.repository import GLib, Gtk, WebKit2

        if self._win is not None:
            # Window already exists — reposition and reveal it
            win = self._win
            def _reshow():
                win.move(x, y)
                win.resize(w, h)
                win.show_all()
                return False
            GLib.idle_add(_reshow)
            return True

        # First visit: create the GTK window on a daemon thread that runs
        # its own GLib main loop.  The loop (and thread) live for the entire
        # lifetime of the process — we never destroy the WebView, only hide it.
        loop = GLib.MainLoop()

        def _gtk_thread():
            def _build():
                settings = WebKit2.Settings()
                settings.set_enable_webgl(True)
                settings.set_enable_javascript(True)
                settings.set_enable_accelerated_2d_canvas(True)
                settings.set_hardware_acceleration_policy(
                    WebKit2.HardwareAccelerationPolicy.ALWAYS
                )

                wv = WebKit2.WebView.new_with_settings(settings)
                wv.load_html(html, "file:///")

                win = Gtk.Window()
                win.set_decorated(False)
                win.set_skip_taskbar_hint(True)
                win.set_skip_pager_hint(True)
                win.set_default_size(w, h)
                win.add(wv)

                # Set override_redirect before mapping so the window manager
                # never touches this window (no title bar, no decorations).
                win.realize()
                win.get_window().set_override_redirect(True)

                win.move(x, y)
                win.show_all()

                self._win = win
                return False  # don't repeat

            GLib.idle_add(_build)
            loop.run()  # runs until process exits

        threading.Thread(target=_gtk_thread, daemon=True).start()
        return True

    def hide(self) -> None:
        """
        Hide the WebKit window instantly.  The GTK thread and WebView stay
        alive so re-entering the map screen is instant with no teardown risk.
        """
        if self._win is None:
            return
        try:
            from gi.repository import GLib
            win = self._win
            GLib.idle_add(win.hide)
        except ImportError:
            pass


# ── Screen ────────────────────────────────────────────────────────────────────

class MapScreen(Screen):
    _webkit: _WebkitHost

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._webkit = _WebkitHost()

    def on_enter(self):
        Clock.schedule_once(self._init, 0.05)

    def _init(self, _dt):
        ds = DataService.get()
        self._on_connections()
        ds.bind(
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            gps_float_rtk=self._on_connections,
            gps_status_text=self._on_connections,
            imu_ok=self._on_connections,
        )
        self._clock = Clock.schedule_interval(self._tick_clock, 1)
        self._tick_clock(0)
        # Give layout one more frame to finalise sizes before measuring
        Clock.schedule_once(self._start_webview, 0.1)

    def _start_webview(self, _dt=None):
        ph = self.ids.map_placeholder

        # Widget bottom-left in Kivy window coords (Y=0 at bottom of window)
        win_x, win_y_bot = ph.to_window(0, 0)

        # Convert to screen top-left (Y=0 at top of screen)
        win_left = int(getattr(Window, "left", 0))
        win_top  = int(getattr(Window, "top",  0))
        screen_x = win_left + int(win_x)
        screen_y = win_top  + int(Window.height - win_y_bot - ph.height)

        html = _build_map_html(BOOT_LAT, BOOT_LON)
        ok = self._webkit.show(
            html,
            screen_x, screen_y,
            int(ph.width), int(ph.height),
        )
        if not ok:
            self.ids.map_no_browser.opacity = 1

    def on_leave(self):
        ds = DataService.get()
        ds.unbind(
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            gps_float_rtk=self._on_connections,
            gps_status_text=self._on_connections,
            imu_ok=self._on_connections,
        )
        if hasattr(self, "_clock"):
            self._clock.cancel()
        self._webkit.hide()

    # ── Data bindings ─────────────────────────────────────────────────────────

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

    def _tick_clock(self, _dt):
        now = datetime.datetime.now()
        self.ids.lbl_time_map.text = now.strftime("%H:%M:%S")
        self.ids.lbl_date_map.text = now.strftime("%A, %d %b %Y")
