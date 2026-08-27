import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/app/main.py -> project root
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 1. Import unified routes from v1
from app.api.v1.router import api_router
from app.services.vision_subscriber import run_vision_subscriber
from app.websockets.router import router as websocket_router

import threading
import time as _time

from src.run_all_cameras import CameraOrchestrator

MULTI_CAMERA_IDS = ["camera_01","camera_02"]
_ROOT_VENV_PYTHON = _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

CAMERA_RESTART_POLL_INTERVAL_SECONDS = 3.0
CAMERA_CRASH_LOOP_WINDOW_SECONDS = 5.0
CAMERA_CRASH_LOOP_THRESHOLD = 3
CAMERA_CRASH_LOOP_BACKOFF_SECONDS = 30.0

_camera_orchestrator: CameraOrchestrator | None = None
_camera_supervisor_thread: threading.Thread | None = None
_camera_supervisor_stop_event = threading.Event()


def _camera_supervisor_loop(orchestrator: CameraOrchestrator, camera_ids: list[str]) -> None:
    """Restart-on-exit loop for a fixed list of camera_ids, each independent.

    Runs in a plain background thread (not asyncio) -- CameraOrchestrator
    and run_live.py subprocess management are synchronous by design (see
    src/run_all_cameras.py's own docstring).
    """
    recent_exits: dict[str, list[float]] = {cid: [] for cid in camera_ids}

    while not _camera_supervisor_stop_event.is_set():
        for camera_id in camera_ids:
            cam_proc = orchestrator.processes.get(camera_id)
            if cam_proc is None or cam_proc.popen.poll() is None:
                continue  # not launched yet, or still running

            now = _time.time()
            print(f"[camera-supervisor] camera_id={camera_id!r} exited -- restarting.")

            recent = [t for t in recent_exits[camera_id] if now - t < CAMERA_CRASH_LOOP_WINDOW_SECONDS]
            recent.append(now)
            recent_exits[camera_id] = recent
            if len(recent) >= CAMERA_CRASH_LOOP_THRESHOLD:
                print(
                    f"[camera-supervisor][WARNING] camera_id={camera_id!r} exited "
                    f"{len(recent)}x within {CAMERA_CRASH_LOOP_WINDOW_SECONDS}s -- "
                    f"backing off {CAMERA_CRASH_LOOP_BACKOFF_SECONDS}s."
                )
                if _camera_supervisor_stop_event.wait(CAMERA_CRASH_LOOP_BACKOFF_SECONDS):
                    break
                recent_exits[camera_id] = []

            try:
                orchestrator.launch_camera(camera_id)
            except Exception as e:
                print(f"[camera-supervisor][ERROR] restart failed for camera_id={camera_id!r}: {e}")

        _camera_supervisor_stop_event.wait(CAMERA_RESTART_POLL_INTERVAL_SECONDS)


app = FastAPI(
    title="Restaurant Floor Monitoring System",
    description="Backend API for real-time video monitoring and floor management",
    version="1.0.0",
)
app.include_router(websocket_router)


# Cross-process WebSocket relay (see docs/AI_BACKEND_WIRING.md's
# "Cross-process WebSocket relay" section): each camera's EventDispatcher
# runs in its own separate OS process and can only reach dashboard clients
# by publishing to Redis -- this background task is the other half, running
# inside this API process, that subscribes and re-broadcasts over the real,
# process-local ConnectionManager.
@app.on_event("startup")
async def _start_vision_subscriber() -> None:
    app.state.vision_subscriber_task = asyncio.create_task(run_vision_subscriber())


@app.on_event("shutdown")
async def _stop_vision_subscriber() -> None:
    task = getattr(app.state, "vision_subscriber_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@app.on_event("startup")
async def _start_camera_orchestrator() -> None:
    global _camera_orchestrator, _camera_supervisor_thread
    if os.environ.get("DISABLE_CAMERA_AI", "false").lower() == "true":
        print("[camera-supervisor] DISABLE_CAMERA_AI=true -- skipping camera auto-start.")
        return
    _camera_orchestrator = CameraOrchestrator(
        python_executable=str(_ROOT_VENV_PYTHON),
        extra_args=["--max-frames", "100000"],
    )
    _camera_orchestrator.logs_dir.mkdir(parents=True, exist_ok=True)
    for camera_id in MULTI_CAMERA_IDS:
        _camera_orchestrator.launch_camera(camera_id)

    _camera_supervisor_stop_event.clear()
    _camera_supervisor_thread = threading.Thread(
        target=_camera_supervisor_loop,
        args=(_camera_orchestrator, MULTI_CAMERA_IDS),
        daemon=True,
        name="camera-supervisor",
    )
    _camera_supervisor_thread.start()


@app.on_event("shutdown")
async def _stop_camera_orchestrator() -> None:
    _camera_supervisor_stop_event.set()
    if _camera_supervisor_thread is not None:
        _camera_supervisor_thread.join(timeout=10.0)
    if _camera_orchestrator is not None:
        _camera_orchestrator.shutdown()

# 2. CORS settings (to allow frontend connection)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 3. Include unified routers
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
def read_root():
    return {"message": "Welcome to Restaurant Monitoring API"}

# from app.websockets.manager import manager as ws_manager

# @app.get("/test-broadcast")
# async def test_broadcast():
#     await ws_manager.broadcast({"event_type": "TEST", "message": "hello from server"})
#     return {"sent": True}
