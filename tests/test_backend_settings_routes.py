import asyncio
import unittest
from unittest.mock import patch

from backend.api import routes_settings


def _settings_data() -> dict:
    return {
        'ntrip': {
            'enabled': False,
            'host': '',
            'port': '2101',
            'mountpoint': '',
            'username': '',
            'password': '',
        },
        'map': {
            'marker_style': 'snowcat',
            'imu_heading_enabled': False,
            'imu_yaw_zero_deg': 123.4,
        },
    }


class BackendSettingsRoutesTest(unittest.TestCase):
    def test_partial_map_update_preserves_imu_yaw_zero(self):
        data = _settings_data()

        with patch.object(routes_settings.SettingsService, 'load', return_value=data), \
             patch.object(routes_settings.SettingsService, 'save'), \
             patch.object(routes_settings.state_manager, 'update'):
            result = asyncio.run(
                routes_settings.post_settings({'map': {'imu_heading_enabled': True}})
            )

        self.assertTrue(result['settings']['map']['imu_heading_enabled'])
        self.assertAlmostEqual(data['map']['imu_yaw_zero_deg'], 123.4)

    def test_explicit_null_map_update_clears_imu_yaw_zero(self):
        data = _settings_data()

        with patch.object(routes_settings.SettingsService, 'load', return_value=data), \
             patch.object(routes_settings.SettingsService, 'save'), \
             patch.object(routes_settings.state_manager, 'update'):
            result = asyncio.run(
                routes_settings.post_settings({'map': {'imu_yaw_zero_deg': None}})
            )

        self.assertIsNone(result['settings']['map']['imu_yaw_zero_deg'])


if __name__ == '__main__':
    unittest.main()