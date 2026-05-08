"""
Standalone pywebview host — launched as a subprocess by MapHost.

Usage: python map_webview_host.py <x> <y> <w> <h> <html_path>

  x, y       : ignored. Window is always created off-screen so the parent
               can reparent it as a strict child before any visible frame
               appears. Kept in argv for backward compatibility.
  w, h       : pixel dimensions of the initial map area (chosen so MapLibre
               lays out at a realistic resolution and starts loading the
               right tile pyramid).
  html_path  : path to the generated MapLibre HTML file

The Kivy parent process owns all embedding logic. This host process just
runs pywebview's event loop and exposes a stable, off-screen window that
the parent can claim by enumerating top-level windows by PID.
"""

import os
import platform
import sys

import webview


# Far off-screen — outside any plausible display geometry. Both Win32 and
# X11 honour negative top-level coordinates; the WM may briefly try to
# decorate it but the Kivy parent reparents it within ~100ms.
_OFFSCREEN = (-32000, -32000)


def main() -> None:
    if len(sys.argv) != 6:
        print(
            "Usage: map_webview_host.py x y w h html_path",
            file=sys.stderr,
        )
        sys.exit(1)

    w, h      = int(sys.argv[3]), int(sys.argv[4])
    html_path = sys.argv[5]

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    on_windows = platform.system() == "Windows"
    create_x, create_y = _OFFSCREEN

    our_title = f"SnowLink Map {os.getpid()}"

    print(
        f"[map_webview_host] creating window title={our_title!r} "
        f"platform={platform.system()} pos=({create_x},{create_y}) size=({w}x{h})"
    )

    try:
        webview.create_window(
            our_title,
            html=html,
            x=create_x, y=create_y, width=w, height=h,
            frameless=True,
            on_top=not on_windows,
            background_color="#0e1b30",
            easy_drag=False,
            min_size=(100, 100),
        )

        webview.start(debug=False)
    except Exception as exc:
        print(f"[map_webview_host] failed to start: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
