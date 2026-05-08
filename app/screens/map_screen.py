"""
MapScreen – 3D interactive map using MapLibre GL JS.

Mirrors the Raven .NET approach: generates a MapLibre GL HTML page and
displays it in a pywebview subprocess window (frameless) positioned exactly
over the map area in the Kivy window — no visible OS chrome.

MapLibre provides out-of-the-box:
  • Kartverket topo tiles (same URL as Raven)
  • 3D terrain via AWS Terrarium DEM tiles
  • Hillshade layer
  • Pan / tilt / zoom / rotate with touch or mouse
  • Topo ↔ Aerial basemap switch
  • Snowcat position marker

Webview window lifecycle:
  on_enter  → write HTML to temp file → launch map_webview_host.py subprocess
  on_leave  → terminate subprocess
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from app.services.data_service import DataService

# ─── Boot coordinates ─────────────────────────────────────────────────────────

BOOT_LAT = 60.0137563
BOOT_LON = 11.0196226

# ─── Tile sources (same as Raven appsettings.desktop.json) ────────────────────

_TOPO_TILES = (
    "https://cache.kartverket.no/v1/wmts/1.0.0"
    "/topo/default/webmercator/{z}/{y}/{x}.png"
)
_AERIAL_TILES = (
    "https://services.arcgisonline.com/ArcGIS/rest/services"
    "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
# DEM uses standard XYZ order (x before y) – different from Kartverket REST
_DEM_TILES = "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png"

# ─── MapLibre HTML template ───────────────────────────────────────────────────
# Uses BOOT_LAT_PH / BOOT_LON_PH as placeholder tokens so we can do a plain
# string replace without wrestling with Python f-string brace escaping.

_MAP_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="stylesheet"
  href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
<style>
html,body,#map{width:100%;height:100%;margin:0;padding:0;overflow:hidden;background:#0e1b30}
.maplibregl-ctrl-attrib{font-size:10px}
#controls{
  position:absolute;top:10px;right:10px;z-index:5;
  display:flex;flex-direction:column;gap:6px
}
#controls button{
  background:rgba(14,27,48,0.88);color:#e2e8f0;
  border:1px solid #00b4d8;border-radius:6px;
  padding:7px 14px;font-size:13px;cursor:pointer;
  min-width:80px;touch-action:manipulation
}
#controls button.active{background:#00b4d8;color:#0e1b30;font-weight:600}
</style>
</head>
<body>
<div id="map"></div>
<div id="controls">
  <button id="btn-topo"   class="active" onclick="setBasemap('topo')">Topo</button>
  <button id="btn-aerial"              onclick="setBasemap('aerial')">Aerial</button>
</div>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
var bootLat = BOOT_LAT_PH;
var bootLon = BOOT_LON_PH;

var style = {
  version: 8,
  sources: {
    kartverket: {
      type: 'raster',
      tiles: ['TOPO_TILES_PH'],
      tileSize: 256,
      maxzoom: 18,
      attribution: 'Kartdata © Kartverket'
    },
    aerial: {
      type: 'raster',
      tiles: ['AERIAL_TILES_PH'],
      tileSize: 256,
      maxzoom: 18,
      attribution: 'Imagery © Esri'
    },
    terrainDem: {
      type: 'raster-dem',
      tiles: ['DEM_TILES_PH'],
      tileSize: 256,
      encoding: 'terrarium',
      maxzoom: 16
    }
  },
  layers: [
    {id:'base-topo',  type:'raster', source:'kartverket', layout:{visibility:'visible'}},
    {id:'base-aerial',type:'raster', source:'aerial',     layout:{visibility:'none'}},
    {
      id:'hillshade', type:'hillshade', source:'terrainDem',
      paint:{
        'hillshade-shadow-color':'#4B5563',
        'hillshade-illumination-direction':335,
        'hillshade-exaggeration':0.35
      }
    }
  ],
  terrain: {source:'terrainDem', exaggeration:1.2}
};

var map = new maplibregl.Map({
  container: 'map',
  center: [bootLon, bootLat],
  zoom: 15,
  pitch: 55,
  bearing: 0,
  maxPitch: 85,
  maxZoom: 20,
  style: style
});

map.addControl(new maplibregl.NavigationControl({visualizePitch:true}), 'bottom-right');

map.on('load', function() {
  // Snowcat position marker
  var el = document.createElement('div');
  el.style.cssText = [
    'width:22px','height:22px','border-radius:50%',
    'background:#00b4d8','border:3px solid #fff',
    'box-shadow:0 0 10px rgba(0,180,216,0.7)'
  ].join(';');
  new maplibregl.Marker({element:el})
    .setLngLat([bootLon, bootLat])
    .setPopup(
      new maplibregl.Popup({offset:16})
        .setHTML(
          '<div style="font-family:sans-serif;font-size:12px;line-height:1.6">' +
          '<strong>Boot position</strong><br/>' +
          bootLat.toFixed(6) + ' N<br/>' +
          bootLon.toFixed(6) + ' E</div>'
        )
    )
    .addTo(map);
});

function setBasemap(name) {
  map.setLayoutProperty('base-topo',   'visibility', name==='topo'   ? 'visible':'none');
  map.setLayoutProperty('base-aerial', 'visibility', name==='aerial' ? 'visible':'none');
  document.getElementById('btn-topo').classList.toggle('active',   name==='topo');
  document.getElementById('btn-aerial').classList.toggle('active', name==='aerial');
}

// External control: called via webview bridge or JS injection
window.setGroomerPosition = function(lat, lon, bearing) {
  bootLat = lat; bootLon = lon;
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
        .replace("TOPO_TILES_PH",   _TOPO_TILES)
        .replace("AERIAL_TILES_PH", _AERIAL_TILES)
        .replace("DEM_TILES_PH",    _DEM_TILES)
    )


# ─── Webview host helpers ─────────────────────────────────────────────────────

_HOST_SCRIPT = Path(__file__).parent.parent / "map_webview_host.py"


def _launch_webview(html_path: str, x: int, y: int, w: int, h: int) -> Optional[subprocess.Popen]:
    """Launch map_webview_host.py; on Windows it embeds itself in the Kivy HWND."""
    parent_title = Window.title or "SnowLink"
    try:
        proc = subprocess.Popen(
            [sys.executable, str(_HOST_SCRIPT),
             parent_title, str(x), str(y), str(w), str(h), html_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[MapScreen] Webview PID {proc.pid} at ({x},{y}) {w}×{h}")
        return proc
    except Exception as e:
        print(f"[MapScreen] Failed to launch webview host: {e}")
        return None


# ─── MapView ──────────────────────────────────────────────────────────────────

class MapView(FloatLayout):
    """
    Placeholder widget that occupies the map area in the Kivy layout.

    The actual map is rendered by a frameless pywebview subprocess window
    positioned exactly on top of this widget.  The dark background here
    is visible while the webview is loading.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self._browser: Optional[subprocess.Popen] = None
        self._html_path: Optional[str]            = None
        self._started = False

        # Dark background matching app theme
        with self.canvas.before:
            Color(0.055, 0.075, 0.145, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_bg, size=self._update_bg)

        self._lbl = Label(
            text="Loading 3D map…",
            color=(0.0, 0.706, 0.847, 1),
            font_size="18sp",
            pos_hint={"center_x": 0.5, "center_y": 0.5},
        )
        self.add_widget(self._lbl)

    def _update_bg(self, *_):
        self._bg.pos  = self.pos
        self._bg.size = self.size

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        """Write HTML and open the browser window over this widget."""
        if self._browser and self._browser.poll() is None:
            return  # already running

        # Write HTML to a persistent temp file (browser needs to read it)
        if self._html_path is None:
            fd, path = tempfile.mkstemp(suffix=".html", prefix="snowlink_map_")
            os.close(fd)
            self._html_path = path

        html = _build_map_html(BOOT_LAT, BOOT_LON)
        with open(self._html_path, "w", encoding="utf-8") as f:
            f.write(html)

        # Convert Kivy widget coords (bottom-left origin) → screen coords (top-left origin)
        win_left = getattr(Window, "left", 0) or 0
        win_top  = getattr(Window, "top",  0) or 0

        x = win_left + int(self.x)
        y = win_top  + int(Window.height - self.y - self.height)
        w = int(self.width)
        h = int(self.height)

        self._browser = _launch_webview(self._html_path, x, y, w, h)
        if self._browser:
            self._lbl.text = ""
        else:
            self._lbl.text = "pywebview not available.\nRun: pip install pywebview"

    def stop(self):
        """Terminate the browser subprocess."""
        if self._browser:
            try:
                self._browser.terminate()
            except Exception:
                pass
            self._browser = None
        self._lbl.text = "Loading 3D map…"

    def __del__(self):
        self.stop()
        if self._html_path and os.path.exists(self._html_path):
            try:
                os.unlink(self._html_path)
            except Exception:
                pass


# ─── MapScreen ────────────────────────────────────────────────────────────────

class MapScreen(Screen):
    """Map screen: header bar + MapLibre 3D map in subprocess browser."""

    def on_enter(self):
        # Give Kivy one frame to finish layout so widget.pos/size are correct
        Clock.schedule_once(self._init, 0.1)

    def _init(self, _dt):
        self.ids.map_view.start()
        ds = DataService.get()
        self._on_connections()
        ds.bind(
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            imu_ok=self._on_connections,
        )
        Clock.schedule_interval(self._tick_clock, 1)
        self._tick_clock(0)

    def on_leave(self):
        self.ids.map_view.stop()
        DataService.get().unbind(
            cellular_ok=self._on_connections,
            gps_ok=self._on_connections,
            imu_ok=self._on_connections,
        )
        Clock.unschedule(self._tick_clock)

    def _on_connections(self, *_):
        ds = DataService.get()
        self.ids.ind_5g.is_ok  = ds.cellular_ok
        self.ids.ind_gps.is_ok = ds.gps_ok
        self.ids.ind_imu.is_ok = ds.imu_ok

    def _tick_clock(self, _dt):
        now = datetime.datetime.now()
        self.ids.lbl_time_map.text = now.strftime("%H:%M:%S")
        self.ids.lbl_date_map.text = now.strftime("%A, %d %b %Y")
