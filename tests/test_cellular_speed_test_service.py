import unittest
from unittest.mock import patch

from backend.services.cellular_speed_test_service import CellularSpeedTestService


class CellularSpeedTestServiceTest(unittest.TestCase):
    def test_run_returns_latency_download_and_upload_metrics(self):
        with patch.object(CellularSpeedTestService, '_measure_latency_ms', return_value=41.5) as latency, \
             patch.object(CellularSpeedTestService, '_measure_download_mbps', return_value=18.25) as download, \
             patch.object(CellularSpeedTestService, '_measure_upload_mbps', return_value=7.5) as upload:
            result = CellularSpeedTestService.run('usb0', '192.168.8.100')

        self.assertTrue(result.ok)
        self.assertEqual(result.iface, 'usb0')
        self.assertEqual(result.source_ip, '192.168.8.100')
        self.assertAlmostEqual(result.latency_ms, 41.5)
        self.assertAlmostEqual(result.download_mbps, 18.25)
        self.assertAlmostEqual(result.upload_mbps, 7.5)
        self.assertIsNone(result.error)
        latency.assert_called_once_with('usb0', '192.168.8.100')
        download.assert_called_once_with('192.168.8.100')
        upload.assert_called_once_with('192.168.8.100')

    def test_run_rejects_invalid_interface_name(self):
        result = CellularSpeedTestService.run('../usb0', '192.168.8.100')

        self.assertFalse(result.ok)
        self.assertEqual(result.download_mbps, 0.0)
        self.assertEqual(result.upload_mbps, 0.0)
        self.assertIn('Invalid cellular interface', result.error)

    def test_run_returns_failure_result_when_measurement_fails(self):
        with patch.object(CellularSpeedTestService, '_measure_latency_ms', side_effect=TimeoutError('timed out')), \
             patch.object(CellularSpeedTestService, '_measure_download_mbps') as download, \
             patch.object(CellularSpeedTestService, '_measure_upload_mbps') as upload:
            result = CellularSpeedTestService.run('usb0', '192.168.8.100')

        self.assertFalse(result.ok)
        self.assertEqual(result.latency_ms, 0.0)
        self.assertEqual(result.download_mbps, 0.0)
        self.assertEqual(result.upload_mbps, 0.0)
        self.assertIn('timed out', result.error)
        download.assert_not_called()
        upload.assert_not_called()


if __name__ == '__main__':
    unittest.main()