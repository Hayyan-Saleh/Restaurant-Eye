"""
test_event_engine.py
---------------------
Tests for src/events/event_engine.py (Task 6's Event Engine), covering each
of the 7 event rules independently with synthetic FrameOutput sequences
that deliberately trigger, and deliberately do NOT trigger, each event --
plus the "first sighting generates no event" and multi-camera key-isolation
requirements from the internal-state design section.

Run with:  python -m pytest tests/test_event_engine.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.pipeline_output import FrameOutput, TrackOutput
from src.events.event_engine import EventEngine, EngineEvent, engine_event_to_dict


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_track(
    track_id: int,
    role: str = "unknown",
    zone_id: str | None = None,
    zone_type: str | None = None,
    action: str | None = None,
    global_id: str | None = None,
) -> TrackOutput:
    return TrackOutput(
        track_id=track_id,
        global_id=global_id or f"gid_{track_id}",
        bbox=[0.0, 0.0, 10.0, 10.0],
        zone_id=zone_id,
        zone_type=zone_type,
        role=role,
        confidence=1.0,
        action=action,
        zone_name=None,
    )


def make_frame(
    camera_id: str,
    frame_index: int,
    time_seconds: float,
    tracks: list[TrackOutput],
    table_states: dict[str, str] | None = None,
) -> FrameOutput:
    return FrameOutput(
        camera_id=camera_id,
        video_id="video_test",
        frame_index=frame_index,
        time_seconds=time_seconds,
        tracks=tracks,
        table_states=table_states or {},
    )


def types_of(events: list[dict]) -> list[str]:
    return [e["event_type"] for e in events]


# ---------------------------------------------------------------------------
# process_frame() return shape
# ---------------------------------------------------------------------------

def test_process_frame_returns_list_of_plain_dicts_with_required_fields():
    engine = EventEngine()
    frame = make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="w", zone_type="walk")])
    events = engine.process_frame(frame)
    assert events == []  # first sighting

    frame2 = make_frame("cam1", 1, 1.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")])
    events2 = engine.process_frame(frame2)
    assert len(events2) >= 1
    for e in events2:
        assert isinstance(e, dict)
        assert {"event_type", "camera_id", "time_seconds", "track_id", "global_id", "zone_id", "details"} <= e.keys()


def test_engine_event_to_dict_uses_dataclasses_asdict():
    event = EngineEvent(event_type="X", camera_id="cam1", time_seconds=1.0, details={"a": 1})
    d = engine_event_to_dict(event)
    assert d == {
        "event_type": "X", "camera_id": "cam1", "time_seconds": 1.0,
        "track_id": None, "global_id": None, "zone_id": None, "details": {"a": 1},
    }


# ---------------------------------------------------------------------------
# First sighting -> no event (applies across all rules)
# ---------------------------------------------------------------------------

# def test_first_sighting_generates_no_event():
#     engine = EventEngine()
#     frame = make_frame("cam1", 0, 0.0, [
#         make_track(1, role="customer", zone_id="t1", zone_type="table"),
#         make_track(2, role="worker", zone_id="staff", zone_type="staff"),
#     ])
#     assert engine.process_frame(frame) == []
def test_first_sighting_generates_no_event():
    """First sighting emits no events for non-worker tracks. Worker tracks
    are the one deliberate exception -- they get an immediate WORKER_ACTIVE
    (see event_engine.py's first-sighting branch) so a worker who's active
    from frame one still gets a Redis key right away, instead of staying
    invisible to /workers/status until their first real idle->active
    transition."""
    engine = EventEngine()
    frame = make_frame("cam1", 0, 0.0, [
        make_track(1, role="customer", zone_id="t1", zone_type="table"),
        make_track(2, role="worker", zone_id="staff", zone_type="staff"),
    ])
    events = engine.process_frame(frame)
    assert len(events) == 1
    assert events[0]["event_type"] == "WORKER_ACTIVE"
    assert events[0]["track_id"] == 2
    assert events[0]["details"]["reason"] == "first_sighting"


def test_multi_camera_same_numeric_track_id_is_independent_first_sighting():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="a", zone_type="walk")]))

    # cam2's track_id=1 must be its OWN first sighting, not "already known"
    # from cam1:1 -- proves per-track state is keyed by camera_id AND
    # track_id, never track_id alone.
    events = engine.process_frame(
        make_frame("cam2", 0, 0.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")])
    )
    assert events == []


# ---------------------------------------------------------------------------
# Rule 1: CUSTOMER_SEATED
# ---------------------------------------------------------------------------

def test_customer_seated_fires_on_zone_change_into_table():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="walk", zone_type="walk")]))

    events = engine.process_frame(
        make_frame("cam1", 1, 1.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")])
    )
    seated = [e for e in events if e["event_type"] == "CUSTOMER_SEATED"]
    assert len(seated) == 1
    assert seated[0]["track_id"] == 1
    assert seated[0]["zone_id"] == "t1"
    assert seated[0]["camera_id"] == "cam1"
    assert seated[0]["time_seconds"] == 1.0


def test_customer_seated_does_not_fire_without_zone_change():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")]))
    events = engine.process_frame(make_frame("cam1", 1, 1.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")]))
    assert "CUSTOMER_SEATED" not in types_of(events)


def test_customer_seated_does_not_fire_for_non_table_zone():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="walk1", zone_type="walk")]))
    events = engine.process_frame(make_frame("cam1", 1, 1.0, [make_track(1, role="customer", zone_id="walk2", zone_type="walk")]))
    assert "CUSTOMER_SEATED" not in types_of(events)


def test_customer_seated_does_not_fire_for_non_customer_role():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="worker", zone_id="walk", zone_type="walk")]))
    events = engine.process_frame(make_frame("cam1", 1, 1.0, [make_track(1, role="worker", zone_id="t1", zone_type="table")]))
    assert "CUSTOMER_SEATED" not in types_of(events)


# ---------------------------------------------------------------------------
# Rule 2: CUSTOMER_LEFT
# ---------------------------------------------------------------------------

def test_customer_left_fires_when_still_present_but_zone_no_longer_table():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="walk", zone_type="walk")]))
    engine.process_frame(make_frame("cam1", 1, 5.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")]))

    events = engine.process_frame(make_frame("cam1", 2, 25.0, [make_track(1, role="customer", zone_id="walk2", zone_type="walk")]))
    left = [e for e in events if e["event_type"] == "CUSTOMER_LEFT"]
    assert len(left) == 1
    assert left[0]["zone_id"] == "t1"
    assert left[0]["details"]["duration_seconds"] == 20.0
    assert left[0]["details"]["reason"] == "zone_change"


def test_customer_left_fires_when_track_disappears_entirely():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="walk", zone_type="walk")]))
    engine.process_frame(make_frame("cam1", 1, 5.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")]))

    events = engine.process_frame(make_frame("cam1", 2, 40.0, []))
    left = [e for e in events if e["event_type"] == "CUSTOMER_LEFT"]
    assert len(left) == 1
    assert left[0]["zone_id"] == "t1"
    assert left[0]["details"]["duration_seconds"] == 35.0
    assert left[0]["details"]["reason"] == "track_disappeared"


def test_customer_left_does_not_fire_for_a_track_never_seated():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="walk", zone_type="walk")]))
    events = engine.process_frame(make_frame("cam1", 1, 5.0, []))  # disappears, was never at a table
    assert "CUSTOMER_LEFT" not in types_of(events)


# ---------------------------------------------------------------------------
# Rules 3/4: STAFF_IDLE / WORKER_ACTIVE
# ---------------------------------------------------------------------------

def test_staff_idle_fires_exactly_once_at_threshold():
    engine = EventEngine(config={"events": {"staff_idle_threshold_frames": 3}})
    # track = lambda: make_track(1, role="worker", zone_id="staff", zone_type="staff")
    track = lambda: make_track(1, role="worker", zone_id="staff", zone_type="staff", action="standing")
    engine.process_frame(make_frame("cam1", 0, 0.0, [track()]))  # first sighting

    e1 = engine.process_frame(make_frame("cam1", 1, 1.0, [track()]))  # idle_counter=1
    assert "STAFF_IDLE" not in types_of(e1)

    e2 = engine.process_frame(make_frame("cam1", 2, 2.0, [track()]))  # idle_counter=2
    assert "STAFF_IDLE" not in types_of(e2)

    e3 = engine.process_frame(make_frame("cam1", 3, 3.0, [track()]))  # idle_counter=3 -> fires
    idle = [e for e in e3 if e["event_type"] == "STAFF_IDLE"]
    assert len(idle) == 1
    assert idle[0]["details"]["idle_frames"] == 3
    assert idle[0]["details"]["threshold_frames"] == 3

    # Must NOT re-fire on every subsequent frame while idle continues.
    e4 = engine.process_frame(make_frame("cam1", 4, 4.0, [track()]))
    assert "STAFF_IDLE" not in types_of(e4)
    e5 = engine.process_frame(make_frame("cam1", 5, 5.0, [track()]))
    assert "STAFF_IDLE" not in types_of(e5)


def test_worker_active_fires_when_idle_worker_changes_zone():
    engine = EventEngine(config={"events": {"staff_idle_threshold_frames": 2}})
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="worker", zone_id="staff", zone_type="staff", action="standing")]))
    engine.process_frame(make_frame("cam1", 1, 1.0, [make_track(1, role="worker", zone_id="staff", zone_type="staff", action="standing")]))
    e2 = engine.process_frame(make_frame("cam1", 2, 2.0, [make_track(1, role="worker", zone_id="staff", zone_type="staff", action="standing")]))
    assert "STAFF_IDLE" in types_of(e2)  # confirm idle was actually reached first

    e3 = engine.process_frame(make_frame("cam1", 3, 12.0, [make_track(1, role="worker", zone_id="kitchen", zone_type="staff")]))
    active = [e for e in e3 if e["event_type"] == "WORKER_ACTIVE"]
    assert len(active) == 1
    assert active[0]["zone_id"] == "kitchen"
    assert active[0]["details"]["idle_frames"] == 2
    assert active[0]["details"]["idle_duration_seconds"] == 12.0  # zone_entered_time was 0.0

    # Idle counter must have reset -- a fresh idle streak needs the full
    # threshold again, it must not immediately re-fire STAFF_IDLE.
    e4 = engine.process_frame(make_frame("cam1", 4, 13.0, [make_track(1, role="worker", zone_id="kitchen", zone_type="staff")]))
    assert "STAFF_IDLE" not in types_of(e4)


def test_worker_active_does_not_fire_if_never_reached_idle_threshold():
    engine = EventEngine(config={"events": {"staff_idle_threshold_frames": 300}})
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="worker", zone_id="staff", zone_type="staff", action="standing")]))
    engine.process_frame(make_frame("cam1", 1, 1.0, [make_track(1, role="worker", zone_id="staff", zone_type="staff", action="standing")]))
    events = engine.process_frame(make_frame("cam1", 2, 2.0, [make_track(1, role="worker", zone_id="kitchen", zone_type="staff")]))
    assert "WORKER_ACTIVE" not in types_of(events)


# ---------------------------------------------------------------------------
# Rule 5: DELAY_ALERT
# ---------------------------------------------------------------------------

def test_delay_alert_fires_once_past_threshold_and_not_before():
    engine = EventEngine(config={"events": {"delay_alert_threshold_seconds": 5.0}})
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="walk", zone_type="walk")]))

    e1 = engine.process_frame(make_frame(
        "cam1", 1, 0.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")],
        table_states={"t1": "occupied"},
    ))
    assert "CUSTOMER_SEATED" in types_of(e1)
    assert "DELAY_ALERT" not in types_of(e1)

    e2 = engine.process_frame(make_frame(
        "cam1", 2, 4.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")],
        table_states={"t1": "occupied"},
    ))
    assert "DELAY_ALERT" not in types_of(e2)  # 4.0s < 5.0s threshold

    e3 = engine.process_frame(make_frame(
        "cam1", 3, 6.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")],
        table_states={"t1": "occupied"},
    ))
    alerts = [e for e in e3 if e["event_type"] == "DELAY_ALERT"]
    assert len(alerts) == 1
    assert alerts[0]["zone_id"] == "t1"
    assert alerts[0]["details"]["occupied_seconds"] == 6.0

    # Must not re-fire every subsequent frame while still over threshold.
    e4 = engine.process_frame(make_frame(
        "cam1", 4, 10.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")],
        table_states={"t1": "occupied"},
    ))
    assert "DELAY_ALERT" not in types_of(e4)


def test_delay_alert_resets_on_serving_action_then_can_refire():
    engine = EventEngine(config={"events": {"delay_alert_threshold_seconds": 5.0}})

    engine.process_frame(make_frame("cam1", 0, 0.0, [
        make_track(1, role="customer", zone_id="walk", zone_type="walk"),
        make_track(2, role="worker", zone_id="t1", zone_type="table", action="standing"),
    ]))
    engine.process_frame(make_frame("cam1", 1, 0.0, [
        make_track(1, role="customer", zone_id="t1", zone_type="table"),
        make_track(2, role="worker", zone_id="t1", zone_type="table", action="standing"),
    ], table_states={"t1": "occupied"}))

    e2 = engine.process_frame(make_frame("cam1", 2, 6.0, [
        make_track(1, role="customer", zone_id="t1", zone_type="table"),
        make_track(2, role="worker", zone_id="t1", zone_type="table", action="standing"),
    ], table_states={"t1": "occupied"}))
    assert len([e for e in e2 if e["event_type"] == "DELAY_ALERT"]) == 1

    # A "serving" worker at the table resets the clock.
    e3 = engine.process_frame(make_frame("cam1", 3, 6.5, [
        make_track(1, role="customer", zone_id="t1", zone_type="table"),
        make_track(2, role="worker", zone_id="t1", zone_type="table", action="serving"),
    ], table_states={"t1": "occupied"}))
    assert "DELAY_ALERT" not in types_of(e3)

    # Not enough time has passed since the reset yet.
    e4 = engine.process_frame(make_frame("cam1", 4, 9.0, [
        make_track(1, role="customer", zone_id="t1", zone_type="table"),
        make_track(2, role="worker", zone_id="t1", zone_type="table", action="standing"),
    ], table_states={"t1": "occupied"}))
    assert "DELAY_ALERT" not in types_of(e4)

    # Threshold exceeded again since the service-reset point (6.5) -> refires.
    e5 = engine.process_frame(make_frame("cam1", 5, 13.0, [
        make_track(1, role="customer", zone_id="t1", zone_type="table"),
        make_track(2, role="worker", zone_id="t1", zone_type="table", action="standing"),
    ], table_states={"t1": "occupied"}))
    assert len([e for e in e5 if e["event_type"] == "DELAY_ALERT"]) == 1


def test_delay_alert_resets_when_table_becomes_free():
    engine = EventEngine(config={"events": {"delay_alert_threshold_seconds": 5.0}})
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="customer", zone_id="walk", zone_type="walk")]))
    engine.process_frame(make_frame("cam1", 1, 0.0, [make_track(1, role="customer", zone_id="t1", zone_type="table")],
                                     table_states={"t1": "occupied"}))

    # Table reported free (e.g. TableKBS decided it emptied out) before the
    # threshold would have been crossed -- and stays free afterward.
    e2 = engine.process_frame(make_frame("cam1", 2, 100.0, [], table_states={"t1": "free"}))
    assert "DELAY_ALERT" not in types_of(e2)


def test_delay_alert_does_not_fire_without_any_customer_seated_event():
    # table_states reports "occupied" but no CUSTOMER_SEATED has ever fired
    # for this zone (e.g. only non-customer tracks present) -- per this
    # engine's literal reading of rule 5 ("set when CUSTOMER_SEATED
    # fired"), occupied_since is never opened, so no alert can occur.
    engine = EventEngine(config={"events": {"delay_alert_threshold_seconds": 1.0}})
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, role="worker", zone_id="t1", zone_type="table")],
                                     table_states={"t1": "occupied"}))
    events = engine.process_frame(make_frame("cam1", 1, 100.0, [make_track(1, role="worker", zone_id="t1", zone_type="table")],
                                              table_states={"t1": "occupied"}))
    assert "DELAY_ALERT" not in types_of(events)


# ---------------------------------------------------------------------------
# Rule 6: ZONE_TRANSITION (downsampled)
# ---------------------------------------------------------------------------

def test_zone_transition_fires_and_is_downsampled_within_interval():
    engine = EventEngine(config={"events": {"zone_transition_min_interval_seconds": 2.0}})
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, zone_id="a", zone_type="walk")]))

    e1 = engine.process_frame(make_frame("cam1", 1, 0.5, [make_track(1, zone_id="b", zone_type="walk")]))
    transitions1 = [e for e in e1 if e["event_type"] == "ZONE_TRANSITION"]
    assert len(transitions1) == 1
    assert transitions1[0]["details"] == {"from_zone_id": "a", "to_zone_id": "b"}

    # Only 0.5s since the last EMITTED transition -> suppressed, even though
    # the zone did change again.
    e2 = engine.process_frame(make_frame("cam1", 2, 1.0, [make_track(1, zone_id="c", zone_type="walk")]))
    assert "ZONE_TRANSITION" not in types_of(e2)

    # 2.1s since the last emitted transition (t=0.5) -> fires again, and
    # from_zone_id reflects the most recent actual zone ("c"), proving zone
    # bookkeeping still updates every frame even when the event is suppressed.
    e3 = engine.process_frame(make_frame("cam1", 3, 2.6, [make_track(1, zone_id="d", zone_type="walk")]))
    transitions3 = [e for e in e3 if e["event_type"] == "ZONE_TRANSITION"]
    assert len(transitions3) == 1
    assert transitions3[0]["details"] == {"from_zone_id": "c", "to_zone_id": "d"}


def test_zone_transition_does_not_fire_without_zone_change():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, zone_id="a", zone_type="walk")]))
    events = engine.process_frame(make_frame("cam1", 1, 1.0, [make_track(1, zone_id="a", zone_type="walk")]))
    assert "ZONE_TRANSITION" not in types_of(events)


# ---------------------------------------------------------------------------
# Rule 7: ZONE_OCCUPANCY_CHANGE
# ---------------------------------------------------------------------------

def test_zone_occupancy_change_silent_on_first_frame_then_fires_on_real_change():
    engine = EventEngine()
    frame0 = make_frame("cam1", 0, 0.0, [
        make_track(1, zone_id="z1", zone_type="walk"),
        make_track(2, zone_id="z1", zone_type="walk"),
    ])
    e0 = engine.process_frame(frame0)
    assert "ZONE_OCCUPANCY_CHANGE" not in types_of(e0)  # bootstrap frame, no real "previous" to diff

    frame1 = make_frame("cam1", 1, 1.0, [make_track(1, zone_id="z1", zone_type="walk")])  # z1: 2 -> 1
    e1 = engine.process_frame(frame1)
    occ = [e for e in e1 if e["event_type"] == "ZONE_OCCUPANCY_CHANGE"]
    assert len(occ) == 1
    assert occ[0]["zone_id"] == "z1"
    assert occ[0]["details"] == {"old_count": 2, "new_count": 1}


def test_zone_occupancy_change_does_not_fire_for_unchanged_zone_counts():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, zone_id="z1", zone_type="walk")]))
    events = engine.process_frame(make_frame("cam1", 1, 1.0, [make_track(1, zone_id="z1", zone_type="walk")]))
    assert "ZONE_OCCUPANCY_CHANGE" not in types_of(events)


def test_zone_occupancy_change_ignores_tracks_with_no_zone():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [make_track(1, zone_id=None, zone_type=None)]))
    events = engine.process_frame(make_frame("cam1", 1, 1.0, [make_track(1, zone_id=None, zone_type=None)]))
    assert "ZONE_OCCUPANCY_CHANGE" not in types_of(events)


# ---------------------------------------------------------------------------
# Rule 8 (addendum): TABLE_STATE_CHANGED
# ---------------------------------------------------------------------------

def test_table_state_changed_first_sighting_emits_nothing():
    engine = EventEngine()
    events = engine.process_frame(make_frame("cam1", 0, 0.0, [], table_states={"t1": "free"}))
    assert "TABLE_STATE_CHANGED" not in types_of(events)


def test_table_state_changed_fires_with_correct_previous_and_new_state():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [], table_states={"t1": "free"}))

    events = engine.process_frame(make_frame("cam1", 1, 1.0, [], table_states={"t1": "occupied"}))
    changed = [e for e in events if e["event_type"] == "TABLE_STATE_CHANGED"]
    assert len(changed) == 1
    assert changed[0]["zone_id"] == "t1"
    assert changed[0]["camera_id"] == "cam1"
    assert changed[0]["details"] == {"previous_state": "free", "new_state": "occupied", "time_seconds": 1.0}

    # A further real transition (occupied -> dirty) fires again.
    events2 = engine.process_frame(make_frame("cam1", 2, 2.0, [], table_states={"t1": "dirty"}))
    changed2 = [e for e in events2 if e["event_type"] == "TABLE_STATE_CHANGED"]
    assert len(changed2) == 1
    assert changed2[0]["details"] == {"previous_state": "occupied", "new_state": "dirty", "time_seconds": 2.0}


def test_table_state_changed_does_not_fire_when_state_unchanged():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [], table_states={"t1": "occupied"}))
    events = engine.process_frame(make_frame("cam1", 1, 1.0, [], table_states={"t1": "occupied"}))
    assert "TABLE_STATE_CHANGED" not in types_of(events)


def test_table_state_changed_fires_independent_of_any_tracked_person():
    # No tracks at all in either frame -- the event is table-level only,
    # not derived from any tracked person's movement.
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [], table_states={"t1": "occupied"}))
    events = engine.process_frame(make_frame("cam1", 1, 30.0, [], table_states={"t1": "dirty"}))

    changed = [e for e in events if e["event_type"] == "TABLE_STATE_CHANGED"]
    assert len(changed) == 1
    assert changed[0]["track_id"] is None
    assert changed[0]["global_id"] is None
    assert changed[0]["details"] == {"previous_state": "occupied", "new_state": "dirty", "time_seconds": 30.0}


def test_table_state_changed_is_scoped_per_camera():
    engine = EventEngine()
    engine.process_frame(make_frame("cam1", 0, 0.0, [], table_states={"t1": "free"}))
    # cam2's zone_id "t1" (same name) must be its own independent first
    # sighting, not treated as already-known from cam1:t1.
    events = engine.process_frame(make_frame("cam2", 0, 0.0, [], table_states={"t1": "occupied"}))
    assert "TABLE_STATE_CHANGED" not in types_of(events)
