"""
MapScreen – 3D interactive map using MapLibre GL JS.

The map is rendered by a single pywebview subprocess (WebKitGTK on Linux,
EdgeChromium on Windows) launched at app start by ``MapHost``. The subprocess
window is reparented as a strict child of the Kivy SDL2 window (override-redirect
on X11, ``WS_CHILD`` on Win32) so it cannot be dragged or detached.

Lifecycle:
  App.on_start  → MapHost.ensure_started() launches subprocess offscreen and
                  embeds it as a clipped child → map tiles preload immediately.
  MapScreen.on_enter → MapHost.attach(view) moves the embedded child over the
                       MapView area.
  MapScreen.on_leave → MapHost.detach() moves the child back offscreen
                       (still embedded, still alive).
  App.on_stop   → MapHost.stop() terminates subprocess and cleans temp files.
"""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import threading
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
_LAST_LAUNCH_ERROR = ""
_HOST_OUTPUT: dict[int, list[str]] = {}
_EMBED_TIMEOUT_TICKS = 150


def _remember_host_output(pid: int, line: str) -> None:
    lines = _HOST_OUTPUT.setdefault(pid, [])
    lines.append(line)
    if len(lines) > 20:
        del lines[:-20]


def _host_output_tail(pid: int) -> str:
    lines = _HOST_OUTPUT.get(pid, [])
    return " | ".join(lines[-3:])


def _stream_host_output(proc: subprocess.Popen) -> None:
    if proc.stdout is None:
        return
    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            _remember_host_output(proc.pid, line)
            print(f"[map_host:{proc.pid}] {line}")
    except Exception as exc:
        print(f"[MapScreen] Failed to read host output for PID {proc.pid}: {exc}")


def _launch_webview(
    html_path: str,
    x: int,
    y: int,
    w: int,
    h: int,
    handle_path: Optional[str] = None,
) -> Optional[subprocess.Popen]:
    """Launch map_webview_host.py as a subprocess. Embedding is done by us."""
    global _LAST_LAUNCH_ERROR
    _LAST_LAUNCH_ERROR = ""
    try:
        cmd = [
            sys.executable,
            "-u",
            str(_HOST_SCRIPT),
            str(x),
            str(y),
            str(w),
            str(h),
            html_path,
        ]
        if handle_path:
            cmd.append(handle_path)

        # -u: unbuffered IO so any host print() is visible immediately.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(
            target=_stream_host_output,
            args=(proc,),
            daemon=True,
        ).start()
        print(f"[MapScreen] Webview PID {proc.pid} at ({x},{y}) {w}×{h}")
        return proc
    except Exception as e:
        _LAST_LAUNCH_ERROR = str(e)
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


# ─── Linux/Pi: xdotool embedding ──────────────────────────────────────────────

def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _have_xdotool() -> bool:
    return shutil.which("xdotool") is not None


def _xdotool_search_pid(pid: int) -> int:
    """Return the largest X11 window owned by `pid`, or 0."""
    if not _have_xdotool():
        return 0
    try:
        out = subprocess.check_output(
            ["xdotool", "search", "--pid", str(pid)],
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()
    except Exception:
        return 0
    wids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            wids.append(int(line))
    if not wids:
        return 0

    # Pick the one with the largest geometry (Chromium spawns helper windows).
    best = (0, wids[-1])
    for wid in wids:
        try:
            geo = subprocess.check_output(
                ["xdotool", "getwindowgeometry", "--shell", str(wid)],
                stderr=subprocess.DEVNULL, text=True, timeout=2,
            )
        except Exception:
            continue
        wpx = hpx = 0
        for ln in geo.splitlines():
            if ln.startswith("WIDTH="):
                try: wpx = int(ln.split("=", 1)[1])
                except ValueError: pass
            elif ln.startswith("HEIGHT="):
                try: hpx = int(ln.split("=", 1)[1])
                except ValueError: pass
        area = wpx * hpx
        if area > best[0]:
            best = (area, wid)
    return best[1]


def _kivy_x11_xid() -> int:
    """Return the Kivy SDL2 window's X11 XID, or 0."""
    if not _is_linux():
        return 0
    try:
        info = Window.get_window_info()
        return int(getattr(info, "window", 0) or 0)
    except Exception as e:
        print(f"[MapScreen] Window.get_window_info failed: {e}")
        return 0


def _xdotool_reparent(child: int, parent: int) -> bool:
    if not _have_xdotool():
        return False
    try:
        subprocess.check_call(
            ["xdotool", "windowreparent", str(child), str(parent)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
        return True
    except Exception as e:
        print(f"[MapScreen] xdotool windowreparent failed: {e}")
        return False


def _xdotool_unmap(wid: int) -> bool:
    if not _have_xdotool() or not wid:
        return False
    try:
        subprocess.check_call(
            ["xdotool", "windowunmap", str(wid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
        return True
    except Exception:
        return False


def _xdotool_map(wid: int) -> bool:
    if not _have_xdotool() or not wid:
        return False
    try:
        subprocess.check_call(
            ["xdotool", "windowmap", "--sync", str(wid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
        return True
    except Exception as e:
        print(f"[MapScreen] xdotool windowmap failed: {e}")
        return False


def _xdotool_set_override_redirect(wid: int) -> bool:
    if not _have_xdotool() or not wid:
        return False
    try:
        subprocess.check_call(
            ["xdotool", "set_window", "--overrideredirect", "1", str(wid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
        return True
    except Exception as e:
        print(f"[MapScreen] xdotool set_window overrideredirect failed: {e}")
        return False


def _embed_x11_child(wid: int, parent: int,
                     cx: int, cy: int, w: int, h: int) -> bool:
    """Reparent ``wid`` as a strict (override-redirect) child of ``parent``.

    Used for the pywebview WebKitGTK window on Linux. The unmap → set
    override-redirect → reparent → map sequence is required so the WM never
    decorates or tracks the window as a top-level.
    """
    if not _have_xdotool() or not wid or not parent:
        return False

    _xdotool_unmap(wid)
    if not _xdotool_set_override_redirect(wid):
        return False
    if not _xdotool_reparent(wid, parent):
        return False
    _xdotool_move_resize(wid, cx, cy, w, h)
    if not _xdotool_map(wid):
        return False
    _xdotool_move_resize(wid, cx, cy, w, h)
    return True


def _xdotool_move_resize(wid: int, x: int, y: int, w: int, h: int) -> None:
    if not _have_xdotool() or not wid:
        return
    try:
        subprocess.check_call(
            ["xdotool",
             "windowmove", "--sync", str(wid), str(x), str(y),
             "windowsize", "--sync", str(wid), str(w), str(h)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
    except Exception as e:
        print(f"[MapScreen] xdotool move/resize wid={wid} failed: {e}")


def _xdotool_raise(wid: int) -> None:
    if not _have_xdotool() or not wid:
        return
    try:
        subprocess.check_call(
            ["xdotool", "windowraise", str(wid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
    except Exception:
        pass


# ─── MapHost (app-level singleton) ────────────────────────────────────────────
#
# Owns the pywebview subprocess for the entire app lifetime. The subprocess is
# launched at App.on_start() so map tiles preload while the user is on other
# screens. The X11/Win32 child window is reparented into the Kivy SDL2 window
# once and then simply repositioned (visible over MapView vs. clipped offscreen)
# whenever the user enters or leaves MapScreen — never re-launched, never
# detached. WebGL + tile cache state is preserved across screen changes.

# Hidden offscreen position (parent-relative). Negative coords are clipped to
# the parent's client area on both Win32 (WS_CHILD) and X11 (override-redirect),
# so the child stays mapped (no tear-down) but is invisible.
_HIDDEN_X = -20000
_HIDDEN_Y = -20000


def _read_window_id_file(path: Optional[str]) -> int:
    if not path or not os.path.exists(path):
        return 0
    try:
        raw = Path(path).read_text(encoding="ascii").strip()
        if not raw:
            return 0
        if raw.startswith("ERROR:"):
            print(f"[MapHost] host could not publish XID: {raw[6:]}")
            return -1
        wid = int(raw)
        return wid if wid > 0 else 0
    except Exception as exc:
        print(f"[MapHost] failed to read XID file {path}: {exc}")
        return 0


class MapHost:
    """Singleton owning the webview subprocess and its embedding state."""

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._html_path: Optional[str] = None
        self._handle_path: Optional[str] = None
        self._wid: int = 0  # HWND on Windows, X11 wid on Linux
        self._parent: int = 0
        self._embedded: bool = False
        self._embed_ticks: int = 0
        self._embed_event = None
        self._monitor_event = None
        self._sync_event = None
        self._attached_view: Optional["MapView"] = None
        # Initial offscreen launch size; chosen large so MapLibre lays out at a
        # realistic resolution and starts loading the right tile pyramid.
        self._init_w: int = 1280
        self._init_h: int = 660

    # ── Public API ────────────────────────────────────────────────────────

    def ensure_started(self) -> None:
        """Launch the webview subprocess offscreen. Idempotent."""
        if self._proc and self._proc.poll() is None:
            return

        if self._html_path is None:
            fd, path = tempfile.mkstemp(suffix=".html", prefix="snowlink_map_")
            os.close(fd)
            self._html_path = path
        if self._handle_path and os.path.exists(self._handle_path):
            try:
                os.unlink(self._handle_path)
            except Exception:
                pass
        fd, path = tempfile.mkstemp(suffix=".xid", prefix="snowlink_map_")
        os.close(fd)
        self._handle_path = path
        with open(self._html_path, "w", encoding="utf-8") as f:
            f.write(_build_map_html(BOOT_LAT, BOOT_LON))

        # Estimate a sensible initial size from the Kivy window dims (header
        # ~70dp + navbar ~70dp). Falls back to defaults pre-build.
        try:
            win_w = int(getattr(Window, "width", 1280) or 1280)
            win_h = int(getattr(Window, "height", 800) or 800)
            self._init_w = max(640, win_w)
            self._init_h = max(360, win_h - 140)
        except Exception:
            pass

        # Launch off-screen so the OS WM never paints the bare window before
        # we reparent it.
        self._proc = _launch_webview(
            self._html_path,
            _HIDDEN_X,
            _HIDDEN_Y,
            self._init_w,
            self._init_h,
            self._handle_path,
        )
        self._embedded = False
        self._wid = 0
        self._parent = 0
        self._embed_ticks = 0

        if not self._proc:
            print("[MapHost] webview subprocess failed to launch: "
                  + (_LAST_LAUNCH_ERROR or "unknown"))
            return

        Window.bind(
            on_resize=self._on_window_change,
            on_restore=self._on_window_change,
            on_maximize=self._on_window_change,
        )
        self._embed_event = Clock.schedule_interval(self._try_embed, 0.1)
        self._monitor_event = Clock.schedule_interval(self._monitor_proc, 2.0)
        # Lightweight sync: only does anything when an attached view exists.
        self._sync_event = Clock.schedule_interval(
            lambda _dt: self._sync_to_view(), 0.5
        )

    def attach(self, view: "MapView") -> None:
        """Bind the embedded child to ``view`` and move it over the view."""
        self._attached_view = view
        if self._embedded:
            self._sync_to_view()
            view.set_status("")
        else:
            view.set_status("Loading 3D map…")

    def detach(self) -> None:
        """Hide the embedded child but keep the subprocess alive."""
        self._attached_view = None
        if self._embedded:
            self._move_offscreen()

    def update_geometry(self, view: "MapView") -> None:
        """Re-sync the child to ``view`` if it is currently attached."""
        if self._attached_view is view and self._embedded:
            self._sync_to_view()

    def stop(self) -> None:
        """Terminate the subprocess and clean up. Called on app shutdown."""
        for ev_attr in ("_embed_event", "_monitor_event", "_sync_event"):
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
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self._wid = 0
        self._parent = 0
        self._embedded = False
        self._attached_view = None
        if self._html_path and os.path.exists(self._html_path):
            try:
                os.unlink(self._html_path)
            except Exception:
                pass
            self._html_path = None
        if self._handle_path and os.path.exists(self._handle_path):
            try:
                os.unlink(self._handle_path)
            except Exception:
                pass
            self._handle_path = None

    # ── Internals ─────────────────────────────────────────────────────────

    def _move_offscreen(self) -> None:
        if not self._wid:
            return
        w, h = self._init_w, self._init_h
        if _is_windows():
            _move_child(self._wid, _HIDDEN_X, _HIDDEN_Y, w, h)
        elif _is_linux():
            _xdotool_move_resize(self._wid, _HIDDEN_X, _HIDDEN_Y, w, h)

    def _sync_to_view(self) -> None:
        view = self._attached_view
        if not view or not self._embedded or not self._wid:
            return
        _, _, w, h, cx, cy = view._client_rect()
        if w <= 0 or h <= 0:
            return
        if _is_windows():
            _move_child(self._wid, cx, cy, w, h)
        elif _is_linux():
            _xdotool_move_resize(self._wid, cx, cy, w, h)

    def _on_window_change(self, *_):
        Clock.schedule_once(lambda _dt: self._sync_to_view(), 0)

    def _monitor_proc(self, _dt):
        if not self._proc:
            return False
        rc = self._proc.poll()
        if rc is None:
            return True
        tail = _host_output_tail(self._proc.pid)
        msg = "Map process exited.\n" + (tail or f"Exit code {rc}")
        print(f"[MapHost] webview process exited rc={rc}; {tail}")
        if self._attached_view:
            self._attached_view.set_status(msg)
        return False

    def _try_embed(self, _dt) -> Optional[bool]:
        if self._embedded:
            return False
        if not self._proc or self._proc.poll() is not None:
            print("[MapHost] subprocess gone before embed; stopping poll")
            if self._attached_view:
                self._attached_view.set_status(
                    "Map failed to start.\n" + (_LAST_LAUNCH_ERROR or "")
                )
            return False

        self._embed_ticks += 1
        if _is_windows():
            return self._try_embed_windows()
        if _is_linux():
            return self._try_embed_linux()
        return False  # unsupported platform

    def _try_embed_windows(self) -> Optional[bool]:
        hwnd = _find_toplevel_for_pid(self._proc.pid)
        if not hwnd:
            if self._embed_ticks in (1, 5, 20, 50, 100):
                print(f"[MapHost] embed tick {self._embed_ticks}: "
                      f"no top-level for PID {self._proc.pid} yet")
            if self._embed_ticks > _EMBED_TIMEOUT_TICKS:
                if self._attached_view:
                    self._attached_view.set_status(
                        "Map window not found.\n" + _host_output_tail(self._proc.pid)
                    )
                return False
            return None

        parent = _get_parent_hwnd()
        if not parent:
            print("[MapHost] could not resolve Kivy parent HWND")
            return False

        # Embed offscreen first; attach() will reposition.
        w, h = self._init_w, self._init_h
        if _reparent_into(hwnd, parent, _HIDDEN_X, _HIDDEN_Y, w, h):
            self._wid = hwnd
            self._parent = parent
            self._embedded = True
            print(f"[MapHost] embedded child HWND 0x{hwnd:x} into parent 0x{parent:x}")
            if self._attached_view:
                Clock.schedule_once(lambda _dt: self._sync_to_view(), 0.05)
            return False
        return None

    def _try_embed_linux(self) -> Optional[bool]:
        wid = _read_window_id_file(self._handle_path)
        if wid < 0:
            wid = _xdotool_search_pid(self._proc.pid)
        elif not wid and self._embed_ticks > 10:
            wid = _xdotool_search_pid(self._proc.pid)
        if not wid:
            if self._embed_ticks in (1, 5, 20, 50, 100):
                print(f"[MapHost] embed tick {self._embed_ticks}: "
                      f"no X11 window for PID {self._proc.pid} yet")
            if self._embed_ticks > _EMBED_TIMEOUT_TICKS:
                if self._attached_view:
                    self._attached_view.set_status(
                        "Map window not found.\n" + _host_output_tail(self._proc.pid)
                    )
                return False
            return None

        parent = _kivy_x11_xid()
        if not parent:
            return None

        w, h = self._init_w, self._init_h
        if _embed_x11_child(wid, parent, _HIDDEN_X, _HIDDEN_Y, w, h):
            self._wid = wid
            self._parent = parent
            self._embedded = True
            print(f"[MapHost] embedded webview wid={wid} into Kivy xid={parent}")
            # WebKitGTK on Pi often needs a geometry "wiggle" before it paints
            # correctly (replaces the manual maximize/minimize hack).
            for delay in (0.1, 0.3, 0.7, 1.5):
                Clock.schedule_once(self._geometry_wiggle, delay)
            if self._attached_view:
                Clock.schedule_once(lambda _dt: self._sync_to_view(), 0.05)
            return False

        if self._embed_ticks > _EMBED_TIMEOUT_TICKS:
            _xdotool_unmap(wid)
            tail = _host_output_tail(self._proc.pid)
            if self._attached_view:
                self._attached_view.set_status(
                    "Map could not be embedded in the Kivy window.\n" + tail
                )
            try:
                self._proc.terminate()
            except Exception:
                pass
            return False
        return None

    def _geometry_wiggle(self, _dt) -> None:
        """Force WebKitGTK to recompute its drawing area after reparent."""
        if not self._embedded or not self._wid:
            return
        view = self._attached_view
        if view:
            _, _, w, h, cx, cy = view._client_rect()
        else:
            w, h, cx, cy = self._init_w, self._init_h, _HIDDEN_X, _HIDDEN_Y
        if w <= 1 or h <= 1:
            return
        if _is_linux():
            _xdotool_move_resize(self._wid, cx, cy, max(2, w - 1), max(2, h - 1))
            _xdotool_move_resize(self._wid, cx, cy, w, h)
        elif _is_windows():
            _move_child(self._wid, cx, cy, max(2, w - 1), max(2, h - 1))
            _move_child(self._wid, cx, cy, w, h)


_MAP_HOST: Optional[MapHost] = None


def get_map_host() -> MapHost:
    """Return the process-wide MapHost singleton."""
    global _MAP_HOST
    if _MAP_HOST is None:
        _MAP_HOST = MapHost()
    return _MAP_HOST


# ─── MapView ──────────────────────────────────────────────────────────────────

class MapView(FloatLayout):
    """Placeholder widget over which the embedded webview is positioned.

    All subprocess lifecycle lives in :class:`MapHost`; this widget only
    publishes its geometry and forwards attach/detach when the screen is
    entered or left.
    """

    def __init__(self, **kw):
        super().__init__(**kw)

        # Dark background visible while the webview is loading or hidden.
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
        self._bg.pos = self.pos
        self._bg.size = self.size
        # Keep the embedded child window aligned with this widget.
        get_map_host().update_geometry(self)

    def _client_rect(self) -> tuple[int, int, int, int, int, int]:
        """Return (screen_x, screen_y, w, h, client_x, client_y)."""
        win_left = getattr(Window, "left", 0) or 0
        win_top  = getattr(Window, "top",  0) or 0
        abs_x, abs_y = self.to_window(0, 0, relative=False)
        cx = int(abs_x)
        cy = int(Window.height - abs_y - self.height)
        return win_left + cx, win_top + cy, int(self.width), int(self.height), cx, cy

    def set_status(self, text: str) -> None:
        self._lbl.text = text

    def attach(self) -> None:
        get_map_host().attach(self)

    def detach(self) -> None:
        get_map_host().detach()


# ─── MapScreen ────────────────────────────────────────────────────────────────

class MapScreen(Screen):
    """Map screen: header bar + MapLibre 3D map (embedded webview)."""

    def on_enter(self):
        # Give Kivy one frame to finish layout so widget.pos/size are correct
        Clock.schedule_once(self._init, 0.1)

    def _init(self, _dt):
        self.ids.map_view.attach()
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
        self.ids.map_view.detach()
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
