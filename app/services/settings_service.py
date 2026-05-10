import copy
import json
import os

_SETTINGS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'settings.json')
)

_DEFAULTS = {
    'ntrip': {
        'enabled': False,
        'host': '',
        'port': '2101',
        'mountpoint': '',
        'username': '',
        'password': '',
    },
    'map': {
        'marker_style': 'snowcat',   # 'snowcat' | 'dot'
    },
}


class SettingsService:
    _data = None

    @classmethod
    def load(cls):
        if cls._data is not None:
            return cls._data
        if os.path.exists(_SETTINGS_PATH):
            try:
                with open(_SETTINGS_PATH) as f:
                    cls._data = json.load(f)
                cls._merge_defaults(cls._data, _DEFAULTS)
            except (json.JSONDecodeError, OSError):
                cls._data = copy.deepcopy(_DEFAULTS)
        else:
            cls._data = copy.deepcopy(_DEFAULTS)
        return cls._data

    @classmethod
    def save(cls):
        if cls._data is None:
            return
        try:
            with open(_SETTINGS_PATH, 'w') as f:
                json.dump(cls._data, f, indent=2)
        except OSError:
            pass

    @classmethod
    def _merge_defaults(cls, target, defaults):
        for k, v in defaults.items():
            if k not in target:
                target[k] = v
            elif isinstance(v, dict) and isinstance(target[k], dict):
                cls._merge_defaults(target[k], v)
