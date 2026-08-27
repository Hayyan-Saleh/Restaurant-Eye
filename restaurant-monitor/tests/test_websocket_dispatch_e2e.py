"""
test_websocket_dispatch_e2e.py
--------------------------------
Same-process regression test for the dispatcher -> WebSocket relay: proves
that a real event -- produced by feeding real frames from a real recorded
video through the real, unmodified LivePipeline
(src/core/live_pipeline.py) and the real EventDispatchBridge
(src/events/dispatch_bridge.py) -- genuinely reaches a real WebSocket
client connected to the real, unmodified /ws/dashboard endpoint
(backend/app/websockets/router.py). Nothing is manually pushed onto the
socket: the only call this test makes is bridge.submit(frame_output);
everything from there (EventEngine.process_frame -> EventDispatcher.dispatch
-> Redis PUBLISH on "camera_live" -> vision_subscriber's re-broadcast via
ConnectionManager) is the real, already-existing code path.

Cross-process relay update
----------------------------
EventDispatcher no longer calls `ws_manager.broadcast()` directly -- it
publishes to the Redis channel `src.events.event_dispatcher.CAMERA_LIVE_CHANNEL`
("camera_live"), and `backend/app/services/vision_subscriber.py` (started
from `backend/app/main.py`'s startup event) is what actually subscribes and
re-broadcasts. This was a required fix: `ws_manager` is a process-local
singleton, and in the real deployment `run_all_cameras.py` launches each
camera's `run_live.py` as a fully separate OS subprocess (see
run_all_cameras.py's own module docstring) -- a direct broadcast call from
inside a camera subprocess could only ever reach that subprocess's own,
always-empty ConnectionManager, never the real dashboard clients connected
to the actual `uvicorn app.main:app` process. See
docs/AI_BACKEND_WIRING.md's "Cross-process WebSocket relay" section for
the full writeup, and tests/test_cross_process_websocket_relay.py (real
`uvicorn` subprocess + real `run_live.py` subprocess + real WebSocket
client, three separate OS processes) for the test that actually proves the
cross-process case end-to-end -- this file cannot prove that by itself
(see below).

Why THIS test still runs everything in one process
-------------------------------------------------------
This file uses a `TestClient`-backed ASGI app in the SAME process as the
pipeline/bridge, entered via `with TestClient(app) as client:` so the
app's real startup event fires and `vision_subscriber` actually starts
subscribing -- `import app.websockets.manager` in both places then
resolves to the literal same ConnectionManager instance. That proves the
full relay plumbing (publish -> subscribe -> re-broadcast) is wired
correctly, and (via EventDispatchBridge's own thread/loop) that a Redis
PUBLISH issued from a different thread's own event loop than the
subscriber's is delivered correctly -- but it does NOT, by construction,
prove cross-OS-process delivery, since nothing here spawns a second real
process. That is what the separate multi-process test proves.

Slow test: constructs a real LivePipeline (loads real YOLO-pose, PoseLSTM,
and ReID models), same cost class as tests/test_detector.py /
tests/test_tracker.py. Skipped automatically (not failed) if Postgres or
Redis is unreachable, same as tests/test_event_dispatcher.py.

Run with:  python -m pytest tests/test_websocket_dispatch_e2e.py -v -s
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import cv2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy import delete, select  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.admin import Admin  # noqa: E402

from src.core.live_pipeline import LivePipeline  # noqa: E402
from src.events.dispatch_bridge import EventDispatchBridge  # noqa: E402

from tests.test_event_dispatcher import (  # noqa: E402 -- reuse the same real-service skip markers
    pytestmark,
)

REAL_CAMERA_ID = "camera_01"  # already seeded in restaurant_db with real
# zones matching config/zones_config.json's camera_01 geometry -- required
# so the real ZoneManager's zone matches actually satisfy the events_log/
# worker_states/table_states foreign keys (see backend/app/seed/seed_zones.py).
REAL_VIDEO_PATH = PROJECT_ROOT / "data" / "raw_videos" / "camera_01" / "video_001.mp4"

MAX_FRAMES_TO_FEED = 120


def _write_scratch_config(tmp_path: Path) -> Path:
    """A copy of the real config/config.json with two test-only overrides,
    neither of which touches the real file:
      - pipeline.device forced to "cpu" (this host has no CUDA device).
      - a lowered "events" section so at least one of EventEngine's 8 rules
        (almost certainly STAFF_IDLE, given this segment of camera_01's
        video) fires within MAX_FRAMES_TO_FEED frames instead of needing
        the full 300-frame/900-second production thresholds -- the same
        technique tests/test_event_dispatcher_smoke_e2e.py already uses
        (there, directly on EventEngine's constructor; here, threaded
        through the real config file the real LivePipeline/bridge load).
    """
    real_config_path = PROJECT_ROOT / "config" / "config.json"
    config = json.loads(real_config_path.read_text(encoding="utf-8"))
    config["pipeline"]["device"] = "cpu"
    config["redis"] = {"host": "127.0.0.1", "port": 6379, "db": 0}
    config["events"] = {
        "staff_idle_threshold_frames": 20,
        "delay_alert_threshold_seconds": 5.0,
        "zone_transition_min_interval_seconds": 0.2,
    }
    scratch_path = tmp_path / "config_ws_e2e.json"
    scratch_path.write_text(json.dumps(config), encoding="utf-8")
    return scratch_path


@pytest.fixture()
async def admin_token():
    """A real, disposable Admin row + a real JWT for it (backend/app/core/
    security.py's own create_access_token) -- exactly what a real dashboard
    client would send as /ws/dashboard's `token` query param."""
    async with AsyncSessionLocal() as db:
        email = f"ws_e2e_{uuid.uuid4().hex[:8]}@test.invalid"
        admin = Admin(email=email, password_hash=hash_password("not-a-real-password"))
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        admin_id = admin.id

        token = create_access_token(str(admin_id))
        try:
            yield token
        finally:
            await db.execute(delete(Admin).where(Admin.id == admin_id))
            await db.commit()


async def test_real_video_driven_event_reaches_websocket_dashboard(admin_token, tmp_path):
    scratch_config_path = _write_scratch_config(tmp_path)

    assert REAL_VIDEO_PATH.exists(), f"required real test video missing: {REAL_VIDEO_PATH}"

    # `with TestClient(app) as client:` -- NOT a bare `TestClient(app)` --
    # is required so the app's real startup event actually fires and
    # `vision_subscriber` starts subscribing to CAMERA_LIVE_CHANNEL before
    # any frame is fed below. Without this, EventDispatcher's later
    # `redis.publish()` calls would have zero subscribers and be silently
    # dropped (Redis pub/sub does not queue messages for late subscribers).
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/dashboard?token={admin_token}") as ws:
            # asyncio.create_task() in the startup handler only *schedules*
            # run_vision_subscriber() -- give it a brief moment to actually
            # reach `await pubsub.subscribe(...)` before publishing anything.
            # In practice this is dwarfed by LivePipeline's own ~10s of real
            # model loading below, but this makes the ordering requirement
            # explicit rather than relying solely on that incidental delay.
            time.sleep(0.5)

            # Real LivePipeline + real EventDispatchBridge -- identical
            # construction/lifecycle to what run_live.py's main() does.
            pipeline = LivePipeline(config_path=str(scratch_config_path))
            bridge = EventDispatchBridge(
                config=pipeline.config,
                redis_host="127.0.0.1",
                redis_port=6379,
                redis_db=0,
            )
            bridge.start()

            capture = cv2.VideoCapture(str(REAL_VIDEO_PATH))
            assert capture.isOpened(), f"could not open real test video: {REAL_VIDEO_PATH}"
            fps = capture.get(cv2.CAP_PROP_FPS)
            pipeline.set_frame_rate(fps)

            try:
                frame_index = 0
                for _ in range(MAX_FRAMES_TO_FEED):
                    ret, frame = capture.read()
                    if not ret:
                        break
                    time_seconds = frame_index / fps if fps and fps > 0 else None
                    result = pipeline.process_frame(
                        frame=frame,
                        camera_id=REAL_CAMERA_ID,
                        video_id="test_ws_e2e",
                        pose_stride=1,
                        frame_index=frame_index,
                        time_seconds=time_seconds,
                    )
                    bridge.submit(result["frame_output"])
                    frame_index += 1
            finally:
                capture.release()
                # Blocks until every FrameOutput submitted above has been run
                # through the real EventEngine + EventDispatcher (including
                # its redis.publish() call) -- by the time this returns, any
                # event this run produced has already been published to
                # CAMERA_LIVE_CHANNEL for vision_subscriber to relay.
                bridge.stop()

            message = ws.receive_json()

    print(f"\n=== Real event received over /ws/dashboard (via camera_live relay) ===\n{message}")

    assert message.get("event_type") in {
        "CUSTOMER_SEATED", "CUSTOMER_LEFT", "STAFF_IDLE", "WORKER_ACTIVE",
        "DELAY_ALERT", "ZONE_TRANSITION", "ZONE_OCCUPANCY_CHANGE",
        "TABLE_STATE_CHANGED", "NEW_ALERT",
    }
    assert message.get("camera_id") == REAL_CAMERA_ID
