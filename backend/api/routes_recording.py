from fastapi import APIRouter, Body

from backend.services.recording_service import RecordingService

router = APIRouter()


@router.post("/recording/start")
async def start_recording(payload: dict = Body(...)) -> dict:
    session_name = str(payload.get("session_name", "")).strip()
    streams      = payload.get("streams", ["gnss", "imu", "system"])
    if not isinstance(streams, list):
        streams = ["gnss", "imu", "system"]
    valid = {"gnss", "imu", "system"}
    streams = [s for s in streams if s in valid] or ["gnss", "imu", "system"]
    return RecordingService.get().start(session_name, streams)


@router.post("/recording/stop")
async def stop_recording() -> dict:
    return RecordingService.get().stop()


@router.get("/recording/sessions")
async def list_sessions() -> dict:
    return {"sessions": RecordingService.get().list_sessions()}
