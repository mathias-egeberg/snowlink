import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.api import routes_settings
from backend.services.cellular_speed_test_service import CellularSpeedTestResult
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

    def test_run_cellular_speed_test_updates_state_and_usage(self):
        speed_result = CellularSpeedTestResult(
            ok=True,
            iface='usb0',
            source_ip='192.168.8.100',
            latency_ms=42.5,
            download_mbps=18.2,
            upload_mbps=7.4,
            tested_at_ms=789,
        )
        usage = {
            'bytes_received': 2048,
            'bytes_sent': 1024,
            'bytes_total': 3072,
            'reset_at_ms': 123,
            'updated_at_ms': 456,
        }
        final_snapshot = {
            'cellular_iface': 'usb0',
            'cellular_ipv4': '192.168.8.100',
            'cellular_speed_test_download_mbps': 18.2,
        }
        with patch.object(
            routes_settings.state_manager,
            'get_snapshot',
            side_effect=[
                {'cellular_iface': 'usb0', 'cellular_ipv4': '192.168.8.100'},
                final_snapshot,
            ],
        ), patch.object(routes_settings.state_manager, 'update') as update, \
             patch.object(routes_settings.CellularSpeedTestService, 'run', return_value=speed_result) as run, \
             patch.object(routes_settings.CellularUsageService, 'sample', return_value=usage):
            result = asyncio.run(routes_settings.run_cellular_speed_test())

        self.assertTrue(result['ok'])
        self.assertEqual(result['speed_test']['download_mbps'], 18.2)
        self.assertEqual(result['state'], final_snapshot)
        run.assert_called_once_with('usb0', '192.168.8.100')
        self.assertGreaterEqual(update.call_count, 2)
        final_update = update.call_args_list[-1].kwargs
        self.assertEqual(final_update['cellular_speed_test_status'], 'Complete')
        self.assertEqual(final_update['cellular_bytes_total'], 3072)

    def test_run_cellular_speed_test_requires_cellular_ip(self):
        with patch.object(
            routes_settings.state_manager,
            'get_snapshot',
            return_value={'cellular_iface': '', 'cellular_ipv4': ''},
        ), patch.object(routes_settings.cellular_selftest, 'probe', return_value=routes_settings.cellular_selftest.CellularProbeResult(detected=False)):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(routes_settings.run_cellular_speed_test())

        self.assertEqual(raised.exception.status_code, 409)

    def test_run_cellular_speed_test_records_failure_result(self):
        speed_result = CellularSpeedTestResult(
            ok=False,
            iface='usb0',
            source_ip='192.168.8.100',
            error='timed out',
            tested_at_ms=789,
        )
        usage = {
            'bytes_received': 10,
            'bytes_sent': 5,
            'bytes_total': 15,
            'reset_at_ms': 123,
            'updated_at_ms': 456,
        }
        with patch.object(
            routes_settings.state_manager,
            'get_snapshot',
            side_effect=[
                {'cellular_iface': 'usb0', 'cellular_ipv4': '192.168.8.100'},
                {'cellular_speed_test_status': 'Failed'},
            ],
        ), patch.object(routes_settings.state_manager, 'update') as update, \
             patch.object(routes_settings.CellularSpeedTestService, 'run', return_value=speed_result), \
             patch.object(routes_settings.CellularUsageService, 'sample', return_value=usage):
            result = asyncio.run(routes_settings.run_cellular_speed_test())

        self.assertFalse(result['ok'])
        final_update = update.call_args_list[-1].kwargs
        self.assertFalse(final_update['cellular_speed_test_running'])
        self.assertEqual(final_update['cellular_speed_test_status'], 'Failed')
        self.assertEqual(final_update['cellular_speed_test_error'], 'timed out')


if __name__ == '__main__':
    unittest.main()