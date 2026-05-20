import asyncio
import unittest

from fastapi import Response

from backend.api.routes_state import get_state


class BackendStateRoutesTest(unittest.TestCase):
    def test_get_state_disables_cache(self):
        response = Response()

        snapshot = asyncio.run(get_state(response))

        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('imu_yaw_deg', snapshot)
        self.assertIn('cellular_status_text', snapshot)
        self.assertIn('cellular_bytes_total', snapshot)
        self.assertIn('cellular_usage_reset_at_ms', snapshot)
        self.assertIn('cellular_speed_test_status', snapshot)
        self.assertIn('cellular_signal_quality_pct', snapshot)


if __name__ == '__main__':
    unittest.main()