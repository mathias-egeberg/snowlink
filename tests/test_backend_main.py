import unittest

from backend import main


class BackendMainConfigTest(unittest.TestCase):
    def test_websocket_broadcast_interval_supports_smooth_imu_updates(self):
        self.assertEqual(main.BROADCAST_INTERVAL_SECONDS, 0.05)


if __name__ == '__main__':
    unittest.main()