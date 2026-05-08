"""
Standalone pywebview host — launched as a subprocess by MapView.

Usage: python map_webview_host.py <parent_title> <x> <y> <w> <h> <html_path>

  parent_title : OS window title of the Kivy parent window ("SnowLink")
  x, y        : screen-absolute top-left of the map area (initial placement)
  w, h        : pixel dimensions of the map area
  html_path   : path to the generated MapLibre HTML file

Windows: the webview window is re-parented into the Kivy HWND via Win32
SetParent so it behaves like a native child control — no separate taskbar
entry, no window frame, no alt-tab entry.

Linux/Pi: runs frameless at the given screen position; in fullscreen kiosk
mode this is visually indistinguishable from being embedded.
"""

import os
import platform
import sys
import threading
import time

import webview


# ─── Windows embedding ────────────────────────────────────────────────────────

def _find_hwnd(title: str, timeout: float = 4.0) -> int:
    """Poll for a visible window with the given title; return HWND or 0."""
    import ctypes
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = user32.FindWindowW(None, title)
        if hwnd and user32.IsWindowVisible(hwnd):
            return hwnd
        time.sleep(0.05)
    return 0


def _embed_in_parent(
    our_title: str, parent_title: str,
    screen_x: int, screen_y: int,
    w: int, h: int,
) -> None:
    """Re-parent the webview HWND into the Kivy window's client area."""
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32

    GWL_STYLE           = -16
    GWL_EXSTYLE         = -20
    WS_CHILD            = 0x40000000
    WS_VISIBLE          = 0x10000000
    WS_OVERLAPPEDWINDOW = 0x00CF0000
    WS_EX_TOOLWINDOW    = 0x00000080
    WS_EX_APPWINDOW     = 0x00040000
    SWP_FRAMECHANGED    = 0x0020
    SWP_NOZORDER        = 0x0004
    SWP_SHOWWINDOW      = 0x0040

    our_hwnd    = _find_hwnd(our_title)
    parent_hwnd = _find_hwnd(parent_title)

    if not our_hwnd or not parent_hwnd:
        print(
            f"[MapWebview] HWND lookup failed: our={our_hwnd} parent={parent_hwnd}",
            file=sys.stderr,
        )
        return

    # Convert the screen position to parent client-area coordinates so the
    # webview lands over the correct map area after reparenting.
    pt = ctypes.wintypes.POINT(screen_x, screen_y)
    user32.ScreenToClient(parent_hwnd, ctypes.byref(pt))
    cx, cy = pt.x, pt.y

    # Replace overlapped-window style with child+visible.
    style = user32.GetWindowLongW(our_hwnd, GWL_STYLE)
    style = (style & ~WS_OVERLAPPEDWINDOW) | WS_CHILD | WS_VISIBLE
    user32.SetWindowLongW(our_hwnd, GWL_STYLE, style)

    # Remove from taskbar and alt-tab list.
    exstyle = user32.GetWindowLongW(our_hwnd, GWL_EXSTYLE)
    exstyle = (exstyle | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    user32.SetWindowLongW(our_hwnd, GWL_EXSTYLE, exstyle)

    # Attach to Kivy window and position within its client area.
    user32.SetParent(our_hwnd, parent_hwnd)
    user32.SetWindowPos(
        our_hwnd, 0, cx, cy, w, h,
        SWP_FRAMECHANGED | SWP_NOZORDER | SWP_SHOWWINDOW,
    )

    print(f"[MapWebview] Embedded at client ({cx},{cy}) {w}×{h}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) != 7:
        print(
            "Usage: map_webview_host.py parent_title x y w h html_path",
            file=sys.stderr,
        )
        sys.exit(1)

    parent_title = sys.argv[1]
    x, y         = int(sys.argv[2]), int(sys.argv[3])
    w, h         = int(sys.argv[4]), int(sys.argv[5])
    html_path    = sys.argv[6]

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Unique title lets us find this exact HWND even when multiple instances run.
    our_title = f"SnowLink Map {os.getpid()}"

    webview.create_window(
        our_title,
        html=html,
        x=x, y=y, width=w, height=h,
        frameless=True,
        background_color="#0e1b30",
        easy_drag=False,
        min_size=(100, 100),
    )

    if platform.system() == "Windows":
        # Start the embedding thread before the event loop so it can
        # poll for the HWND that the event loop is about to create.
        threading.Thread(
            target=_embed_in_parent,
            args=(our_title, parent_title, x, y, w, h),
            daemon=True,
        ).start()

    webview.start(debug=False)


if __name__ == "__main__":
    main()
