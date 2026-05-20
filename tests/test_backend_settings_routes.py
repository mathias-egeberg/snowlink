import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.api import routes_settings
from backend.services.cellular_usage_service import CellularUsagePersistenceError


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

    def test_reset_cellular_usage_updates_state_from_local_counter(self):
        usage = {
            'bytes_received': 0,
            'bytes_sent': 0,
            'bytes_total': 0,
            'reset_at_ms': 123,
            'updated_at_ms': 456,
        }
        with patch.object(routes_settings.CellularUsageService, 'reset', return_value=usage) as reset, \
             patch.object(routes_settings.state_manager, 'get_snapshot', return_value={'cellular_iface': 'usb0'}), \
             patch.object(routes_settings.state_manager, 'update') as update:
            result = asyncio.run(routes_settings.reset_cellular_usage())

        self.assertTrue(result['ok'])
        reset.assert_called_once_with('usb0')
        update.assert_called_once()

    def test_reset_cellular_usage_returns_http_error_when_persistence_fails(self):
        with patch.object(
            routes_settings.CellularUsageService,
            'reset',
            side_effect=CellularUsagePersistenceError('disk full'),
        ), patch.object(routes_settings.state_manager, 'get_snapshot', return_value={'cellular_iface': 'usb0'}):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(routes_settings.reset_cellular_usage())

        self.assertEqual(raised.exception.status_code, 500)


if __name__ == '__main__':
    unittest.main()