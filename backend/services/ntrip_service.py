"""
NTRIP client service — streams RTCM3 corrections from a caster to the GPS module.

Lifecycle:
  - Background _loop polls settings every POLL_INTERVAL seconds.
  - When enabled and GPS serial is available, opens a TCP connection to the
    configured NTRIP caster and writes RTCM3 chunks straight to the GPS serial
    port via GpsService.send_rtcm().
  - Reconnects automatically on socket drop or settings change.
  - When disabled, stays idle and reports "Disabled".
"""
from __future__ import annotations

import base64
import logging
import socket
import threading
import time
from typing import Optional

from backend.services.settings_service import SettingsService
from backend.state import state_manager

log = logging.getLogger("snowlink.ntrip")

POLL_INTERVAL   = 5.0    # seconds between _tick() calls
CONNECT_TIMEOUT = 10.0   # TCP connect / HTTP header timeout
READ_TIMEOUT    = 15.0   # per-recv timeout inside the RTCM stream
CHUNK_SIZE      = 4096


class NtripService:

    def __init__(self, data_service, gps_service) -> None:
        self._ds       = data_service
        self._gps      = gps_service
        self._lock     = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._cur_cfg: Optional[tuple]      = None   # config key of active connection
        self._active   = True
        threading.Thread(
            target=self._loop, daemon=True, name="ntrip-loop"
        ).start()

    # ── Main loop ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._active:
            try:
                self._tick()
            except Exception:
                log.exception("NTRIP loop tick failed")
            time.sleep(POLL_INTERVAL)

    def _tick(self) -> None:
        settings = SettingsService.load()
        cfg      = settings.get("ntrip", {})
        enabled  = cfg.get("enabled", False)

        with self._lock:
            sock = self._sock

        if not enabled:
            if sock is not None:
                self._disconnect()
            state_manager.update(ntrip_ok=False, ntrip_status_text="Disabled")
            return

        # Reconnect if settings changed while connected.
        cfg_key = (
            cfg.get("host"), cfg.get("port"), cfg.get("mountpoint"),
            cfg.get("username"), cfg.get("password"),
        )
        if sock is not None and self._cur_cfg != cfg_key:
            log.debug("NTRIP settings changed — reconnecting")
            self._disconnect()
            sock = None

        if sock is None:
            self._connect(cfg, cfg_key)

    # ── Connection ────────────────────────────────────────────────────────

    def _connect(self, cfg: dict, cfg_key: tuple) -> None:
        host       = cfg.get("host", "").strip()
        port_str   = cfg.get("port", "2101").strip()
        mountpoint = cfg.get("mountpoint", "").strip().lstrip("/")
        username   = cfg.get("username", "")
        password   = cfg.get("password", "")

        if not host or not mountpoint:
            state_manager.update(ntrip_ok=False, ntrip_status_text="Not configured")
            return

        try:
            port = int(port_str)
        except ValueError:
            state_manager.update(ntrip_ok=False, ntrip_status_text="Invalid port")
            return

        state_manager.update(ntrip_ok=False, ntrip_status_text="Connecting…")

        # ── Open TCP socket ──────────────────────────────────────────────
        try:
            sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
            sock.settimeout(CONNECT_TIMEOUT)
        except OSError as exc:
            log.warning("NTRIP TCP connect failed (%s:%s): %s", host, port, exc)
            state_manager.update(ntrip_ok=False, ntrip_status_text="Connect failed")
            return

        # ── Send HTTP GET request ────────────────────────────────────────
        creds   = base64.b64encode(f"{username}:{password}".encode()).decode()
        request = (
            f"GET /{mountpoint} HTTP/1.0\r\n"
            f"Host: {host}:{port}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"User-Agent: NTRIP SnowLink/1.0\r\n"
            f"Authorization: Basic {creds}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        try:
            sock.sendall(request.encode("ascii"))
        except OSError as exc:
            log.warning("NTRIP send request failed: %s", exc)
            sock.close()
            state_manager.update(ntrip_ok=False, ntrip_status_text="Connect failed")
            return

        # ── Read HTTP response header ─────────────────────────────────────
        # Supports both NTRIP 1.0 ("ICY 200 OK") and 2.0 ("HTTP/1.x 200 OK").
        # Read until we see the blank line that ends the headers.
        try:
            header = b""
            while len(header) < 8192:
                chunk = sock.recv(256)
                if not chunk:
                    break
                header += chunk
                if b"\r\n\r\n" in header:
                    break
                # ICY header: some NTRIP 1.0 casters use one \r\n after "ICY 200 OK"
                # with RTCM data starting immediately — treat that as success too.
                if header.lstrip().startswith(b"ICY 200 OK\r\n"):
                    break
        except OSError as exc:
            log.warning("NTRIP read header failed: %s", exc)
            sock.close()
            state_manager.update(ntrip_ok=False, ntrip_status_text="No response")
            return

        header_str = header.decode("ascii", errors="ignore")

        ok_icy  = header_str.lstrip().startswith("ICY 200 OK")
        ok_http = "HTTP/" in header_str and " 200 " in header_str

        if not ok_icy and not ok_http:
            log.warning("NTRIP bad response: %r", header_str[:200])
            sock.close()
            if "401" in header_str:
                state_manager.update(ntrip_ok=False, ntrip_status_text="Auth failed")
            elif "404" in header_str:
                state_manager.update(ntrip_ok=False, ntrip_status_text="Mountpoint not found")
            else:
                state_manager.update(ntrip_ok=False, ntrip_status_text="Caster error")
            return

        # ── Success — register socket and start streaming ─────────────────
        sock.settimeout(READ_TIMEOUT)
        with self._lock:
            self._sock    = sock
            self._cur_cfg = cfg_key

        state_manager.update(ntrip_ok=True, ntrip_status_text="Connected")
        log.info("NTRIP connected to %s:%s/%s", host, port, mountpoint)

        threading.Thread(
            target=self._stream, args=(sock,), daemon=True, name="ntrip-stream"
        ).start()

    # ── RTCM stream thread ─────────────────────────────────────────────────

    def _stream(self, sock: socket.socket) -> None:
        try:
            while self._active:
                try:
                    data = sock.recv(CHUNK_SIZE)
                except OSError:
                    break
                if not data:
                    break
                self._gps.send_rtcm(data)
        finally:
            try:
                sock.close()
            except OSError:
                pass
            with self._lock:
                if self._sock is sock:
                    self._sock    = None
                    self._cur_cfg = None
            if self._active:
                state_manager.update(ntrip_ok=False, ntrip_status_text="Disconnected")
            log.info("NTRIP stream ended")

    # ── Disconnect helper ──────────────────────────────────────────────────

    def _disconnect(self) -> None:
        with self._lock:
            sock          = self._sock
            self._sock    = None
            self._cur_cfg = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        self._disconnect()
