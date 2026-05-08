"""
Standalone pywebview host — launched as a subprocess by MapHost.

Usage: python map_webview_host.py <x> <y> <w> <h> <html_path> [handle_path]

    x, y       : ignored. Window is always created mapped but far off-screen so
                             WebKitGTK can render while the parent reparents it as a strict
                             child. Kept in argv for backward compatibility.
  w, h       : pixel dimensions of the initial map area (chosen so MapLibre
               lays out at a realistic resolution and starts loading the
               right tile pyramid).
    html_path  : path to the generated MapLibre HTML file
    handle_path: optional path where the Linux X11 window id is written once the
                             off-screen GTK window has been realized

The Kivy parent process owns all embedding logic. This host process just
runs pywebview's event loop and exposes a stable, off-screen window that
the parent can claim by enumerating top-level windows by PID.
"""

import os
import platform
import sys
import time
from typing import Any

if platform.system() == "Linux":
    os.environ["GDK_BACKEND"] = "x11"

import webview


# Far off-screen — outside any plausible display geometry. Both Win32 and
# X11 honour negative top-level coordinates; the WM may briefly try to
# decorate it but the Kivy parent reparents it within ~100ms.
_OFFSCREEN = (-32000, -32000)


def _get_linux_xid(gdk_window: Any) -> int:
    get_xid = getattr(gdk_window, "get_xid", None)
    if callable(get_xid):
        return int(get_xid())

    try:
        import gi

        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11

        return int(GdkX11.X11Window.get_xid(gdk_window))
    except Exception as exc:
        raise RuntimeError(
            "GTK is not exposing an X11 window id. "
            "SnowLink map embedding requires X11/XWayland. "
            f"DISPLAY={os.environ.get('DISPLAY')!r}, "
            f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE')!r}, "
            f"GDK_BACKEND={os.environ.get('GDK_BACKEND')!r}."
        ) from exc


def _write_linux_xid(window: Any, handle_path: str | None) -> None:
    if not handle_path or platform.system() != "Linux":
        return

    try:
        xid = 0
        for _attempt in range(40):
            native = getattr(window, "native", None)
            if native is not None:
                native.realize()
                native.move(*_OFFSCREEN)
                native.set_skip_taskbar_hint(True)
                native.set_skip_pager_hint(True)
                gdk_window = native.get_window()
                if gdk_window is not None:
                    xid = _get_linux_xid(gdk_window)
                    break
            time.sleep(0.05)

        if xid <= 0:
            raise RuntimeError("GTK backend did not expose an X11 window id")

        with open(handle_path, "w", encoding="ascii") as f:
            f.write(str(xid))
        print(f"[map_webview_host] linux_xid={xid}")
    except Exception as exc:
        try:
            with open(handle_path, "w", encoding="ascii") as f:
                f.write(f"ERROR:{exc}")
        except Exception:
            pass
        print(f"[map_webview_host] failed to publish linux xid: {exc}", file=sys.stderr)


def main() -> None:
    if len(sys.argv) not in (6, 7):
        print(
            "Usage: map_webview_host.py x y w h html_path [handle_path]",
            file=sys.stderr,
        )
        sys.exit(1)

    w, h      = int(sys.argv[3]), int(sys.argv[4])
    html_path = sys.argv[5]
    handle_path = sys.argv[6] if len(sys.argv) == 7 else None

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    on_windows = platform.system() == "Windows"
    create_x, create_y = _OFFSCREEN

    our_title = f"SnowLink Map {os.getpid()}"

    print(
        f"[map_webview_host] creating window title={our_title!r} "
        f"platform={platform.system()} pos=({create_x},{create_y}) size=({w}x{h}) "
        f"display={os.environ.get('DISPLAY')!r} "
        f"session={os.environ.get('XDG_SESSION_TYPE')!r} "
        f"gdk={os.environ.get('GDK_BACKEND')!r}"
    )

    try:
        window = webview.create_window(
            our_title,
            html=html,
            x=create_x, y=create_y, width=w, height=h,
            hidden=False,
            frameless=True,
            on_top=False,
            resizable=False,
            background_color="#0e1b30",
            easy_drag=False,
            draggable=False,
            shadow=False,
            focus=False,
            min_size=(100, 100),
        )

        webview.start(_write_linux_xid, args=(window, handle_path), debug=False)
    except Exception as exc:
        print(f"[map_webview_host] failed to start: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
