# AI Pipeline → Backend Wiring: EventEngine/EventDispatcher Bridge

Closes the wiring gap where `src/events/event_engine.py` and
`src/events/event_dispatcher.py` were fully built and unit-tested but never
called from the actually-running pipeline (`src/core/live_pipeline.py`,
driven by `src/run_live.py` / `src/run_all_cameras.py`). Real detection data
now reaches Postgres, Redis, and connected WebSocket dashboard clients.

Verification date: 2026-08-14. All commands below were actually run against
this repo's real local Postgres (`restaurant_db`) and Redis instances; every
row/key printed in this document is real output from those runs.

## 1. The bridge design

`LivePipeline` (`src/core/live_pipeline.py`) is fully synchronous: a plain
`while` loop, blocking `cv2.VideoCapture.read()`, blocking CPU/GPU inference.
`EventDispatcher.dispatch()` is `async` and needs a live `AsyncSession` and a
`redis.asyncio.Redis` client. These two execution models cannot call into
each other directly.

**Chosen design: one background thread per OS process, running its own
dedicated `asyncio` event loop for the process's lifetime**, exactly as
specified in the task. No deviation from the specified approach was needed
or used.

- New module: `src/events/dispatch_bridge.py`, class `EventDispatchBridge`.
- `start()` spawns a daemon thread (`_run`) that creates a new event loop
  (`asyncio.new_event_loop()`), sets it as the thread's current loop, and
  runs `_main()` on it via `run_until_complete`.
- `submit(frame_output)` is called from the synchronous pipeline loop (main
  thread), once per processed frame. It is non-blocking: `queue.Queue.put_nowait`,
  never `asyncio.Queue` (stdlib `queue.Queue` is the thread-safe primitive
  actually required here — an `asyncio.Queue` is bound to one event loop and
  is not safe to `put()` into from the main thread while a *different* event
  loop's coroutine is doing `get()`).
- The background thread reads via
  `await loop.run_in_executor(None, self._queue.get)` — a blocking call
  handed to the executor, not a sleep-poll loop — so the thread is fully
  idle (no CPU spin) between frames.
- `stop()` pushes one sentinel object onto the same queue and joins the
  thread. **Because `queue.Queue` is FIFO and the pipeline loop is the
  queue's only producer**, every frame submitted before `stop()` is called
  is guaranteed to be dequeued and processed before the thread ever sees the
  sentinel — a clean `stop()` flushes everything already queued, with no
  separate drain step.

### Why the queue carries raw `FrameOutput`, not pre-computed events

The task's own text describes the queue payload as "(FrameOutput,
EventEngine-produced events)", which read literally would mean computing
events in the main thread. But the **critical constraint** explicitly
requires the `EventEngine` instance — like the `AsyncSession` and the redis
client — to be constructed *inside* the background thread's own event loop,
never on the main thread and handed over. Those two instructions conflict
if `EventEngine.process_frame()` runs on the main thread.

Resolution: the queue carries the raw `FrameOutput`. The background thread's
own single, long-lived `EventEngine` instance (constructed inside `_main()`,
per the constraint) computes that frame's events itself, then immediately
dispatches them. This is a deliberate reading of the spec, not an
accidental one — documented here per the task's own "if you deviate,
justify it" instruction. Justification: `EventEngine` is stateful and
diffs each frame against *its own* previous frame per `camera_id`; splitting
"ingest frame" (main thread) from "compute events" (background thread)
would force either two competing `EventEngine` instances (breaking its
statefulness) or constructing it on the main thread and handing it over
(explicitly disallowed). `EventEngine.process_frame()` is pure, synchronous,
in-memory code — no I/O — so running it on the background thread ahead of
the `await dispatcher.dispatch(...)` call costs nothing and keeps all three
long-lived resources built in exactly one place.

### Backpressure policy (queue is bounded: `maxsize=500`)

**Drop the oldest queued item, log a warning, admit the new one.**
Rationale: this is a live monitoring pipeline. `submit()` must never block
the synchronous capture/inference loop — a blocked `submit()` would desync
`frame_index` from wall-clock video time and stall detection/tracking
itself, which is strictly worse than losing a handful of stale queued
events. Staying close to real time matters more than guaranteeing delivery
of every historical event. The alternative (blocking `submit()` until space
frees up) was considered and rejected for exactly this reason.

### Long-lived resources, all constructed inside the background thread's own coroutine

Inside `EventDispatchBridge._main()` (running on the thread's own loop):

```python
db = AsyncSessionLocal()                      # one AsyncSession, process-lifetime
redis_client = aioredis.Redis(host=..., ...)  # one redis.asyncio client, process-lifetime
event_engine = EventEngine(config=self._config)
dispatcher = EventDispatcher(db, redis_client)
```

None of these are created on the main thread. This matters beyond just
following the letter of the constraint: SQLAlchemy's async engine and
`redis.asyncio`'s connection pool bind internal asyncio primitives to
whichever loop is running the first time they're actually used — this
repo's own `pytest.ini` already documents exactly this failure mode
("Event loop is closed" / "another operation is in progress") for the
backend's test suite. Constructing them inside the bridge thread's own
loop, and only ever using them from that same loop for the rest of the
process's life, avoids that class of bug entirely.

After every frame's events are dispatched, the bridge calls `await
db.commit()` — `EventDispatcher` itself deliberately never commits (see its
own docstring: committing is the caller's responsibility). Without this,
nothing would become visible to any other Postgres connection (the API,
`psql`, another camera process) until the whole process exited.

### Failure isolation

`_process_one()` never lets an exception escape: `EventEngine.process_frame()`
crashing is caught and logged (skips that frame's events, does not crash the
thread); `EventDispatcher.dispatch()` crashing is caught, the session is
rolled back, and logged. `EventDispatcher.dispatch()` itself already isolates
per-event failures internally (one bad event can't abort the rest of the
batch — see its own docstring on `begin_nested()` SAVEPOINTs). Neither the
synchronous pipeline nor the background thread can be crashed by a bad frame
or a bad event.

## 2. Files modified

| File | Change |
|---|---|
| `src/events/dispatch_bridge.py` | **New.** `EventDispatchBridge` — the whole bridge described above. |
| `src/run_live.py` | Construct + `start()` an `EventDispatchBridge` before the frame loop; `bridge.submit(event["frame_output"])` once per frame; `bridge.stop()` in the `finally:` block (covers normal completion, `KeyboardInterrupt`, and a caught `SIGTERM`). Added a `signal.signal(SIGTERM, ...)` handler (`_install_signal_handlers()`) that raises `_GracefulShutdown`, caught around the frame loop so SIGTERM runs the same flush-and-exit path as Ctrl+C. Added `--no-event-dispatch` CLI flag to disable the bridge entirely (useful for pipeline-only debugging without touching Postgres/Redis). Added `logging.basicConfig(..., force=True)` — without `force=True` the bridge's own INFO/WARNING/ERROR logs were silently swallowed, because `ultralytics`/`absl` (imported transitively via `LivePipeline`) already attach their own root-logger handlers before this module's code runs; this was caught empirically during verification (see §5). |
| `src/run_all_cameras.py` | **Not modified.** See §3 for why. |
| `backend/requirements.txt` | Added `PyJWT==2.13.0` and `pwdlib==0.3.1` — both are imported directly by `backend/app/core/security.py` (`import jwt`, `from pwdlib import PasswordHash`) but were entirely absent from this file and from the dev environment; discovered while building the WebSocket test (§6), unrelated to the bridge itself but necessary for the `/ws/dashboard` auth path to import at all. `python-jose` was already listed but is not what `security.py` actually imports. Left `python-jose` in place (not this task's scope to remove; something else may depend on it) — only added the missing, actually-used package. |
| `tests/test_websocket_dispatch_e2e.py` | **New.** Requirement 2's test — see §6. |

`backend/app/api/v1/endpoints/`, `backend/app/models/`, and
`backend/app/schemas/` were not touched, per the task's constraint.

## 3. Why `run_all_cameras.py` needed no code changes

`run_all_cameras.py` never processes a frame itself. Its entire job is
launching one independent OS subprocess running `python src/run_live.py
--camera-id <id> ...` per enabled camera (`subprocess.Popen`, not
`multiprocessing` — see its own module docstring) and supervising those
subprocesses. Since the bridge lives entirely inside `run_live.py`'s
`main()`, **every camera subprocess `run_all_cameras.py` launches
automatically gets its own `EventDispatchBridge` instance, its own
background thread, its own dedicated event loop, and its own single
`AsyncSession`/redis client/`EventEngine` — one full, independent set per
camera process, satisfying "per camera process" by construction.** No
wiring changes were needed there; only run_live.py needed to actually gain
the bridge, which then just correctly comes along for the ride under
`run_all_cameras.py`'s existing subprocess model.

## 4. End-to-end video run: real rows in Postgres, real keys in Redis

Command actually run (device forced to `cpu` in a scratch copy of
`config/config.json` — no CUDA available on this dev host; `config/config.json`
itself was never modified):

```
PYTHONPATH=. python -m src.run_live \
  --source data/raw_videos/camera_01/video_001.mp4 \
  --camera-id camera_01 --video-id video_001 \
  --config <scratch>/config_cpu.json \
  --max-frames 250
```

`camera_01` was used because it's a real, already-seeded row in
`restaurant_db.cameras` with real `zones` rows (`cam01_table_1`,
`cam01_table_2`, `cam01_service_auto`) whose polygons match
`config/zones_config.json` — i.e. real foreign-key targets, not invented
ones, so `EventDispatcher`'s inserts hit real constraints exactly like
production traffic would.

**First attempt caught a real bug**: the background thread crashed
immediately with `ModuleNotFoundError: No module named 'app'`, because
`dispatch_bridge.py` imported `app.core.database` before anything had put
`backend/` on `sys.path` (that normally happens as a side effect of
importing `event_dispatcher.py`, which wasn't imported yet at that point).
Worse, the original `_run()` swallowed the crash and still called
`self._ready.set()`, so `bridge.start()` returned successfully — the pipeline
ran for 200 frames believing events were being dispatched while the
background thread was already dead and nothing was ever queued-and-drained
(items just piled up in the queue, never dispatched). Fixed two ways:
`dispatch_bridge.py` now inserts `backend/` onto `sys.path` itself at module
import time (mirroring `event_dispatcher.py`'s own pattern, so import order
can never matter again), and `start()`/`_run()` now track a real
`_start_error` and `start()` raises `RuntimeError` if the thread died during
startup, instead of returning as if it were healthy.

After the fix, a fresh run (250 frames, with a scratch-config-only override
of `staff_idle_threshold_frames: 20` — down from the 300-frame production
default, purely so a real rule fires within a short verification run; same
technique `tests/test_event_dispatcher_smoke_e2e.py` already uses on
`EventEngine` directly) produced:

```
2026-08-14 16:48:03 INFO src.events.dispatch_bridge: EventDispatchBridge started (queue_maxsize=500)
...
2026-08-14 16:49:06 INFO src.events.dispatch_bridge: EventDispatchBridge stopped cleanly (processed=75 failed=0 dropped=0)
```

Queried directly out of `restaurant_db` and Redis immediately afterward —
these are the actual rows/keys, not paraphrased:

```
=== events_log (camera_01) ===
  id=354 type=STAFF_IDLE entity_id=worker_85334586 role=WORKER zone_id=cam01_service_auto prev=ACTIVE new=IDLE ts=2026-08-14 13:48:59.235658
  id=353 type=STAFF_IDLE entity_id=worker_74967ac6 role=WORKER zone_id=cam01_service_auto prev=ACTIVE new=IDLE ts=2026-08-14 13:48:52.185036
  id=352 type=STAFF_IDLE entity_id=worker_11d804c6 role=WORKER zone_id=cam01_service_auto prev=ACTIVE new=IDLE ts=2026-08-14 13:48:51.658688
  id=351 type=STAFF_IDLE entity_id=worker_74967ac6 role=WORKER zone_id=cam01_service_auto prev=ACTIVE new=IDLE ts=2026-08-14 13:48:37.642684
  id=350 type=STAFF_IDLE entity_id=worker_b5bbb8d2 role=WORKER zone_id=cam01_service_auto prev=ACTIVE new=IDLE ts=2026-08-14 13:48:32.722662
  id=349 type=STAFF_IDLE entity_id=worker_74967ac6 role=WORKER zone_id=cam01_service_auto prev=ACTIVE new=IDLE ts=2026-08-14 13:48:12.444834
  id=348 type=STAFF_IDLE entity_id=worker_11d804c6 role=WORKER zone_id=cam01_service_auto prev=ACTIVE new=IDLE ts=2026-08-14 13:48:12.118100

=== worker_states (camera_01) ===
  entity_id=worker_b5bbb8d2 status=WorkerActivityStatus.IDLE zone_id=cam01_service_auto last_seen_at=2026-08-14 13:48:32.722773
  entity_id=worker_11d804c6 status=WorkerActivityStatus.IDLE zone_id=cam01_service_auto last_seen_at=2026-08-14 13:48:51.658761
  entity_id=worker_74967ac6 status=WorkerActivityStatus.IDLE zone_id=cam01_service_auto last_seen_at=2026-08-14 13:48:52.185119
  entity_id=worker_85334586 status=WorkerActivityStatus.IDLE zone_id=cam01_service_auto last_seen_at=2026-08-14 13:48:59.235749

=== alerts (camera_01) ===
  (none -- idle_duration never crossed the 60s WORKER_IDLE_TOO_LONG default limit in this short run; expected, not a bug)

=== Redis (real, db 0) ===
worker:worker_11d804c6:status = IDLE
worker:worker_74967ac6:status = IDLE
worker:worker_85334586:status = IDLE
worker:worker_b5bbb8d2:status = IDLE
zone:camera_01:cam01_service_auto:count = 3
```

This is real detection data: real `worker_*` global IDs assigned by the
real Re-ID/central-identity pipeline, real zone IDs from the real
`zones_config.json` geometry, timestamped at real dispatch time. No
`customer_sessions`/`table_states` rows were produced in this particular
250-frame window — this segment of `camera_01`'s video is two workers in a
service corridor, nobody seated at a table — which is a property of the
video content, not a dispatcher gap (a separate 340-frame run at the
*production* threshold of 300 idle frames also produced zero events for the
same reason: DeepSort track-ID churn on this scene means no single track ID
survives 300 consecutive same-zone frames; lowering the threshold for
verification, as above, sidesteps that without touching real config).

## 5. WebSocket test: a real, video-driven event received over `/ws/dashboard`

New file: `tests/test_websocket_dispatch_e2e.py`.

**Why this test runs the pipeline in the same process as the WebSocket
client, instead of spawning `run_live.py` as a real subprocess:**
`backend/app/websockets/manager.py`'s `manager` is a plain module-level
`ConnectionManager()` singleton — it only means anything within one Python
process's memory. In the real deployment, `run_all_cameras.py` launches
every camera as a fully separate OS process (§3); a dispatcher's
`ws_manager.broadcast()` call inside a camera subprocess therefore
broadcasts to *that subprocess's own*, always-empty `ConnectionManager` —
never to the real dashboard clients connected to the actual `uvicorn
app.main:app` process. **This is a pre-existing property of
`event_dispatcher.py`'s design** (broadcasting via a process-local
singleton, already true before this task), not something this task's
bridge introduces or is positioned to fix — fixing it for real would mean
either running camera pipelines in-process with the API server, or adding a
cross-process relay (e.g. a Redis pub/sub channel the API subscribes to and
re-broadcasts from), and both are architecture decisions well outside
"wire the existing pieces together."

Before writing the test, an isolated experiment
(`cross_loop_experiment.py`, not checked in) confirmed empirically that
calling `ws_manager.broadcast()` from a *different* thread running its
*own* event loop (i.e. exactly what `EventDispatchBridge` does) than the
one serving the WebSocket connection still delivers successfully — no
exception, no hang, message received. So within one process, the bridge's
thread/loop design does not itself block real-time WebSocket delivery.

The test (`test_real_video_driven_event_reaches_websocket_dashboard`):
creates a real, disposable `Admin` row and a real JWT
(`create_access_token`); opens a real `TestClient` WebSocket connection to
`/ws/dashboard?token=...` (real auth path: `decode_token`, revoked-token
check against real Redis, real DB lookup — nothing mocked); constructs a
real `LivePipeline` and a real `EventDispatchBridge` exactly as
`run_live.py` does; feeds up to 120 real frames from
`data/raw_videos/camera_01/video_001.mp4` through `pipeline.process_frame()`
and `bridge.submit()`; calls `bridge.stop()` (which blocks until every
submitted frame has been through `EventEngine` → `EventDispatcher` →
`ws_manager.broadcast()`); then reads one message off the socket. Nothing
is manually pushed onto the socket — the only call the test makes is
`bridge.submit(frame_output)`.

Actual result (`pytest -s`):

```
=== Requirement 2: real event received over /ws/dashboard ===
{'event_type': 'ZONE_OCCUPANCY_CHANGE', 'camera_id': 'camera_01', 'time_seconds': 0.8666291224831899,
 'track_id': None, 'global_id': None, 'zone_id': 'cam01_service_auto', 'details': {'old_count': 2, 'new_count': 3}}
PASSED
```

## 6. Full test suite

```
python -m pytest -q
```

Result: **9 failed, 87 passed** (was **9 failed, 86 passed** before this
task — the 87th is the new WebSocket test; +1 exactly, nothing else moved).
The 9 failures are pre-existing and unrelated to this task — confirmed by
running the exact same baseline *before* touching any code: stale test
signatures against `LivePipeline._run_kbs`/`_update_person_state` (method
signatures the tests expect no longer match the current pipeline code) and
one `RestaurantTracker.update()` call-shape mismatch in
`tests/test_tracker.py`. None of the 9 touch `event_engine.py`,
`event_dispatcher.py`, `run_live.py`, or the new bridge module.

Specifically required files:

```
python -m pytest -q tests/test_event_engine.py tests/test_event_dispatcher.py tests/test_event_dispatcher_smoke_e2e.py
# 47 passed
```

## 7. SIGTERM vs. in-flight/queued events

**Empirically tested on this host (Windows 11).** A real `run_live.py`
subprocess was launched against `camera_01`'s video with dispatch enabled,
given 25 seconds to warm up (models loaded, frames actively being submitted
to the bridge), then sent `SIGTERM` via `os.kill(pid, signal.SIGTERM)`.

Result: **the process exited in 0.12 seconds, with exit code 15 (`=
SIGTERM`'s numeric value), and none of `run_live.py`'s own cleanup code ran
at all** — no `"Received SIGTERM"`, no `"EventDispatchBridge stopped
cleanly"` log line, nothing. On Windows, `os.kill(pid, signal.SIGTERM)` (and
equally `subprocess.Popen.terminate()`, and `taskkill`) call
`TerminateProcess()` directly at the OS level — this bypasses every
registered Python signal handler, the `try/finally` block, and the
background thread's queue drain entirely. **Answer: on this host, any
events still sitting in the queue (or mid-flight inside a single
`dispatch()` call) at the moment of SIGTERM are LOST, not flushed** — this
matches `run_all_cameras.py`'s own pre-existing comment about the same
limitation (`"SIGTERM isn't independently catchable on every
platform/thread... delivered as an unconditional TerminateProcess on
Windows"`), now confirmed directly against the bridge itself rather than
just the orchestrator.

`run_live.py` *does* register `signal.signal(signal.SIGTERM, ...)`
(`_install_signal_handlers()`, raising `_GracefulShutdown` into the same
`try/except/finally` that already handles `KeyboardInterrupt`/normal
completion and calls `bridge.stop()`). On POSIX (Linux/macOS — the likely
real production target), Python's signal handlers genuinely do run between
bytecode instructions, unlike Windows' `TerminateProcess`; a real `kill
-TERM <pid>` there **should** flow through the same flush-and-exit path
already verified for normal completion in §4/§5 above. **This expectation
is based on Python's documented signal-handling semantics, not verified
empirically in this environment — no Linux host was available to test
against.** Stated plainly rather than left undiscovered, per the task's
requirement: known-lost-on-Windows (verified), expected-flushed-on-POSIX
(reasoned, not verified here). A hard `SIGKILL` on any platform gives no
code anywhere a chance to run and always loses in-memory queue contents —
this is unavoidable and not specific to this design.

## 8. Postgres connection load

Queried directly against the real local Postgres instance:

```
max_connections = 100
current active connections (baseline, before any camera process) = 9
```

`backend/app/core/database.py`'s `create_async_engine(...)` sets no
explicit pool size, so SQLAlchemy's `AsyncAdaptedQueuePool` defaults apply:
`pool_size=5`, `max_overflow=10` (confirmed by inspecting the live engine
object) — i.e. up to **15** connections *per process that imports this
module*, though only as many as are actually concurrently checked out are
ever really opened.

**Per camera process (`EventDispatchBridge`):** exactly one `AsyncSession`
is created (`AsyncSessionLocal()`), used by exactly one background thread,
processing the queue strictly one item at a time (`await
dispatcher.dispatch(events); await db.commit()` — fully sequential, no
concurrent queries ever issued from that session). So each camera process's
bridge checks out **at most 1 physical Postgres connection at any instant**,
regardless of `pool_size=5`/`max_overflow=10` being available — those
higher numbers are simply never exercised because there is never more than
one in-flight query.

**× 16 camera processes** (per the task's instruction to use 16; this repo's
`config/camera_config.json` currently has 15 of 16 defined cameras
`"enabled": true`) **= 16 additional Postgres connections**, on top of
whatever the backend API's own process's pool opens (up to 15 under
concurrent request load, typically far fewer at rest — the 9 already
observed above are pre-existing, unrelated to this task).

**Total added worst case: 16 (bridge, hard ceiling — this is a real
ceiling, not an estimate, because the design is strictly one
sequential consumer per process) + up to 15 (API pool, soft ceiling,
only reached under real concurrent dashboard traffic) = up to 31**,
against a real, configured `max_connections = 100`. Comfortably within
budget; no connection-pool exhaustion risk from this change at 16 cameras.

## 9. Performance concerns observed

- **CPU-only inference is slow** on this dev host (no CUDA): roughly
  0.3–1s per frame end-to-end (detection + pose + Re-ID + KBS), observed
  directly while producing the runs in §4–§6. Production presumably runs
  with `pipeline.device: "cuda"` (the real `config/config.json`'s existing
  setting, left untouched) and would be far faster; this is a dev-host
  constraint, not a bridge-introduced one.
- `run_live.py` hardcodes `pose_stride = 1` (pre-existing, not changed by
  this task) — every frame runs full KBS/pose inference regardless of
  `config.pipeline.process_fps`, and therefore submits one `FrameOutput` to
  the bridge per raw video frame, not per "processed" frame. At native
  camera FPS (~15 for `camera_01`), that's up to 15 `submit()` calls/second
  per camera.
- In every real run performed here, the queue never approached its
  `maxsize=500` bound and `dropped=0` in every completion log line —
  `EventDispatcher.dispatch()` against local Postgres/Redis is
  consistently faster than a new frame is produced, so steady-state queue
  depth stays near zero. The realistic risk isn't sustained throughput; it's
  a **transient stall** (a slow query, a network blip to Postgres/Redis) —
  the queue would absorb a burst up to 500 frames (at ~15 fps, roughly half
  a minute of backlog) before the drop-oldest policy engages.

## 10. Assumptions made (not explicitly specified by the task)

- **Redis connection target**: `config/config.json` has no `"redis"`
  section by default. `EventDispatchBridge` reads `config.get("redis", {})`
  and defaults to `localhost:6379/db0` — the exact same convention
  `CentralIdentityStore`/`run_all_cameras.py` already use, for consistency.
- **Which camera/video to use for real verification**: chose `camera_01` /
  `video_001.mp4` specifically because it was the only readily-available
  combination already fully seeded in `restaurant_db` (real `cameras` row +
  real `zones` rows matching `zones_config.json`'s real polygon geometry) —
  required for `EventDispatcher`'s real foreign-key constraints to be
  satisfiable at all, not an arbitrary pick.
- **Lowering `events` thresholds for verification only**: used a scratch
  copy of `config/config.json` with `staff_idle_threshold_frames` lowered
  from 300 to 20 (and `delay_alert_threshold_seconds`/
  `zone_transition_min_interval_seconds` similarly lowered) purely so a real
  rule fires within a short (≤300-frame) verification run on CPU inference,
  mirroring the technique `tests/test_event_dispatcher_smoke_e2e.py`
  already established. `config/config.json` itself was never modified.
- **`device: "cpu"` override for verification**: this dev host has no CUDA
  device; used a scratch config copy, never touched the real
  `pipeline.device: "cuda"` setting in `config/config.json`.
- **Missing `PyJWT`/`pwdlib` packages**: entirely absent from both this dev
  environment and `backend/requirements.txt`, discovered only because
  `/ws/dashboard`'s auth path (`backend/app/core/security.py`) needed them
  to import at all for the WebSocket test. Installed and added to
  `backend/requirements.txt` as the minimal fix; did not touch
  `security.py` itself (out of scope) or remove the pre-existing
  (differently-used) `python-jose` entry.
- **`queue_maxsize=500`, `start()`/`stop()` timeouts of 30s/20s**: not
  specified by the task; chosen as reasonable defaults (roughly half a
  minute of frame backlog at native camera FPS) and exposed as constructor
  parameters, not hardcoded.
- **`--no-event-dispatch` CLI flag** added to `run_live.py`: not requested,
  but useful for isolating pipeline-only debugging from Postgres/Redis
  side effects; defaults to dispatch **enabled**, so existing/default
  invocations are unaffected.

---

## 11. Cross-process WebSocket relay

Follow-up fix, same date (2026-08-14), for a gap this document itself
flagged in §5 above: `EventDispatcher` broadcast directly via
`backend/app/websockets/manager.py`'s `ConnectionManager` singleton, which
is process-local. Every camera runs as its own separate OS process
(`src/run_live.py`, one per camera under `run_all_cameras.py`'s
subprocess-per-camera model), so that direct call only ever reached each
camera process's own, always-empty `ConnectionManager` — never the real
dashboard clients connected to the actual running `uvicorn app.main:app`
process. §5's WebSocket test avoided ever exercising this, because it
deliberately ran the pipeline and the WebSocket client in the *same*
process (documented there as a known limitation, not a fix).

### What changed and why

**`src/events/event_dispatcher.py`**: both `ws_manager.broadcast()` call
sites (the main per-event broadcast inside `dispatch()`, and the extra
`NEW_ALERT` signal in `_broadcast_new_alert()`) now call a new
`_publish_event()` helper instead:

```python
CAMERA_LIVE_CHANNEL = "camera_live"

async def _publish_event(self, event: dict[str, Any]) -> None:
    await self._redis.publish(CAMERA_LIVE_CHANNEL, json.dumps(event))
```

This reuses `self._redis` — the same `redis.asyncio` client already
dependency-injected into `EventDispatcher.__init__` for every other Redis
write in this class (table/worker/zone state keys) — no second connection,
no new resource. The exact same event dict that used to go to
`broadcast()` now goes to `publish()`, unchanged. The
`from app.websockets.manager import manager as ws_manager` import was
removed; `EventDispatcher` no longer imports or knows about
`ConnectionManager` at all. **Nothing else in this file changed** — every
Postgres write (`events_log`, `customer_sessions`, `alerts`,
`worker_states`, `table_states`) and every Redis *state* key write
(`table:*:status`, `worker:*:status`, `zone:*:count`, `zone:*:last_seen`)
is byte-for-byte the same code as before, per the task's explicit
constraint.

**Channel name**: the task asked to reuse `"camera_live"` "if one isn't
already defined elsewhere." A repo-wide search (`grep -rn "camera_live"`)
found no such literal string anywhere — the only existing pub/sub
convention on record is `docs/ARCHITECTURE.md`/`docs/EVENT_ENGINE.md`'s
`events:{camera_id}` **per-camera** channel scheme, which belongs to a
different, explicitly aspirational, not-yet-built architecture (three-tier
event levels, KPI engine, `/ws/live` — see `event_engine.py`'s own
docstring on why that document doesn't describe what's actually built).
Per the task's own fallback instruction, `"camera_live"` was picked as a
single **shared** channel (not one per camera_id) — this matches the
already-existing, already-unfiltered fan-out semantics of
`ConnectionManager.broadcast()` itself, which has never filtered by
camera_id; introducing per-camera channels here would add complexity with
no consumer-side benefit today.

**`backend/app/services/vision_subscriber.py`** (previously an empty
stub): `run_vision_subscriber()` — subscribes to `CAMERA_LIVE_CHANNEL` via
`app.core.redis.redis_client.pubsub()`, and calls
`ws_manager.broadcast(json.loads(message["data"]))` for every real message
received. Runs in an outer `while True:` with the whole subscribe/listen
body wrapped in `try/except Exception` — a transient Redis error logs and
reconnects after `RECONNECT_DELAY_SECONDS` (2s) instead of silently ending
the task and leaving every future event undelivered for the rest of the
process's life. `asyncio.CancelledError` is re-raised (not swallowed) so
task cancellation on shutdown works correctly.

**`backend/app/main.py`**: wired via `@app.on_event("startup")` /
`@app.on_event("shutdown")` — the task's own instruction named "startup
event" and `asyncio.create_task(...)` explicitly, so that's what was used
(confirmed still functional in this FastAPI version — it emits a
`DeprecationWarning`, harmless, pointing at the newer `lifespan` context
manager style, not a hard error).

```python
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
```

Verified directly (not just by inspection): a `with TestClient(app) as
client:` smoke check confirmed the task starts on entry (`Task pending
name='Task-2' coro=<run_vision_subscriber() ...>`) and is `done()` — cancelled
cleanly, no hang — immediately after the context exits.

**`tests/test_websocket_dispatch_e2e.py`** (the §5 same-process test):
updated, not deleted — it now enters `with TestClient(app) as client:`
(previously a bare `TestClient(app)`) so the app's real startup event
fires and `vision_subscriber` actually starts subscribing before any
frame is fed; otherwise `EventDispatcher`'s `redis.publish()` calls would
have zero subscribers and be silently dropped (Redis pub/sub does not
queue messages for late subscribers — this is a real, easy-to-hit trap,
not a hypothetical one). Its docstring was rewritten to describe the new
relay path and to be explicit that it still only proves the *plumbing* is
wired correctly (single process, both dispatcher and subscriber sharing
one Python interpreter) — not cross-process delivery, which needs an
actually separate process on each side. Re-run after the change: still
**PASSED**, receiving `{'event_type': 'ZONE_OCCUPANCY_CHANGE', 'camera_id':
'camera_01', ...}` — same event type as before, now arriving via
publish→subscribe→broadcast instead of a direct call.

### Real multi-process verification (required — not a same-process test)

New file: `tests/test_cross_process_websocket_relay.py`. Three genuinely
separate OS processes, confirmed by PID:

1. **This test's own process** — creates a real, disposable `Admin` row +
   real JWT directly against the real Postgres, and runs a real WebSocket
   client (the `websockets` package, over a real TCP loopback socket, not
   an in-process ASGI transport).
2. **A real `uvicorn app.main:app` subprocess** (`subprocess.Popen`,
   `python -m uvicorn app.main:app --host 127.0.0.1 --port 8793`) — the
   actual API server process, running the real, unmodified `/ws/dashboard`
   endpoint and the real `vision_subscriber` background task.
3. **A real `python src/run_live.py` subprocess** (`subprocess.Popen`,
   identical argv shape to `run_all_cameras.py`'s own
   `build_camera_command()`/`launch_camera()` — same executable, same
   `--camera-id`/`--video-id`/`--source`/`--config`/`--max-frames` flags,
   same `cwd=PROJECT_ROOT` + `PYTHONPATH` env-var approach) — decodes real
   frames from `data/raw_videos/camera_01/video_001.mp4` and dispatches
   real events through the real `EventDispatchBridge` (§1).

The test opens the WebSocket connection to process (2) **before** starting
process (3), then does nothing but `await ws.recv()`. If a message
arrives, it was produced entirely inside process (3), published to Redis
from inside process (3), and delivered to a socket owned by process (2) —
proving the relay crosses the actual OS process boundary that the direct
`ws_manager.broadcast()` call could not.

**Two real environment issues were hit and fixed while building this
test** (both specific to this Windows host, not the relay logic itself):

1. `httpx.get()`'s readiness-poll helper failed every attempt with
   `RemoteProtocolError: Server disconnected without sending a response`,
   even though `curl` against the same URL worked immediately. Root cause,
   confirmed by inspecting the traceback (`httpcore\_sync\http_proxy.py`
   in the call stack) and reproducing directly: this host has a **system
   SOCKS proxy** configured (Windows registry-level, not an env var —
   `env | grep -i proxy` and `os.environ` both showed nothing), which
   `httpx`'s default `trust_env=True` auto-detects via `urllib`'s
   Windows-registry proxy lookup and does not correctly bypass for
   `127.0.0.1`. Fixed with `httpx.get(..., trust_env=False)`.
2. `websockets.connect()` failed immediately with
   `InvalidProxy: socks://127.0.0.1:63025 isn't a valid proxy: scheme
   socks isn't supported` — the same underlying system SOCKS proxy,
   independently auto-detected by the `websockets` library's own
   `proxy=True` default. Fixed with `websockets.connect(ws_uri, proxy=None)`.

**A real orphaned-process false-positive was also caught and corrected**
during verification: an earlier *manual* debugging session (run directly
in this environment, not part of the test suite) left three orphaned
`python -m uvicorn ...` processes bound to ports 8793/8794/8795 (backgrounding
via shell `&` + `kill $PID`/`kill %1` did not reliably terminate the real
Windows process in this shell). The test still passed while those orphans
were alive, but for the wrong reason — `_wait_for_server_ready()` may have
been hitting an orphaned leftover process rather than the one the test
itself spawned, which would have made the "two separate processes" claim
unverifiable even though the assertion passed. Confirmed via
`Get-CimInstance Win32_Process`, killed the orphans (`Stop-Process -Force`),
confirmed zero matching processes remained, and **re-ran clean** before
trusting the result.

**Actual result, clean run** (`pytest -s`, `Get-CimInstance` confirmed no
other `uvicorn`/`run_live` process running immediately beforehand):

```
=== Cross-process relay: real event received by a WebSocket client in a
separate process from the camera pipeline that produced it ===
{'event_type': 'ZONE_OCCUPANCY_CHANGE', 'camera_id': 'camera_01', 'time_seconds': 0.8666291224831899,
 'track_id': None, 'global_id': None, 'zone_id': 'cam01_service_auto', 'details': {'old_count': 2, 'new_count': 3}}
PASSED
```

The real `uvicorn` subprocess's own log confirms the WebSocket connection
was genuinely accepted by that process (not simulated):

```
INFO:     Started server process [11596]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8793 (Press CTRL+C to quit)
INFO:     127.0.0.1:57016 - "GET / HTTP/1.1" 200 OK
INFO:     127.0.0.1:57020 - "WebSocket /ws/dashboard?token=eyJhbGci...uwI" [accepted]
INFO:     connection open
```

And the real `run_live.py` subprocess's own log confirms it was still
mid-run (frame 14 of 150) at the moment the event fired, in a completely
separate process from the one above:

```
[RoleEngine] EVIDENCE fired: gid=worker_283d36bd rule_tag=sit_nontable (will not re-fire until reset/decay episode)
  [RoleEngine] track 1: role=worker  active_state=inactive  conf=1.00  ws=9.9  cs=0.0
  [RoleEngine] track 3: role=unknown  active_state=active  conf=0.00  ws=0.0  cs=0.0
  [RoleEngine] track 2: role=unknown  active_state=active  conf=1.00  ws=11.7  cs=0.0
Frame 14/150 done
```

Full suite re-run after this fix: `python -m pytest -q` → **9 failed, 88
passed** (same 9 pre-existing/unrelated failures as before this task and
before this follow-up; +1 pass over §6's count, for the new multi-process
test). `tests/test_event_engine.py`, `tests/test_event_dispatcher.py`,
`tests/test_event_dispatcher_smoke_e2e.py` together: **47 passed**, unchanged.

### Files modified (this follow-up)

| File | Change |
|---|---|
| `src/events/event_dispatcher.py` | Both `ws_manager.broadcast()` calls replaced with `self._publish_event()` → `self._redis.publish(CAMERA_LIVE_CHANNEL, json.dumps(event))`. Removed the now-unused `ws_manager` import. No Postgres or Redis-state-key write changed. |
| `backend/app/services/vision_subscriber.py` | Implemented (was an empty stub): `run_vision_subscriber()` subscribes to `"camera_live"` and calls `ws_manager.broadcast()` per message, with reconnect-on-error. |
| `backend/app/main.py` | Added `@app.on_event("startup")`/`@app.on_event("shutdown")` handlers that create/cancel the subscriber task via `asyncio.create_task(...)`, stored on `app.state`. |
| `tests/test_websocket_dispatch_e2e.py` | Updated (not rewritten from scratch) to enter `TestClient` as a context manager so the relay actually runs in this same-process test; docstring updated to describe the new path and its scope. |
| `tests/test_cross_process_websocket_relay.py` | **New.** The real multi-process test described above. |

`backend/app/api/v1/endpoints/`, `backend/app/models/`, and
`backend/app/schemas/` were not touched, per the task's constraint.

### Assumptions made (this follow-up)

- **Channel name**: `"camera_live"`, a single shared channel rather than
  per-camera — see "What changed and why" above for the full reasoning;
  the literal string wasn't found pre-existing anywhere in the repo, so
  this is a new convention introduced by this fix, documented here as the
  task instructed.
- **`@app.on_event` over `lifespan=`**: the task's instructions used the
  words "startup event" and named `asyncio.create_task(...)` explicitly;
  `on_event` matches that literally and is still functional in the
  installed FastAPI version (confirmed by direct import/signature check
  and by the passing smoke test), so it was used as asked rather than
  substituted on our own initiative for the newer `lifespan`
  context-manager style, which the task didn't request and which would
  be a larger, out-of-scope change to `main.py`'s existing structure.
- **Reused the existing redis client, not a second connection**: the task
  didn't specify whether `EventDispatcher` should open a dedicated
  pub/sub connection; it already has one injected (`self._redis`, used for
  every other Redis write), so `PUBLISH` was issued from that same client
  rather than constructing a second one — one fewer connection per camera
  process, consistent with §8's connection-budget discussion above.
- **Port 8793 for the multi-process test's `uvicorn` instance**: arbitrary,
  chosen only to avoid colliding with a `:8000` dev server that might
  already be running; not otherwise significant.
- **`trust_env=False` / `proxy=None` workarounds are test-harness-only**:
  confined to `tests/test_cross_process_websocket_relay.py`'s own HTTP/WS
  client calls used purely to *drive* the test from this specific Windows
  host's environment. They do not affect `vision_subscriber.py`,
  `event_dispatcher.py`, or any other production code path — none of
  those make outbound HTTP/WebSocket client calls of their own.
