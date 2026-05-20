import struct
import threading
import unittest
from unittest.mock import patch

from backend.services import imu_service
from backend.services.imu_service import ImuService


def _fdfc_euler_packet(yaw_deg: float) -> bytes:
    payload = bytearray(52)
    struct.pack_into(
        "<fff",
        payload,
        24,
        yaw_deg * 3.141592653589793 / 180.0,
        0.0,
        0.0,
    )
    return bytes([0xFD, 0xFC, 0x41, 0x30]) + bytes(payload)


class _RecordingDataService:
    def __init__(self) -> None:
        self.yaws: list[float] = []
        self.connections: list[bool] = []
        self.clear_count = 0

    def set_imu_yaw(self, yaw_deg: float) -> None:
        self.yaws.append(yaw_deg)

    def clear_imu_yaw(self) -> None:
        self.clear_count += 1

    def set_imu_connection(self, connected: bool) -> None:
        self.connections.append(connected)


class _FakeSerial:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])
        self.is_open = True
        self.baudrate = 921600
        self.closed = False

    def read(self, _size: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        self.is_open = False
        return b""

    def close(self) -> None:
        self.closed = True
        self.is_open = False


def _service_for_test(data_service: _RecordingDataService, serial) -> ImuService:
    service = object.__new__(ImuService)
    service._ds = data_service
    service._lock = threading.Lock()
    service._serial = serial
    service._active = True
    service._baud_index = 0
    service._last_good_baud = None
    return service


class ImuServiceConfigTest(unittest.TestCase):
    def test_selftest_interval_is_three_seconds(self):
        self.assertEqual(imu_service.SELFTEST_INTERVAL, 3.0)

    def test_reader_loads_fdfc_yaw_into_data_service(self):
        data_service = _RecordingDataService()
        serial = _FakeSerial([_fdfc_euler_packet(135.0)])
        service = _service_for_test(data_service, serial)

        service._reader(serial)

        self.assertEqual(len(data_service.yaws), 1)
        self.assertAlmostEqual(data_service.yaws[0], 135.0, places=4)
        self.assertEqual(service._last_good_baud, 921600)
        self.assertTrue(serial.closed)

    def test_tick_marks_disconnected_when_usb_port_disappears(self):
        data_service = _RecordingDataService()
        serial = _FakeSerial()
        service = _service_for_test(data_service, serial)

        with patch.object(imu_service.imu_selftest, 'find_port', return_value=None):
            service._tick()

        self.assertTrue(serial.closed)
        self.assertEqual(data_service.connections, [False])


if __name__ == '__main__':
    unittest.main()