"""
GPS selftest – u-blox ZED-F9P RTK module.

Provides find_port() used by GpsService every 3 seconds to verify the
device is still physically connected.
"""
from __future__ import annotations

_VID = 0x1546   # U-Blox AG
_PID = 0x01A9   # ZED-F9P


def find_port() -> str | None:
    """Return the serial port path for the GPS module, or None if not found."""
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.vid == _VID and p.pid == _PID:
                return p.device
    except Exception:
        pass
    return None
