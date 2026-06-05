"""
Persistent JSON settings manager for SnowLink web backend.

Identical contract to app/services/settings_service.py but path-resolved
from this file's location (backend/services/ → project root).
"""
import copy
import json
import os

# settings.json lives at the project root (two levels above this file).
_SETTINGS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'settings.json')
)

_DEFAULTS: dict = {
    'ntrip': {
        'enabled': False,
        'host': '',
        'port': '2101',
        'mountpoint': '',
        'username': '',
        'password': '',
    },
    'map': {
        'marker_style': 'snowcat',        # 'snowcat' | 'dot'
        'imu_heading_enabled': False,
        'imu_yaw_zero_deg': None,
    },
    'snow_grid': {
        'enabled': True,                  # global on/off for the snow grid
        'simulation_enabled': True,       # generate fake tiles around vehicle
        'tile_size_m': 20.0,              # physical edge length of one tile
        'cell_size_m': 0.5,               # edge length of one cell
        'active_radius_tiles': 1,         # 1 -> 3x3, 2 -> 5x5
    },
}

VALID_MARKER_STYLES = {'snowcat', 'dot'}


class SettingsService:
    _data: dict | None = None

    @classmethod
    def load(cls) -> dict:
        if cls._data is not None:
            return cls._data
        if os.path.exists(_SETTINGS_PATH):
            try:
                with open(_SETTINGS_PATH) as f:
                    cls._data = json.load(f)
                cls._merge_defaults(cls._data, _DEFAULTS)
                cls._validate_loaded_data(cls._data)
            except (json.JSONDecodeError, OSError):
                cls._data = copy.deepcopy(_DEFAULTS)
        else:
            cls._data = copy.deepcopy(_DEFAULTS)
        return cls._data

    @classmethod
    def save(cls) -> None:
        if cls._data is None:
            return
        try:
            with open(_SETTINGS_PATH, 'w') as f:
                json.dump(cls._data, f, indent=2)
        except OSError:
            pass

    @classmethod
    def invalidate(cls) -> None:
        """Force reload from disk on next load() call."""
        cls._data = None

    @classmethod
    def _merge_defaults(cls, target: dict, defaults: dict) -> None:
        for k, v in defaults.items():
            if k not in target:
                target[k] = v
            elif isinstance(v, dict) and isinstance(target[k], dict):
                cls._merge_defaults(target[k], v)

    @classmethod
    def _validate_loaded_data(cls, data: dict) -> None:
        if not isinstance(data.get('ntrip'), dict):
            data['ntrip'] = copy.deepcopy(_DEFAULTS['ntrip'])
        if not isinstance(data.get('map'), dict):
            data['map'] = copy.deepcopy(_DEFAULTS['map'])
        if not isinstance(data.get('snow_grid'), dict):
            data['snow_grid'] = copy.deepcopy(_DEFAULTS['snow_grid'])

        ntrip = data['ntrip']
        if not isinstance(ntrip.get('enabled'), bool):
            ntrip['enabled'] = _DEFAULTS['ntrip']['enabled']
        for key in ('host', 'port', 'mountpoint', 'username', 'password'):
            if not isinstance(ntrip.get(key), str):
                ntrip[key] = _DEFAULTS['ntrip'][key]

        map_settings = data['map']
        if map_settings.get('marker_style') not in VALID_MARKER_STYLES:
            map_settings['marker_style'] = _DEFAULTS['map']['marker_style']
        if not isinstance(map_settings.get('imu_heading_enabled'), bool):
            map_settings['imu_heading_enabled'] = _DEFAULTS['map']['imu_heading_enabled']

        zero = map_settings.get('imu_yaw_zero_deg')
        if zero is None:
            pass
        else:
            try:
                map_settings['imu_yaw_zero_deg'] = float(zero) % 360.0
            except (TypeError, ValueError):
                map_settings['imu_yaw_zero_deg'] = _DEFAULTS['map']['imu_yaw_zero_deg']

        snow = data['snow_grid']
        if not isinstance(snow.get('enabled'), bool):
            snow['enabled'] = _DEFAULTS['snow_grid']['enabled']
        if not isinstance(snow.get('simulation_enabled'), bool):
            snow['simulation_enabled'] = _DEFAULTS['snow_grid']['simulation_enabled']
        try:
            snow['tile_size_m'] = max(1.0, float(snow.get('tile_size_m',
                                                          _DEFAULTS['snow_grid']['tile_size_m'])))
        except (TypeError, ValueError):
            snow['tile_size_m'] = _DEFAULTS['snow_grid']['tile_size_m']
        try:
            snow['cell_size_m'] = max(0.05, float(snow.get('cell_size_m',
                                                           _DEFAULTS['snow_grid']['cell_size_m'])))
        except (TypeError, ValueError):
            snow['cell_size_m'] = _DEFAULTS['snow_grid']['cell_size_m']
        try:
            snow['active_radius_tiles'] = max(0, int(snow.get('active_radius_tiles',
                                                              _DEFAULTS['snow_grid']['active_radius_tiles'])))
        except (TypeError, ValueError):
            snow['active_radius_tiles'] = _DEFAULTS['snow_grid']['active_radius_tiles']
