from fastapi import APIRouter, Response
from backend.state import state_manager

router = APIRouter()


@router.get("/state")
async def get_state(response: Response) -> dict:
    """Return the current application state snapshot."""
    response.headers["Cache-Control"] = "no-store"
    return state_manager.get_snapshot()
