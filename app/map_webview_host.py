"""
Standalone pywebview host — launched as a subprocess by MapView.

Usage: python map_webview_host.py <x> <y> <w> <h> <html_path>

  x, y       : screen-absolute top-left for initial placement. Ignored on
               Windows (host creates window off-screen; the Kivy parent
               re-parents it into the Kivy HWND by PID).
  w, h       : pixel dimensions of the map area
  html_path  : path to the generated MapLibre HTML file

The Kivy parent process owns all embedding logic. This host process just
runs pywebview's event loop and exposes a stable, off-screen window that
the parent can claim by enumerating top-level windows by PID.
"""

import os
import platform
import sys

import webview


def main() -> None:
    if len(sys.argv) != 6:
        print(
            "Usage: map_webview_host.py x y w h html_path",
            file=sys.stderr,
        )
        sys.exit(1)

    x, y      = int(sys.argv[1]), int(sys.argv[2])
    w, h      = int(sys.argv[3]), int(sys.argv[4])
    html_path = sys.argv[5]

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Create off-screen on Windows so the HWND exists immediately and the
    # Kivy parent can reparent us before any visible top-level frame appears.
    on_windows = platform.system() == "Windows"
    create_x, create_y = (-32000, -32000) if on_windows else (x, y)

    our_title = f"SnowLink Map {os.getpid()}"

    webview.create_window(
        our_title,
        html=html,
        x=create_x, y=create_y, width=w, height=h,
        frameless=True,
        background_color="#0e1b30",
        easy_drag=False,
        min_size=(100, 100),
    )

    webview.start(debug=False)


if __name__ == "__main__":
    main()
