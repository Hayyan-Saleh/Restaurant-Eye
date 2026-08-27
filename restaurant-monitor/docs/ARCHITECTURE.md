# System Architecture — Restaurant Floor Monitoring & Analytics Platform

## 0. Status of this document

This describes the system **as extended in this session**, built on top of an
already-working vision/tracking/classification pipeline (see `changes.txt` for
its debugging history). Nothing described as "existing" below was written this
session; only "New this session" components were added. The decision to
extend rather than rewrite the vision layer was made explicitly with the
project owner — see `README.md` history and session report
(`docs/SESSION_REPORT.md`) for the rationale.

## 1. High-level data flow

```
 16x RTSP/file camera sources (config/camera_config.json)
        │  (one isolated async worker task per camera — a crash on
        │   camera_07 must not affect camera_01..16)
        ▼
 ┌───────────────────────────── Vision Engine (existing) ─────────────────────────────┐
 │  PersonDetector (YOLOv8n-pose, ultralytics)                                        │
 │        → RestaurantTracker (DeepSort, mobilenet embedder, per-camera TrackIDs)     │
 │        → ZoneManager.get_bbox_zone() (shapely polygons + keypoint-aware matching)  │
 │        → PoseLSTM action classifier (sit / stand / walk / serve, from 3-frame      │
 │          keypoint sequence)                                                        │
 └───────────────────────────────────────────────────────────────────────────────────┘
        ▼
 ┌───────────────────────── Worker Re-ID Engine (existing) ────────────────────────────┐
 │  OSNet-x0.25 (torchreid) embedding, 512-d, cosine via FAISS IndexFlatIP             │
 │  ReIDGallery (permanent, promoted workers) + SessionGallery (30-min TTL, unknowns)  │
 │  Similarity threshold 0.72 + 0.05 margin-over-runner-up anti-swap guard            │
 └───────────────────────────────────────────────────────────────────────────────────┘
        ▼
 ┌────────────────────── Staff/Customer + Table KBS (existing) ────────────────────────┐
 │  RoleEngine (experta production system): per-global-id evidence accumulator with    │
 │  decay, zone-history, confirm/reverse rules → role ∈ {worker, customer, unknown}    │
 │  TableKBS (experta): per-zone customer_count + was_served → {free, occupied, dirty} │
 └───────────────────────────────────────────────────────────────────────────────────┘
        ▼  LivePipeline.process_frame() returns a per-frame snapshot dict
 ┌────────────────────────── Event Engine (NEW this session) ──────────────────────────┐
 │  Level 1 — Raw Events: emitted straight from the per-frame snapshot                 │
 │            (TrackSeen, ZoneEntered/Exited, RoleAssigned, TableStateChanged, ...)     │
 │  Level 2 — Semantic Events: per-person / per-table time-windowed state machines      │
 │            consume Level-1 events, apply dwell thresholds                           │
 │            (CustomerSeated@20s, StaffArrivedTable@5s, CustomerIdle@60s, ...)         │
 │  Level 3 — Business Events: KPI-relevant lifecycle events derived from Level-2       │
 │            transitions (WaitingEnded, TableTurnoverStarted/Ended, ServiceStarted)    │
 │  Implementation: src/events/ — async stream processor, one StateMachine instance    │
 │  per (camera_id, zone_id) and per (camera_id, global_id), keyed dict + asyncio queue│
 └───────────────────────────────────────────────────────────────────────────────────┘
        ▼ every event is persisted (Postgres, append-only `events` table) and
        ▼ published to Redis pub/sub channel `events:{camera_id}`
 ┌───────────────────────────── KPI Engine (NEW this session) ─────────────────────────┐
 │  Rolling/windowed aggregation consuming the Level-2/3 event stream + event log       │
 │  replay. Produces the 8 required KPIs (§ formulas in docs/EVENT_ENGINE.md).          │
 │  Materialized into `kpi_snapshots` on a fixed cadence (default 30s) + on-demand      │
 │  recompute-from-event-log for historical ranges (REST reports).                      │
 └───────────────────────────────────────────────────────────────────────────────────┘
        ▼
 ┌────────────────────────── Backend (NEW this session) ───────────────────────────────┐
 │  FastAPI, async. REST: zones/cameras/staff-gallery config, historical events/KPIs,   │
 │  reports. WebSocket: /ws/live — fans out Level-1..3 events + KPI deltas per camera,  │
 │  subscribed via Redis pub/sub so multiple API replicas can share one pipeline        │
 │  process. Auth: OAuth2 password bearer + JWT (see docs/SECURITY.md).                │
 │  Postgres via SQLAlchemy async engine (asyncpg driver).                             │
 └───────────────────────────────────────────────────────────────────────────────────┘
        ▼
 React dashboard (existing component shells, wired this session): live table-status
 map (abstract layout, not re-streamed video — bounding boxes are drawn server-side
 into the snapshot event, coordinates only), per-staff KPI views, heatmap viewer,
 alerting UI, time-range/employee filters.
```

## 2. Model / tracker choice — justification

The existing system already made these choices; re-justifying them against the
brief's suggested alternatives (YOLOv9/v10/RT-DETR, ByteTrack/BoT-SORT/StrongSORT):

| Component | Chosen | Alternative considered | Why chosen wins here |
|---|---|---|---|
| Detector | YOLOv8n-pose | YOLOv9/v10, RT-DETR | Need **pose keypoints**, not just boxes — hand/ankle/hip keypoints drive zone-matching (`_estimate_zone_point`, `_find_table_by_hands`) and the PoseLSTM action classifier. YOLOv8-pose is the only one of the three with a mature pretrained pose head; RT-DETR/v9/v10 are detection-only unless a separate pose model is bolted on, doubling inference cost. On a 4GB RTX 2050 driving up to 16 camera streams at 2 FPS process rate, a second full pose network is not affordable. |
| Tracker | DeepSort (mobilenet embedder) | ByteTrack / BoT-SORT | ByteTrack is faster and IoU-only (no appearance model), which sounds attractive, but this system relies on **short-term appearance continuity** to survive brief occlusion at crowded tables (see `changes.txt` re: track fragmentation being handled by `SessionGallery` at the Re-ID layer). DeepSort's appearance embedding is what feeds that. BoT-SORT would be the "correct" SOTA upgrade path (ByteTrack's motion association + ReID appearance) — noted as a **future improvement**, not done this session because swapping the tracker changes `track_id` semantics that `RoleEngine`'s `ScoreAccumulator`/`MergeRequest` logic (evidence persistence keyed by track continuity) is tuned against, and re-validating that is out of scope for a backend/event-engine session. |
| Re-ID | OSNet-x0.25 + FAISS | Transformer Re-ID (e.g. TransReID) | OSNet-x0.25 is already the lighter, CPU-viable option the brief itself suggests, and it's already meeting the accuracy bar per the margin-check tuning history. A transformer Re-ID model would cost more compute for marginal gain on a small, low-res CCTV worker gallery (few dozen staff, not thousands of identities — this is not an open-set re-id benchmark). |

**Net effect**: the CV stack is appropriate for the constraint (single 4GB GPU,
16 streams, near-real-time), and matches the brief's own latency/accuracy
tradeoff guidance even though it doesn't use the exact model names listed as
examples in the brief.

## 3. Fault tolerance

Each camera runs as its own `asyncio.Task` (`api/workers/camera_worker.py`,
new this session) wrapping a dedicated `LivePipeline` instance. A worker crash
(decode failure, CUDA OOM, corrupt frame) is caught at the task boundary,
logged, the camera is marked `degraded` in `/api/cameras/status`, and the
worker is retried with exponential backoff — it does not propagate to other
camera tasks or take down the FastAPI process. This was not present before
this session (`run_live.py` is a single-stream synchronous CLI script); it is
new infrastructure in `api/workers/`.

## 4. Security

- Dashboard/config endpoints require a bearer JWT (`api/auth.py`, new). No
  anonymous access to `/api/*` or `/ws/live`.
- Recommended deployment: reverse proxy (nginx/Caddy) terminating TLS in
  front of FastAPI; the app itself trusts `X-Forwarded-Proto`. This repo does
  not ship a TLS cert — that's an infra/ops step outside the app's scope.
- **Biometric data**: the Re-ID gallery persists **only 512-d float embeddings
  and FAISS index rows**, never raw crop images, in both the existing
  in-memory implementation and the new DB-backed persistence
  (`worker_gallery` table, §DB_SCHEMA.md). Embeddings are not cryptographically
  reversible to a photo, but they *are* biometric data (linkable across
  sessions) — documented tradeoff: no reversible-encryption-at-rest is
  implemented for embeddings in this session (would require key management
  infra not requested); if this is deployed against real customer-identifiable
  data, encrypting the `embedding` column at rest (e.g. pgcrypto) is a
  follow-up.

## 5. What requires physical hardware / real data collection (cannot be faked)

Per the brief's non-negotiable #5, stated explicitly rather than mocked:

1. **Live RTSP camera ingestion** — this session (and the existing pipeline)
   only exercises the system against the 11 pre-recorded MP4s in
   `data/raw_videos/`. `camera_worker.py` is written against `cv2.VideoCapture`
   which accepts an RTSP URL identically to a file path, so no code change is
   needed to point it at real cameras — but it has not been (and cannot be,
   in this environment) tested against a live RTSP stream, real network
   jitter, or real camera drift/focus changes.
2. **Staff gallery enrollment** — the system currently *bootstraps* the
   worker gallery automatically (a track the RoleEngine confirms as `worker`
   gets promoted from the session gallery into the permanent gallery). A
   proper deployment should also support **manual enrollment** from a known
   staff photo (e.g., an ID-badge photo taken at onboarding) so a new hire is
   recognized on day one instead of only after the automatic
   confirmation heuristic fires. The REST endpoint for this
   (`POST /api/staff-gallery/enroll`, new this session) is implemented and
   tested with a synthetic image, but there is no real staff photo dataset
   available in this environment to validate recognition accuracy against.
3. **Zone/camera calibration** — the tooling already exists
   (`src/utils/zone_annotator_v3.html`, `zone_manager.py`,
   `validate_zones_config.py`, `visualize_zones.py`) and 8 of 16 cameras
   (`camera_01, 02, 11, 12, 13, 14, 15, 16`) already have calibrated zones +
   reference frames in `config/zones_config.json` / `data/debug/zones/`. The
   remaining 8 cameras (`camera_03, 04, 05, 06, 07, 08, 09, 10`) have no zone
   polygons yet — that requires a human to draw them against that camera's
   actual field of view using the existing annotator; it is a data-entry
   step, not a code gap. The Event/KPI engines built this session degrade
   gracefully for un-zoned cameras (zone-dependent KPIs simply have no data
   for those camera_ids until zones are added) rather than erroring.
4. **KPI ground-truth validation** — the KPI formulas are implemented exactly
   as specified and unit-tested against synthetic event sequences, but no
   real "manager stopwatch" ground truth exists in this environment to
   validate that, e.g., computed `CustomerServiceWaitingTime` matches reality
   on the restaurant floor. That validation requires an on-site observer.
