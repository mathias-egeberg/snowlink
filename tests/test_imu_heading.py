import unittest
import struct

from backend.services.imu_heading import (
    apply_yaw_calibration,
    extract_ascii_yaws,
    extract_fdfc_yaws,
    extract_wit_yaws,
    normalize_yaw,
    parse_ascii_yaw,
    parse_wit_angle_packet,
    shortest_yaw_delta,
)


def _wit_angle_packet(yaw_deg: float) -> bytes:
    yaw_raw = int(yaw_deg / 180.0 * 32768.0)
    packet = bytearray([0x55, 0x53, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    packet[6:8] = yaw_raw.to_bytes(2, byteorder="little", signed=True)
    packet[10] = sum(packet[:10]) & 0xFF
    return bytes(packet)


def _fdfc_euler_packet(yaw_deg: float, pitch_deg: float = 0.0, roll_deg: float = 0.0) -> bytes:
    payload = bytearray(52)
    struct.pack_into(
        "<fff",
        payload,
        24,
        yaw_deg * 3.141592653589793 / 180.0,
        pitch_deg * 3.141592653589793 / 180.0,
        roll_deg * 3.141592653589793 / 180.0,
    )
    return bytes([0xFD, 0xFC, 0x41, 0x30]) + bytes(payload)


class ImuHeadingTests(unittest.TestCase):
    def test_normalize_yaw_wraps_to_compass_range(self):
        self.assertEqual(normalize_yaw(45.0), 45.0)
        self.assertEqual(normalize_yaw(360.0), 0.0)
        self.assertEqual(normalize_yaw(361.0), 1.0)
        self.assertEqual(normalize_yaw(-45.0), 315.0)
        self.assertAlmostEqual(normalize_yaw(720.5), 0.5)

    def test_apply_yaw_calibration_wraps_relative_to_zero(self):
        self.assertEqual(apply_yaw_calibration(90.0, 10.0), 80.0)
        self.assertEqual(apply_yaw_calibration(10.0, 30.0), 340.0)
        self.assertEqual(apply_yaw_calibration(0.0, 0.0), 0.0)

    def test_shortest_yaw_delta_handles_wraparound(self):
        self.assertEqual(shortest_yaw_delta(2.0, 358.0), 4.0)
        self.assertEqual(shortest_yaw_delta(90.0, 110.0), 20.0)

    def test_parse_wit_angle_packet_reads_yaw(self):
        self.assertAlmostEqual(parse_wit_angle_packet(_wit_angle_packet(90.0)), 90.0)
        self.assertAlmostEqual(parse_wit_angle_packet(_wit_angle_packet(-90.0)), 270.0)

    def test_extract_wit_yaws_keeps_incomplete_packet(self):
        packet = _wit_angle_packet(45.0)
        yaws, remaining = extract_wit_yaws(b"noise" + packet + packet[:4])

        self.assertEqual(len(yaws), 1)
        self.assertAlmostEqual(yaws[0], 45.0)
        self.assertEqual(remaining, packet[:4])

    def test_parse_ascii_yaw_supports_common_formats(self):
        self.assertEqual(parse_ascii_yaw("Yaw: -45.0"), 315.0)
        self.assertEqual(parse_ascii_yaw("heading=181.5"), 181.5)
        self.assertEqual(parse_ascii_yaw("Angle: 1.0,2.0,3.0"), 3.0)
        self.assertEqual(parse_ascii_yaw('{"yaw": 12.25}'), 12.25)
        self.assertEqual(parse_ascii_yaw("1,2,3,4,5,6,7,8,9,10,11,359.5"), 359.5)
        self.assertEqual(parse_ascii_yaw("$PASHR,123519,123.4,T,1.2,3.4"), 123.4)
        self.assertEqual(parse_ascii_yaw("$GPCHC,1,2,321.0,4,5,6,7,8,9,10,11"), 321.0)

    def test_extract_ascii_yaws_preserves_partial_line(self):
        yaws, pending = extract_ascii_yaws("", "Yaw: 10\nHeading: 2")

        self.assertEqual(yaws, (10.0,))
        self.assertEqual(pending, "Heading: 2")

    def test_extract_fdfc_yaws_reads_euler_frame(self):
        packet = _fdfc_euler_packet(270.0)
        yaws, remaining = extract_fdfc_yaws(b"noise" + packet + packet[:5])

        self.assertEqual(len(yaws), 1)
        self.assertAlmostEqual(yaws[0], 270.0, places=4)
        self.assertEqual(remaining, packet[:5])

    def test_extract_fdfc_yaws_preserves_truncated_euler_frame(self):
        packet = _fdfc_euler_packet(45.0)
        yaws, remaining = extract_fdfc_yaws(packet[:12])

        self.assertEqual(yaws, ())
        self.assertEqual(remaining, packet[:12])


if __name__ == "__main__":
    unittest.main()