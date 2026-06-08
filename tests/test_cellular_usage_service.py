import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services.cellular_usage_service import CellularUsageService


class CellularUsageServiceTest(unittest.TestCase):
    def _write_stats(self, sys_net: Path, iface: str, rx: int, tx: int) -> None:
        stats_dir = sys_net / iface / 'statistics'
        stats_dir.mkdir(parents=True, exist_ok=True)
        (stats_dir / 'rx_bytes').write_text(str(rx))
        (stats_dir / 'tx_bytes').write_text(str(tx))

    def test_sample_accumulates_positive_interface_deltas(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage_path = root / 'cellular_usage.json'
            sys_net = root / 'net'
            self._write_stats(sys_net, 'wwan0', 1000, 200)

            with patch.object(CellularUsageService, '_usage_path', usage_path), \
                 patch.object(CellularUsageService, '_sys_class_net', sys_net):
                first = CellularUsageService.sample('wwan0')
                self._write_stats(sys_net, 'wwan0', 1250, 275)
                second = CellularUsageService.sample('wwan0')

        self.assertEqual(first['bytes_received'], 0)
        self.assertEqual(first['bytes_sent'], 0)
        self.assertEqual(second['bytes_received'], 250)
        self.assertEqual(second['bytes_sent'], 75)
        self.assertEqual(second['bytes_total'], 325)

    def test_reset_uses_current_kernel_counters_as_new_baseline(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage_path = root / 'cellular_usage.json'
            sys_net = root / 'net'
            self._write_stats(sys_net, 'usb0', 1000, 200)

            with patch.object(CellularUsageService, '_usage_path', usage_path), \
                 patch.object(CellularUsageService, '_sys_class_net', sys_net):
                CellularUsageService.sample('usb0')
                self._write_stats(sys_net, 'usb0', 1200, 260)
                CellularUsageService.sample('usb0')
                reset = CellularUsageService.reset('usb0')
                self._write_stats(sys_net, 'usb0', 1300, 300)
                after_reset = CellularUsageService.sample('usb0')

        self.assertEqual(reset['bytes_received'], 0)
        self.assertEqual(reset['bytes_sent'], 0)
        self.assertGreater(reset['reset_at_ms'], 0)
        self.assertEqual(after_reset['bytes_received'], 100)
        self.assertEqual(after_reset['bytes_sent'], 40)

    def test_snapshot_returns_zeroes_when_file_is_missing(self):
        with TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / 'cellular_usage.json'

            with patch.object(CellularUsageService, '_usage_path', usage_path):
                snapshot = CellularUsageService.get_snapshot()

        self.assertEqual(snapshot['bytes_received'], 0)
        self.assertEqual(snapshot['bytes_sent'], 0)
        self.assertEqual(snapshot['bytes_total'], 0)

    def test_snapshot_returns_zeroes_when_file_is_corrupt(self):
        with TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / 'cellular_usage.json'
            usage_path.write_text('{bad json')

            with patch.object(CellularUsageService, '_usage_path', usage_path):
                snapshot = CellularUsageService.get_snapshot()

        self.assertEqual(snapshot['bytes_received'], 0)
        self.assertEqual(snapshot['bytes_sent'], 0)
        self.assertEqual(snapshot['bytes_total'], 0)

    def test_sample_rejects_path_traversal_interface_name(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage_path = root / 'cellular_usage.json'
            sys_net = root / 'net'
            self._write_stats(sys_net, 'wwan0', 1000, 200)

            with patch.object(CellularUsageService, '_usage_path', usage_path), \
                 patch.object(CellularUsageService, '_sys_class_net', sys_net):
                snapshot = CellularUsageService.sample('../wwan0')

        self.assertEqual(snapshot['bytes_received'], 0)
        self.assertEqual(snapshot['bytes_sent'], 0)
        self.assertFalse(usage_path.exists())


if __name__ == '__main__':
    unittest.main()