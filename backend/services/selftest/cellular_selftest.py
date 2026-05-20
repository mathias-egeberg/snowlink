"""
Cellular selftest – 5G/LTE modem connectivity check.

Checks whether any known mobile-data network interface is up and has an
IP address.  Common interface names on Raspberry Pi OS:
  wwan0   – ModemManager / QMI modems
  usb0    – CDC-Ethernet dongles
  ppp0    – PPP dial-up modems
  rmnet0  – Qualcomm RMNET
"""
from __future__ import annotations

import socket
import struct
import fcntl

_CELLULAR_IFACES = ("wwan0", "usb0", "ppp0", "rmnet0", "wwan1", "usb1")

# ioctl to read IPv4 address from an interface (Linux-only)
_SIOCGIFADDR = 0x8915


def _iface_has_ip(name: str) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ifreq = struct.pack("16sH14s", name.encode(), socket.AF_INET, b"\x00" * 14)
            res = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, ifreq)
            addr = socket.inet_ntoa(res[20:24])
            return addr not in ("0.0.0.0", "")
        finally:
            sock.close()
    except OSError:
        return False


def is_connected() -> bool:
    """True if a cellular interface is up and has an IPv4 address."""
    for iface in _CELLULAR_IFACES:
        if _iface_has_ip(iface):
            return True
    return False
