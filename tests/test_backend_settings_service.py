"""Tests for the new backend settings service."""
import copy
import unittest

from backend.services.settings_service import SettingsService, _DEFAULTS


class BackendSettingsServiceTest(unittest.TestCase):
    def test_validate_sanitizes_bad_marker_style(self):
        data = copy.deepcopy({
            'ntrip': {'enabled': False, 'host': '', 'port': '2101',
                      'mountpoint': '', 'username': '', 'password': ''},
            'map':   {'marker_style': 'invalid', 'imu_heading_enabled': False,
                      'imu_yaw_zero_deg': None},
        })
        SettingsService._validate_loaded_data(data)
        self.assertEqual(data['map']['marker_style'], 'snowcat')

    def test_validate_sanitizes_bad_enabled_flag(self):
        data = copy.deepcopy({
            'ntrip': {'enabled': 'yes', 'host': 123, 'port': None,
                      'mountpoint': [], 'username': {}, 'password': False},
            'map':   {'marker_style': 'dot', 'imu_heading_enabled': 'true',
                      'imu_yaw_zero_deg': '370.5'},
        })
        SettingsService._validate_loaded_data(data)
        self.assertFalse(data['ntrip']['enabled'])
        self.assertEqual(data['ntrip']['host'], '')
        self.assertTrue(data['map']['imu_heading_enabled'])
        self.assertAlmostEqual(data['map']['imu_yaw_zero_deg'], 10.5)

    def test_default_imu_heading_enabled_is_true(self):
        self.assertTrue(_DEFAULTS['map']['imu_heading_enabled'])

    def test_validate_restores_missing_sections(self):
        data = {'ntrip': 'bad', 'map': 42}
        SettingsService._validate_loaded_data(data)
        self.assertIsInstance(data['ntrip'], dict)
        self.assertIsInstance(data['map'], dict)

    def test_valid_marker_styles(self):
        from backend.services.settings_service import VALID_MARKER_STYLES
        self.assertIn('snowcat', VALID_MARKER_STYLES)
        self.assertIn('dot', VALID_MARKER_STYLES)


if __name__ == '__main__':
    unittest.main()
