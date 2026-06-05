"""Debug / control endpoints for the snow-depth grid service."""
from __future__ import annotations

from fastapi import APIRouter

from backend.services.snow_grid_service import SnowGridService

router = APIRouter(prefix="/snow-grid", tags=["snow-grid"])


@router.get("/config")
async def get_snow_grid_config() -> dict:
    """Effective snow-grid config + current origin / cache stats."""
    return SnowGridService.get().get_config()


@router.get("/tiles/active")
async def get_active_snow_tiles() -> dict:
    """All tiles currently in the active set around the snowcat.

    Returns the same per-tile shape that ``/ws/state`` broadcasts.
    """
    svc = SnowGridService.get()
    return {
        "config": svc.get_config(),
        "tiles": svc.get_active_tile_messages(),
    }


@router.post("/simulate/reset")
async def reset_snow_grid_simulation() -> dict:
    """Clear all cached tiles and let origin re-establish from next GPS fix."""
    SnowGridService.get().reset_simulation()
    return {"ok": True}
