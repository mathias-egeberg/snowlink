from fastapi import APIRouter, Body, HTTPException
from backend.state import state_manager, VALID_DEVICE_KEYS

router = APIRouter()


@router.post("/control/device")
async def toggle_device(payload: dict = Body(...)) -> dict:
    """Toggle a device on/off.

    Body: {"device_key": "heat_roof_on"}
    Valid keys: heat_roof_on, heat_gutter_on, pump_on, sensor_light_on
    """
    device_key = payload.get("device_key", "")
    if device_key not in VALID_DEVICE_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid device_key. Valid: {sorted(VALID_DEVICE_KEYS)}",
        )
    state_manager.toggle_device(device_key)
    return {"ok": True, "state": state_manager.get_snapshot()}
