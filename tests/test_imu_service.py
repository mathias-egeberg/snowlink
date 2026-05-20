import unittest

from backend.services import imu_service


class ImuServiceConfigTest(unittest.TestCase):
    def test_selftest_interval_is_three_seconds(self):
        self.assertEqual(imu_service.SELFTEST_INTERVAL, 3.0)


if __name__ == '__main__':
    unittest.main()