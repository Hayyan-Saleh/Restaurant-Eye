"""
test_cross_process_websocket_relay.py
----------------------------------------
Real multi-process proof that the "Cross-process WebSocket relay" fix
(see docs/AI_BACKEND_WIRING.md) actually works across separate OS
processes -- the exact scenario the earlier same-process test
(tests/test_websocket_dispatch_e2e.py) could not, and does not claim to,
prove by itself.

Three independent real processes are involved:
  1. This test's own process (pytest) -- creates a real disposable Admin +
     JWT directly against the real Postgres, and runs a real WebSocket
     client (the `websockets` package) over a real TCP socket.
  2. A real `uvicorn app.main:app` subprocess -- the actual API server,
     serving the real, unmodified /ws/dashboard endpoint and running the
     real `vision_subscriber` background task started from
     backend/app/main.py's startup event.
  3. A real `python src/run_live.py` subprocess (subprocess.Popen, the
     exact same invocation shape run_all_cameras.py itself uses -- see
     its own build_camera_command()) -- decodes real frames from a real
     recorded video and dispatches real events through the real
     EventDispatchBridge -> EventEngine -> EventDispatcher chain, which
     now publishes to Redis instead of calling ws_manager.broadcast()
     directly.

Nothing is manually pushed onto the socket. If this passes, a real event
produced entirely inside process (3) was delivered to a WebSocket client
connected to the entirely separate process (2) -- proving the Redis
"camera_live" pub/sub relay (src/events/event_dispatcher.py's publish +
backend/app/services/vision_subscriber.py's subscribe) actually crosses
the OS process boundary that a direct ws_manager.broadcast() call could
not (that was the whole bug this file exists to catch).

Run with:  python -m pytest tests/test_cross_process_websocket_relay.py -v -s
(Slow: starts a real uvicorn server and a real run_live.py subprocess that
loads real CV models and decodes real video.)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

from tests.test_event_dispatcher import (  # noqa: E402 -- reuse the same real-service skip markers
    pytestmark,
)

API_HOST = "127.0.0.1"
API_PORT = 8793  # uncommon port -- avoids colliding with a dev server on 8000
REAL_CAMERA_ID = "camera_01"
REAL_VIDEO_PATH = PROJECT_ROOT / "data" / "raw_videos" / "camera_01" / "video_001.mp4"
SERVER_READY_TIMEOUT_SECONDS = 30.0
EVENT_WAIT_TIMEOUT_SECONDS = 120.0
MAX_CAMERA_FRAMES = 150


def _write_scratch_config(tmp_path: Path) -> Path:
    """Same technique as tests/test_websocket_dispatch_e2e.py: a scratch
    copy of the real config/config.json, never the real file itself.
    device forced to "cpu" (no CUDA on this host) and the "events"
    thresholds lowered so a real rule fires within MAX_CAMERA_FRAMES."""
    real_config_path = PROJECT_ROOT / "config" / "config.json"
    config = json.loads(real_config_path.read_text(encoding="utf-8"))
    config["pipeline"]["device"] = "cpu"
    config["redis"] = {"host": "127.0.0.1", "port": 6379, "db": 0}
    config["events"] = {
        "staff_idle_threshold_frames": 20,
        "delay_alert_threshold_seconds": 5.0,
        "zone_transition_min_interval_seconds": 0.2,
    }
    scratch_path = tmp_path / "config_cross_process.json"
    scratch_path.write_text(json.dumps(config), encoding="utf-8")
    return scratch_path


async def _create_admin_token() -> tuple[str, str]:
    """A real, disposable Admin row + a real JWT, created directly against
    the real Postgres from this test's own process (the test harness --
    not either of the two subprocesses under test)."""
    from app.core.database import AsyncSessionLocal
    from app.core.security import create_access_token, hash_password
    from app.models.admin import Admin

    async with AsyncSessionLocal() as db:
        email = f"xproc_e2e_{uuid.uuid4().hex[:8]}@test.invalid"
        admin = Admin(email=email, password_hash=hash_password("not-a-real-password"))
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        admin_id = str(admin.id)

    token = create_access_token(admin_id)
    return admin_id, token


async def _delete_admin(admin_id: str) -> None:
    from sqlalchemy import delete

    from app.core.database import AsyncSessionLocal
    from app.models.admin import Admin

    async with AsyncSessionLocal() as db:
        await db.execute(delete(Admin).where(Admin.id == admin_id))
        await db.commit()


def _wait_for_server_ready(base_url: str, timeout: float, log_path: Path) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            # trust_env=False: this Windows host has a system-level proxy
            # setting that httpx's default trust_env=True picks up via
            # urllib's Windows-registry proxy detection, which does not
            # correctly bypass it for 127.0.0.1 -- every request comes back
            # "Server disconnected without sending a response" even though
            # the server is healthy (confirmed directly with curl, which
            # does not hit this path). Empirically verified during this
            # task's own verification -- see docs/AI_BACKEND_WIRING.md.
            r = httpx.get(base_url + "/", timeout=2.0, trust_env=False)
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 -- retry until timeout
            last_error = exc
        time.sleep(0.5)
    log_tail = ""
    if log_path.exists():
        log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise RuntimeError(
        f"uvicorn server did not become ready within {timeout}s "
        f"(last error: {last_error})\n--- server log tail ---\n{log_tail}"
    )


async def test_real_event_crosses_from_camera_subprocess_to_api_process(tmp_path):
    assert REAL_VIDEO_PATH.exists(), f"required real test video missing: {REAL_VIDEO_PATH}"

    admin_id, token = await _create_admin_token()

    server_log_path = tmp_path / "uvicorn_server.log"
    camera_log_path = tmp_path / "run_live_camera.log"

    server_env = os.environ.copy()
    server_env["PYTHONPATH"] = str(BACKEND_DIR)

    # -- Process 2: the real API server, as a real separate OS process ------
    with open(server_log_path, "w", encoding="utf-8") as server_log:
        server_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", API_HOST, "--port", str(API_PORT)],
            cwd=str(BACKEND_DIR), env=server_env,
            stdout=server_log, stderr=subprocess.STDOUT,
        )

    camera_proc: subprocess.Popen | None = None
    camera_log = None
    try:
        _wait_for_server_ready(f"http://{API_HOST}:{API_PORT}", SERVER_READY_TIMEOUT_SECONDS, server_log_path)

        ws_uri = f"ws://{API_HOST}:{API_PORT}/ws/dashboard?token={token}"
        # proxy=None: this host has a system SOCKS proxy configured
        # (discovered empirically -- see the trust_env=False comment above
        # for the same underlying cause hitting httpx). websockets'
        # default (proxy=True) auto-detects it and fails immediately since
        # it can't speak SOCKS to a plain "socks://" URL; explicitly
        # disabling proxy use connects directly, which is correct for a
        # loopback address in any case.
        async with websockets.connect(ws_uri, proxy=None) as ws:
            # -- Process 3: a real, fully separate run_live.py subprocess,
            # launched exactly the way run_all_cameras.py's own
            # build_camera_command()/launch_camera() do it: subprocess.Popen,
            # cwd=PROJECT_ROOT, PYTHONPATH set via env (see that module's own
            # comment on why bare `python src/run_live.py` needs this).
            scratch_config_path = _write_scratch_config(tmp_path)
            camera_env = os.environ.copy()
            camera_env["PYTHONPATH"] = str(PROJECT_ROOT)
            camera_cmd = [
                sys.executable, str(PROJECT_ROOT / "src" / "run_live.py"),
                "--camera-id", REAL_CAMERA_ID,
                "--video-id", "cross_process_test",
                "--source", str(REAL_VIDEO_PATH),
                "--config", str(scratch_config_path),
                "--max-frames", str(MAX_CAMERA_FRAMES),
            ]
            camera_log = open(camera_log_path, "w", encoding="utf-8")
            camera_proc = subprocess.Popen(
                camera_cmd, cwd=str(PROJECT_ROOT), env=camera_env,
                stdout=camera_log, stderr=subprocess.STDOUT,
            )

            # run_live.py now also publishes a CAMERA_STREAM_STARTED lifecycle
            # event to this same channel before the frame loop begins (see
            # docs/VIDEO_STREAMING.md's "Stream lifecycle events" section) --
            # that arrives first, ahead of any real detection event. Skip
            # past it so this test still proves what it always proved: a
            # real *detection* event, computed inside the camera subprocess,
            # crosses to a WebSocket client in this separate process.
            event = None
            deadline = time.time() + EVENT_WAIT_TIMEOUT_SECONDS
            while time.time() < deadline:
                message = await asyncio.wait_for(
                    ws.recv(), timeout=max(0.1, deadline - time.time())
                )
                candidate = json.loads(message)
                if candidate.get("event_type") == "CAMERA_STREAM_STARTED":
                    continue
                event = candidate
                break
            assert event is not None, "no detection event received (only lifecycle events?)"
    finally:
        if camera_proc is not None and camera_proc.poll() is None:
            camera_proc.terminate()
        server_proc.terminate()
        try:
            server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        if camera_proc is not None:
            try:
                camera_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                camera_proc.kill()
        if camera_log is not None:
            camera_log.close()
        await _delete_admin(admin_id)

    print(
        "\n=== Cross-process relay: real event received by a WebSocket client "
        "in a separate process from the camera pipeline that produced it ===\n"
        f"{event}"
    )

    assert event.get("event_type") in {
        "CUSTOMER_SEATED", "CUSTOMER_LEFT", "STAFF_IDLE", "WORKER_ACTIVE",
        "DELAY_ALERT", "ZONE_TRANSITION", "ZONE_OCCUPANCY_CHANGE",
        "TABLE_STATE_CHANGED", "NEW_ALERT",
    }
    assert event.get("camera_id") == REAL_CAMERA_ID
