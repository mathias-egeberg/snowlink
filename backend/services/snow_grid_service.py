"""
SnowGridService — backend simulated snow-depth data tiles.

Maintains a tile cache of measured snow-depth/confidence around the
current vehicle position.  Tiles use a local ENU coordinate system
(metric, origin = first GPS position or fallback), NOT z/x/y web-map
tiles.

Public surface:
    SnowGridService.get()                # singleton (auto-starts sim thread)
    get_config() -> dict                 # current effective config + origin
    get_active_tile_messages() -> list   # all tiles currently in active set
    collect_pending_messages() -> list   # tiles updated since last collect
    reset_simulation() -> None
    set_origin(lat, lon) -> None         # force origin (optional helper)

Tile message shape (also broadcast over /ws/state):
    {
      "type": "snow_tile_update",
      "origin_lat": ..., "origin_lon": ..., "projection": "local_enu",
      "tile_x": int, "tile_y": int,
      "tile_size_m": 20.0, "cell_size_m": 0.5,
      "width": 40, "height": 40,
      "depth_unit": "m",
      "depths": [null, 0.84, ...],      # len = width*height (row-major, y-major)
      "confidence": [0, 0.72, ...],     # len = width*height
      "updated_at": <unix seconds>
    }

The simulation is intentionally simple — smooth sinusoidal field plus
a "driven swath" depression along the recent vehicle path.  Real LiDAR
ingestion will replace ``_simulate_tile`` later.
"""
from __future__ import annotations

import logging
import math
import random
import threading
import time
from dataclasses import dataclass
from typing import Optional

from backend.services.settings_service import SettingsService
from backend.state import state_manager

log = logging.getLogger("snowlink.snowgrid")

# ── Defaults (used when settings.json has no snow_grid block) ─────────
DEFAULT_TILE_SIZE_M = 20.0
DEFAULT_CELL_SIZE_M = 0.5
DEFAULT_ACTIVE_RADIUS_TILES = 1   # 1 → 3×3, 2 → 5×5
DEFAULT_ENABLED = True
DEFAULT_SIMULATION_ENABLED = True

# Distance (m) from the snowcat within which simulated cells get a value.
# Cells outside this radius stay unmeasured (None).
_SIMULATED_VEHICLE_REACH_M = 28.0

# Radius (m) around the recent vehicle path that gets compacted snow.
_PATH_SWATH_M = 1.6

# Approximate ENU conversion constants.
_METERS_PER_DEG_LAT = 111320.0


def meters_per_deg_lon(lat_deg: float) -> float:
    return _METERS_PER_DEG_LAT * math.cos(math.radians(lat_deg))


def lat_lon_to_local_meters(
    lat: float, lon: float, origin_lat: float, origin_lon: float
) -> tuple[float, float]:
    """Approximate ENU: return ``(east_m, north_m)`` from ``origin``."""
    east  = (lon - origin_lon) * meters_per_deg_lon(origin_lat)
    north = (lat - origin_lat) * _METERS_PER_DEG_LAT
    return east, north


def local_meters_to_lat_lon(
    east_m: float, north_m: float, origin_lat: float, origin_lon: float
) -> tuple[float, float]:
    lat = origin_lat + north_m / _METERS_PER_DEG_LAT
    mpl = meters_per_deg_lon(origin_lat)
    lon = origin_lon + (east_m / mpl if mpl else 0.0)
    return lat, lon


def tile_key_for_local(
    x_m: float, y_m: float, tile_size_m: float
) -> tuple[int, int]:
    """Return ``(tile_x, tile_y)`` for a local-metric position."""
    return (
        int(math.floor(x_m / tile_size_m)),
        int(math.floor(y_m / tile_size_m)),
    )


def cell_index_in_tile(
    x_m: float, y_m: float, tile_size_m: float, cell_size_m: float
) -> tuple[int, int]:
    """Return ``(cell_x, cell_y)`` inside the tile that owns the position."""
    tx, ty = tile_key_for_local(x_m, y_m, tile_size_m)
    cx = int(math.floor((x_m - tx * tile_size_m) / cell_size_m))
    cy = int(math.floor((y_m - ty * tile_size_m) / cell_size_m))
    return cx, cy


# ── Tile model ────────────────────────────────────────────────────────

@dataclass
class SnowTile:
    tile_x: int
    tile_y: int
    width: int                 # cells in the x direction
    height: int                # cells in the y direction
    depths: list               # list[Optional[float]], len = width * height
    confidence: list           # list[float],            len = width * height
    updated_at: float

    def to_message_dict(
        self,
        origin_lat: float,
        origin_lon: float,
        tile_size_m: float,
        cell_size_m: float,
    ) -> dict:
        return {
            "type": "snow_tile_update",
            "origin_lat": origin_lat,
            "origin_lon": origin_lon,
            "projection": "local_enu",
            "tile_x": self.tile_x,
            "tile_y": self.tile_y,
            "tile_size_m": tile_size_m,
            "cell_size_m": cell_size_m,
            "width": self.width,
            "height": self.height,
            "depth_unit": "m",
            "depths": self.depths,
            "confidence": self.confidence,
            "updated_at": self.updated_at,
        }


# ── Service ───────────────────────────────────────────────────────────

class SnowGridService:
    _instance: Optional["SnowGridService"] = None

    @classmethod
    def get(cls) -> "SnowGridService":
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.start()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tiles: dict[tuple[int, int], SnowTile] = {}
        self._active_keys: set[tuple[int, int]] = set()
        self._pending: set[tuple[int, int]] = set()

        self._origin_lat: Optional[float] = None
        self._origin_lon: Optional[float] = None

        self._vehicle_xy: Optional[tuple[float, float]] = None
        self._vehicle_path: list[tuple[float, float]] = []

        self._sim_t0 = time.time()
        self._stop = False
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background simulation thread if not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._sim_loop, daemon=True, name="snow-grid-sim",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    # ── Config ────────────────────────────────────────────────────────

    def _raw_config(self) -> dict:
        try:
            cfg = SettingsService.load().get("snow_grid", {}) or {}
        except Exception:
            log.exception("snow_grid settings load failed")
            cfg = {}

        def _f(key, default):
            try:
                return float(cfg.get(key, default))
            except (TypeError, ValueError):
                return float(default)

        def _i(key, default):
            try:
                return int(cfg.get(key, default))
            except (TypeError, ValueError):
                return int(default)

        return {
            "enabled":            bool(cfg.get("enabled", DEFAULT_ENABLED)),
            "simulation_enabled": bool(cfg.get("simulation_enabled", DEFAULT_SIMULATION_ENABLED)),
            "tile_size_m":        max(1.0, _f("tile_size_m", DEFAULT_TILE_SIZE_M)),
            "cell_size_m":        max(0.05, _f("cell_size_m", DEFAULT_CELL_SIZE_M)),
            "active_radius_tiles": max(0, _i("active_radius_tiles", DEFAULT_ACTIVE_RADIUS_TILES)),
        }

    def get_config(self) -> dict:
        """Public config snapshot + derived width/height + origin."""
        cfg = self._raw_config()
        width = max(1, int(round(cfg["tile_size_m"] / cfg["cell_size_m"])))
        cfg["width"]  = width
        cfg["height"] = width
        with self._lock:
            cfg["origin_lat"] = self._origin_lat
            cfg["origin_lon"] = self._origin_lon
            cfg["has_origin"] = self._origin_lat is not None
            cfg["active_tile_count"] = len(self._active_keys)
            cfg["cached_tile_count"] = len(self._tiles)
        return cfg

    def set_origin(self, lat: float, lon: float) -> None:
        with self._lock:
            self._origin_lat = float(lat)
            self._origin_lon = float(lon)

    # ── Public query API ──────────────────────────────────────────────

    def get_active_tile_messages(self) -> list[dict]:
        """Return a message for every currently active tile (no pending state)."""
        cfg = self._raw_config()
        with self._lock:
            if self._origin_lat is None or self._origin_lon is None:
                return []
            return [
                self._tiles[k].to_message_dict(
                    self._origin_lat, self._origin_lon,
                    cfg["tile_size_m"], cfg["cell_size_m"],
                )
                for k in sorted(self._active_keys)
                if k in self._tiles
            ]

    def collect_pending_messages(self) -> list[dict]:
        """Return all tile messages whose data changed since last collect.

        Called by the WebSocket broadcast loop.  Clears the pending set.
        """
        cfg = self._raw_config()
        if not cfg["enabled"]:
            return []
        with self._lock:
            if self._origin_lat is None or self._origin_lon is None:
                return []
            keys = list(self._pending)
            self._pending.clear()
            return [
                self._tiles[k].to_message_dict(
                    self._origin_lat, self._origin_lon,
                    cfg["tile_size_m"], cfg["cell_size_m"],
                )
                for k in keys
                if k in self._tiles
            ]

    def reset_simulation(self) -> None:
        """Clear all cached tiles and the established origin."""
        with self._lock:
            self._tiles.clear()
            self._active_keys.clear()
            self._pending.clear()
            self._vehicle_path.clear()
            self._origin_lat = None
            self._origin_lon = None
            self._sim_t0 = time.time()
        log.info("snow grid simulation reset")

    # ── Simulation loop ───────────────────────────────────────────────

    def _sim_loop(self) -> None:
        while not self._stop:
            try:
                self.tick()
            except Exception:
                log.exception("snow grid tick failed")
            time.sleep(0.5)

    def tick(self) -> None:
        """One simulation step.  Public so tests can drive it deterministically."""
        cfg = self._raw_config()
        if not cfg["enabled"]:
            return

        snap = state_manager.get_snapshot()
        gps_lat = snap.get("gps_lat")
        gps_lon = snap.get("gps_lon")

        # Establish origin on first valid position.
        with self._lock:
            if self._origin_lat is None:
                if gps_lat is None or gps_lon is None:
                    return
                self._origin_lat = float(gps_lat)
                self._origin_lon = float(gps_lon)
            origin_lat = self._origin_lat
            origin_lon = self._origin_lon

        # Compute vehicle position in local metres.
        if gps_lat is not None and gps_lon is not None:
            vx, vy = lat_lon_to_local_meters(
                float(gps_lat), float(gps_lon), origin_lat, origin_lon,
            )
        else:
            # Demo mode: slowly drift in a small circle around origin.
            t = time.time() - self._sim_t0
            vx = 6.0 * math.sin(t * 0.05)
            vy = 6.0 * math.cos(t * 0.05)

        self._vehicle_xy = (vx, vy)

        # Append to path history (decimated).
        if not self._vehicle_path or (
            (vx - self._vehicle_path[-1][0]) ** 2
            + (vy - self._vehicle_path[-1][1]) ** 2
            > 0.25 ** 2
        ):
            self._vehicle_path.append((vx, vy))
            if len(self._vehicle_path) > 80:
                self._vehicle_path.pop(0)

        tile_size = cfg["tile_size_m"]
        cell_size = cfg["cell_size_m"]
        width = max(1, int(round(tile_size / cell_size)))
        radius = cfg["active_radius_tiles"]

        center_tx = int(math.floor(vx / tile_size))
        center_ty = int(math.floor(vy / tile_size))

        new_active: set[tuple[int, int]] = set()
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                new_active.add((center_tx + dx, center_ty + dy))

        with self._lock:
            self._active_keys = new_active

            if cfg["simulation_enabled"]:
                sim_t = time.time() - self._sim_t0
                path_snapshot = list(self._vehicle_path[-40:])
                for key in new_active:
                    tile = self._simulate_tile(
                        key, width, cell_size, (vx, vy), path_snapshot, sim_t,
                    )
                    self._tiles[key] = tile
                    self._pending.add(key)

    def _simulate_tile(
        self,
        key: tuple[int, int],
        width: int,
        cell_size: float,
        vehicle_xy: tuple[float, float],
        path: list[tuple[float, float]],
        sim_t: float,
    ) -> SnowTile:
        tx, ty = key
        n = width * width
        depths: list = [None] * n
        confidence: list = [0.0] * n
        vx, vy = vehicle_xy

        tile_origin_x = tx * width * cell_size      # = tx * tile_size
        tile_origin_y = ty * width * cell_size

        # Quick reject: if the tile's centre is way out of vehicle reach,
        # we can still attempt simulation; per-cell distance check will
        # leave all cells None.
        path_tail = path[-40:] if path else ()

        for j in range(width):
            cy = tile_origin_y + (j + 0.5) * cell_size
            row_off = j * width
            dy_cache = cy - vy
            for i in range(width):
                cx = tile_origin_x + (i + 0.5) * cell_size
                dxv = cx - vx
                dist_v = math.hypot(dxv, dy_cache)
                if dist_v > _SIMULATED_VEHICLE_REACH_M:
                    continue  # leave unmeasured

                # Smoothly varying snow field — sinusoids in global metres.
                base = (
                    0.85
                    + 0.55 * math.sin(cx * 0.07 + sim_t * 0.05)
                    + 0.35 * math.cos(cy * 0.09 - sim_t * 0.03)
                    + 0.20 * math.sin((cx + cy) * 0.15)
                )
                base += random.uniform(-0.05, 0.05)

                # Driven swath: compress snow along recent vehicle path.
                min_path_dist: Optional[float] = None
                for px, py in path_tail:
                    d = math.hypot(cx - px, cy - py)
                    if min_path_dist is None or d < min_path_dist:
                        min_path_dist = d
                if min_path_dist is not None and min_path_dist < _PATH_SWATH_M:
                    f = 1.0 - (min_path_dist / _PATH_SWATH_M)
                    base = base * (1.0 - 0.70 * f) + 0.05 * f

                depth = max(0.0, min(2.2, base))

                # Confidence: 1.0 at vehicle → ~0.4 at the reach edge.
                c = 1.0 - (dist_v / _SIMULATED_VEHICLE_REACH_M) * 0.6
                if c < 0.0: c = 0.0
                elif c > 1.0: c = 1.0

                idx = row_off + i
                depths[idx] = round(depth, 3)
                confidence[idx] = round(c, 3)

        return SnowTile(
            tile_x=tx, tile_y=ty,
            width=width, height=width,
            depths=depths, confidence=confidence,
            updated_at=time.time(),
        )
