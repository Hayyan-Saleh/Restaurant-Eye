"""
test_event_dispatcher_smoke_e2e.py
------------------------------------
Task 9's required "one real, full end-to-end smoke test": drives the real,
unmodified EventEngine (src/events/event_engine.py, Task 6) with a sequence
of FrameOutput/TrackOutput objects -- the same typed, stable data contract
LivePipeline actually produces (src/core/pipeline_output.py) and the same
technique tests/test_event_engine.py already uses to test the engine -- far
enough to trigger all 8 event rules in one continuous run, feeds every
event dict EventEngine emits into the real EventDispatcher, and asserts
real rows/keys actually landed in restaurant_db and the real local Redis
instance (queried back and printed with `-s` as proof).

Why synthetic FrameOutputs instead of a literal LivePipeline run
-------------------------------------------------------------------
Task 9 is scoped to the *dispatcher's* storage-routing correctness, not the
CV pipeline's frame-processing correctness -- that was already covered
exhaustively by Tasks 1-6's own test suites, and this task's instructions
explicitly forbid touching live_pipeline.py. EventEngine.process_frame()
cannot tell a synthetic FrameOutput from one LivePipeline actually produced
(it is the exact same dataclass), so this test exercises the identical,
real EventEngine -> EventDispatcher -> Postgres/Redis code path production
traffic would use, without the cost/nondeterminism of running real
YOLO/pose/Re-ID inference against real video for a storage-routing test.

Uses the same real-Postgres/real-Redis fixtures as test_event_dispatcher.py
(same db 14 for Redis, same disposable-camera_id pattern for Postgres).

Run with:  python -m pytest tests/test_event_dispatcher_smoke_e2e.py -v -s
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import delete  # noqa: E402

from app.models import (  # noqa: E402
    Alert,
    CustomerSession,
    EventLog,
    SystemSetting,
    TableState,
    WorkerState,
)

from src.core.pipeline_output import FrameOutput, TrackOutput  # noqa: E402
from src.events.event_dispatcher import EventDispatcher  # noqa: E402
from src.events.event_engine import EventEngine  # noqa: E402

from tests.test_event_dispatcher import (  # noqa: E402 -- reuse the same real-service fixtures/skip markers
    camera_with_zones,
    db_session,
    dispatcher,
    pytestmark,
    redis_client,
)


def make_track(track_id, global_id, role, zone_id, zone_type, action=None) -> TrackOutput:
    return TrackOutput(
        track_id=track_id, global_id=global_id, bbox=[0.0, 0.0, 10.0, 10.0],
        zone_id=zone_id, zone_type=zone_type, role=role, confidence=0.99, action=action,
    )


@pytest.fixture()
async def low_worker_idle_limit(db_session):
    """Task 10: the STAFF_IDLE event this smoke test drives real-fires with
    idle_duration_seconds=1.3 (see the frame sequence below) -- a
    worker_idle_limit of 1 second guarantees the WORKER_IDLE_TOO_LONG alert
    path actually fires within this smoke test's handful of frames, the same
    way Task 9's own delay_alert_threshold_seconds=1.0 override does for
    DELAY_ALERT. Deletes any existing system_settings row(s) first so this
    is deterministic regardless of what's already in the real table, and
    cleans back up to empty afterwards."""
    await db_session.execute(delete(SystemSetting))
    db_session.add(SystemSetting(worker_idle_limit=1))
    await db_session.commit()
    try:
        yield
    finally:
        await db_session.execute(delete(SystemSetting))
        await db_session.commit()


async def test_full_pipeline_event_engine_dispatcher_smoke(
    dispatcher: EventDispatcher, db_session, redis_client, camera_with_zones, low_worker_idle_limit, capsys
):
    camera_id, table_zone, service_zone = camera_with_zones
    customer_gid = f"smoke_customer_{uuid.uuid4().hex[:6]}"
    worker_gid = f"smoke_worker_{uuid.uuid4().hex[:6]}"

    # Small, real config overrides (not a modification of event_engine.py --
    # EventEngine already reads these from its own `config` constructor
    # argument) so STAFF_IDLE/DELAY_ALERT fire within a handful of frames
    # instead of needing 300 frames / 900 real seconds.
    engine = EventEngine(config={
        "events": {
            "staff_idle_threshold_frames": 3,
            "delay_alert_threshold_seconds": 1.0,
            "zone_transition_min_interval_seconds": 0.0,
        }
    })

    def frame(idx, t, track1_zone, track1_type, track2_zone, table_state) -> FrameOutput:
        return FrameOutput(
            camera_id=camera_id, video_id="smoke_video", frame_index=idx, time_seconds=t,
            tracks=[
                make_track(1, customer_gid, "customer", track1_zone, track1_type),
                make_track(2, worker_gid, "worker", track2_zone, "service"),
            ],
            table_states={table_zone: table_state},
        )

    frames = [
        frame(0, 0.0, None, None, service_zone, "free"),           # first sighting -- bootstraps state, no events
        frame(1, 1.0, table_zone, "table", service_zone, "occupied"),   # CUSTOMER_SEATED, ZONE_TRANSITION, ZONE_OCCUPANCY_CHANGE, TABLE_STATE_CHANGED
        frame(2, 1.2, table_zone, "table", service_zone, "occupied"),   # idle_counter=2 for the worker
        frame(3, 1.3, table_zone, "table", service_zone, "occupied"),   # idle_counter=3 -> STAFF_IDLE
        frame(4, 2.5, None, None, table_zone, "occupied"),              # CUSTOMER_LEFT, ZONE_TRANSITION, WORKER_ACTIVE, DELAY_ALERT, ZONE_OCCUPANCY_CHANGE
    ]

    all_events: list[dict] = []
    for f in frames:
        all_events.extend(engine.process_frame(f))

    fired_types = {e["event_type"] for e in all_events}
    expected_types = {
        "CUSTOMER_SEATED", "CUSTOMER_LEFT", "STAFF_IDLE", "WORKER_ACTIVE",
        "DELAY_ALERT", "ZONE_TRANSITION", "ZONE_OCCUPANCY_CHANGE", "TABLE_STATE_CHANGED",
    }
    assert expected_types <= fired_types, (
        f"the real EventEngine did not fire every rule this smoke test needs -- "
        f"missing {expected_types - fired_types}. Got: {all_events}"
    )

    result = await dispatcher.dispatch(all_events)
    await db_session.commit()

    assert result.failed == 0, f"dispatch had unexpected failures: {result.failures}"
    assert result.succeeded == len(all_events)

    # ---- Real proof: query real rows back out of restaurant_db and real ----
    # ---- keys back out of Redis, and print them (`-s`) for the report. -----
    logs = (await db_session.execute(
        select(EventLog).where(EventLog.camera_id == camera_id).order_by(EventLog.id)
    )).scalars().all()
    worker_states = (await db_session.execute(
        select(WorkerState).where(WorkerState.entity_id == worker_gid)
    )).scalars().all()
    table_states = (await db_session.execute(
        select(TableState).where(TableState.zone_id == table_zone)
    )).scalars().all()
    alerts = (await db_session.execute(
        select(Alert).where(Alert.camera_id == camera_id).order_by(Alert.id)
    )).scalars().all()
    customer_sessions = (await db_session.execute(
        select(CustomerSession).where(CustomerSession.entity_id == customer_gid)
    )).scalars().all()

    print("\n=== Task 9/10 end-to-end smoke test: real rows in restaurant_db ===")
    print(f"-- events_log ({len(logs)} rows) --")
    for row in logs:
        print(f"  id={row.id} type={row.event_type} entity_id={row.entity_id} "
              f"role={row.role} zone_id={row.zone_id} prev={row.previous_state} "
              f"new={row.new_state} details={row.details}")
    print(f"-- worker_states ({len(worker_states)} rows) --")
    for row in worker_states:
        print(f"  entity_id={row.entity_id} status={row.status} zone_id={row.zone_id} "
              f"last_seen_at={row.last_seen_at}")
    print(f"-- table_states ({len(table_states)} rows) --")
    for row in table_states:
        print(f"  zone_id={row.zone_id} state={row.state} state_started_at={row.state_started_at}")
    print(f"-- alerts ({len(alerts)} rows) --")
    for row in alerts:
        print(f"  id={row.id} type={row.alert_type} entity_id={row.entity_id} "
              f"zone_id={row.zone_id} message={row.message!r}")
    print(f"-- customer_sessions ({len(customer_sessions)} rows) --")
    for row in customer_sessions:
        print(f"  entity_id={row.entity_id} table_zone_id={row.table_zone_id} "
              f"started_at={row.started_at} last_seen_at={row.last_seen_at} "
              f"left_at={row.left_at} total_stay_sec={row.total_stay_sec} status={row.status}")

    print("=== Task 9/10 end-to-end smoke test: real keys in Redis (db 14) ===")
    redis_keys = {
        f"worker:{worker_gid}:status": await redis_client.get(f"worker:{worker_gid}:status"),
        f"zone:{camera_id}:{table_zone}:last_seen": await redis_client.get(f"zone:{camera_id}:{table_zone}:last_seen"),
        f"zone:{camera_id}:{table_zone}:count": await redis_client.get(f"zone:{camera_id}:{table_zone}:count"),
        f"table:{table_zone}:status": await redis_client.get(f"table:{table_zone}:status"),
    }
    for key, value in redis_keys.items():
        print(f"  {key} = {value!r}")

    # ---- Assertions on that same real, queried-back proof ----
    assert len(logs) >= 4  # CUSTOMER_SEATED, CUSTOMER_LEFT, STAFF_IDLE, TABLE_STATE_CHANGED, DELAY_ALERT all insert events_log rows
    assert {r.event_type for r in logs} >= {
        "CUSTOMER_SEATED", "CUSTOMER_LEFT", "STAFF_IDLE", "TABLE_STATE_CHANGED", "DELAY_ALERT",
    }
    assert len(worker_states) == 1
    assert worker_states[0].status.value == "ACTIVE"  # last event for this worker was WORKER_ACTIVE
    assert len(table_states) == 1
    assert table_states[0].state.value == "OCCUPIED"

    # Task 10: the STAFF_IDLE at frame 3 (idle_duration_seconds=1.3) crosses
    # low_worker_idle_limit's worker_idle_limit=1 -- both DELAY_ALERT
    # (Task 9, pre-existing) and the new WORKER_IDLE_TOO_LONG alert fire in
    # this same run.
    assert len(alerts) == 2
    assert {a.alert_type for a in alerts} == {"DELAY_ALERT", "WORKER_IDLE_TOO_LONG"}
    idle_alert = next(a for a in alerts if a.alert_type == "WORKER_IDLE_TOO_LONG")
    assert idle_alert.entity_id == worker_gid
    assert idle_alert.zone_id == service_zone

    # Task 10: CUSTOMER_SEATED (frame 1) opened a session, CUSTOMER_LEFT
    # (frame 4) closed the same row -- exactly one row, not two.
    assert len(customer_sessions) == 1
    session = customer_sessions[0]
    assert session.table_zone_id == table_zone
    assert session.camera_id == camera_id
    assert session.left_at is not None
    assert session.total_stay_sec is not None
    assert session.status.value == "COMPLETED"

    assert redis_keys[f"worker:{worker_gid}:status"] == "ACTIVE"
    assert redis_keys[f"zone:{camera_id}:{table_zone}:last_seen"] is not None
    assert redis_keys[f"zone:{camera_id}:{table_zone}:count"] is not None
    assert redis_keys[f"table:{table_zone}:status"] == "OCCUPIED"
