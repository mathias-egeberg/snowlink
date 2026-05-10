"""
USB IMU detection for WheelTech N100.

The WheelTech N100 uses a Silicon Labs CP2102 USB-to-UART chip:
  VID 0x10C4 / PID 0xEA60

Detection is by VID/PID so it works regardless of which /dev/ttyUSBx
port the OS assigns. A keyword fallback catches units that report a
different description string.
"""
from kivy.clock import Clock

# Silicon Labs CP2102 — the USB chip inside the WheelTech N100.
_WHEELTECH_VID = 0x10C4
_WHEELTECH_PID = 0xEA60

# Keyword fallback — matched against port description + hardware ID string.
_MATCH_KEYWORDS = ("wheeltech", "n100", "cp2102")


def _is_imu_port(port) -> bool:
    if port.vid == _WHEELTECH_VID and port.pid == _WHEELTECH_PID:
        return True
    combined = f"{port.description} {port.hwid}".lower()
    return any(kw in combined for kw in _MATCH_KEYWORDS)


def _detect_imu() -> bool:
    try:
        from serial.tools import list_ports
        return any(_is_imu_port(p) for p in list_ports.comports())
    except ImportError:
        return False
    except Exception:
        return False


class ImuService:
    """Polls USB ports and keeps DataService.imu_ok current."""

    POLL_INTERVAL = 3.0

    def __init__(self, data_service):
        self._ds = data_service
        self._poll(0)
        self._event = Clock.schedule_interval(self._poll, self.POLL_INTERVAL)

    def _poll(self, dt):
        connected = _detect_imu()
        if self._ds.imu_ok != connected:
            self._ds.imu_ok = connected

    def stop(self):
        if self._event:
            Clock.unschedule(self._event)
            self._event = None
