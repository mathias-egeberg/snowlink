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
    """Launch map_webview_host.py as a subprocess. Embedding is done by us."""
    try:
        # -u: unbuffered IO so any host print() is visible immediately.
        proc = subprocess.Popen(
            [sys.executable, "-u", str(_HOST_SCRIPT),
             str(x), str(y), str(w), str(h), html_path],
        )
        print(f"[MapScreen] Webview PID {proc.pid} at ({x},{y}) {w}×{h}")
        return proc
    except Exception as e:
        print(f"[MapScreen] Failed to launch webview host: {e}")
        return None


# ─── Win32 embedding (parent-side, PID-based) ─────────────────────────────────

def _is_windows() -> bool:
    return sys.platform == "win32"


# Lazy-initialised user32 with proper 64-bit-safe signatures. Without explicit
# argtypes/restype, ctypes assumes c_int — which truncates HWNDs on x64.
_user32 = None


def _u32():
    global _user32
    if _user32 is not None:
        return _user32
    if not _is_windows():
        return None
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32

    # HWND/LPARAM/etc. are pointer-sized on 64-bit; declare them.
    HWND   = wintypes.HWND
    LPARAM = wintypes.LPARAM
    DWORD  = wintypes.DWORD
    BOOL   = wintypes.BOOL
    LONG_PTR = ctypes.c_ssize_t  # what GetWindowLongPtrW returns/takes

    u.EnumWindows.argtypes              = [ctypes.c_void_p, LPARAM]
    u.EnumWindows.restype               = BOOL
    u.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
    u.GetWindowThreadProcessId.restype  = DWORD
    u.GetParent.argtypes                = [HWND]
    u.GetParent.restype                 = HWND
    u.GetWindowRect.argtypes            = [HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowRect.restype             = BOOL
    u.IsWindowVisible.argtypes          = [HWND]
    u.IsWindowVisible.restype           = BOOL
    u.SetParent.argtypes                = [HWND, HWND]
    u.SetParent.restype                 = HWND
    u.SetWindowPos.argtypes             = [HWND, HWND, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.SetWindowPos.restype              = BOOL
    u.GetWindowLongPtrW.argtypes        = [HWND, ctypes.c_int]
    u.GetWindowLongPtrW.restype         = LONG_PTR
    u.SetWindowLongPtrW.argtypes        = [HWND, ctypes.c_int, LONG_PTR]
    u.SetWindowLongPtrW.restype         = LONG_PTR
    u.GetWindowTextLengthW.argtypes     = [HWND]
    u.GetWindowTextLengthW.restype      = ctypes.c_int
    u.GetWindowTextW.argtypes           = [HWND, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowTextW.restype            = ctypes.c_int

    _user32 = u
    return u


def _window_title(hwnd: int) -> str:
    u = _u32()
    if not u or not hwnd:
        return ""
    import ctypes
    n = u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _get_parent_hwnd() -> int:
    """Return the Kivy window's HWND, or 0 on failure / non-Windows."""
    if not _is_windows():
        return 0
    try:
        info = Window.get_window_info()
        hwnd = int(getattr(info, "window", 0) or 0)
        if hwnd:
            return hwnd
    except Exception as e:
        print(f"[MapScreen] Window.get_window_info failed: {e}")
    # Fallback: find a top-level window owned by *our* process.
    try:
        return _find_toplevel_for_pid(os.getpid(), require_visible=True) or 0
    except Exception as e:
        print(f"[MapScreen] parent HWND fallback failed: {e}")
        return 0


def _find_toplevel_for_pid(pid: int, require_visible: bool = False) -> int:
    """Enumerate top-level windows and return the largest one owned by `pid`
    or any of its descendant processes."""
    u = _u32()
    if not u:
        return 0
    import ctypes
    from ctypes import wintypes

    pids = _descendant_pids(pid) | {pid}

    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    matches: list[tuple[int, int, int, str]] = []  # (area, hwnd, owning_pid, title)

    def _cb(hwnd, _lparam):
        wpid = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value not in pids:
            return True
        # Note: EnumWindows already returns only top-level windows; do NOT
        # filter by GetParent, since that returns the owner for top-level
        # popups (which pywebview's frameless window often is).
        if require_visible and not u.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        matches.append((area, int(hwnd), wpid.value, _window_title(hwnd)))
        return True

    cb = EnumProc(_cb)
    u.EnumWindows(ctypes.cast(cb, ctypes.c_void_p), 0)
    if not matches:
        return 0
    matches.sort(reverse=True)
    print(f"[MapScreen] PID set {sorted(pids)} top-level candidates: " +
          ", ".join(f"hwnd=0x{h:x} pid={p} {a}px '{t}'"
                    for a, h, p, t in matches[:6]))
    # Prefer one whose title looks like ours; otherwise the largest.
    expected_title = f"SnowLink Map {pid}"
    for _a, h, _p, t in matches:
        if t == expected_title:
            return h
    return matches[0][1]


def _descendant_pids(root_pid: int) -> set[int]:
    """Return all descendant PIDs of `root_pid` using Toolhelp32 snapshot."""
    if not _is_windows():
        return set()
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize",              wintypes.DWORD),
            ("cntUsage",            wintypes.DWORD),
            ("th32ProcessID",       wintypes.DWORD),
            ("th32DefaultHeapID",   ctypes.c_void_p),
            ("th32ModuleID",        wintypes.DWORD),
            ("cntThreads",          wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase",      wintypes.LONG),
            ("dwFlags",             wintypes.DWORD),
            ("szExeFile",           wintypes.WCHAR * 260),
        ]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype  = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32FirstW.restype  = wintypes.BOOL
    k32.Process32NextW.argtypes  = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.restype   = wintypes.BOOL
    k32.CloseHandle.argtypes     = [wintypes.HANDLE]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return set()

    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        parent_of: dict[int, int] = {}
        if k32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                parent_of[entry.th32ProcessID] = entry.th32ParentProcessID
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        k32.CloseHandle(snap)

    # BFS descendants
    out: set[int] = set()
    frontier = {root_pid}
    while frontier:
        nxt = set()
        for cpid, ppid in parent_of.items():
            if ppid in frontier and cpid not in out and cpid != root_pid:
                out.add(cpid)
                nxt.add(cpid)
        frontier = nxt
    return out


# Style/SWP constants (module-level so both reparent + move use them)
_GWL_STYLE           = -16
_GWL_EXSTYLE         = -20
_WS_CHILD            = 0x40000000
_WS_VISIBLE          = 0x10000000
_WS_POPUP            = 0x80000000
_WS_OVERLAPPEDWINDOW = 0x00CF0000
_WS_EX_TOOLWINDOW    = 0x00000080
_WS_EX_APPWINDOW     = 0x00040000
_SWP_FRAMECHANGED    = 0x0020
_SWP_NOZORDER        = 0x0004
_SWP_SHOWWINDOW      = 0x0040
_SWP_NOACTIVATE      = 0x0010


def _reparent_into(child_hwnd: int, parent_hwnd: int,
                   cx: int, cy: int, w: int, h: int) -> bool:
    """Make `child_hwnd` a WS_CHILD of `parent_hwnd` and place it at (cx,cy)."""
    u = _u32()
    if not u or not child_hwnd or not parent_hwnd:
        return False

    style = u.GetWindowLongPtrW(child_hwnd, _GWL_STYLE)
    new_style = (style & ~(_WS_OVERLAPPEDWINDOW | _WS_POPUP | _WS_VISIBLE)) | _WS_CHILD
    u.SetWindowLongPtrW(child_hwnd, _GWL_STYLE, new_style)

    exstyle = u.GetWindowLongPtrW(child_hwnd, _GWL_EXSTYLE)
    new_ex = (exstyle | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW
    u.SetWindowLongPtrW(child_hwnd, _GWL_EXSTYLE, new_ex)

    prev_parent = u.SetParent(child_hwnd, parent_hwnd)
    actual_parent = u.GetParent(child_hwnd)
    print(f"[MapScreen] SetParent child=0x{child_hwnd:x} → parent=0x{parent_hwnd:x} "
          f"(prev=0x{int(prev_parent or 0):x}, actual=0x{int(actual_parent or 0):x})")

    if int(actual_parent or 0) != int(parent_hwnd):
        return False

    ok = u.SetWindowPos(
        child_hwnd, 0, int(cx), int(cy), int(w), int(h),
        _SWP_FRAMECHANGED | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_SHOWWINDOW,
    )
    if not ok:
        print("[MapScreen] SetWindowPos returned 0")
    return True


def _move_child(child_hwnd: int, cx: int, cy: int, w: int, h: int) -> None:
    u = _u32()
    if not u or not child_hwnd:
        return
    try:
        u.SetWindowPos(
            child_hwnd, 0, int(cx), int(cy), int(w), int(h),
            _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_SHOWWINDOW,
        )
    except Exception as e:
        print(f"[MapScreen] SetWindowPos failed: {e}")


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
        self._child_hwnd: int                     = 0
        self._embedded: bool                      = False
        self._embed_event                         = None
        self._sync_event                          = None

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
        # Keep the embedded child window aligned with this widget.
        self._sync_child_geometry()

    # ── Geometry ──────────────────────────────────────────────────────────

    def _client_rect(self) -> tuple[int, int, int, int, int, int]:
        """Return (screen_x, screen_y, w, h, client_x, client_y)."""
        win_left = getattr(Window, "left", 0) or 0
        win_top  = getattr(Window, "top",  0) or 0
        abs_x, abs_y = self.to_window(0, 0, relative=False)
        cx = int(abs_x)
        cy = int(Window.height - abs_y - self.height)
        return win_left + cx, win_top + cy, int(self.width), int(self.height), cx, cy

    def _sync_child_geometry(self, *_):
        if not self._embedded or not self._child_hwnd:
            return
        _, _, w, h, cx, cy = self._client_rect()
        _move_child(self._child_hwnd, cx, cy, w, h)

    # ── Embedding poll ────────────────────────────────────────────────────

    def _try_embed(self, _dt):
        """Poll for the subprocess's top-level window and reparent it."""
        if self._embedded:
            return False  # cancel the schedule
        if not self._browser or self._browser.poll() is not None:
            print(f"[MapScreen] Subprocess gone (rc={self._browser.poll() if self._browser else None}); stopping embed poll")
            return False

        self._embed_ticks = getattr(self, "_embed_ticks", 0) + 1
        hwnd = _find_toplevel_for_pid(self._browser.pid)
        if not hwnd:
            if self._embed_ticks in (1, 5, 20, 50, 100):
                print(f"[MapScreen] embed tick {self._embed_ticks}: no top-level window for PID {self._browser.pid} yet")
            return  # keep polling

        parent = _get_parent_hwnd()
        print(f"[MapScreen] Found child HWND 0x{hwnd:x}; parent HWND 0x{parent:x}")
        if not parent:
            print("[MapScreen] Could not resolve Kivy parent HWND")
            return False

        _, _, w, h, cx, cy = self._client_rect()
        abs_x, abs_y = self.to_window(0, 0, relative=False)
        print(f"[MapScreen] At embed: MapView pos={self.pos} abs=({abs_x:.1f},{abs_y:.1f}) size={self.size} "
              f"Window size=({Window.width}x{Window.height}) → child rect ({cx},{cy}) {w}×{h}")
        if _reparent_into(hwnd, parent, cx, cy, w, h):
            self._child_hwnd = hwnd
            self._embedded   = True
            self._lbl.text   = ""
            print(f"[MapScreen] Embedded child HWND 0x{hwnd:x} into parent 0x{parent:x} "
                  f"at client ({cx},{cy}) {w}×{h}")
            # Re-sync a few times in case Kivy layout settles after embed.
            for delay in (0.05, 0.2, 0.5, 1.0):
                Clock.schedule_once(lambda _dt: self._sync_child_geometry(), delay)
            return False  # stop polling
        else:
            print(f"[MapScreen] Reparent failed for HWND 0x{hwnd:x}; will retry")
            return  # try again next tick

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        """Launch the webview subprocess and embed it over this widget."""
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

        screen_x, screen_y, w, h, _cx, _cy = self._client_rect()

        self._browser  = _launch_webview(self._html_path, screen_x, screen_y, w, h)
        self._embedded = False
        self._child_hwnd = 0
        self._embed_ticks = 0

        if not self._browser:
            self._lbl.text = "pywebview not available.\nRun: pip install pywebview"
            return

        self._lbl.text = "Loading 3D map…"

        # Stay aligned on Kivy window resize/move.
        Window.bind(
            on_resize=self._on_window_change,
            on_restore=self._on_window_change,
            on_maximize=self._on_window_change,
        )

        if _is_windows():
            # Poll a few times a second until the subprocess's top-level window
            # exists, then reparent it. Stops itself once embedded.
            self._embed_event = Clock.schedule_interval(self._try_embed, 0.1)
            # Lightweight position sync (cheap; only runs when embedded).
            self._sync_event  = Clock.schedule_interval(
                lambda _dt: self._sync_child_geometry(), 0.5
            )

    def _on_window_change(self, *_):
        Clock.schedule_once(lambda _dt: self._sync_child_geometry(), 0)

    def stop(self):
        """Terminate the browser subprocess and tear down hooks."""
        for ev_attr in ("_embed_event", "_sync_event"):
            ev = getattr(self, ev_attr, None)
            if ev is not None:
                try:
                    ev.cancel()
                except Exception:
                    pass
                setattr(self, ev_attr, None)
        try:
            Window.unbind(
                on_resize=self._on_window_change,
                on_restore=self._on_window_change,
                on_maximize=self._on_window_change,
            )
        except Exception:
            pass
        if self._browser:
            try:
                self._browser.terminate()
            except Exception:
                pass
            self._browser = None
        self._child_hwnd = 0
        self._embedded   = False
        self._lbl.text   = "Loading 3D map…"

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
