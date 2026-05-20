"""Cellular self-test for Huawei USB LTE dongles and fallback modems."""
from __future__ import annotations

import logging
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

HUAWEI_VENDOR_ID = '12d1'
_SYS_CLASS_NET = Path('/sys/class/net')
_FALLBACK_IFACE_PREFIXES = ('wwan', 'usb', 'ppp', 'rmnet', 'enx')
_INTERNET_TARGETS = (('1.1.1.1', 443), ('8.8.8.8', 53))
_CONNECT_TIMEOUT_SECONDS = 1.5
_REACHABILITY_SUCCESS_TTL_SECONDS = 30.0
_REACHABILITY_FAILURE_TTL_SECONDS = 15.0
_VALID_IFACE_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,15}$')
_HEX_ID_RE = re.compile(r'^[0-9a-fA-F]{4}$')
_reachability_cache: dict[tuple[str, str], tuple[bool, float]] = {}
_reachability_cache_lock = threading.Lock()
log = logging.getLogger('snowlink.cellular')

# ioctl to read IPv4 address from an interface (Linux-only)
_SIOCGIFADDR = 0x8915


@dataclass(frozen=True)
class CellularProbeResult:
    detected: bool
    iface: str | None = None
    ipv4: str | None = None
    internet_reachable: bool = False
    is_huawei: bool = False
    vendor_id: str | None = None
    product_id: str | None = None
    manufacturer: str | None = None
    product: str | None = None

    @property
    def ok(self) -> bool:
        return self.detected and self.ipv4 is not None and self.internet_reachable

    @property
    def status_text(self) -> str:
        if self.ok:
            return 'Online'
        if not self.detected:
            return 'No modem'
        if self.ipv4 is None:
            return 'No IP address'
        return 'No internet'


@dataclass(frozen=True)
class _InterfaceCandidate:
    name: str
    is_huawei: bool = False
    vendor_id: str | None = None
    product_id: str | None = None
    manufacturer: str | None = None
    product: str | None = None


def _is_valid_iface(name: str) -> bool:
    return bool(_VALID_IFACE_RE.fullmatch(name)) and '..' not in name and '/' not in name


def _read_text(path: Path, max_chars: int = 64) -> str | None:
    try:
        with path.open(encoding='utf-8') as text_file:
            value = text_file.read(max_chars + 1)[:max_chars]
        return ''.join(ch for ch in value.strip() if ch.isprintable())
    except OSError:
        return None


def _is_sysfs_path(path: Path) -> bool:
    roots = [Path('/sys')]
    try:
        roots.append(_SYS_CLASS_NET.resolve(strict=False).parent)
    except OSError:
        roots.append(_SYS_CLASS_NET.parent)
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _interface_names() -> list[str]:
    try:
        return sorted(
            path.name for path in _SYS_CLASS_NET.iterdir()
            if path.name != 'lo' and _is_valid_iface(path.name)
        )
    except OSError:
        return []


def _usb_metadata(iface: str) -> dict[str, str | None]:
    device_path = _SYS_CLASS_NET / iface / 'device'
    try:
        search_root = device_path.resolve(strict=False)
    except OSError:
        search_root = device_path
    if not _is_sysfs_path(search_root):
        return {}

    for path in (search_root, *search_root.parents):
        if not _is_sysfs_path(path):
            break
        vendor_id = _read_text(path / 'idVendor')
        if vendor_id is None or not _HEX_ID_RE.fullmatch(vendor_id):
            continue
        product_id = _read_text(path / 'idProduct')
        return {
            'vendor_id': vendor_id.lower(),
            'product_id': product_id.lower() if product_id and _HEX_ID_RE.fullmatch(product_id) else None,
            'manufacturer': _read_text(path / 'manufacturer'),
            'product': _read_text(path / 'product'),
        }
    return {}


def _candidate_interfaces() -> list[_InterfaceCandidate]:
    candidates: list[_InterfaceCandidate] = []
    for iface in _interface_names():
        metadata = _usb_metadata(iface)
        vendor_id = metadata.get('vendor_id')
        is_huawei = vendor_id == HUAWEI_VENDOR_ID
        is_fallback = iface.startswith(_FALLBACK_IFACE_PREFIXES)
        if not is_huawei and not is_fallback:
            continue
        candidates.append(
            _InterfaceCandidate(
                name=iface,
                is_huawei=is_huawei,
                vendor_id=vendor_id,
                product_id=metadata.get('product_id'),
                manufacturer=metadata.get('manufacturer'),
                product=metadata.get('product'),
            )
        )
    return sorted(candidates, key=lambda item: (not item.is_huawei, item.name))


def _iface_ipv4(name: str) -> str | None:
    if fcntl is None:
        return None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ifreq = struct.pack('16sH14s', name.encode(), socket.AF_INET, b'\x00' * 14)
            res = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, ifreq)
            address = socket.inet_ntoa(res[20:24])
            if address in ('0.0.0.0', ''):
                return None
            return address
        finally:
            sock.close()
    except OSError:
        return None


def _iface_has_ip(name: str) -> bool:
    return _iface_ipv4(name) is not None


def _bind_socket_to_interface(sock: socket.socket, iface: str, source_ip: str) -> None:
    bind_to_device = getattr(socket, 'SO_BINDTODEVICE', None)
    if bind_to_device is not None:
        try:
            sock.setsockopt(socket.SOL_SOCKET, bind_to_device, iface.encode() + b'\0')
            return
        except OSError as exc:
            log.debug("SO_BINDTODEVICE failed for %s: %s", iface, exc)
    try:
        sock.bind((source_ip, 0))
    except OSError as exc:
        log.debug("Source-IP bind failed for %s on %s: %s", source_ip, iface, exc)


def _internet_reachable(iface: str, source_ip: str) -> bool:
    cache_key = (iface, source_ip)
    now = time.monotonic()
    with _reachability_cache_lock:
        cached = _reachability_cache.get(cache_key)
    if cached is not None:
        cached_ok, expires_at = cached
        if now < expires_at:
            return cached_ok

    reachable = False
    for target in _INTERNET_TARGETS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(_CONNECT_TIMEOUT_SECONDS)
                _bind_socket_to_interface(sock, iface, source_ip)
                sock.connect(target)
                reachable = True
                break
            finally:
                sock.close()
        except OSError:
            continue

    ttl = _REACHABILITY_SUCCESS_TTL_SECONDS if reachable else _REACHABILITY_FAILURE_TTL_SECONDS
    with _reachability_cache_lock:
        _reachability_cache[cache_key] = (reachable, now + ttl)
    return reachable


def _result(
    candidate: _InterfaceCandidate,
    ipv4: str | None,
    internet_reachable: bool,
) -> CellularProbeResult:
    return CellularProbeResult(
        detected=True,
        iface=candidate.name,
        ipv4=ipv4,
        internet_reachable=internet_reachable,
        is_huawei=candidate.is_huawei,
        vendor_id=candidate.vendor_id,
        product_id=candidate.product_id,
        manufacturer=candidate.manufacturer,
        product=candidate.product,
    )


def probe(require_internet: bool = True) -> CellularProbeResult:
    """Return the current cellular USB modem and internet status."""
    candidates = _candidate_interfaces()
    if not candidates:
        return CellularProbeResult(detected=False)

    first_detected = _result(candidates[0], None, False)
    first_with_ip: CellularProbeResult | None = None
    for candidate in candidates:
        ipv4 = _iface_ipv4(candidate.name)
        if ipv4 is None:
            continue
        reachable = not require_internet or _internet_reachable(candidate.name, ipv4)
        result = _result(candidate, ipv4, reachable)
        if result.ok:
            return result
        if first_with_ip is None:
            first_with_ip = result
    return first_with_ip or first_detected


def is_connected() -> bool:
    """True if a cellular modem is detected, has an IP, and reaches the internet."""
    return probe().ok
