import asyncio

from fastapi import APIRouter, Body, HTTPException
from backend.services.cellular_speed_test_service import CellularSpeedTestService
from backend.services.cellular_usage_service import (
    CellularUsagePersistenceError,
    CellularUsageService,
)
from backend.services.selftest import cellular_selftest
from backend.services.settings_service import SettingsService, VALID_MARKER_STYLES
from backend.state import state_manager

router = APIRouter()
_CELLULAR_SPEED_TEST_TIMEOUT_SECONDS = 40.0


@router.get("/settings")
async def get_settings() -> dict:
    """Return the full persisted settings object."""
    return SettingsService.load()


@router.post("/settings")
async def post_settings(payload: dict = Body(...)) -> dict:
    """Persist settings and sync relevant fields to live state.

    Accepts a partial or full settings dict with 'ntrip' and/or 'map' keys.
    Unknown keys are silently ignored.
    """
    data = SettingsService.load()

    # ── NTRIP section ─────────────────────────────────────────────────────
    ntrip_in = payload.get("ntrip")
    if isinstance(ntrip_in, dict):
        n = data["ntrip"]
        if isinstance(ntrip_in.get("enabled"), bool):
            n["enabled"] = ntrip_in["enabled"]
        for key in ("host", "port", "mountpoint", "username", "password"):
            if isinstance(ntrip_in.get(key), str):
                n[key] = ntrip_in[key]

    # ── Map section ───────────────────────────────────────────────────────
    map_in = payload.get("map")
    if isinstance(map_in, dict):
        m = data["map"]

        new_style = map_in.get("marker_style")
        if new_style in VALID_MARKER_STYLES:
            m["marker_style"] = new_style

        new_imu = map_in.get("imu_heading_enabled")
        if isinstance(new_imu, bool):
            m["imu_heading_enabled"] = new_imu

        # imu_yaw_zero_deg is only written by the calibration endpoint,
        # but allow explicit updates here too for completeness.
        if "imu_yaw_zero_deg" in map_in:
            zero = map_in["imu_yaw_zero_deg"]
            if zero is None:
                m["imu_yaw_zero_deg"] = None
            elif isinstance(zero, (int, float)):
                m["imu_yaw_zero_deg"] = float(zero) % 360.0

    # ── Snow grid section ─────────────────────────────────────────────────
    snow_in = payload.get("snow_grid")
    if isinstance(snow_in, dict):
        s = data.setdefault("snow_grid", {})
        for bkey in ("enabled", "simulation_enabled"):
            if isinstance(snow_in.get(bkey), bool):
                s[bkey] = snow_in[bkey]
        for fkey in ("tile_size_m", "cell_size_m"):
            if isinstance(snow_in.get(fkey), (int, float)):
                s[fkey] = float(snow_in[fkey])
        if isinstance(snow_in.get("active_radius_tiles"), int):
            s["active_radius_tiles"] = snow_in["active_radius_tiles"]

    SettingsService._validate_loaded_data(data)
    SettingsService.save()

    # Sync map settings into live state so WS clients stay current.
    state_manager.update(
        marker_style=data["map"]["marker_style"],
        imu_heading_enabled=data["map"]["imu_heading_enabled"],
    )

    return {"ok": True, "settings": data}


@router.post("/settings/calibrate-imu")
async def calibrate_imu() -> dict:
    """Use the current IMU yaw as the zero/forward calibration reference."""
    from backend.services.data_service import DataService
    success = DataService.get().calibrate_imu_heading_to_boot()
    if not success:
        raise HTTPException(
            status_code=409,
            detail="IMU yaw not available — ensure IMU is connected and sending data.",
        )
    return {"ok": True, "state": state_manager.get_snapshot()}


@router.post("/settings/cellular-usage/reset")
async def reset_cellular_usage() -> dict:
    """Reset the local cellular data counter and keep current counters as baseline."""
    try:
        snapshot = state_manager.get_snapshot()
        iface = snapshot.get('cellular_iface') or None
        usage = CellularUsageService.reset(iface)
    except CellularUsagePersistenceError as exc:
        raise HTTPException(status_code=500, detail="Unable to reset cellular usage counter") from exc
    state_manager.update(
        cellular_bytes_received=usage['bytes_received'],
        cellular_bytes_sent=usage['bytes_sent'],
        cellular_bytes_total=usage['bytes_total'],
        cellular_usage_reset_at_ms=usage['reset_at_ms'],
        cellular_usage_updated_at_ms=usage['updated_at_ms'],
        last_event="Cellular data counter reset",
    )
    return {"ok": True, "state": state_manager.get_snapshot()}


@router.post("/settings/cellular-speed-test")
async def run_cellular_speed_test() -> dict:
    """Run a lightweight speed test through the current cellular interface."""
    snapshot = state_manager.get_snapshot()
    iface = snapshot.get('cellular_iface') or None
    ipv4 = snapshot.get('cellular_ipv4') or None

    if not iface or not ipv4:
        probe = cellular_selftest.probe(require_internet=False)
        iface = probe.iface
        ipv4 = probe.ipv4

    if not iface or not ipv4:
        state_manager.update(
            cellular_speed_test_running=False,
            cellular_speed_test_status='No cellular IP',
            cellular_speed_test_error='Cellular modem must be detected with an IPv4 address first',
        )
        raise HTTPException(status_code=409, detail='Cellular modem must be detected with an IPv4 address first')

    state_manager.update(
        cellular_speed_test_running=True,
        cellular_speed_test_status='Testing',
        cellular_speed_test_error='',
        last_event='Cellular speed test started',
    )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(CellularSpeedTestService.run, iface, ipv4),
            timeout=_CELLULAR_SPEED_TEST_TIMEOUT_SECONDS,
        )
        usage = CellularUsageService.sample(iface)
    except asyncio.TimeoutError as exc:
        state_manager.update(
            cellular_speed_test_running=False,
            cellular_speed_test_status='Timed out',
            cellular_speed_test_error='Speed test timed out',
            last_event='Cellular speed test timed out',
        )
        raise HTTPException(status_code=504, detail='Cellular speed test timed out') from exc
    except Exception as exc:
        state_manager.update(
            cellular_speed_test_running=False,
            cellular_speed_test_status='Failed',
            cellular_speed_test_error='Speed test failed',
            last_event='Cellular speed test failed',
        )
        raise HTTPException(status_code=500, detail='Cellular speed test failed') from exc
    status = 'Complete' if result.ok else 'Failed'
    state_manager.update(
        cellular_speed_test_running=False,
        cellular_speed_test_status=status,
        cellular_speed_test_download_mbps=result.download_mbps,
        cellular_speed_test_upload_mbps=result.upload_mbps,
        cellular_speed_test_latency_ms=result.latency_ms,
        cellular_speed_test_last_run_at_ms=result.tested_at_ms,
        cellular_speed_test_error=result.error or '',
        cellular_bytes_received=usage['bytes_received'],
        cellular_bytes_sent=usage['bytes_sent'],
        cellular_bytes_total=usage['bytes_total'],
        cellular_usage_reset_at_ms=usage['reset_at_ms'],
        cellular_usage_updated_at_ms=usage['updated_at_ms'],
        last_event='Cellular speed test complete' if result.ok else 'Cellular speed test failed',
    )
    return {"ok": result.ok, "speed_test": result.to_dict(), "state": state_manager.get_snapshot()}
