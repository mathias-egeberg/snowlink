import unittest

from backend.services.settings_service import SettingsService


class SettingsServiceTests(unittest.TestCase):
    def test_validate_loaded_data_sanitizes_map_settings(self):
        data = {
            'ntrip': {
                'enabled': 'yes',
                'host': 123,
                'port': None,
                'mountpoint': [],
                'username': {},
                'password': False,
            },
            'map': {
                'marker_style': "');alert(1);//",
                'imu_heading_enabled': 'true',
                'imu_yaw_zero_deg': '370.5',
            },
        }

        SettingsService._validate_loaded_data(data)

        self.assertEqual(data['map']['marker_style'], 'snowcat')
        self.assertTrue(data['map']['imu_heading_enabled'])
        self.assertEqual(data['map']['imu_yaw_zero_deg'], 10.5)
        self.assertFalse(data['ntrip']['enabled'])
        self.assertEqual(data['ntrip']['host'], '')
        self.assertEqual(data['ntrip']['port'], '2101')

    def test_validate_loaded_data_restores_missing_sections(self):
        data = {'ntrip': [], 'map': 'bad'}

        SettingsService._validate_loaded_data(data)

        self.assertIsInstance(data['ntrip'], dict)
        self.assertIsInstance(data['map'], dict)
        self.assertEqual(data['map']['marker_style'], 'snowcat')


if __name__ == "__main__":
    unittest.main()