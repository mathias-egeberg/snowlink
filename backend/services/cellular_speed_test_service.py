"""Lightweight cellular speed test bound to the modem interface IP."""
from __future__ import annotations

import http.client
import ipaddress
import logging
import re
import socket
import time
from dataclasses import asdict, dataclass, field

_VALID_IFACE_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,15}$')
_SPEED_TEST_HOST = 'speed.cloudflare.com'
_DOWNLOAD_BYTES = 2_000_000
_UPLOAD_BYTES = 1_000_000
_HTTP_TIMEOUT_SECONDS = 12.0
_CONNECT_TIMEOUT_SECONDS = 4.0
_CHUNK_SIZE = 64 * 1024
_LATENCY_TARGETS = (('1.1.1.1', 443), ('8.8.8.8', 53))
log = logging.getLogger('snowlink.cellular_speed')


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class CellularSpeedTestResult:
    ok: bool
    iface: str = ''
    source_ip: str = ''
    latency_ms: float = 0.0
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    tested_at_ms: int = field(default_factory=_now_ms)
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class CellularSpeedTestService:
    @classmethod
    def run(cls, iface: str | None, source_ip: str | None) -> CellularSpeedTestResult:
        clean_iface = cls._clean_iface(iface)
        clean_ip = cls._clean_ipv4(source_ip)
        if clean_iface is None:
            return cls._failure('', clean_ip or '', 'Invalid cellular interface')
        if clean_ip is None:
            return cls._failure(clean_iface, '', 'Invalid cellular IP address')

        try:
            latency_ms = cls._measure_latency_ms(clean_iface, clean_ip)
            download_mbps = cls._measure_download_mbps(clean_ip)
            upload_mbps = cls._measure_upload_mbps(clean_ip)
            return CellularSpeedTestResult(
                ok=True,
                iface=clean_iface,
                source_ip=clean_ip,
                latency_ms=round(latency_ms, 1),
                download_mbps=round(download_mbps, 2),
                upload_mbps=round(upload_mbps, 2),
            )
        except Exception as exc:
            log.warning("Cellular speed test failed on %s: %s", clean_iface, exc)
            return cls._failure(clean_iface, clean_ip, str(exc) or exc.__class__.__name__)

    @classmethod
    def _measure_latency_ms(cls, iface: str, source_ip: str) -> float:
        last_error: OSError | None = None
        for host, port in _LATENCY_TARGETS:
            start = time.perf_counter()
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.settimeout(_CONNECT_TIMEOUT_SECONDS)
                    cls._bind_socket(sock, iface, source_ip)
                    sock.connect((host, port))
                    return (time.perf_counter() - start) * 1000.0
                finally:
                    sock.close()
            except OSError as exc:
                last_error = exc
        raise TimeoutError(f'Latency check failed: {last_error}')

    @classmethod
    def _measure_download_mbps(cls, source_ip: str) -> float:
        conn = http.client.HTTPSConnection(
            _SPEED_TEST_HOST,
            timeout=_HTTP_TIMEOUT_SECONDS,
            source_address=(source_ip, 0),
        )
        bytes_read = 0
        start = time.perf_counter()
        try:
            conn.request('GET', f'/__down?bytes={_DOWNLOAD_BYTES}', headers={'Cache-Control': 'no-cache'})
            response = conn.getresponse()
            if response.status < 200 or response.status >= 400:
                raise OSError(f'Download test returned HTTP {response.status}')
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                bytes_read += len(chunk)
        finally:
            conn.close()
        return cls._mbps(bytes_read, start)

    @classmethod
    def _measure_upload_mbps(cls, source_ip: str) -> float:
        conn = http.client.HTTPSConnection(
            _SPEED_TEST_HOST,
            timeout=_HTTP_TIMEOUT_SECONDS,
            source_address=(source_ip, 0),
        )
        payload = b'0' * _UPLOAD_BYTES
        start = time.perf_counter()
        try:
            conn.request(
                'POST',
                '/__up',
                body=payload,
                headers={
                    'Content-Type': 'application/octet-stream',
                    'Content-Length': str(len(payload)),
                    'Cache-Control': 'no-cache',
                },
            )
            response = conn.getresponse()
            response.read()
            if response.status < 200 or response.status >= 400:
                raise OSError(f'Upload test returned HTTP {response.status}')
        finally:
            conn.close()
        return cls._mbps(len(payload), start)

    @staticmethod
    def _bind_socket(sock: socket.socket, iface: str, source_ip: str) -> None:
        device_bound = False
        bind_to_device = getattr(socket, 'SO_BINDTODEVICE', None)
        if bind_to_device is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, bind_to_device, iface.encode() + b'\0')
                device_bound = True
            except OSError as exc:
                log.debug("SO_BINDTODEVICE failed for %s: %s", iface, exc)
        try:
            sock.bind((source_ip, 0))
        except OSError as exc:
            if not device_bound:
                raise
            log.warning("Source-IP bind failed after SO_BINDTODEVICE succeeded for %s: %s", iface, exc)

    @staticmethod
    def _mbps(byte_count: int, start: float) -> float:
        elapsed = max(time.perf_counter() - start, 0.001)
        return (byte_count * 8.0) / elapsed / 1_000_000.0

    @staticmethod
    def _clean_iface(iface: str | None) -> str | None:
        if iface is None:
            return None
        return iface if _VALID_IFACE_RE.fullmatch(iface) and '..' not in iface and '/' not in iface else None

    @staticmethod
    def _clean_ipv4(source_ip: str | None) -> str | None:
        if not source_ip:
            return None
        try:
            address = ipaddress.ip_address(source_ip)
        except ValueError:
            return None
        return str(address) if address.version == 4 else None

    @staticmethod
    def _failure(iface: str, source_ip: str, error: str) -> CellularSpeedTestResult:
        return CellularSpeedTestResult(ok=False, iface=iface, source_ip=source_ip, error=error)