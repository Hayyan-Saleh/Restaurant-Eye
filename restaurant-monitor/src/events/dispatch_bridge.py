"""
dispatch_bridge.py
--------------------
Sync-to-async bridge that wires LivePipeline's synchronous, blocking
per-frame loop (src/core/live_pipeline.py, run via src/run_live.py) into
EventEngine (src/events/event_engine.py) and EventDispatcher
(src/events/event_dispatcher.py), which are async and need a live
AsyncSession + redis.asyncio client.

Design
------
One background thread, started once per OS process (one camera == one
run_live.py process, per run_all_cameras.py's subprocess-per-camera
model) and kept alive for that process's lifetime. It runs its own
dedicated asyncio event loop via loop.run_forever()-equivalent
(run_until_complete(self._main())) -- never the main thread's loop,
which doesn't exist here since LivePipeline's loop is plain synchronous
code with no asyncio anywhere in it.

The synchronous frame loop (run_live.py) calls EventDispatchBridge.submit(
frame_output) once per processed frame. submit() is non-blocking: it puts
the FrameOutput onto a bounded queue.Queue and returns immediately, never
touching the network or the database itself.

Why the queue carries raw FrameOutput, not pre-computed events
-------------------------------------------------------------------
The task's constraints require the EventEngine instance -- like the
AsyncSession and the redis client -- to be constructed *inside* the
background thread's own event loop/coroutine, never on the main thread
and handed over. EventEngine is a single, stateful, in-memory instance
that must see every frame *in order* to correctly diff "this frame vs.
the previous frame" (see event_engine.py's docstring); splitting frame
ingestion (main thread) from event computation (background thread) would
mean either two EventEngine instances (breaking its statefulness) or
constructing it on the main thread and handing it to the background
thread (explicitly disallowed). Queuing the raw FrameOutput and letting
the background thread's own long-lived EventEngine instance compute
events resolves this: EventEngine.process_frame() is pure, in-memory,
synchronous code (no I/O), so running it on the background thread ahead
of the async dispatch() call costs nothing extra and keeps all three
long-lived resources (EventEngine, AsyncSession, redis client)
constructed in exactly one place, per the stated constraint.

Backpressure policy
--------------------
The queue is bounded (default maxsize=500). If the background thread
falls behind (slow DB/Redis, network hiccup) and the queue fills up,
submit() DROPS THE OLDEST queued item (not the newest one) and logs a
warning, then enqueues the new frame. Rationale: this is a live
monitoring pipeline -- staying close to real-time matters more than
guaranteeing delivery of every single historical event, and the
synchronous video capture/inference loop must never block on dispatch
falling behind (blocking it would desync frame_index from wall-clock
time and stall detection/tracking, which is worse than losing a handful
of stale events). This is a deliberate, documented choice, not an
oversight -- see docs/AI_BACKEND_WIRING.md for the full rationale and the
alternative considered (blocking submit()).

Shutdown / flush semantics
----------------------------
stop() enqueues a single sentinel object onto the *same* queue.Queue.
Because queue.Queue is FIFO and LivePipeline's frame loop is the queue's
only producer, every FrameOutput submitted before stop() was called is
guaranteed to be dequeued and processed before the background thread
ever sees the sentinel -- i.e. a clean stop() flushes the queue by
construction, no separate drain step required. This only covers a clean
shutdown path (normal loop exit, KeyboardInterrupt/SIGINT, or a caught
SIGTERM -- see run_live.py's signal handler). An unhandled hard kill
(SIGKILL, or SIGTERM on Windows, which the OS delivers as an unconditional
TerminateProcess bypassing all Python code -- see docs/AI_BACKEND_WIRING.md)
gives stop() no chance to run at all, and whatever is still queued (or
mid-flight inside a single dispatch() call) is lost. That trade-off is
documented, not silently swallowed.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from src.events.event_engine import EventEngine

# event_dispatcher.py inserts backend/ onto sys.path at ITS OWN import time,
# but this module may need `app.core.database` (see _main()) before
# event_dispatcher gets imported -- mirror that same bootstrap here so import
# order never matters (see docs/AI_BACKEND_WIRING.md for the
# ModuleNotFoundError this caught during real end-to-end verification).
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

if TYPE_CHECKING:
    from src.core.pipeline_output import FrameOutput

logger = logging.getLogger(__name__)

_SHUTDOWN_SENTINEL = object()

DEFAULT_QUEUE_MAXSIZE = 500
DEFAULT_THREAD_START_TIMEOUT_SECONDS = 30.0
DEFAULT_STOP_TIMEOUT_SECONDS = 20.0


class EventDispatchBridge:
    """Owns the background thread + its dedicated event loop + the
    long-lived EventEngine/AsyncSession/redis-client triple used to turn
    LivePipeline's synchronous FrameOutputs into real Postgres/Redis/
    WebSocket effects.

    One instance per OS process (i.e. one per camera under
    run_all_cameras.py's subprocess-per-camera model). Not thread-safe for
    multiple concurrent producers calling submit() -- LivePipeline's frame
    loop is single-threaded, and the FIFO-flush guarantee in stop()'s
    docstring depends on there being exactly one producer.
    """

    def __init__(
        self,
        *,
        config: dict | None = None,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
    ):
        self._config = config or {}
        self._queue: "queue.Queue[object]" = queue.Queue(maxsize=queue_maxsize)
        self._redis_host = redis_host
        self._redis_port = redis_port
        self._redis_db = redis_db

        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._start_error: BaseException | None = None
        self._dropped_count = 0
        self._processed_count = 0
        self._failed_count = 0

    # -- Lifecycle -----------------------------------------------------------

    def start(self, timeout: float = DEFAULT_THREAD_START_TIMEOUT_SECONDS) -> None:
        """Start the background thread and block until its event loop and
        long-lived resources (AsyncSession, redis client, EventEngine) are
        constructed and ready to accept work. Raises RuntimeError if the
        thread fails to become ready within `timeout` seconds (e.g. the DB
        is unreachable at construction time -- constructing an AsyncSession
        itself never touches the network, so this is unlikely, but a wedged
        thread should fail loudly at startup rather than silently accepting
        submit() calls into a void)."""
        if self._thread is not None:
            raise RuntimeError("EventDispatchBridge.start() called twice")

        self._thread = threading.Thread(
            target=self._run, name="event-dispatch-bridge", daemon=True
        )
        self._thread.start()

        if not self._ready.wait(timeout=timeout):
            raise RuntimeError(
                f"EventDispatchBridge background thread did not become ready "
                f"within {timeout}s"
            )
        if self._start_error is not None:
            raise RuntimeError(
                "EventDispatchBridge background thread failed during startup"
            ) from self._start_error
        logger.info("EventDispatchBridge started (queue_maxsize=%d)", self._queue.maxsize)

    def submit(self, frame_output: "FrameOutput") -> None:
        """Non-blocking. Called from the synchronous pipeline loop (the main
        thread), once per processed frame. See module docstring for the
        drop-oldest backpressure policy."""
        try:
            self._queue.put_nowait(frame_output)
            return
        except queue.Full:
            pass

        try:
            dropped = self._queue.get_nowait()
        except queue.Empty:
            dropped = None
        self._dropped_count += 1
        logger.warning(
            "EventDispatchBridge queue full (maxsize=%d) -- dropped oldest queued "
            "frame (camera_id=%s frame_index=%s) to admit the newest one. "
            "Total dropped so far: %d",
            self._queue.maxsize,
            getattr(dropped, "camera_id", None),
            getattr(dropped, "frame_index", None),
            self._dropped_count,
        )
        try:
            self._queue.put_nowait(frame_output)
        except queue.Full:
            # Lost a race against eviction -- should not happen with a single
            # producer thread, but never propagate into the sync frame loop.
            logger.error(
                "EventDispatchBridge queue still full immediately after eviction "
                "-- dropping the newest frame instead (camera_id=%s frame_index=%s)",
                getattr(frame_output, "camera_id", None),
                getattr(frame_output, "frame_index", None),
            )

    def stop(self, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> None:
        """Signal the background thread to drain whatever is left in the
        queue and exit, then block until it does (or `timeout` elapses).
        Idempotent -- safe to call from a `finally:`/signal-handler cleanup
        path even if start() was never called or stop() already ran."""
        if self._thread is None or not self._thread.is_alive():
            return
        if self._stopped.is_set():
            self._thread.join(timeout=timeout)
            return

        self._stopped.set()
        self._queue.put(_SHUTDOWN_SENTINEL)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.error(
                "EventDispatchBridge background thread did not exit within %ss "
                "(queue had ~%d items pending at shutdown)",
                timeout, self._queue.qsize(),
            )
        else:
            logger.info(
                "EventDispatchBridge stopped cleanly (processed=%d failed=%d dropped=%d)",
                self._processed_count, self._failed_count, self._dropped_count,
            )

    # -- Background thread ----------------------------------------------------

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main(loop))
        except Exception as exc:
            logger.exception("EventDispatchBridge background loop crashed")
            if not self._ready.is_set():
                # Crashed before finishing startup (e.g. constructing the
                # AsyncSession/redis client/EventEngine raised) -- record the
                # real cause so start() raises it instead of silently
                # returning as if the thread were healthy, then unblock the
                # waiting start() call.
                self._start_error = exc
                self._ready.set()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                logger.exception("EventDispatchBridge failed shutting down asyncgens")
            loop.close()

    async def _main(self, loop: asyncio.AbstractEventLoop) -> None:
        # Everything long-lived is constructed HERE -- inside this coroutine,
        # already running on this thread's own dedicated event loop -- per
        # the task's explicit constraint that the AsyncSession, redis
        # client, and EventEngine must never be created on the main thread
        # and handed over.
        import redis.asyncio as aioredis

        from app.core.database import AsyncSessionLocal  # backend/ import, safe: event_dispatcher.py already
        # puts backend/ on sys.path at import time.

        db = AsyncSessionLocal()
        redis_client = aioredis.Redis(
            host=self._redis_host,
            port=self._redis_port,
            db=self._redis_db,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
        event_engine = EventEngine(config=self._config)

        # EventDispatcher itself is a thin, cheap, stateless-except-for-one-
        # dedup-set object -- imported here (not at module import time) so
        # its own backend/ sys.path bootstrap (see event_dispatcher.py's
        # module docstring) happens on first use from this thread, matching
        # every other resource here.
        from src.events.event_dispatcher import EventDispatcher

        dispatcher = EventDispatcher(db, redis_client)

        self._ready.set()
        try:
            while True:
                item = await loop.run_in_executor(None, self._queue.get)
                if item is _SHUTDOWN_SENTINEL:
                    break
                await self._process_one(item, event_engine, dispatcher, db)
        finally:
            try:
                await db.close()
            except Exception:
                logger.exception("EventDispatchBridge failed closing AsyncSession")
            try:
                await redis_client.aclose()
            except Exception:
                logger.exception("EventDispatchBridge failed closing redis client")

    async def _process_one(self, frame_output, event_engine: EventEngine, dispatcher, db) -> None:
        """Compute this frame's events and dispatch them. Never raises --
        every failure is logged and swallowed so one bad frame/event can
        never crash the background thread (and, by extension, the pipeline
        it's decoupled from)."""
        try:
            events = event_engine.process_frame(frame_output)
        except Exception:
            logger.exception(
                "EventEngine.process_frame crashed for camera_id=%s frame_index=%s "
                "-- skipping this frame's events",
                getattr(frame_output, "camera_id", None),
                getattr(frame_output, "frame_index", None),
            )
            return

        if not events:
            return

        try:
            result = await dispatcher.dispatch(events)
            await db.commit()
        except Exception:
            logger.exception(
                "EventDispatcher.dispatch crashed for camera_id=%s frame_index=%s "
                "-- rolling back this frame's DB writes",
                getattr(frame_output, "camera_id", None),
                getattr(frame_output, "frame_index", None),
            )
            try:
                await db.rollback()
            except Exception:
                logger.exception("EventDispatchBridge rollback also failed")
            self._failed_count += len(events)
            return

        self._processed_count += result.succeeded
        self._failed_count += result.failed
        if result.failures:
            for failure in result.failures:
                logger.error(
                    "EventDispatchBridge: dispatch failure for event %r: %s",
                    failure.event, failure.error,
                )
