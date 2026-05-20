"""
DataService – coordinates sensor simulation and hardware services.

On platforms without hardware the service runs in simulation mode;
sensor values fluctuate every 5 s.  On Raspberry Pi the GPS, IMU, and
cellular services run as background threads reporting real state.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Optional

from backend.state import state_manager
from backend.services.imu_heading import apply_yaw_calibration, normalize_yaw
from backend.services.settings_service import SettingsService

log = logging.getLogger("snowlink.data")

SELFTEST_INTERVAL = 3.0


class DataService:
    _instance: Optional["DataService"] = None

    @classmethod
    def get(cls) -> "DataService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        self._active = True

        settings = SettingsService.load()
        state_manager.update(
            marker_style=settings['map']['marker_style'],
            imu_heading_enabled=settings['map']['imu_heading_enabled'],
        )
        self.refresh_imu_heading_calibration()

        threading.Thread(target=self._sim_loop, daemon=True, name="sim-loop").start()

        from backend.services.imu_service import ImuService
        self._imu_service = ImuService(self)

        from backend.services.gps_service import GpsService
        self._gps_service = GpsService(self)

        threading.Thread(target=self._cellular_loop, daemon=True, name="cellular-loop").start()
        threading.Thread(target=self._boot_report,   daemon=True, name="boot-report").start()

    # ── Simulation ────────────────────────────────────────────────────────

    def _sim_loop(self) -> None:
        while self._active:
            time.sleep(5)
            s = state_manager.get_snapshot()
            state_manager.update(
                temperature_outside=round(s['temperature_outside'] + random.uniform(-0.3, 0.3), 1),
                temperature_roof=round(s['temperature_roof']    + random.uniform(-0.2, 0.2), 1),
                snow_depth_cm=round(max(0.0, s['snow_depth_cm'] + random.uniform(-0.5, 0.5)), 1),
                wind_speed_ms=round(max(0.0, s['wind_speed_ms'] + random.uniform(-0.5, 0.5)), 1),
            )

    # ── Cellular loop ─────────────────────────────────────────────────────

    def _cellular_loop(self) -> None:
        while self._active:
            try:
                from backend.services.selftest import cellular_selftest
                ok   = cellular_selftest.is_connected()
                prev = state_manager.get_snapshot()['cellular_ok']
                if ok != prev:
                    state_manager.update(cellular_ok=ok)
                    state_manager.update(
                        last_event="Cellular connected" if ok else "Cellular disconnected"
                    )
            except Exception:
                log.exception("Cellular selftest error")
            time.sleep(SELFTEST_INTERVAL)

    # ── Boot report ───────────────────────────────────────────────────────

    def _boot_report(self) -> None:
        time.sleep(SELFTEST_INTERVAL + 1.0)
        try:
            from backend.services.selftest import imu_selftest, gps_selftest, cellular_selftest
            missing = []
            if not imu_selftest.find_port():
                missing.append("IMU")
            if not gps_selftest.find_port():
                missing.append("GPS")
            if not cellular_selftest.is_connected():
                missing.append("Cellular")
            if missing:
                state_manager.update(last_event=f"Not found: {', '.join(missing)}")
        except Exception:
            log.exception("Boot report error")

    # ── IMU interface ─────────────────────────────────────────────────────

    def set_imu_yaw(self, yaw_deg: float) -> None:
        normalized = normalize_yaw(yaw_deg)
        settings   = SettingsService.load()
        zero       = settings['map'].get('imu_yaw_zero_deg')
        calibrated = zero is not None
        try:
            heading = apply_yaw_calibration(normalized, float(zero)) if calibrated else normalized
        except (TypeError, ValueError):
            heading    = normalized
            calibrated = False
        state_manager.update(
            imu_yaw_deg=normalized,
            imu_yaw_valid=True,
            imu_heading_deg=heading,
            imu_heading_calibrated=calibrated,
        )

    def clear_imu_yaw(self) -> None:
        state_manager.update(imu_yaw_valid=False)

    def set_imu_connection(self, connected: bool) -> None:
        prev_ok = state_manager.get_snapshot()['imu_ok']
        state_manager.update(imu_ok=connected)
        if not connected:
            self.clear_imu_yaw()
        if connected and not prev_ok:
            state_manager.update(last_event="IMU connected")
        elif not connected and prev_ok:
            state_manager.update(last_event="IMU disconnected")

    # ── GPS interface ─────────────────────────────────────────────────────

    def set_gps(self, ok: bool, float_rtk: bool, status_text: str) -> None:
        prev_ok = state_manager.get_snapshot()['gps_ok']
        state_manager.update(
            gps_ok=ok,
            gps_float_rtk=float_rtk,
            gps_status_text=status_text,
        )
        if ok and not prev_ok:
            state_manager.update(last_event="GPS connected")
        elif not ok and prev_ok:
            state_manager.update(last_event="GPS disconnected")

    # ── IMU calibration ───────────────────────────────────────────────────

    def calibrate_imu_heading_to_boot(self) -> bool:
        s = state_manager.get_snapshot()
        if not s['imu_yaw_valid']:
            return False
        settings = SettingsService.load()
        settings['map']['imu_yaw_zero_deg'] = round(s['imu_yaw_deg'], 4)
        SettingsService.save()
        self.refresh_imu_heading_calibration()
        state_manager.update(last_event="IMU heading calibrated")
        return True

    def refresh_imu_heading_calibration(self) -> None:
        s          = state_manager.get_snapshot()
        settings   = SettingsService.load()
        zero       = settings['map'].get('imu_yaw_zero_deg')
        calibrated = zero is not None
        try:
            heading = (
                apply_yaw_calibration(s['imu_yaw_deg'], float(zero))
                if calibrated else normalize_yaw(s['imu_yaw_deg'])
            )
        except (TypeError, ValueError):
            heading    = normalize_yaw(s['imu_yaw_deg'])
            calibrated = False
        state_manager.update(imu_heading_calibrated=calibrated, imu_heading_deg=heading)

    # ── Cleanup ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._active = False
        if hasattr(self, '_imu_service'):
            self._imu_service.stop()
        if hasattr(self, '_gps_service'):
            self._gps_service.stop()
