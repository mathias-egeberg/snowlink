"""
MapScreen – 3D interactive map using MapLibre GL JS + Three.js.

The map is rendered by a webkit2gtk WebView running in a GLib event loop
on a daemon thread.  The GTK window is frameless (override_redirect=True)
and positioned exactly over the Kivy placeholder, making it appear fully
embedded.  MapLibre GL JS delivers 3D terrain + full pan/tilt/zoom/rotate
via WebGL.  The snowcat is rendered as a real 3D GLB model via a Three.js
custom layer sharing MapLibre's WebGL context.

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

# Base URL for the WebView – gives file:// access to project assets
_BASE_URL = "file://" + os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
) + "/"

# GLB model embedded as base64 so the WebView needs no file:// fetch
def _load_glb_b64() -> str:
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "assets", "snowcat.glb")
    )
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return ""

_SNOWCAT_GLB_B64 = _load_glb_b64()

# ── MapLibre + Three.js HTML ──────────────────────────────────────────────────
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
.maplibregl-marker{opacity:1!important}
</style>
</head>
<body>
<div id="map"></div>
<div id="basemap-toggle">
  <button id="btn-topo"   class="active" onclick="setBasemap('topo')">Topo</button>
  <button id="btn-aerial"              onclick="setBasemap('aerial')">Aerial</button>
</div>

<!-- MapLibre (sets global maplibregl before module script runs) -->
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>

<!-- GLB model embedded as base64 – avoids any file:// fetch restrictions -->
<script>window._glbB64='GLB_B64_PH';</script>

<!-- Three.js ES module imports -->
<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.169.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.169.0/examples/jsm/"
  }
}
</script>

<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// ── Coordinates (replaced by Python) ─────────────────────────────────────────
var bootLat = BOOT_LAT_PH;
var bootLon = BOOT_LON_PH;

// ── Map ───────────────────────────────────────────────────────────────────────
var map = new maplibregl.Map({
  container: 'map',
  center: [bootLon, bootLat],
  zoom: 17,
  pitch: 55,
  bearing: 0,
  maxPitch: 85,
  touchZoomRotate: true,
  dragRotate: true,
  canvasContextAttributes: {antialias: true},
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

// ── Shared state ──────────────────────────────────────────────────────────────
window._modelLat     = bootLat;
window._modelLon     = bootLon;
window._modelBearing = 0;
window._showSnowcat  = true;

// ── Three.js custom 3D layer ──────────────────────────────────────────────────
// Model extents (from trimesh): ~375mm × 214mm × 162mm native units.
// Real snowcat ≈ 8 m long → world scale factor = 8000/375 ≈ 21.3
var MODEL_SCALE_FACTOR = 21.3;

function _mercatorTransform(lat, lon, bearingDeg) {
  var coord = maplibregl.MercatorCoordinate.fromLngLat([lon, lat], 0);
  var mpu   = coord.meterInMercatorCoordinateUnits(); // metres → Mercator units
  return {
    tx: coord.x, ty: coord.y, tz: coord.z,
    // model in mm; /1000 converts mm→m; *mpu converts m→Mercator; *factor = world size
    scale: (mpu / 1000) * MODEL_SCALE_FACTOR,
    bearing: bearingDeg
  };
}

var snowcatLayer = {
  id: 'snowcat-3d',
  type: 'custom',
  renderingMode: '3d',

  onAdd: function(map, gl) {
    this._map = map;
    this.camera = new THREE.Camera();
    this.scene  = new THREE.Scene();

    // Lighting – ambient + two directional lights for a convincing look
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    var sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(1, -1, 2).normalize();
    this.scene.add(sun);
    var fill = new THREE.DirectionalLight(0xffffff, 0.5);
    fill.position.set(-1, 1, 0.5).normalize();
    this.scene.add(fill);

    // Decode the embedded base64 GLB and parse it directly –
    // avoids any file:// fetch restrictions in WebKit2.
    var self = this;
    var loader = new GLTFLoader();
    fetch('data:model/gltf-binary;base64,' + window._glbB64)
      .then(function(r) { return r.arrayBuffer(); })
      .then(function(buf) {
        loader.parse(buf, '',
          function(gltf) {
            // Activate vertex colours on every mesh – trimesh exports them as
            // COLOR_0 attributes but GLTFLoader won't enable them automatically
            // unless we tell the material.
            gltf.scene.traverse(function(child) {
              if (child.isMesh && child.geometry.attributes.color) {
                if (Array.isArray(child.material)) {
                  child.material.forEach(function(m) {
                    m.vertexColors = true; m.needsUpdate = true;
                  });
                } else {
                  child.material.vertexColors = true;
                  child.material.needsUpdate = true;
                }
              }
            });
            self.scene.add(gltf.scene);
            map.triggerRepaint();
          },
          function(err) { console.error('[snowcat-3d] parse error:', err); }
        );
      })
      .catch(function(err) { console.error('[snowcat-3d] fetch error:', err); });

    // Share MapLibre's canvas + WebGL context with Three.js
    this.renderer = new THREE.WebGLRenderer({
      canvas: map.getCanvas(),
      context: gl,
      antialias: true
    });
    this.renderer.autoClear = false;
  },

  render: function(gl, args) {
    if (!window._showSnowcat) return;

    var t = _mercatorTransform(
      window._modelLat, window._modelLon, window._modelBearing
    );

    // rotateX: PI/2 (GLTF Y-up → Mercator Z-up) + PI/2 (tilt forward) + PI (flip) = 2PI = 0
    var rotX = new THREE.Matrix4().makeRotationAxis(
      new THREE.Vector3(1, 0, 0), 0
    );
    // Fixed 90° right yaw offset so the model faces its natural forward direction
    var rotZ_init = new THREE.Matrix4().makeRotationAxis(
      new THREE.Vector3(0, 0, 1), -Math.PI / 2
    );
    // Dynamic bearing from GPS/IMU (clockwise from north)
    var rotZ = new THREE.Matrix4().makeRotationAxis(
      new THREE.Vector3(0, 0, 1), -t.bearing * Math.PI / 180
    );

    // Support both MapLibre 4.x (args.defaultProjectionData.mainMatrix)
    // and older versions where args itself was the raw matrix array.
    var rawMatrix = (args && args.defaultProjectionData)
      ? args.defaultProjectionData.mainMatrix
      : args;
    var proj = new THREE.Matrix4().fromArray(rawMatrix);
    var local = new THREE.Matrix4()
      .makeTranslation(t.tx, t.ty, t.tz)
      .scale(new THREE.Vector3(t.scale, -t.scale, t.scale))
      .multiply(rotZ)
      .multiply(rotZ_init)
      .multiply(rotX);

    this.camera.projectionMatrix = proj.multiply(local);
    this.renderer.resetState();
    this.renderer.render(this.scene, this.camera);
    this._map.triggerRepaint();
  }
};

// ── Map load ──────────────────────────────────────────────────────────────────
map.on('load', function() {
  map.addLayer(snowcatLayer);

  // GPS dot fallback marker
  var dotEl = document.createElement('div');
  dotEl.style.cssText = [
    'width:20px', 'height:20px', 'border-radius:50%',
    'background:#00b4d8', 'border:3px solid #fff',
    'box-shadow:0 0 12px rgba(0,180,216,0.9)'
  ].join(';');

  window._dotMarker = new maplibregl.Marker({
    element: dotEl,
    occludedOpacity: 1
  }).setLngLat([bootLon, bootLat]).addTo(map);

  // Apply the initial marker style baked in at HTML build time
  window.setMarkerStyle('INIT_MARKER_STYLE_PH');
});

// ── Global JS API (called from Python via wv.run_javascript) ─────────────────
window.setMarkerStyle = function(style) {
  window._showSnowcat = (style === 'snowcat');
  if (window._dotMarker) {
    window._dotMarker.getElement().style.display =
      (style === 'dot') ? '' : 'none';
  }
  map.triggerRepaint();
};

window.moveMarker = function(lat, lon, bearing) {
  window._modelLat     = lat;
  window._modelLon     = lon;
  window._modelBearing = bearing;
  if (window._dotMarker) window._dotMarker.setLngLat([lon, lat]);
  map.easeTo({center: [lon, lat], duration: 200});
  map.triggerRepaint();
};

window.setBasemap = function(name) {
  map.setLayoutProperty('topo-layer',   'visibility',
    name === 'topo'   ? 'visible' : 'none');
  map.setLayoutProperty('aerial-layer', 'visibility',
    name === 'aerial' ? 'visible' : 'none');
  document.getElementById('btn-topo')
    .classList.toggle('active', name === 'topo');
  document.getElementById('btn-aerial')
    .classList.toggle('active', name === 'aerial');
};

</script>
</body>
</html>
"""


def _build_map_html(lat: float, lon: float, marker_style: str = "snowcat") -> str:
    return (
        _MAP_HTML
        .replace("BOOT_LAT_PH", f"{lat:.8f}")
        .replace("BOOT_LON_PH", f"{lon:.8f}")
        .replace("TOPO_PH",   _TOPO_TILES)
        .replace("AERIAL_PH", _AERIAL_TILES)
        .replace("DEM_PH",    _DEM_TILES)
        .replace("GLB_B64_PH", _SNOWCAT_GLB_B64)
        .replace("INIT_MARKER_STYLE_PH", marker_style)
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
        self._win:     Optional[object] = None
        self._webview: Optional[object] = None
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
            win = self._win
            def _reshow():
                win.move(x, y)
                win.resize(w, h)
                win.show_all()
                return False
            GLib.idle_add(_reshow)
            return True

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
                # Allow the page (loaded from file://) to fetch local assets
                settings.set_allow_file_access_from_file_urls(True)
                settings.set_allow_universal_access_from_file_urls(True)

                wv = WebKit2.WebView.new_with_settings(settings)
                # Use project root as base URL so assets/ resolves correctly
                wv.load_html(html, _BASE_URL)
                self._webview = wv

                win = Gtk.Window()
                win.set_decorated(False)
                win.set_skip_taskbar_hint(True)
                win.set_skip_pager_hint(True)
                win.set_default_size(w, h)
                win.add(wv)

                win.realize()
                win.get_window().set_override_redirect(True)

                win.move(x, y)
                win.show_all()

                self._win = win
                return False

            GLib.idle_add(_build)
            loop.run()

        threading.Thread(target=_gtk_thread, daemon=True).start()
        return True

    def hide(self) -> None:
        if self._win is None:
            return
        try:
            from gi.repository import GLib
            win = self._win
            GLib.idle_add(win.hide)
        except ImportError:
            pass

    def set_marker_style(self, style: str) -> None:
        """Inject JS to switch between 3D model ('snowcat') and GPS dot ('dot')."""
        if self._webview is None:
            return
        try:
            from gi.repository import GLib
            wv = self._webview
            script = f"if(window.setMarkerStyle){{setMarkerStyle('{style}');}}"
            GLib.idle_add(lambda: wv.run_javascript(script, None, None, None) or False)
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
        Clock.schedule_once(self._start_webview, 0.1)

    def _start_webview(self, _dt=None):
        ph = self.ids.map_placeholder

        win_x, win_y_bot = ph.to_window(0, 0)

        win_left = int(getattr(Window, "left", 0))
        win_top  = int(getattr(Window, "top",  0))
        screen_x = win_left + int(win_x)
        screen_y = win_top  + int(Window.height - win_y_bot - ph.height)

        from app.services.settings_service import SettingsService
        marker_style = SettingsService.load()['map']['marker_style']

        html = _build_map_html(BOOT_LAT, BOOT_LON, marker_style)
        ok = self._webkit.show(
            html,
            screen_x, screen_y,
            int(ph.width), int(ph.height),
        )
        if not ok:
            self.ids.map_no_browser.opacity = 1
        else:
            self._webkit.set_marker_style(marker_style)

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
