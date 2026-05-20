import unittest
from unittest.mock import patch

from backend.services.selftest import cellular_selftest


class CellularSelftestTest(unittest.TestCase):
    def test_is_connected_returns_false_when_fcntl_is_unavailable(self):
        with patch.object(cellular_selftest, 'fcntl', None):
            self.assertFalse(cellular_selftest.is_connected())


if __name__ == '__main__':
    unittest.main()