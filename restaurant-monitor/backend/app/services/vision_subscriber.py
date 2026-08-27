"""
vision_subscriber.py
-----------------------
Cross-process WebSocket relay: subscribes to the Redis pub/sub channel
`src.events.event_dispatcher.EventDispatcher` publishes every dispatched
event to (`CAMERA_LIVE_CHANNEL`, see that module), and re-broadcasts each
message to every currently-connected /ws/dashboard client via
backend/app/websockets/manager.py's ConnectionManager singleton.

Why this exists
-----------------
EventDispatcher runs inside each camera's own OS process
(src/run_live.py, one per camera under run_all_cameras.py's
subprocess-per-camera model -- see docs/AI_BACKEND_WIRING.md). The
ConnectionManager singleton it used to call directly
(`ws_manager.broadcast(event)`) is process-local: a call from inside a
camera subprocess only ever reached that subprocess's own, always-empty
ConnectionManager, never the real dashboard clients connected to the
actual `uvicorn app.main:app` process. This module is the other half of
the fix: it runs *inside* that API process (started from
backend/app/main.py's startup event), subscribes to the same Redis channel
every camera process publishes to, and is the thing that actually calls
the real, process-local ConnectionManager.broadcast() on the API server's
own event loop. See docs/AI_BACKEND_WIRING.md's "Cross-process WebSocket
relay" section for the full writeup.

This module intentionally does NOT touch how EventDispatcher writes to
Postgres or to its own Redis state keys (table:*:status, worker:*:status,
zone:*:count, zone:*:last_seen) -- those are unrelated to the WebSocket
broadcast path and are unchanged by this fix.
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.core.redis import redis_client
from app.websockets.manager import manager as ws_manager

logger = logging.getLogger(__name__)

# Must match src.events.event_dispatcher.CAMERA_LIVE_CHANNEL exactly --
# duplicated here rather than imported because src/ and backend/app/ are
# two independently-importable trees (see event_dispatcher.py's own
# "why this file lives in src/events/ but imports backend/app/models"
# note for the same cross-tree situation in the other direction) and this
# module must not depend on anything under src/ to stay import-light for
# the API process's startup path.
CAMERA_LIVE_CHANNEL = "camera_live"

# How long to wait before retrying after the subscribe loop errors out
# (e.g. Redis briefly unreachable) -- this task is meant to run for the
# whole lifetime of the API process, so it reconnects instead of dying
# silently and leaving every future event undelivered.
RECONNECT_DELAY_SECONDS = 2.0


async def run_vision_subscriber() -> None:
    """Runs until cancelled. Intended to be started once, as a single
    asyncio.Task, from backend/app/main.py's startup event, and cancelled
    from its shutdown event."""
    while True:
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(CAMERA_LIVE_CHANNEL)
            logger.info("vision_subscriber: subscribed to redis channel %r", CAMERA_LIVE_CHANNEL)

            async for message in pubsub.listen():
                if message.get("type") != "message":
                    # "subscribe"/"unsubscribe" confirmation messages, etc.
                    continue

                raw = message.get("data")
                try:
                    event = json.loads(raw)
                except (TypeError, ValueError):
                    logger.error("vision_subscriber: could not decode message payload: %r", raw)
                    continue

                await ws_manager.broadcast(event)
        except asyncio.CancelledError:
            logger.info("vision_subscriber: cancelled -- shutting down")
            raise
        except Exception:
            logger.exception(
                "vision_subscriber: subscribe loop crashed -- reconnecting in %ss",
                RECONNECT_DELAY_SECONDS,
            )
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        finally:
            try:
                await pubsub.unsubscribe(CAMERA_LIVE_CHANNEL)
            except Exception:
                pass
            await pubsub.aclose()
