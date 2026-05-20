from fastapi import APIRouter, Body, HTTPException
from backend.services.cellular_usage_service import (
    CellularUsagePersistenceError,
    CellularUsageService,
)
from backend.services.settings_service import SettingsService, VALID_MARKER_STYLES
from backend.state import state_manager

router = APIRouter()


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
