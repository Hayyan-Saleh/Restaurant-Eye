# Video Streaming (MJPEG-over-HTTP)

`GET /api/v1/video/{camera_id}/stream` — implemented in
`backend/app/api/v1/endpoints/video.py`, registered in
`backend/app/api/v1/router.py` under prefix `/video`. Streams either the
**real, live-detection annotated frame** (bounding boxes, zone outlines,
table-state overlay — whatever a running `src/run_live.py` process is
actually seeing) or a **looping raw recorded file** as a fallback, as
`multipart/x-mixed-replace` MJPEG that any `<img>`/`<video>` tag can
consume directly.

Verification date: 2026-08-14. All commands/output below are real, run
against this repo's real local Postgres/Redis and real recorded video.

## 1. URL shape and auth (frontend-facing)

```
GET /api/v1/video/{camera_id}/stream?token=<jwt>
```

- `camera_id`: e.g. `camera_01` — same IDs used everywhere else in this
  system (`config/camera_config.json`, `data/metadata/videos/videos_index.json`).
- `token`: a query param, **not** an `Authorization` header — neither
  `<img src="...">` nor `<video src="...">` can send custom headers.
  Validated identically to `/ws/dashboard`'s own token check (this module
  reuses `backend/app/websockets/router.py`'s `_authenticate_websocket()`
  directly: decode → check Redis token revocation → confirm the admin
  still exists). A frontend that already knows how to attach `?token=`
  to `/ws/dashboard` needs no new auth logic for this endpoint.
- **401** `{"detail": "Invalid or missing token"}` if the token is
  missing/invalid/revoked/for a nonexistent admin.
- **404** (raw-fallback path only) if `camera_id` has no recorded-video
  mapping in `videos_index.json`, or the mapped file is missing on disk.

Response is a single, long-lived HTTP connection
(`multipart/x-mixed-replace; boundary=frame`) that keeps sending new
parts until the client disconnects — treat it exactly like a standard
MJPEG camera stream (`<img src="/api/v1/video/camera_01/stream?token=...">`
works directly in a browser).

## 2. `X-Stream-Source` header — annotated vs. raw

Every response carries one of:

| Header value | Meaning |
|---|---|
| `X-Stream-Source: annotated` | Real live-detection frames: a `run_live.py` process is currently running for this `camera_id` and publishing frames with real bounding boxes / zone outlines / table-state coloring drawn in. |
| `X-Stream-Source: raw` | Fallback: no live annotated frame is available right now (no `run_live.py` process running for this `camera_id`, or it hasn't published in the last 5 seconds). Instead streams the recorded file mapped to `camera_id` in `videos_index.json`, looping indefinitely. |

**The choice is made once, at request time** (one Redis `GET` before the
`StreamingResponse` is even constructed) and holds for the lifetime of
that connection — it does not switch mid-stream. A frontend that wants
to detect "the pipeline just started/stopped" should reconnect
periodically (or on its own signal, e.g. a WebSocket event) rather than
expect this one connection's mode to change.

If `run_live.py` stops publishing partway through an *annotated* stream
(crash, `--max-frames` limit hit, etc.), the connection is **not** torn
down: once the Redis key's 5s TTL expires, the polling generator simply
stops receiving new bytes and stops yielding new parts — the client just
stops receiving frames (last frame stays on screen) rather than getting
an error or an abrupt close. Reconnecting is what gets it a fresh
`raw`-fallback response.

## 3. How annotated mode actually works

### Publish side — `src/run_live.py` / `src/core/live_pipeline.py`

No new drawing code was added. `run_live.py`'s existing per-frame loop
already draws everything onto its `frame` variable **in place**, for the
local `--show` debug window:

```python
pipeline.draw_zones(frame, camera_id)          # zone outline polygons (green)
pipeline.draw_tracks(frame, event["tracked_objects"])   # person boxes, role-colored
draw_custom_table_states(frame, camera_id, pipeline)    # table state overlay (green/orange/red)
```

The streaming feature just **encodes and publishes that same, already-fully-annotated
`frame`** right after this block runs — no second drawing pass, no
`.copy()`, no new method on `LivePipeline`:

```python
ok, jpeg = cv2.imencode(".jpg", frame)
if ok:
    frame_publish_client.set(f"camera:{camera_id}:latest_frame", jpeg.tobytes(), ex=5)
```

**Implementation note (corrected during development, not left
undiscovered):** the first version of this feature added a new,
independent `LivePipeline.build_annotated_frame()` method that only drew
person bounding boxes from `FrameOutput.tracks`, called *before*
`draw_zones`/`draw_custom_table_states` ran — meaning the published frame
was missing zone outlines and table-state coloring that the local
`--show` window has. That method has been deleted entirely. The
publish call was moved to run *after* the existing draw block, publishing
the same `frame` object those calls already mutated. §5 below documents
the real, decoded-pixel-level verification that this specific defect is
fixed (zone-outline green pixels are present in the published frame, not
just track-box colors).

- **Redis key**: `camera:{camera_id}:latest_frame` — raw JPEG bytes (not
  base64, not JSON-wrapped).
- **TTL**: `EX 5` (5 seconds). If `run_live.py` stops or crashes, the key
  self-expires with no separate "pipeline died" signal needed — the
  streaming endpoint's request-time fallback check (§2) picks this up
  automatically on the next connection.
- **Rate**: **not throttled**. One `SET` per processed frame, exactly as
  fast as the pipeline naturally produces frames. On this dev host (no
  CUDA), that's the real, honest ~0.3–1s/frame CPU-bound inference speed
  documented in `docs/AI_BACKEND_WIRING.md` §9 — deliberately not
  papered over with artificial pacing.
- **Redis client**: a separate, plain **synchronous** `redis.Redis(...)`
  client (`frame_publish_client`), constructed once in `main()` and
  closed in the existing `finally:` block. Deliberately *not* the same
  machinery as `EventDispatchBridge` (`src/events/dispatch_bridge.py`):
  that bridge exists because `EventDispatcher` is async and needs a
  background thread + its own event loop to be called from this
  synchronous pipeline loop at all. Publishing one JPEG per frame is a
  single, one-shot, synchronous `SET` call — no async machinery needed,
  matching the task's explicit "much simpler, purely synchronous need."
  A short `socket_timeout=2.0`/`socket_connect_timeout=2.0` bounds how
  long a hung Redis connection could ever block the main frame loop, and
  the whole encode+publish call is wrapped in `try/except` so a Redis
  hiccup logs a warning and never crashes the pipeline (same resilience
  posture as every other Redis write in this codebase).

### Consume side — `backend/app/api/v1/endpoints/video.py`

- `_frame_redis_client`: a **separate** `redis.asyncio.Redis` client from
  `app.core.redis`'s shared singleton, specifically because that shared
  client is constructed with `decode_responses=True` (every other value
  it reads/writes in this backend is text). Reading raw JPEG bytes
  through a `decode_responses=True` client would make redis-py try to
  UTF-8-decode binary image data — a real bug (`UnicodeDecodeError` or
  corrupted bytes), not a hypothetical one. This client leaves
  `decode_responses` at its `False` default.
- `_annotated_frame_generator()` polls the key every
  `_FRAME_POLL_INTERVAL_SECONDS = 0.1` (100ms) and **only yields a new
  multipart part when the bytes actually changed** since the last one
  sent — so a poll that fires between two real pipeline frames (or after
  the key has gone stale) never re-sends an identical frame. The poll
  interval is a latency ceiling on how quickly a new frame is *noticed*,
  never a pace imposed on the source (which stays exactly as fast/slow as
  `run_live.py` itself runs).

## 4. Raw-fallback mode (unchanged, pre-existing behavior)

`_mjpeg_frame_generator()` — untouched by this task. Opens the recorded
file with `cv2.VideoCapture`, reads/encodes/yields at a fixed
`STREAM_TARGET_FPS = 12` (`asyncio.sleep` between frames), loops back to
the start on EOF via release+reopen (not a `CAP_PROP_POS_FRAMES` seek —
not reliably supported across every codec OpenCV's FFmpeg backend can
open), and checks `await request.is_disconnected()` once per frame inside
a `try/finally` that always releases the `cv2.VideoCapture` — so a
dropped client never leaks an open video file handle. `camera_id` →
recorded-file path resolution reuses `src/run_all_cameras.py`'s own
`load_camera_video_source()` (the exact same lookup that decides which
file each camera's `run_live.py` subprocess processes), not a second,
parallel mapping.

## 5. Real verification (this session, 2026-08-14)

### 5a. Annotated mode: real `run_live.py` subprocess → real drawn frame

A real `run_live.py` subprocess was launched (`subprocess.Popen`, dispatch
enabled, `--camera-id camera_01`, real recorded video, CPU device) and a
real `uvicorn` subprocess served the API. After a 20s warm-up (model
load + several real frames published), the endpoint was requested with a
real JWT:

```
=== TEST 1: annotated stream (camera_id=camera_01, run_live.py pid=3228 alive=True) ===
status: 200
X-Stream-Source: annotated
content-type: multipart/x-mixed-replace; boundary=frame
saved raw JPEG bytes to .../annotated_frame_decoded.jpg (693861 bytes)
decoded image dimensions: 1280x1440
Canny edge pixel count: 237167, rectangle-like contours found: 16
-- role box color pixel counts (tolerance=30) --
  unknown/white (255, 255, 255): 105852 pixels
  customer/blue (255, 100, 0): 56 pixels
  worker/orange (0, 140, 255): 0 pixels
  inactive-worker/grey (128, 128, 128): 146925 pixels
-- zone-outline / table-state color pixel counts (tolerance=30) --
  zone-outline/table-empty green (0, 255, 0): 10013 pixels
  table-occupied red (0, 0, 255): 0 pixels
  table-dirty orange (0, 165, 255): 0 pixels

RESULT: any_box_color=True, any_zone_color=True, rect_like_contours=16
```

The received bytes were decoded with real `cv2.imdecode` (not just
"no exception was thrown"): a real 1280×1440 JPEG, 16 rectangle-shaped
contours found via `cv2.Canny` + `cv2.findContours` +
`cv2.approxPolyDP`, and — the specific check added to confirm the
fix — **10,013 pixels matching `draw_zones`'s pure green `(0,255,0)`
line color** (well above JPEG-compression noise), proving the published
frame contains real zone-outline lines, not just person boxes. The
`inactive-worker/grey` count (146,925 pixels) and `worker/orange` count
(0 pixels) are internally consistent with the pipeline's own concurrent
log output for that exact frame (`role=worker active_state=inactive`) —
independent confirmation this is a real, live-detection-driven frame, not
a static or fabricated one. The decoded frame was also saved as a PNG and
visually inspected: real green zone polygons with `cam01_table_1: Empty`
/ `cam01_table_2: Empty` labels, plus a person tracking box with a
`role|action|zone` + `id:`/`gid:` label, both present in the same image.

### 5b. Raw fallback: no `run_live.py` running

The camera subprocess was terminated, then the code waited 7s (past the
5s Redis TTL) before requesting again:

```
=== TEST 2: raw fallback (camera_id=camera_01, no run_live.py running) ===
status: 200
X-Stream-Source: raw
content-type: multipart/x-mixed-replace; boundary=frame
raw fallback: received a real JPEG frame, 615092 bytes
```

615092 bytes matches the exact frame size independently observed in the
original (pre-annotated-mode) raw-endpoint verification — same file, same
codec, same behavior, confirming the fallback path is genuinely
unmodified.

### 5c. 401 / 404

```
=== TEST 3: no token -> 401 ===
status: 401 body: {"detail":"Invalid or missing token"}

=== TEST 4: unmapped camera_id -> 404 ===
status: 404 body: {"detail":"No recorded video mapped to camera_id='camera_03'"}
```

### 5d. No orphaned processes

`Get-CimInstance Win32_Process` (the same technique used throughout this
repo's prior verification work) immediately after the script exited,
filtered for `uvicorn`/`run_live` command lines, returned **zero rows** —
both subprocesses terminated cleanly (`terminate()` + `wait()` in a
`finally:` block on both the test harness and inside `run_live.py`
itself, per `docs/AI_BACKEND_WIRING.md`'s already-established shutdown
pattern).

### 5e. Full test suite

```
python -m pytest -q
```

Result: **9 failed, 88 passed** — identical to the pre-existing baseline
(same 9 stale/unrelated failures already documented in
`docs/AI_BACKEND_WIRING.md` §6, none touching `run_live.py`,
`live_pipeline.py`, or anything under `backend/app/api/`). No regression.

## 6. Assumptions made (not explicit in the task instructions)

- **Role/color convention**: the task suggested "green=staff, blue=customer"
  as a *default* but explicitly said to check for and reuse an existing
  convention first. One already exists —
  `LivePipeline.ROLE_COLORS` (`unknown`→white, `customer`→blue `(255,100,0)`,
  `worker`→orange `(0,140,255)`, with inactive workers greyed out to
  `(128,128,128)`) — already used by `draw_tracks()`/the `--show` debug
  window. That existing convention was reused as-is; no second
  green/blue scheme was introduced.
- **Publish every frame, not just frames with tracks**: the task's literal
  wording ("for each processed frame that has track data ... draw the
  boxes") could be read as "only publish frames where tracks exist."
  Implemented instead as "publish every processed frame; only frames with
  tracks get any boxes drawn on them" (frames with zero current tracks
  still get zone outlines from `draw_zones` and table coloring from
  `draw_custom_table_states`, which don't depend on tracks existing).
  Gating the publish itself on track presence would make the stream
  visibly freeze or bounce to raw-fallback mode every time the scene is
  briefly empty of people, which seemed like the less useful behavior for
  a "live view" and wasn't what the task's Redis-key/TTL design
  (self-expire only on process death, not per-frame content) implies.
- **`videos_index.json` structure**: unchanged assumption from the
  original raw-endpoint task — a flat JSON array of
  `{"camera_id", "video_id", "path", ...}` records; `load_camera_video_source()`
  returns the *first* matching record for a given `camera_id` (a camera
  with multiple recorded videos, e.g. `camera_11`, only ever streams its
  first-listed one via this endpoint).
- **Four deviations carried over unchanged from the original raw-endpoint
  task** (per explicit instruction not to revisit them): EOF looping via
  release+reopen rather than a `CAP_PROP_POS_FRAMES` seek; reusing
  `_authenticate_websocket` via direct import rather than re-implementing
  it; a fixed (not configurable) `STREAM_TARGET_FPS`; the extra 404 case
  when a mapped file is missing on disk; and a per-part `Content-Length`
  header in the raw generator's MJPEG framing. All four apply only to
  raw-fallback mode; the new annotated-mode generator reuses that same
  `Content-Type`/`Content-Length` per-part framing for consistency
  between the two modes, even though `Content-Length` is technically
  redundant with the multipart boundary itself.

## 7. Stream lifecycle events

`GET /api/v1/video/{camera_id}/stream` (§2 above) only tells a frontend
"annotated vs. raw" **at the moment it connects** — it does not push any
signal when a camera's live-detection pipeline actually starts or stops
mid-session. Previously the only way to notice a pipeline start/stop was
to reconnect and re-check `X-Stream-Source`. This section adds two
explicit WebSocket events for that instead.

### 7a. Event types and shape

`src/run_live.py` now publishes two lifecycle events to the same Redis
pub/sub channel `src/events/event_dispatcher.py` already publishes every
other event to — `CAMERA_LIVE_CHANNEL` (`"camera_live"`), imported from
that module (`from src.events.event_dispatcher import CAMERA_LIVE_CHANNEL`),
not a new or hardcoded channel name:

| Event | When |
|---|---|
| `CAMERA_STREAM_STARTED` | Right after `frame_publish_client` (the synchronous Redis client) and `EventDispatchBridge` are both constructed and ready, once `camera_id` is fully resolved (including the `--first-video` override) — before the frame loop begins. |
| `CAMERA_STREAM_STOPPED` | In the existing `finally:` block, right before `frame_publish_client.close()` — i.e. only on a path that actually reaches cleanup. |

Both use the exact same envelope shape as every other event on this
channel (`event_type`/`camera_id`/`time_seconds`/`track_id`/`global_id`/
`zone_id`/`details`), so no frontend special-casing is needed:

```json
{
  "event_type": "CAMERA_STREAM_STARTED",
  "camera_id": "camera_01",
  "time_seconds": 0.0,
  "track_id": null,
  "global_id": null,
  "zone_id": null,
  "details": {}
}
```

(`CAMERA_STREAM_STOPPED` is identical except for `event_type`.) Both are
published via the existing `frame_publish_client` — the same synchronous
`redis.Redis` client already constructed for the annotated-frame `SET`
calls (§3) — not a second Redis connection. Each publish is wrapped in
its own `try/except` (mirroring the existing frame-publish resilience
posture in the same file): a Redis hiccup logs a warning and never
crashes the pipeline or blocks cleanup.

### 7b. `vision_subscriber.py` needed zero changes

`backend/app/services/vision_subscriber.py` subscribes to
`CAMERA_LIVE_CHANNEL` and re-broadcasts **every** message it receives via
`ws_manager.broadcast(json.loads(message["data"]))`, with no filtering or
branching on `event_type` (see §11 of `docs/AI_BACKEND_WIRING.md`). Since
`CAMERA_STREAM_STARTED`/`CAMERA_STREAM_STOPPED` are published to that same
channel in that same envelope shape, they are relayed to every connected
`/ws/dashboard` client automatically, identically to `ZONE_OCCUPANCY_CHANGE`,
`NEW_ALERT`, etc. This file was not modified for this task.

### 7c. Real verification (this session, 2026-08-15)

Reused the same real multi-process pattern as
`tests/test_cross_process_websocket_relay.py` /
`docs/AI_BACKEND_WIRING.md` §11: a real `python -m uvicorn app.main:app`
subprocess (running the real, unmodified `vision_subscriber` background
task), a real `python src/run_live.py` subprocess against
`data/raw_videos/camera_01/video_001.mp4`, and a real WebSocket client
(the `websockets` package, real TCP loopback socket) in a third, separate
process — connected to the `uvicorn` process **before** the camera
subprocess was started.

**Scenario A — normal exit (`--max-frames 20`):**

```
=== SCENARIO A: normal exit -- expect STARTED then STOPPED ===
Total events received: 8
First event type: CAMERA_STREAM_STARTED
Saw CAMERA_STREAM_STARTED: True
Saw CAMERA_STREAM_STOPPED: True
Full first event: {'event_type': 'CAMERA_STREAM_STARTED', 'camera_id': 'camera_01', 'time_seconds': 0.0, 'track_id': None, 'global_id': None, 'zone_id': None, 'details': {}}
Full last event: {'event_type': 'CAMERA_STREAM_STOPPED', 'camera_id': 'camera_01', 'time_seconds': 0.0, 'track_id': None, 'global_id': None, 'zone_id': None, 'details': {}}
SCENARIO A: PASSED
```

`CAMERA_STREAM_STARTED` was the very first message the WebSocket client
received — ahead of every real detection event from that run — and
`CAMERA_STREAM_STOPPED` arrived after the `run_live.py` subprocess exited
normally (frame limit reached, clean `finally:` path).

**Scenario B — hard kill (`Popen.kill()`, i.e. SIGKILL / Windows
`TerminateProcess`), confirming the already-documented limitation rather
than fixing it:**

```
=== SCENARIO B: hard kill -- expect STARTED but NOT STOPPED ===
Received CAMERA_STREAM_STARTED: {'event_type': 'CAMERA_STREAM_STARTED', 'camera_id': 'camera_01', ...}
Hard-killing run_live.py subprocess (pid=3628)...
Subprocess exit code after kill: 1
Unexpected extra message after kill: {'event_type': 'ZONE_OCCUPANCY_CHANGE', 'camera_id': 'camera_01', ...}
Saw CAMERA_STREAM_STOPPED after hard kill: False
SCENARIO B: PASSED (confirms documented limitation: no STOPPED on hard kill)
```

`CAMERA_STREAM_STARTED` still arrived (published before the frame loop
starts), but no `CAMERA_STREAM_STOPPED` arrived after the hard kill — a
queued detection event already in flight was the last thing received,
then nothing. This is the same, already-documented limitation as
`EventDispatchBridge.stop()`'s queue-flush guarantee and the
`_GracefulShutdown`/SIGTERM handling in `run_live.py` itself: a hard kill
(SIGKILL, or SIGTERM on Windows, which the OS delivers as an
unconditional `TerminateProcess` bypassing all Python code) never reaches
the `finally:` block, so `CAMERA_STREAM_STOPPED` is never published. Not
something this task attempts to solve — a frontend still needs its
existing reconnect-and-check fallback (§2) as a backstop for this case;
these two events are a faster, additive signal for the clean-shutdown
path, not a guarantee for every possible process death.

No orphaned `uvicorn`/`run_live` processes remained after the verification
script exited (confirmed via `Get-CimInstance Win32_Process`, same
technique as `docs/AI_BACKEND_WIRING.md` §11).

### 7d. Full test suite

```
python -m pytest -q
```

Result: **9 failed, 88 passed** — identical to the documented baseline in
`docs/AI_BACKEND_WIRING.md` §11 (same 9 pre-existing, unrelated failures:
`test_live_pipeline_timing.py` ×3, `test_pipeline_output.py` ×1,
`test_pose_stride.py` ×1, `test_table_zone_anchoring.py` ×3,
`test_tracker.py` ×1 — stale test signatures against pipeline internals,
none touching `run_live.py`, `event_dispatcher.py`, or `vision_subscriber.py`).

One test needed a small, deliberate update:
`tests/test_cross_process_websocket_relay.py` opens its WebSocket
connection before starting the camera subprocess and previously asserted
the *first* message received was a detection event. Since
`CAMERA_STREAM_STARTED` is now published before the frame loop begins, it
arrives first instead. The test now skips past `CAMERA_STREAM_STARTED`
messages and asserts on the first real detection event after it —
preserving what the test always proved (a detection event computed inside
the camera subprocess reaches a WebSocket client in a separate process)
without asserting away the new, expected lifecycle event.

### 7e. Files changed

| File | Change |
|---|---|
| `src/run_live.py` | Imports `CAMERA_LIVE_CHANNEL` from `src.events.event_dispatcher`; adds `publish_stream_lifecycle_event()` helper; publishes `CAMERA_STREAM_STARTED` once `camera_id`/`frame_publish_client`/`bridge` are all ready (before the frame loop), and `CAMERA_STREAM_STOPPED` in the existing `finally:` block before `frame_publish_client.close()`. No second Redis connection opened. |
| `tests/test_cross_process_websocket_relay.py` | Updated to skip past `CAMERA_STREAM_STARTED` when waiting for the first detection event (see §7d). |

`backend/app/websockets/`, `src/events/event_engine.py`, and
`src/events/dispatch_bridge.py` were not touched, per this task's
constraint. `backend/app/services/vision_subscriber.py` was not touched
either — it needed zero changes (§7b).
