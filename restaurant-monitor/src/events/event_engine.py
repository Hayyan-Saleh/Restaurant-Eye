"""
event_engine.py
----------------
Level 1 semantic Event Engine: consumes one src.core.pipeline_output.FrameOutput
per call and emits a list of discrete events for meaningful transitions
between the current frame and each track's/table's/zone's *previous* frame.

This is a pure logic layer -- in-memory state only, no Redis, no
sqlalchemy, no network/DB code. It does not recompute anything TableKBS or
RoleEngine already computed; it only reacts to *change* in their
already-computed output (FrameOutput.tracks[i].zone_id/.role and
FrameOutput.table_states).

Scope note: this module implements exactly the 8 event rules specified for
this task and its addendum (CUSTOMER_SEATED, CUSTOMER_LEFT, STAFF_IDLE,
WORKER_ACTIVE, DELAY_ALERT, ZONE_TRANSITION, ZONE_OCCUPANCY_CHANGE,
TABLE_STATE_CHANGED). docs/EVENT_ENGINE.md
describes a larger, three-level, state-machine/Redis/Postgres architecture
for a *future* iteration of this component -- that document is aspirational
for later tasks and is deliberately not what's built here (this task's own
instructions explicitly forbid Redis/DB dependencies and specify a single
flat rule set instead of that doc's Level 1/2/3 split). See docs/TASK6_REPORT.md
for the reasoning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.pipeline_output import FrameOutput


@dataclass
class EngineEvent:
    """One emitted event. track_id/global_id/zone_id are the common
    identifying fields shared by most event types (None when not
    applicable, e.g. ZONE_OCCUPANCY_CHANGE has no single track). Anything
    event-type-specific (durations, counts, thresholds) lives in `details`.
    """

    event_type: str
    camera_id: str
    time_seconds: float
    track_id: int | None = None
    global_id: str | None = None
    zone_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def engine_event_to_dict(event: EngineEvent) -> dict:
    return asdict(event)


class EventEngine:
    """
    Stateful, in-memory Event Engine. One instance is meant to live for the
    lifetime of whatever process feeds it FrameOutputs (state is never reset
    per call) and can be fed frames from multiple camera_ids -- all internal
    state is keyed with camera_id as part of the key, never by track_id or
    zone_id alone, so two cameras' track_id=1 (DeepSort ids are only unique
    within one camera's process) or same-named zones never collide.

    Config: optional `config` dict (e.g. the project's loaded config.json).
    config/config.json currently has no "events" section, so every
    threshold below falls back to its documented default; if one is added
    later (`config["events"]["staff_idle_threshold_frames"]` etc.) it is
    picked up automatically.
    """

    # Rule 3 (STAFF_IDLE): consecutive unchanged-zone frames before firing.
    # No "events" key exists in config/config.json today -- documented
    # default per the task instructions.
    DEFAULT_STAFF_IDLE_THRESHOLD_FRAMES = 300

    # Rule 5 (DELAY_ALERT): seconds a table may stay continuously occupied
    # (since CUSTOMER_SEATED, without an intervening service event) before
    # alerting. Same "no config key exists yet" situation as above.
    DEFAULT_DELAY_ALERT_THRESHOLD_SECONDS = 900.0

    # Rule 6 (ZONE_TRANSITION): minimum real-time gap between two emitted
    # ZONE_TRANSITION events for the *same* track, per the task's explicit
    # "at least 2 real seconds" rule text.
    DEFAULT_ZONE_TRANSITION_MIN_INTERVAL_SECONDS = 2.0
    STATIC_ACTIONS = {"sitting", "standing"}
    def __init__(self, config: dict | None = None):
        events_config = (config or {}).get("events", {})
        self.staff_idle_threshold_frames: int = events_config.get(
            "staff_idle_threshold_frames", self.DEFAULT_STAFF_IDLE_THRESHOLD_FRAMES
        )
        self.delay_alert_threshold_seconds: float = events_config.get(
            "delay_alert_threshold_seconds", self.DEFAULT_DELAY_ALERT_THRESHOLD_SECONDS
        )
        self.zone_transition_min_interval_seconds: float = events_config.get(
            "zone_transition_min_interval_seconds", self.DEFAULT_ZONE_TRANSITION_MIN_INTERVAL_SECONDS
        )

        # -- Per-track state, keyed "{camera_id}:{track_id}" -- never by
        # track_id alone (DeepSort ids are only unique within one camera's
        # own process). Values: zone_id, zone_type, role, idle_counter,
        # is_idle, seated_since, zone_entered_time (see _new_track_state).
        self._track_states: dict[str, dict] = {}

        # -- Per-camera set of track_ids present in the *previous* call, used
        # to detect tracks that vanished entirely (rule 2's second trigger).
        self._camera_track_ids_seen: dict[str, set[int]] = {}

        # -- Per-camera per-zone person count from the *previous* call, for
        # rule 7 (ZONE_OCCUPANCY_CHANGE).
        self._zone_occupancy_counts: dict[str, dict[str, int]] = {}

        # -- Which camera_ids have had at least one frame processed. Used to
        # bootstrap zone-occupancy counts silently on a camera's very first
        # frame (mirrors the "first sighting generates no event" rule at the
        # zone-occupancy level -- there is no genuine previous frame to
        # diff against yet, so a 0->N first reading is not a real change).
        self._camera_seen_before: set[str] = set()

        # -- Rule 5 (DELAY_ALERT) in-memory substitute for what will be a
        # Redis-backed value in a future task (see docs/EVENT_ENGINE.md's
        # "TableStateMachine" concept) -- kept as a plain dict here per this
        # task's explicit "no Redis/DB dependency" constraint. Keyed
        # "{camera_id}:{zone_id}". Values: {"occupied_since": float|None,
        # "delay_alert_fired": bool}.
        self._table_occupancy: dict[str, dict] = {}

        # -- Rule 8 (TABLE_STATE_CHANGED): previous frame's table_states
        # value per zone, keyed "{camera_id}:{zone_id}" (same camera-scoping
        # convention as every other rule). Deliberately a separate dict from
        # _table_occupancy above -- rule 8 is a direct table_states diff,
        # independent of rule 5's CUSTOMER_SEATED-driven occupancy clock.
        self._table_states_prev: dict[str, str] = {}

    # -- Public API --------------------------------------------------------

    def process_frame(self, frame_output: FrameOutput) -> list[dict]:
        """Consume one FrameOutput, return a list of plain-dict events."""
        events = self._process_frame(frame_output)
        return [engine_event_to_dict(e) for e in events]

    # -- Internal ------------------------------------------------------------

    def _new_track_state(self, track, now: float) -> dict:
        return {
            "zone_id": track.zone_id,
            "zone_type": track.zone_type,
            "role": track.role,
            "idle_counter": 0,
            "is_idle": False,
            "seated_since": None,
            "zone_entered_time": now,
            "last_zone_transition_time": None,
        }

    def _process_frame(self, frame_output: FrameOutput) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        camera_id = frame_output.camera_id
        now = frame_output.time_seconds

        current_track_ids: set[int] = set()

        for track in frame_output.tracks:
            current_track_ids.add(track.track_id)
            key = f"{camera_id}:{track.track_id}"
            prev = self._track_states.get(key)

            # # -- First sighting: store state, emit nothing (per spec). ------
            # if prev is None:
            #     self._track_states[key] = self._new_track_state(track, now)
            #     continue
            
            # -- First sighting: store state. Workers additionally get an
            # immediate WORKER_ACTIVE (same event/dispatch path STAFF_IDLE's
            # transition already uses) so a worker who's active from the
            # very first frame gets a Redis key right away, instead of
            # staying absent from /workers/status until their first real
            # idle->active transition (which may never happen).
            if prev is None:
                self._track_states[key] = self._new_track_state(track, now)
                if track.role == "worker":
                    events.append(EngineEvent(
                        event_type="WORKER_ACTIVE", camera_id=camera_id, time_seconds=now,
                        track_id=track.track_id, global_id=track.global_id, zone_id=track.zone_id,
                        details={"idle_frames": 0, "idle_duration_seconds": 0.0, "reason": "first_sighting"},
                    ))
                continue

            zone_changed = track.zone_id != prev["zone_id"]

            # -- Rule 1: CUSTOMER_SEATED -----------------------------------
            if track.role == "customer" and zone_changed and track.zone_type == "table":
                events.append(EngineEvent(
                    event_type="CUSTOMER_SEATED", camera_id=camera_id, time_seconds=now,
                    track_id=track.track_id, global_id=track.global_id, zone_id=track.zone_id,
                ))
                prev["seated_since"] = now

                table_key = f"{camera_id}:{track.zone_id}"
                table = self._table_occupancy.setdefault(
                    table_key, {"occupied_since": None, "delay_alert_fired": False}
                )
                if table["occupied_since"] is None:
                    table["occupied_since"] = now

            # -- Rule 2: CUSTOMER_LEFT (still present, left the table) -----
            if prev["seated_since"] is not None and track.zone_type != "table":
                duration = now - prev["seated_since"]
                events.append(EngineEvent(
                    event_type="CUSTOMER_LEFT", camera_id=camera_id, time_seconds=now,
                    track_id=track.track_id, global_id=track.global_id, zone_id=prev["zone_id"],
                    details={"duration_seconds": duration, "reason": "zone_change"},
                ))
                prev["seated_since"] = None

            # # -- Rules 3/4: STAFF_IDLE / WORKER_ACTIVE ----------------------
            # if track.role == "worker":
            #     if zone_changed:
            #         if prev["is_idle"]:
            #             idle_duration_s = now - prev["zone_entered_time"]
            #             events.append(EngineEvent(
            #                 event_type="WORKER_ACTIVE", camera_id=camera_id, time_seconds=now,
            #                 track_id=track.track_id, global_id=track.global_id, zone_id=track.zone_id,
            #                 details={
            #                     "idle_frames": prev["idle_counter"],
            #                     "idle_duration_seconds": idle_duration_s,
            #                 },
            #             ))
            #             prev["is_idle"] = False
            #         prev["idle_counter"] = 0
            #         prev["zone_entered_time"] = now
            #     else:
            #         prev["idle_counter"] += 1
            #         if prev["idle_counter"] == self.staff_idle_threshold_frames and not prev["is_idle"]:
            #             prev["is_idle"] = True
            #             idle_duration_s = now - prev["zone_entered_time"]
            #             events.append(EngineEvent(
            #                 event_type="STAFF_IDLE", camera_id=camera_id, time_seconds=now,
            #                 track_id=track.track_id, global_id=track.global_id, zone_id=track.zone_id,
            #                 details={
            #                     "idle_frames": prev["idle_counter"],
            #                     "idle_duration_seconds": idle_duration_s,
            #                     "threshold_frames": self.staff_idle_threshold_frames,
            #                 },
            #             ))
            # -- Rules 3/4: STAFF_IDLE / WORKER_ACTIVE ----------------------
            if track.role == "worker":
                is_static_pose = track.action in self.STATIC_ACTIONS
                if zone_changed or not is_static_pose:
                    if prev["is_idle"]:
                        idle_duration_s = now - prev["zone_entered_time"]
                        events.append(EngineEvent(
                            event_type="WORKER_ACTIVE", camera_id=camera_id, time_seconds=now,
                            track_id=track.track_id, global_id=track.global_id, zone_id=track.zone_id,
                            details={
                                "idle_frames": prev["idle_counter"],
                                "idle_duration_seconds": idle_duration_s,
                            },
                        ))
                        prev["is_idle"] = False
                    prev["idle_counter"] = 0
                    prev["zone_entered_time"] = now
                else:
                    prev["idle_counter"] += 1
                    if prev["idle_counter"] == self.staff_idle_threshold_frames and not prev["is_idle"]:
                        prev["is_idle"] = True
                        idle_duration_s = now - prev["zone_entered_time"]
                        events.append(EngineEvent(
                            event_type="STAFF_IDLE", camera_id=camera_id, time_seconds=now,
                            track_id=track.track_id, global_id=track.global_id, zone_id=track.zone_id,
                            details={
                                "idle_frames": prev["idle_counter"],
                                "idle_duration_seconds": idle_duration_s,
                                "threshold_frames": self.staff_idle_threshold_frames,
                            },
                        ))
            else:
                # Non-worker tracks don't accumulate idle state (e.g. a
                # track re-classified from worker -> other role resets clean).
                prev["idle_counter"] = 0
                prev["is_idle"] = False

            # -- Rule 5 (partial): a "serving" worker resets that table's
            # delay clock -- the in-memory substitute for a real "service
            # event" signal (see class docstring / _table_occupancy above).
            if track.role == "worker" and track.action == "serving" and track.zone_id is not None:
                table_key = f"{camera_id}:{track.zone_id}"
                table = self._table_occupancy.get(table_key)
                if table is not None and table["occupied_since"] is not None:
                    table["occupied_since"] = now
                    table["delay_alert_fired"] = False

            # -- Rule 6: ZONE_TRANSITION (downsampled) ----------------------
            if zone_changed:
                last_t = prev["last_zone_transition_time"]
                if last_t is None or (now - last_t) >= self.zone_transition_min_interval_seconds:
                    events.append(EngineEvent(
                        event_type="ZONE_TRANSITION", camera_id=camera_id, time_seconds=now,
                        track_id=track.track_id, global_id=track.global_id, zone_id=track.zone_id,
                        details={"from_zone_id": prev["zone_id"], "to_zone_id": track.zone_id},
                    ))
                    prev["last_zone_transition_time"] = now

            # -- Update stored snapshot for next frame's comparison ---------
            prev["zone_id"] = track.zone_id
            prev["zone_type"] = track.zone_type
            prev["role"] = track.role

        # -- Rule 2 (second trigger): tracks that vanished entirely --------
        previous_track_ids = self._camera_track_ids_seen.get(camera_id, set())
        vanished_track_ids = previous_track_ids - current_track_ids
        for track_id in vanished_track_ids:
            key = f"{camera_id}:{track_id}"
            prev = self._track_states.pop(key, None)
            if prev is None:
                continue
            if prev["seated_since"] is not None:
                duration = now - prev["seated_since"]
                events.append(EngineEvent(
                    event_type="CUSTOMER_LEFT", camera_id=camera_id, time_seconds=now,
                    track_id=track_id, global_id=None, zone_id=prev["zone_id"],
                    details={"duration_seconds": duration, "reason": "track_disappeared"},
                ))
        self._camera_track_ids_seen[camera_id] = current_track_ids

        # -- Rule 7: ZONE_OCCUPANCY_CHANGE ----------------------------------
        current_zone_counts: dict[str, int] = {}
        for track in frame_output.tracks:
            if track.zone_id is None:
                continue
            current_zone_counts[track.zone_id] = current_zone_counts.get(track.zone_id, 0) + 1

        is_first_frame_for_camera = camera_id not in self._camera_seen_before
        self._camera_seen_before.add(camera_id)

        if not is_first_frame_for_camera:
            prev_zone_counts = self._zone_occupancy_counts.get(camera_id, {})
            all_zone_ids = set(current_zone_counts) | set(prev_zone_counts)
            for zone_id in all_zone_ids:
                new_count = current_zone_counts.get(zone_id, 0)
                old_count = prev_zone_counts.get(zone_id, 0)
                if new_count != old_count:
                    events.append(EngineEvent(
                        event_type="ZONE_OCCUPANCY_CHANGE", camera_id=camera_id, time_seconds=now,
                        zone_id=zone_id,
                        details={"old_count": old_count, "new_count": new_count},
                    ))
        self._zone_occupancy_counts[camera_id] = current_zone_counts

        # -- Rule 8: TABLE_STATE_CHANGED ------------------------------------
        # A direct diff of FrameOutput.table_states against its own
        # previous-frame value -- independent of any single tracked person,
        # so it fires even when zero people are currently tracked at that
        # zone_id (e.g. an "occupied" -> "dirty" transition after everyone
        # has already left). Does not read or duplicate TableKBS's internal
        # logic -- only compares the already-computed state string.
        for zone_id, state in frame_output.table_states.items():
            table_state_key = f"{camera_id}:{zone_id}"
            previous_state = self._table_states_prev.get(table_state_key)

            if previous_state is None:
                # First sighting of this zone_id -- store, emit nothing,
                # consistent with every other rule in this engine.
                self._table_states_prev[table_state_key] = state
                continue

            if state != previous_state:
                events.append(EngineEvent(
                    event_type="TABLE_STATE_CHANGED", camera_id=camera_id, time_seconds=now,
                    zone_id=zone_id,
                    details={
                        "previous_state": previous_state,
                        "new_state": state,
                        "time_seconds": now,
                    },
                ))

            self._table_states_prev[table_state_key] = state

        # -- Rule 5 (remainder): consume table_states, reset on vacancy, ---
        # check the delay threshold. Runs after the per-track loop so any
        # occupied_since set by a CUSTOMER_SEATED event this frame is
        # already in place.
        for zone_id, state in frame_output.table_states.items():
            table_key = f"{camera_id}:{zone_id}"
            table = self._table_occupancy.get(table_key)
            if table is None:
                # No occupancy cycle has ever been opened for this zone (no
                # CUSTOMER_SEATED has fired for it yet) -- nothing to reset
                # or alert on. See docs/TASK6_REPORT.md for why this is a
                # deliberate consequence of tying occupied_since strictly to
                # CUSTOMER_SEATED, per the rule's literal wording.
                continue

            if state == "free":
                table["occupied_since"] = None
                table["delay_alert_fired"] = False
                continue

            if table["occupied_since"] is not None and not table["delay_alert_fired"]:
                occupied_duration = now - table["occupied_since"]
                if occupied_duration > self.delay_alert_threshold_seconds:
                    events.append(EngineEvent(
                        event_type="DELAY_ALERT", camera_id=camera_id, time_seconds=now,
                        zone_id=zone_id,
                        details={
                            "occupied_seconds": occupied_duration,
                            "threshold_seconds": self.delay_alert_threshold_seconds,
                        },
                    ))
                    table["delay_alert_fired"] = True

        return events
