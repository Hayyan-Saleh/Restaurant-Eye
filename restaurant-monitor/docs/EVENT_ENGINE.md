# Event Engine — Three-Level State Machine Spec

Implementation: `src/events/` (new this session).

## Design principle

This is **event-sourced**, not a polling loop with global flags: every state
transition is emitted as an immutable, timestamped event appended to an
in-process `asyncio.Queue` → persisted to Postgres `events` (append-only) →
published to Redis `events:{camera_id}` for WebSocket fan-out. KPIs are never
computed by inspecting "current" mutable state; they are computed by
replaying/aggregating the event log over a time window. This makes KPI
recomputation for an arbitrary historical range (a REST report) identical in
logic to the live rolling computation — same aggregation function, different
event source (DB query vs. live queue).

State is held in per-key state machines, not global variables:
- One `PersonStateMachine` per `(camera_id, global_id)`
- One `TableStateMachine` per `(camera_id, zone_id)` for table zones
- One `StaffZoneStateMachine` per `(camera_id, zone_id)` for staff/kitchen zones

Each machine only reacts to events addressed to its own key — this is what
"time-windowed state machine per table/zone/person" means concretely (brief
requirement, Event Engine section).

## Level 1 — Raw Events

Emitted directly from `LivePipeline.process_frame()`'s per-frame snapshot,
one `RawEvent` per fact, every frame processed (2 FPS per camera):

| event_type | payload | source field |
|---|---|---|
| `TrackSeen` | camera_id, track_id, global_id, box, zone_id | `tracked_objects[i]` |
| `ZoneOccupancy` | camera_id, zone_id, zone_type, global_id | `enrich_tracks_with_zones` output |
| `RoleAssigned` | camera_id, global_id, role, confidence | `RoleEngine.get_decisions()` |
| `ActionObserved` | camera_id, track_id, action | `self._action_labels` |
| `TableStateRaw` | camera_id, zone_id, state (free/occupied/dirty) | `TableKBS` facts |

Raw events are the *only* level allowed to fire every frame — they carry no
thresholding logic. `src/events/raw.py::extract_raw_events(snapshot) -> list[RawEvent]`.

## Level 2 — Semantic Events

Derived by feeding the Level-1 stream through the per-key state machines.
Each semantic event requires a **dwell/consistency threshold** — this is
where "raw tracker noise" gets filtered into something meaningful. Thresholds
live in `src/events/config.py`, overridable per deployment:

`PersonStateMachine` (states: `unseen → present → seated/standing → departed`):
- `CustomerSeated` — role=customer, zone_type=table, continuous
  `ZoneOccupancy` in the *same* `zone_id` for ≥ `SEATED_DWELL_S` (default 20s).
  Debounced: a gap ≤ `REACQUIRE_GRACE_S` (default 3s, covers 1 missed
  detection at 2 FPS) does not reset the dwell timer — avoids flicker from a
  single dropped frame, mirroring the existing tracker's own
  `time_since_update` tolerance.
- `StaffArrivedTable` — role=worker, zone_type=table, continuous presence
  ≥ `STAFF_ARRIVE_DWELL_S` (default 5s).
- `StaffDepartedTable` — a previously-arrived staff member's `ZoneOccupancy`
  for that zone_id stops for ≥ `DEPART_GRACE_S` (default 3s).
- `CustomerIdle` — global_id's zone_point speed (Δposition / Δt from
  consecutive `TrackSeen`) stays below `IDLE_SPEED_PX_S` for ≥ 60s continuous,
  AND current zone_type != staff/work zone. Speed is computed in the state
  machine, not stored on the track (existing pipeline has no velocity field).
- `CustomerDeparted` / `StaffDeparted` — no `TrackSeen` for that global_id for
  ≥ `ABSENCE_TIMEOUT_S` (default 15s) — closes the person's presence interval.

`TableStateMachine` (states mirror `TableKBS` but time-stamped):
- `TableStateChanged` — re-emitted from `TableStateRaw` only on actual
  transition (free→occupied→dirty→free), with `entered_at` timestamp attached
  so Level 3 can compute durations. This machine also **debounces** flapping:
  a `TableStateRaw` transition must persist 2 consecutive raw frames (1s at
  2FPS) before being accepted, protecting against the same
  bbox-center/hand-keypoint jitter `zone_manager.py`'s changelog already
  fought at the zone-matching layer — belt-and-suspenders at the event layer.

`src/events/semantic.py::PersonStateMachine`, `TableStateMachine`.

## Level 3 — Business Events

Derived purely from Level-2 transitions (never touches Level-1 directly —
enforces the layering):

- `WaitingStarted` — on `CustomerSeated` for a table with no staff
  (`StaffArrivedTable`) recorded at that zone_id since the seating.
- `WaitingEnded` — on the *first* `StaffArrivedTable` event at that zone_id
  after the matching `CustomerSeated`. Payload carries
  `wait_seconds = t(StaffArrivedTable) - t(CustomerSeated)` —
  directly the `CustomerServiceWaitingTime` KPI input.
- `ServiceStarted` — on `ActionObserved(action="serving")` from a
  worker role at an occupied table zone (mirrors the existing
  `_table_was_served` flag, promoted to a timestamped event).
- `TableTurnoverStarted` — on `TableStateChanged` → `dirty` or `free`
  (table vacated) for a zone_id that was previously `occupied`.
- `TableTurnoverEnded` — on the *next* `TableStateChanged` → `occupied` for
  that same zone_id. Payload: `turnover_seconds`, increments
  `TableTurnoverCount`.
- `StaffActivePeriod` (start/end) — bracket a global_id's continuous
  `StaffArrivedTable`/`StaffDepartedTable` or staff-zone presence into a
  single active-work interval, feeding `StaffUtilization`.

`src/events/business.py::derive_business_events(level2_event, machine_state)`.

## Why per-key state machines, not global flags

Each `PersonStateMachine`/`TableStateMachine` instance owns exactly the
mutable state needed for its own transitions (last-seen timestamp, dwell
accumulator, current state enum). There is no shared mutable dict scanned
every frame for "is anyone waiting too long" — that check is instead a
property of the *event stream itself* (does a `WaitingStarted` for this
zone_id have a matching `WaitingEnded` yet, or has it been open longer than
threshold). This is what makes it event-sourcing rather than a polling loop:
the state machines react to events pushed to them, and any observer (KPI
engine, alerting) subscribes to the *output* stream rather than polling
machine internals.

## Concrete example — a full lifecycle

```
t=0s    ZoneOccupancy(cam01, zone=table_1, gid=customer_abc)      [L1]
t=20s   CustomerSeated(cam01, table_1, customer_abc)              [L2]
t=20s   WaitingStarted(cam01, table_1)                            [L3]
t=45s   ZoneOccupancy(cam01, zone=table_1, gid=worker_x)          [L1]
t=50s   StaffArrivedTable(cam01, table_1, worker_x)               [L2]
t=50s   WaitingEnded(cam01, table_1, wait_seconds=30)             [L3]
t=52s   ActionObserved(worker_x, action=serving)                  [L1]
t=52s   ServiceStarted(cam01, table_1, worker_x)                  [L3]
t=1800s CustomerDeparted(cam01, customer_abc)                     [L2]
t=1810s TableStateChanged(cam01, table_1, dirty, since=1810s)     [L2]
t=1810s TableTurnoverStarted(cam01, table_1)                      [L3]
t=1900s TableStateChanged(cam01, table_1, occupied, since=1900s)  [L2]
t=1900s TableTurnoverEnded(cam01, table_1, turnover_seconds=90)   [L3]
```

## Alerting hook

Alerts (frontend `Alerts.jsx`) subscribe to Level-3 events with a threshold
predicate — e.g. `WaitingStarted` with no matching `WaitingEnded` within
`ALERT_WAIT_THRESHOLD_S` (default 300s) triggers an `AlertRaised` event,
itself persisted and pushed over the same WebSocket channel. This reuses the
event pipeline rather than being a separate polling subsystem.
