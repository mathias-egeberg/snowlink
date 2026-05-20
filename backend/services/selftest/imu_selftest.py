"""
IMU selftest – WheelTech N100 via Silicon Labs CP2102 USB-serial adapter.

Provides find_port() used by ImuService every 3 seconds to verify the
device is still physically connected.
"""
from __future__ import annotations

_VID = 0x10C4
_PID = 0xEA60
_KEYWORDS = ("wheeltech", "n100", "cp2102")


def find_port() -> str | None:
    """Return the serial port path for the IMU, or None if not found."""
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.vid == _VID and p.pid == _PID:
                return p.device
            if any(kw in f"{p.description} {p.hwid}".lower() for kw in _KEYWORDS):
                return p.device
    except Exception:
        pass
    return None
