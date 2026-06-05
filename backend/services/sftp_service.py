"""
SFTP upload service — pushes completed recording sessions to a remote server.

Remote layout:
  <remote_base_path>/YYYY-MM-DD/<session_name>/<files>

A hidden marker file `.uploaded` is written into each local session directory
after a successful upload so that re-runs skip already-synced sessions.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("snowlink.sftp")

_LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
_UPLOADED_MARKER = ".uploaded"

# Regex that matches the date portion inside session directory names.
# Handles both "YYYY-MM-DD_HH-MM-SS_name" and "name_YYYY-MM-DD_HH-MM-SS".
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _date_folder(session_name: str) -> str:
    m = _DATE_RE.search(session_name)
    if m:
        return m.group(1)
    return datetime.now().strftime("%Y-%m-%d")


class SftpService:
    _instance: Optional["SftpService"] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "SftpService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Public API ────────────────────────────────────────────────────────

    def upload_session(self, session_name: str) -> dict:
        """Upload a single session directory in a background thread."""
        threading.Thread(
            target=self._upload_session_sync,
            args=(session_name,),
            daemon=True,
            name=f"sftp-{session_name[:20]}",
        ).start()
        return {"ok": True, "queued": session_name}

    def upload_pending(self) -> None:
        """Upload all local sessions that have not been uploaded yet (background)."""
        threading.Thread(
            target=self._upload_pending_sync,
            daemon=True,
            name="sftp-pending",
        ).start()

    # ── Internal sync helpers ─────────────────────────────────────────────

    def _get_config(self):
        from backend.services.config_service import ConfigService
        return ConfigService.get_sftp()

    def _connect(self, cfg: dict):
        """Return an open (transport, sftp) pair. Caller is responsible for closing."""
        import paramiko

        host = cfg.get("host", "")
        port = int(cfg.get("port", 22))
        username = cfg.get("username", "")
        password = cfg.get("password", "")

        if not host or not username:
            raise ValueError("SFTP host and username must be configured in config.yaml")

        transport = paramiko.Transport((host, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        return transport, sftp

    def _makedirs_remote(self, sftp, remote_path: str) -> None:
        parts = remote_path.rstrip("/").split("/")
        current = ""
        for part in parts:
            if not part:
                current = "/"
                continue
            current = f"{current}/{part}" if current != "/" else f"/{part}"
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    def _upload_session_sync(self, session_name: str) -> None:
        cfg = self._get_config()
        if not cfg.get("enabled"):
            log.debug("SFTP disabled, skipping upload for %s", session_name)
            return

        session_dir = _LOGS_DIR / session_name
        if not session_dir.is_dir():
            log.warning("Session dir not found: %s", session_dir)
            return

        marker = session_dir / _UPLOADED_MARKER
        if marker.exists():
            log.debug("Session already uploaded: %s", session_name)
            return

        base = cfg.get("remote_base_path", "/snowlink_data/logs").rstrip("/")
        date_folder = _date_folder(session_name)
        remote_session = f"{base}/{date_folder}/{session_name}"

        log.info("Uploading session %s → %s", session_name, remote_session)
        transport = sftp = None
        try:
            transport, sftp = self._connect(cfg)
            self._makedirs_remote(sftp, remote_session)

            files = [f for f in session_dir.iterdir() if f.is_file() and f.name != _UPLOADED_MARKER]
            for f in sorted(files):
                remote_file = f"{remote_session}/{f.name}"
                log.debug("  uploading %s", f.name)
                sftp.put(str(f), remote_file)

            # Write marker only after all files are successfully transferred.
            marker.write_text(datetime.now().isoformat())
            log.info("Upload complete: %s (%d files)", session_name, len(files))

        except Exception:
            log.exception("SFTP upload failed for session %s", session_name)
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            if transport:
                try:
                    transport.close()
                except Exception:
                    pass

    def _upload_pending_sync(self) -> None:
        cfg = self._get_config()
        if not cfg.get("enabled"):
            return

        if not _LOGS_DIR.exists():
            return

        pending = [
            d.name
            for d in sorted(_LOGS_DIR.iterdir())
            if d.is_dir() and not (d / _UPLOADED_MARKER).exists()
        ]

        if not pending:
            log.debug("No pending sessions to upload")
            return

        log.info("Uploading %d pending session(s)", len(pending))
        for session_name in pending:
            self._upload_session_sync(session_name)
