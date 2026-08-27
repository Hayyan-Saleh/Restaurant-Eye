"""
test_table_zone_anchoring.py
-----------------------------
Tests for the TableKBS zone_id anchoring fix in LivePipeline._run_kbs():
  (a) a configured table zone with zero customer/serving history appears in
      self._table_states with state "free" instead of being absent.
  (b) a table zone with actual history still gets its correct state
      (occupied / dirty) as before -- no regression.
  (c) a zone_id with history that is NOT a configured table zone triggers a
      logged warning and is still present in the output, not dropped.

LivePipeline.__init__() loads heavy ML models (YOLO, pose LSTM, ReID, etc.),
which is unnecessary to exercise this specific piece of _run_kbs(). Tests
construct a bare instance via object.__new__() and seed only the state
_run_kbs() touches, keeping the test scoped to the fix being verified.

Run with:  python -m pytest tests/test_table_zone_anchoring.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.live_pipeline import LivePipeline
from src.kbs.table_kbs import TableKBS
from src.kbs.reid_gallery import SessionGallery


CAMERA_ID = "camera_test"


class _StubZoneManager:
    def __init__(self, zones_config):
        self.zones_config = zones_config


def make_bare_pipeline(zones_config: dict) -> LivePipeline:
    pipeline = object.__new__(LivePipeline)
    pipeline.zone_manager = _StubZoneManager(zones_config)
    pipeline.table_kbs = TableKBS()
    pipeline.session_gallery = SessionGallery()
    pipeline._action_frame_counts = {}
    pipeline._last_action_zone = {}
    pipeline._global_ids = {}
    pipeline._person_roles = {}
    pipeline._action_labels = {}
    pipeline._role_accumulators = {}
    pipeline._gid_track_counts = {}
    pipeline._table_customer_counts = {}
    pipeline._table_was_served = {}
    pipeline._table_states = {}
    pipeline.person_state = []
    return pipeline


def zones_config_with_tables(*zone_ids, table_area=False):
    zones = [
        {"zone_id": zid, "zone_type": "table", "points": [[0, 0], [1, 0], [1, 1]]}
        for zid in zone_ids
    ]
    if table_area:
        zones.append({
            "zone_id": "table_area_1",
            "zone_type": "table_area",
            "points": [[0, 0], [1, 0], [1, 1]],
        })
    return {CAMERA_ID: {"zones": zones}}


def test_configured_table_with_no_history_defaults_to_free():
    zones_config = zones_config_with_tables("table_1", table_area=True)
    pipeline = make_bare_pipeline(zones_config)

    pipeline._run_kbs(tracked_objects=[], person_crops=[], time_seconds=10.0, camera_id=CAMERA_ID)

    assert pipeline._table_states.get("table_1") == "free"
    # The broader "table_area" container zone must NOT be pulled into the
    # anchored universe -- only the specific leaf "table" zone_type.
    assert "table_area_1" not in pipeline._table_states


def test_table_with_history_still_gets_occupied_and_dirty_state():
    zones_config = zones_config_with_tables("table_occupied", "table_dirty")
    pipeline = make_bare_pipeline(zones_config)

    # Simulate a prior serving event on table_dirty (persisted flag) with no
    # current customers present -> should resolve to "dirty".
    pipeline._table_was_served["table_dirty"] = True

    # Simulate a currently-present, already-confirmed customer sitting at
    # table_occupied.
    tracked_objects = [{
        "track_id": 1,
        "zone": {"zone_id": "table_occupied"},
        "zone_type_normalized": "table",
    }]
    pipeline._person_roles[1] = "customer"
    pipeline._global_ids[1] = "session_customer_1"  # already known -> ReID pre-check just touches it

    pipeline._run_kbs(
        tracked_objects=tracked_objects, person_crops=[], time_seconds=10.0, camera_id=CAMERA_ID
    )

    assert pipeline._table_states.get("table_occupied") == "occupied"
    assert pipeline._table_states.get("table_dirty") == "dirty"


def test_unconfigured_zone_with_history_logs_warning_and_is_kept(capsys):
    zones_config = zones_config_with_tables("table_1")
    pipeline = make_bare_pipeline(zones_config)

    # Historical serving flag for a zone_id that is not in the current config
    # (e.g. stale config, renamed zone).
    pipeline._table_was_served["stale_zone"] = True

    pipeline._run_kbs(tracked_objects=[], person_crops=[], time_seconds=10.0, camera_id=CAMERA_ID)

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert CAMERA_ID in captured.out
    assert "stale_zone" in captured.out

    # Not dropped -- still present in the output (dirty: no customers, was_served=True).
    assert "stale_zone" in pipeline._table_states
    assert pipeline._table_states["stale_zone"] == "dirty"
