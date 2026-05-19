"""Tests for the new backend state model and StateManager."""
import unittest
from backend.state import AppState, StateManager, VALID_DEVICE_KEYS


class AppStateDefaultsTest(unittest.TestCase):
    def test_initial_system_status_is_idle(self):
        s = AppState()
        self.assertEqual(s.system_status, "idle")

    def test_sensor_light_on_by_default(self):
        s = AppState()
        self.assertTrue(s.sensor_light_on)

    def test_gps_fields_default(self):
        s = AppState()
        self.assertFalse(s.gps_ok)
        self.assertEqual(s.gps_status_text, "No Fix")


class StateManagerTest(unittest.TestCase):
    def setUp(self):
        self.mgr = StateManager()

    def test_get_snapshot_returns_dict(self):
        snap = self.mgr.get_snapshot()
        self.assertIsInstance(snap, dict)
        self.assertIn('temperature_outside', snap)
        self.assertIn('system_status', snap)

    def test_update_single_field(self):
        self.mgr.update(temperature_outside=5.5)
        self.assertEqual(self.mgr.get_snapshot()['temperature_outside'], 5.5)

    def test_update_ignores_unknown_keys(self):
        # Should not raise.
        self.mgr.update(nonexistent_field=99)

    def test_recompute_derived_active_devices_and_power(self):
        self.mgr.update(heat_roof_on=True, heat_gutter_on=False,
                        pump_on=False, sensor_light_on=False)
        snap = self.mgr.get_snapshot()
        self.assertEqual(snap['active_devices'], 1)
        self.assertEqual(snap['power_watts'], 1200.0)
        self.assertEqual(snap['system_status'], 'active')

    def test_recompute_sensor_light_only_gives_standby(self):
        self.mgr.update(heat_roof_on=False, heat_gutter_on=False,
                        pump_on=False, sensor_light_on=True)
        snap = self.mgr.get_snapshot()
        self.assertEqual(snap['system_status'], 'standby')
        self.assertEqual(snap['power_watts'], 15.0)

    def test_recompute_no_devices_gives_idle(self):
        self.mgr.update(heat_roof_on=False, heat_gutter_on=False,
                        pump_on=False, sensor_light_on=False)
        snap = self.mgr.get_snapshot()
        self.assertEqual(snap['system_status'], 'idle')
        self.assertEqual(snap['power_watts'], 0.0)

    def test_toggle_device_flips_state(self):
        # Start with all off
        self.mgr.update(heat_roof_on=False)
        ok = self.mgr.toggle_device('heat_roof_on')
        self.assertTrue(ok)
        self.assertTrue(self.mgr.get_snapshot()['heat_roof_on'])

        self.mgr.toggle_device('heat_roof_on')
        self.assertFalse(self.mgr.get_snapshot()['heat_roof_on'])

    def test_toggle_device_writes_last_event(self):
        self.mgr.update(heat_roof_on=False)
        self.mgr.toggle_device('heat_roof_on')
        self.assertIn('Roof heater', self.mgr.get_snapshot()['last_event'])

    def test_toggle_device_rejects_invalid_key(self):
        ok = self.mgr.toggle_device('nonexistent')
        self.assertFalse(ok)

    def test_valid_device_keys_constant(self):
        expected = {'heat_roof_on', 'heat_gutter_on', 'pump_on', 'sensor_light_on'}
        self.assertEqual(VALID_DEVICE_KEYS, expected)


if __name__ == '__main__':
    unittest.main()
