"""
Hardware configuration loaded from config.yaml at the project root.
Falls back to built-in defaults if the file is absent or unparseable.
"""
from __future__ import annotations

import copy
import logging
import os
from typing import Any, Dict

log = logging.getLogger("snowlink.config")

_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")
)

_DEFAULTS: Dict[str, Any] = {
    "imu": {
        "enabled": True,
        "port": "",
        "baudrate": 115200,
        "raw_log_enabled": True,
        "raw_log_rate_hz": 100,
        "heading_log_rate_hz": 10,
    },
    "sftp": {
        "enabled": False,
        "host": "",
        "port": 22,
        "username": "",
        "password": "",
        "remote_base_path": "/snowlink_data/logs",
    },
}


class ConfigService:
    _data: Dict[str, Any] | None = None

    @classmethod
    def load(cls) -> Dict[str, Any]:
        if cls._data is not None:
            return cls._data
        cls._data = copy.deepcopy(_DEFAULTS)
        if os.path.exists(_CONFIG_PATH):
            try:
                import yaml
                with open(_CONFIG_PATH) as f:
                    loaded = yaml.safe_load(f) or {}
                cls._deep_merge(cls._data, loaded)
                log.debug("Loaded config.yaml from %s", _CONFIG_PATH)
            except Exception as exc:
                log.warning("config.yaml load failed (%s), using defaults", exc)
        return cls._data

    @classmethod
    def get_imu(cls) -> Dict[str, Any]:
        return cls.load()["imu"]

    @classmethod
    def get_sftp(cls) -> Dict[str, Any]:
        return cls.load()["sftp"]

    @classmethod
    def invalidate(cls) -> None:
        """Force reload from disk on next load() call."""
        cls._data = None

    @classmethod
    def _deep_merge(cls, base: dict, override: dict) -> None:
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                cls._deep_merge(base[k], v)
            else:
                base[k] = v
