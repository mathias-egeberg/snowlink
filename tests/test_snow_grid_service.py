"""Tests for the simulated snow-depth grid service."""
from __future__ import annotations

import json
import math
import unittest

from backend.services import snow_grid_service as sgs
from backend.services.snow_grid_service import (
    SnowGridService,
    SnowTile,
    cell_index_in_tile,
    lat_lon_to_local_meters,
    local_meters_to_lat_lon,
    tile_key_for_local,
)
from backend.state import StateManager


def _fresh_service() -> SnowGridService:
    # Reset the module-level singleton so each test gets a clean instance
    # with no running thread.
    SnowGridService._instance = None
    return SnowGridService()


class CoordinateHelperTests(unittest.TestCase):
    def test_round_trip_lat_lon_local(self):
        olat, olon = 61.123456, 8.123456
        for lat, lon in [(61.1235, 8.1238), (61.1230, 8.1230), (61.124, 8.122)]:
            x, y = lat_lon_to_local_meters(lat, lon, olat, olon)
            back_lat, back_lon = local_meters_to_lat_lon(x, y, olat, olon)
            self.assertAlmostEqual(lat, back_lat, places=6)
            self.assertAlmostEqual(lon, back_lon, places=6)

    def test_tile_key_for_positive_and_negative_coords(self):
        self.assertEqual(tile_key_for_local( 0.0,  0.0, 20.0), (0, 0))
        self.assertEqual(tile_key_for_local(19.9, 19.9, 20.0), (0, 0))
        self.assertEqual(tile_key_for_local(20.0, 20.0, 20.0), (1, 1))
        self.assertEqual(tile_key_for_local(45.3, 12.0, 20.0), (2, 0))
        # Negative coords must use floor semantics, not int truncation.
        self.assertEqual(tile_key_for_local(-0.1, -0.1, 20.0), (-1, -1))
        self.assertEqual(tile_key_for_local(-20.0, -20.0, 20.0), (-1, -1))
        self.assertEqual(tile_key_for_local(-20.1, -20.1, 20.0), (-2, -2))
        self.assertEqual(tile_key_for_local(-45.3, 12.0, 20.0), (-3, 0))

    def test_cell_index_inside_tile_for_negative_coords(self):
        # A point at local (-0.25, -0.25) belongs to tile (-1, -1).
        # The tile spans [-20, 0); the cell at local (-0.25) is the LAST
        # cell along that axis (cell index 39 with 0.5 m cells).
        cx, cy = cell_index_in_tile(-0.25, -0.25, 20.0, 0.5)
        self.assertEqual((cx, cy), (39, 39))

        cx, cy = cell_index_in_tile(0.25, 0.25, 20.0, 0.5)
        self.assertEqual((cx, cy), (0, 0))

        cx, cy = cell_index_in_tile(19.75, 19.75, 20.0, 0.5)
        self.assertEqual((cx, cy), (39, 39))


class SnowTileMessageTests(unittest.TestCase):
    def test_to_message_dict_shape(self):
        depths = [None, 0.84, 0.86, 0.91]
        confidence = [0.0, 0.72, 0.75, 0.80]
        tile = SnowTile(
            tile_x=1204, tile_y=883,
            width=2, height=2,
            depths=depths, confidence=confidence,
            updated_at=123456.7,
        )
        msg = tile.to_message_dict(61.123456, 8.123456, 20.0, 0.5)
        self.assertEqual(msg["type"], "snow_tile_update")
        self.assertEqual(msg["origin_lat"], 61.123456)
        self.assertEqual(msg["projection"], "local_enu")
        self.assertEqual(msg["tile_x"], 1204)
        self.assertEqual(msg["tile_y"], 883)
        self.assertEqual(msg["tile_size_m"], 20.0)
        self.assertEqual(msg["cell_size_m"], 0.5)
        self.assertEqual(msg["width"], 2)
        self.assertEqual(msg["height"], 2)
        self.assertEqual(msg["depth_unit"], "m")
        self.assertEqual(msg["depths"], depths)
        self.assertEqual(msg["confidence"], confidence)
        self.assertEqual(msg["updated_at"], 123456.7)

    def test_message_is_json_serialisable(self):
        tile = SnowTile(
            tile_x=0, tile_y=0,
            width=2, height=2,
            depths=[None, 0.1, 0.2, 0.3],
            confidence=[0.0, 0.5, 0.5, 0.5],
            updated_at=1.0,
        )
        msg = tile.to_message_dict(61.0, 8.0, 20.0, 0.5)
        text = json.dumps(msg)  # must not raise
        parsed = json.loads(text)
        self.assertEqual(parsed["tile_x"], 0)
        # JSON null round-trips back to None.
        self.assertIsNone(parsed["depths"][0])


class SnowGridServiceTests(unittest.TestCase):
    def setUp(self):
        # Ensure each test starts with a fresh state and singleton.
        sgs.state_manager = StateManager()
        # Cross-module patch so tick() sees our state.
        # (snow_grid_service caches the symbol at import time.)
        sgs.state_manager.update(gps_lat=61.0, gps_lon=8.0, gps_ok=True)
        self.svc = _fresh_service()

    def tearDown(self):
        SnowGridService._instance = None

    def test_default_grid_dimensions_are_40x40(self):
        cfg = self.svc.get_config()
        # 20 / 0.5 = 40 cells per axis.
        self.assertEqual(cfg["width"], 40)
        self.assertEqual(cfg["height"], 40)
        self.assertEqual(cfg["tile_size_m"], 20.0)
        self.assertEqual(cfg["cell_size_m"], 0.5)

    def test_tick_populates_active_tiles_and_pending(self):
        self.svc.tick()
        cfg = self.svc.get_config()
        # Radius 1 -> 3x3 = 9 active tiles.
        self.assertEqual(cfg["active_tile_count"], 9)
        self.assertTrue(cfg["has_origin"])
        self.assertAlmostEqual(cfg["origin_lat"], 61.0)
        self.assertAlmostEqual(cfg["origin_lon"], 8.0)

        msgs = self.svc.collect_pending_messages()
        self.assertEqual(len(msgs), 9)
        for m in msgs:
            self.assertEqual(m["type"], "snow_tile_update")
            self.assertEqual(len(m["depths"]), 40 * 40)
            self.assertEqual(len(m["confidence"]), 40 * 40)
            # Every depth is None or a finite float in [0, 2.2].
            for d in m["depths"]:
                self.assertTrue(d is None or (isinstance(d, float) and 0.0 <= d <= 2.2))
            for c in m["confidence"]:
                self.assertTrue(isinstance(c, float) and 0.0 <= c <= 1.0)

    def test_simulated_tiles_have_some_measured_cells(self):
        self.svc.tick()
        msgs = self.svc.get_active_tile_messages()
        # The centre tile around the vehicle must contain measured cells.
        any_measured = False
        for m in msgs:
            if any(v is not None for v in m["depths"]):
                any_measured = True
                break
        self.assertTrue(any_measured, "simulation produced no measured cells")

    def test_collect_pending_clears_after_call(self):
        self.svc.tick()
        first = self.svc.collect_pending_messages()
        self.assertGreater(len(first), 0)
        again = self.svc.collect_pending_messages()
        self.assertEqual(again, [])

    def test_disabled_does_not_crash_and_returns_no_messages(self):
        # Monkey-patch _raw_config to simulate `enabled=False`.
        original = self.svc._raw_config
        def disabled_cfg():
            cfg = original()
            cfg["enabled"] = False
            return cfg
        self.svc._raw_config = disabled_cfg

        self.svc.tick()  # must not raise
        self.assertEqual(self.svc.collect_pending_messages(), [])
        cfg = self.svc.get_config()
        self.assertFalse(cfg["enabled"])

    def test_simulation_off_still_assigns_active_keys_without_data(self):
        original = self.svc._raw_config
        def sim_off_cfg():
            cfg = original()
            cfg["simulation_enabled"] = False
            return cfg
        self.svc._raw_config = sim_off_cfg

        self.svc.tick()
        cfg = self.svc.get_config()
        # Active set still reflects vehicle position …
        self.assertEqual(cfg["active_tile_count"], 9)
        # … but no tiles are populated, so messages are empty.
        self.assertEqual(self.svc.get_active_tile_messages(), [])

    def test_reset_simulation_clears_state(self):
        self.svc.tick()
        self.svc.reset_simulation()
        cfg = self.svc.get_config()
        self.assertFalse(cfg["has_origin"])
        self.assertEqual(cfg["active_tile_count"], 0)
        self.assertEqual(cfg["cached_tile_count"], 0)
        self.assertEqual(self.svc.get_active_tile_messages(), [])

    def test_origin_not_set_without_gps(self):
        sgs.state_manager = StateManager()
        sgs.state_manager.update(gps_lat=None, gps_lon=None, gps_ok=False)
        svc = _fresh_service()
        svc.tick()
        cfg = svc.get_config()
        self.assertFalse(cfg["has_origin"])
        self.assertEqual(svc.collect_pending_messages(), [])

    def test_tile_origin_geometry_round_trip(self):
        """A tile's reported (tile_x, tile_y) and its origin lat/lon let the
        frontend compute cell positions; verify they match the backend's
        coordinate helpers."""
        self.svc.tick()
        cfg = self.svc.get_config()
        msgs = self.svc.get_active_tile_messages()
        self.assertGreater(len(msgs), 0)

        m = msgs[0]
        olat, olon = cfg["origin_lat"], cfg["origin_lon"]
        # First cell origin in local metres.
        x0 = m["tile_x"] * m["tile_size_m"]
        y0 = m["tile_y"] * m["tile_size_m"]
        lat, lon = local_meters_to_lat_lon(x0, y0, olat, olon)
        # And back again.
        back_x, back_y = lat_lon_to_local_meters(lat, lon, olat, olon)
        self.assertAlmostEqual(back_x, x0, places=3)
        self.assertAlmostEqual(back_y, y0, places=3)


if __name__ == "__main__":
    unittest.main()
