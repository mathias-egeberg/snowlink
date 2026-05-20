import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services.selftest import cellular_selftest


class CellularSelftestTest(unittest.TestCase):
    def test_is_connected_returns_false_when_fcntl_is_unavailable(self):
        with patch.object(cellular_selftest, 'fcntl', None):
            self.assertFalse(cellular_selftest.is_connected())

    def test_probe_detects_huawei_interface_from_sysfs_metadata(self):
        with TemporaryDirectory() as tmp:
            sys_net = Path(tmp)
            iface_dir = sys_net / 'enx001122334455'
            device_dir = iface_dir / 'device'
            device_dir.mkdir(parents=True)
            (device_dir / 'idVendor').write_text('12d1\n')
            (device_dir / 'idProduct').write_text('14db\n')
            (device_dir / 'manufacturer').write_text('HUAWEI\n')
            (device_dir / 'product').write_text('E3372-325\n')

            with patch.object(cellular_selftest, '_SYS_CLASS_NET', sys_net), \
                 patch.object(cellular_selftest, '_iface_ipv4', return_value='192.168.8.100'), \
                 patch.object(cellular_selftest, '_internet_reachable', return_value=True):
                result = cellular_selftest.probe()

        self.assertTrue(result.detected)
        self.assertTrue(result.is_huawei)
        self.assertTrue(result.ok)
        self.assertEqual(result.iface, 'enx001122334455')
        self.assertEqual(result.ipv4, '192.168.8.100')
        self.assertEqual(result.product, 'E3372-325')

    def test_probe_requires_reachable_internet_for_ok_status(self):
        with TemporaryDirectory() as tmp:
            sys_net = Path(tmp)
            iface_dir = sys_net / 'usb0'
            device_dir = iface_dir / 'device'
            device_dir.mkdir(parents=True)
            (device_dir / 'idVendor').write_text('12d1\n')

            with patch.object(cellular_selftest, '_SYS_CLASS_NET', sys_net), \
                 patch.object(cellular_selftest, '_iface_ipv4', return_value='192.168.8.100'), \
                 patch.object(cellular_selftest, '_internet_reachable', return_value=False):
                result = cellular_selftest.probe()

        self.assertTrue(result.detected)
        self.assertFalse(result.internet_reachable)
        self.assertFalse(result.ok)
        self.assertEqual(result.status_text, 'No internet')

    def test_probe_accepts_known_cellular_interface_without_huawei_metadata(self):
        with TemporaryDirectory() as tmp:
            sys_net = Path(tmp)
            (sys_net / 'wwan0').mkdir()

            with patch.object(cellular_selftest, '_SYS_CLASS_NET', sys_net), \
                 patch.object(cellular_selftest, '_iface_ipv4', return_value='10.1.2.3'), \
                 patch.object(cellular_selftest, '_internet_reachable', return_value=True):
                result = cellular_selftest.probe()

        self.assertTrue(result.detected)
        self.assertFalse(result.is_huawei)
        self.assertTrue(result.ok)
        self.assertEqual(result.iface, 'wwan0')


if __name__ == '__main__':
    unittest.main()