"""
video.py
----------
MJPEG-over-HTTP streaming to the frontend, in two modes:

- **annotated** (preferred): the actual live-detection frame -- real
  bounding boxes drawn by src/core/live_pipeline.py's
  `build_annotated_frame()`, published by a running `run_live.py`
  process to Redis key `camera:{camera_id}:latest_frame` (5s TTL). This
  module polls that key and re-serves it as MJPEG.
- **raw** (fallback): a re-read of the recorded file under
  data/raw_videos/, looping indefinitely -- the original behavior of
  this module, unchanged, used whenever no `run_live.py` process is
  currently publishing frames for that camera_id (key missing/expired
  at request time).

Every response carries `X-Stream-Source: annotated` or `X-Stream-Source:
raw` so the frontend can tell which mode it got.

camera_id -> recorded-file path resolution (raw mode only) reuses
src/run_all_cameras.py's own `load_camera_video_source()` (the exact
same lookup that decides which recorded file each camera's
`run_live.py` subprocess processes) rather than a second, parallel
mapping -- see that function's own docstring.

Auth follows backend/app/websockets/router.py's existing convention for
browser-initiated connections that cannot send a custom `Authorization`
header (neither `<img>` nor `<video>` tags can): a `?token=` query
param, validated identically to the Bearer-token path. This module
reuses that router's own `_authenticate_websocket()` helper directly
(it only touches `token`/`db`, never the `WebSocket` object, so it is
already transport-agnostic) rather than re-implementing the same
decode/revocation/admin-lookup logic a second time.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import cv2
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.websockets.router import _authenticate_websocket

# src/ lives at the project root, one level above backend/ -- not on
# sys.path by default when this app is launched the normal way (cwd=
# backend/, e.g. `cd backend && uvicorn app.main:app`). Mirrors
# src/events/event_dispatcher.py's own sys.path bootstrap for the same
# cross-tree situation, just in the opposite direction.
_PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[5]
if str(_PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORT))

from src.run_all_cameras import (  # noqa: E402
    DEFAULT_VIDEOS_INDEX,
    PROJECT_ROOT,
    load_camera_video_source,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 10-15 fps is "reasonable" per the task; 12 splits the difference and
# keeps CPU-only JPEG re-encoding comfortably ahead of the frame budget.
# Applies only to the raw-file fallback generator -- the annotated
# generator's rate is governed by how fast run_live.py actually publishes
# (see _annotated_frame_generator's own docstring).
STREAM_TARGET_FPS = 12
_FRAME_INTERVAL_SECONDS = 1.0 / STREAM_TARGET_FPS

_MJPEG_BOUNDARY = b"frame"

# Separate from app.core.redis's shared `redis_client` singleton
# deliberately: that client is constructed with decode_responses=True
# (every other Redis value this backend reads/writes is text -- worker
# status strings, table state enums, etc.). Reading raw JPEG bytes through
# a decode_responses=True client would make redis-py try to UTF-8-decode
# binary image data, which either raises a UnicodeDecodeError or corrupts
# the bytes -- a real bug, not a hypothetical one. This client is
# binary-safe (decode_responses left at its False default) and used only
# for the annotated-frame key below.
_frame_redis_client = aioredis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    socket_timeout=5.0,
    socket_connect_timeout=5.0,
)

# How often to poll Redis for a new annotated frame. Short enough to feel
# live, long enough to not hammer Redis with GETs -- the actual frame
# *rate* is whatever run_live.py's own CPU-bound inference speed produces
# (~0.3-1s/frame per docs/AI_BACKEND_WIRING.md §9); this poll interval is
# just the ceiling on how quickly a new frame is *noticed*, never an
# artificial pace imposed on the source.
_FRAME_POLL_INTERVAL_SECONDS = 0.1


def _latest_frame_key(camera_id: str) -> str:
    return f"camera:{camera_id}:latest_frame"


async def _mjpeg_frame_generator(video_path: Path, request: Request):
    """Yields one multipart/x-mixed-replace part per frame, forever --
    looping back to the start of the file on EOF (release + reopen,
    rather than relying on CAP_PROP_POS_FRAMES seeking, which is not
    reliably supported across every codec/container OpenCV's FFmpeg
    backend can open) -- until the client disconnects.

    The try/finally guarantees cap.release() runs on every exit path:
    normal break, the explicit is_disconnected() check, or the
    generator being closed early by Starlette when the ASGI connection
    drops out from under an in-progress `yield` (GeneratorExit) -- so no
    OS-level video file handle is ever leaked per dropped connection.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error("video stream: could not open %s", video_path)
        return

    frames_emitted = 0
    try:
        while True:
            if await request.is_disconnected():
                logger.info(
                    "video stream: client disconnected after %d frames (%s)",
                    frames_emitted, video_path,
                )
                break

            ret, frame = cap.read()
            if not ret:
                # End of file -- loop back to the start for continuous
                # playback rather than ending the HTTP response.
                cap.release()
                cap = cv2.VideoCapture(str(video_path))
                if not cap.isOpened():
                    logger.error("video stream: failed to reopen %s for looping", video_path)
                    break
                continue

            ok, jpeg = cv2.imencode(".jpg", frame)
            if not ok:
                continue

            yield (
                b"--" + _MJPEG_BOUNDARY + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode("ascii") + b"\r\n\r\n"
                + jpeg.tobytes() + b"\r\n"
            )
            frames_emitted += 1

            await asyncio.sleep(_FRAME_INTERVAL_SECONDS)
    finally:
        cap.release()


async def _annotated_frame_generator(camera_id: str, request: Request):
    """Yields one multipart/x-mixed-replace part per NEW annotated frame
    published by a running src/run_live.py process (see
    LivePipeline.build_annotated_frame() + run_live.py's main() loop),
    polling `camera:{camera_id}:latest_frame` in Redis.

    Rate: polls every _FRAME_POLL_INTERVAL_SECONDS, but only YIELDS when
    the bytes actually changed since the last part sent -- so a poll that
    fires between two real pipeline frames (or after the pipeline has
    stopped and the key has gone stale-but-not-yet-expired) never
    re-sends an identical, stale frame. The true frame rate this client
    perceives is therefore whatever run_live.py's own inference speed
    produces, never faster, per this task's "do not throttle to a fixed
    fps" requirement -- this poll interval is a ceiling on latency, not a
    pace.

    No local resource to release on exit (unlike _mjpeg_frame_generator's
    cv2.VideoCapture): `_frame_redis_client` is a shared, module-level,
    process-lifetime connection, not opened per-request.

    Does NOT itself detect the key expiring mid-stream and does not need
    to for this task's scope: if run_live.py stops publishing, GETs
    simply keep returning the same last-seen bytes (skipped, since
    unchanged) and then None once the 5s TTL elapses, at which point this
    generator just stops yielding new parts -- the connection stays open
    on the last real frame rather than being torn down. The
    annotated-vs-raw choice itself is made once, at request time, in
    stream_video() below.
    """
    key = _latest_frame_key(camera_id)
    last_bytes: bytes | None = None
    frames_emitted = 0
    try:
        while True:
            if await request.is_disconnected():
                logger.info(
                    "video stream (annotated): client disconnected after %d frames (camera_id=%s)",
                    frames_emitted, camera_id,
                )
                break

            jpeg_bytes = await _frame_redis_client.get(key)
            if jpeg_bytes is not None and jpeg_bytes != last_bytes:
                last_bytes = jpeg_bytes
                yield (
                    b"--" + _MJPEG_BOUNDARY + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg_bytes)).encode("ascii") + b"\r\n\r\n"
                    + jpeg_bytes + b"\r\n"
                )
                frames_emitted += 1

            await asyncio.sleep(_FRAME_POLL_INTERVAL_SECONDS)
    except Exception:
        logger.exception(
            "video stream (annotated): polling loop crashed (camera_id=%s)", camera_id
        )


@router.get("/{camera_id}/stream")
async def stream_video(
    camera_id: str,
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """GET /api/v1/video/{camera_id}/stream?token=<jwt>

    Two modes, chosen once at request time:
      - annotated: camera_id has a live run_live.py process publishing
        real detection frames (Redis key `camera:{camera_id}:latest_frame`
        present) -- streams those, real bounding boxes and all.
      - raw (fallback): no live annotated frame available -- streams
        camera_id's mapped recorded file instead, looping indefinitely
        (unchanged behavior from before annotated mode existed).
    Either way the response carries `X-Stream-Source: annotated|raw`.

    401 if the token is missing/invalid (same checks as /ws/dashboard).
    404 (raw mode only) if camera_id has no recorded-video mapping or the
    mapped file is missing on disk.
    """
    is_valid = await _authenticate_websocket(token, db)
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    latest_annotated_frame = await _frame_redis_client.get(_latest_frame_key(camera_id))
    if latest_annotated_frame is not None:
        return StreamingResponse(
            _annotated_frame_generator(camera_id, request),
            media_type=f"multipart/x-mixed-replace; boundary={_MJPEG_BOUNDARY.decode()}",
            headers={"X-Stream-Source": "annotated"},
        )

    video_source = load_camera_video_source(camera_id, DEFAULT_VIDEOS_INDEX)
    if video_source is None:
        raise HTTPException(
            status_code=404,
            detail=f"No recorded video mapped to camera_id={camera_id!r}",
        )

    relative_path, _video_id = video_source
    video_path = PROJECT_ROOT / relative_path
    if not video_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"camera_id={camera_id!r} is mapped to {relative_path!r}, "
                   f"but that file does not exist on disk",
        )

    return StreamingResponse(
        _mjpeg_frame_generator(video_path, request),
        media_type=f"multipart/x-mixed-replace; boundary={_MJPEG_BOUNDARY.decode()}",
        headers={"X-Stream-Source": "raw"},
    )
