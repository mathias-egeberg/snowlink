"""Persistent local data usage counter for the cellular modem."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_VALID_IFACE_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,15}$')
log = logging.getLogger('snowlink.cellular_usage')


class CellularUsagePersistenceError(RuntimeError):
    pass


class CellularUsageService:
    _usage_path = _ROOT / 'cellular_usage.json'
    _sys_class_net = Path('/sys/class/net')
    _lock = threading.Lock()

    @classmethod
    def get_snapshot(cls) -> dict[str, int | str | None]:
        with cls._lock:
            return cls._snapshot(cls._load_unlocked())

    @classmethod
    def sample(cls, iface: str | None) -> dict[str, int | str | None]:
        with cls._lock:
            data = cls._load_unlocked()
            clean_iface = cls._clean_iface(iface)
            counters = cls._read_interface_counters(clean_iface)
            if counters is None or clean_iface is None:
                return cls._snapshot(data)

            rx_bytes, tx_bytes = counters
            last_iface = data.get('last_iface')
            last_rx = cls._optional_int(data.get('last_rx_bytes'))
            last_tx = cls._optional_int(data.get('last_tx_bytes'))

            if last_iface == clean_iface and last_rx is not None:
                if rx_bytes >= last_rx:
                    data['bytes_received'] = cls._safe_int(data.get('bytes_received')) + (rx_bytes - last_rx)
                else:
                    log.info("RX counter reset detected on %s", clean_iface)
            if last_iface == clean_iface and last_tx is not None:
                if tx_bytes >= last_tx:
                    data['bytes_sent'] = cls._safe_int(data.get('bytes_sent')) + (tx_bytes - last_tx)
                else:
                    log.info("TX counter reset detected on %s", clean_iface)

            now_ms = cls._now_ms()
            data['last_iface'] = clean_iface
            data['last_rx_bytes'] = rx_bytes
            data['last_tx_bytes'] = tx_bytes
            data['updated_at_ms'] = now_ms
            data.setdefault('reset_at_ms', now_ms)
            cls._save_unlocked(data)
            return cls._snapshot(data)

    @classmethod
    def reset(cls, iface: str | None = None) -> dict[str, int | str | None]:
        with cls._lock:
            now_ms = cls._now_ms()
            clean_iface = cls._clean_iface(iface)
            counters = cls._read_interface_counters(clean_iface)
            rx_bytes, tx_bytes = counters if counters is not None else (None, None)
            data: dict[str, Any] = {
                'bytes_received': 0,
                'bytes_sent': 0,
                'last_iface': clean_iface,
                'last_rx_bytes': rx_bytes,
                'last_tx_bytes': tx_bytes,
                'reset_at_ms': now_ms,
                'updated_at_ms': now_ms,
            }
            if not cls._save_unlocked(data):
                raise CellularUsagePersistenceError('Unable to persist cellular usage reset')
            return cls._snapshot(data)

    @classmethod
    def _read_interface_counters(cls, iface: str | None) -> tuple[int, int] | None:
        if not iface or not cls._is_valid_iface(iface):
            return None
        stats_dir = cls._sys_class_net / iface / 'statistics'
        try:
            rx_bytes = int((stats_dir / 'rx_bytes').read_text(encoding='utf-8').strip())
            tx_bytes = int((stats_dir / 'tx_bytes').read_text(encoding='utf-8').strip())
            return rx_bytes, tx_bytes
        except (OSError, ValueError):
            return None

    @classmethod
    def _load_unlocked(cls) -> dict[str, Any]:
        try:
            with cls._usage_path.open(encoding='utf-8') as usage_file:
                data = json.load(usage_file)
        except (OSError, json.JSONDecodeError):
            return cls._defaults()
        if not isinstance(data, dict):
            return cls._defaults()
        return {**cls._defaults(), **data}

    @classmethod
    def _save_unlocked(cls, data: dict[str, Any]) -> bool:
        try:
            cls._usage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = cls._usage_path.with_suffix(cls._usage_path.suffix + '.tmp')
            with tmp_path.open('w', encoding='utf-8') as usage_file:
                json.dump(data, usage_file, indent=2)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, cls._usage_path)
            return True
        except OSError as exc:
            log.warning("Cellular usage file write failed: %s", exc)
            return False

    @classmethod
    def _snapshot(cls, data: dict[str, Any]) -> dict[str, int | str | None]:
        received = cls._safe_int(data.get('bytes_received'))
        sent = cls._safe_int(data.get('bytes_sent'))
        return {
            'bytes_received': received,
            'bytes_sent': sent,
            'bytes_total': received + sent,
            'last_iface': data.get('last_iface') if isinstance(data.get('last_iface'), str) else None,
            'reset_at_ms': cls._safe_int(data.get('reset_at_ms')),
            'updated_at_ms': cls._safe_int(data.get('updated_at_ms')),
        }

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            'bytes_received': 0,
            'bytes_sent': 0,
            'last_iface': None,
            'last_rx_bytes': None,
            'last_tx_bytes': None,
            'reset_at_ms': 0,
            'updated_at_ms': 0,
        }

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_iface(iface: str | None) -> str | None:
        if iface is None:
            return None
        return iface if CellularUsageService._is_valid_iface(iface) else None

    @staticmethod
    def _is_valid_iface(iface: str) -> bool:
        return bool(_VALID_IFACE_RE.fullmatch(iface)) and '..' not in iface and '/' not in iface

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)