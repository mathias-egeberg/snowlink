from fastapi import APIRouter
from backend.state import state_manager

router = APIRouter()


@router.get("/state")
async def get_state() -> dict:
    """Return the current application state snapshot."""
    return state_manager.get_snapshot()
