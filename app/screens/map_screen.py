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

import datetime
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
  el.style.cssText = [
    'width:18px','height:18px','border-radius:50%',
    'background:#00b4d8','border:3px solid #fff',
    'box-shadow:0 0 12px rgba(0,180,216,0.9)'
  ].join(';');
  new maplibregl.Marker({element: el})
    .setLngLat([bootLon, bootLat])
    .setPopup(
      new maplibregl.Popup({offset: 14})
        .setHTML(
          '<div style="font-family:sans-serif;font-size:12px;line-height:1.8">' +
          '<strong>Boot position</strong><br/>' +
          bootLat.toFixed(7) + ' N<br/>' +
          bootLon.toFixed(7) + ' E</div>'
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
  map.easeTo({center:[lon,lat], bearing:bearing, duration:200});
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
        self._loop: Optional[object] = None
        self._win:  Optional[object] = None

    def start(self, html: str, x: int, y: int, w: int, h: int) -> bool:
        """
        Load *html* in a new frameless WebKit2 window at screen position
        (x, y) with size (w, h).  Returns False if webkit2gtk is missing.
        """
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("WebKit2", "4.1")
            from gi.repository import GLib, Gtk, WebKit2  # noqa: F401
        except (ImportError, ValueError):
            return False

        from gi.repository import GLib, Gtk, WebKit2

        loop = GLib.MainLoop()
        self._loop = loop

        def _gtk_thread():
            def _build(_html, _x, _y, _w, _h):
                # WebGL settings
                settings = WebKit2.Settings()
                settings.set_enable_webgl(True)
                settings.set_enable_javascript(True)
                settings.set_enable_accelerated_2d_canvas(True)
                settings.set_hardware_acceleration_policy(
                    WebKit2.HardwareAccelerationPolicy.ALWAYS
                )

                wv = WebKit2.WebView.new_with_settings(settings)
                # load_html() avoids any temp files
                wv.load_html(_html, "file:///")

                win = Gtk.Window()
                win.set_decorated(False)
                win.set_skip_taskbar_hint(True)
                win.set_skip_pager_hint(True)
                win.set_default_size(_w, _h)
                win.add(wv)

                # realize() creates the underlying GDK/X11 window so we can
                # call set_override_redirect() before the window is mapped.
                win.realize()
                win.get_window().set_override_redirect(True)

                win.move(_x, _y)
                win.show_all()

                self._win = win
                return False  # GLib.idle_add: don't repeat

            GLib.idle_add(_build, html, x, y, w, h)
            loop.run()

        threading.Thread(target=_gtk_thread, daemon=True).start()
        return True

    def stop(self):
        """Destroy the window and quit the GLib main loop."""
        loop = self._loop
        win  = self._win
        self._loop = None
        self._win  = None

        if loop is None:
            return

        try:
            from gi.repository import GLib

            def _teardown():
                if win is not None:
                    win.destroy()
                loop.quit()
                return False

            GLib.idle_add(_teardown)
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
        ok = self._webkit.start(
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
        self._webkit.stop()

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
