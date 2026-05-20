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
        'imu_heading_enabled': True,
        'imu_yaw_zero_deg': None,
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
            return
        try:
            map_settings['imu_yaw_zero_deg'] = float(zero) % 360.0
        except (TypeError, ValueError):
            map_settings['imu_yaw_zero_deg'] = _DEFAULTS['map']['imu_yaw_zero_deg']
