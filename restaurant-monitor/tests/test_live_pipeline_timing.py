"""
test_live_pipeline_timing.py
-----------------------------
Focused unit tests for LivePipeline._update_person_state(), covering the
video-relative timing fix:
  (a) Duration is computed from the time_seconds difference, not frame counting.
  (b) datetime.now() is no longer used anywhere in the function.
  (c) _update_person_state() raises ValueError when time_seconds is None.

LivePipeline.__init__() loads heavy ML models (YOLO, pose LSTM, ReID, etc.),
which is unnecessary to exercise this specific method. Tests construct a bare
instance via object.__new__() and seed only the state _update_person_state()
touches (self.person_state), keeping the test fast and scoped to the bug
being fixed rather than a full pipeline integration test.

Run with:  python -m pytest tests/test_live_pipeline_timing.py -v
"""

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.live_pipeline import LivePipeline


def make_bare_pipeline() -> LivePipeline:
    """Construct a LivePipeline instance without running __init__."""
    pipeline = object.__new__(LivePipeline)
    pipeline.person_state = []
    return pipeline


def test_duration_computed_from_time_seconds_difference():
    pipeline = make_bare_pipeline()

    # First sighting at video time 10.0s -> entrance recorded, duration 0.
    pipeline._update_person_state(global_id="worker_1", local_id=1, zone_type="table", time_seconds=10.0)
    record = pipeline.person_state[0]
    assert record["Duration"] == "0:00:00"
    assert record["_entrance_time_seconds"] == 10.0

    # Same zone, later video time -> duration must equal the time_seconds delta (25s),
    # regardless of how many frames were processed in between.
    pipeline._update_person_state(global_id="worker_1", local_id=1, zone_type="table", time_seconds=35.0)
    record = pipeline.person_state[0]
    assert record["Duration"] == "0:00:25"

    # Zone change resets the entrance point and duration.
    pipeline._update_person_state(global_id="worker_1", local_id=1, zone_type="walk", time_seconds=40.0)
    record = pipeline.person_state[0]
    assert record["current_location"] == "walk"
    assert record["Duration"] == "0:00:00"
    assert record["_entrance_time_seconds"] == 40.0

    # Duration keeps tracking from the new entrance point.
    pipeline._update_person_state(global_id="worker_1", local_id=1, zone_type="walk", time_seconds=100.0)
    record = pipeline.person_state[0]
    assert record["Duration"] == "0:01:00"


def test_no_datetime_now_used_in_update_person_state():
    source = inspect.getsource(LivePipeline._update_person_state)
    assert "datetime.now" not in source
    assert "_internal_frames_count" not in source


def test_update_person_state_raises_on_none_time_seconds():
    pipeline = make_bare_pipeline()
    with pytest.raises(ValueError):
        pipeline._update_person_state(global_id="worker_1", local_id=1, zone_type="table", time_seconds=None)
