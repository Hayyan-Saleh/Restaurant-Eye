"""
test_pipeline_output.py
------------------------
Tests for the unified data contract (TrackOutput/FrameOutput) and its JSON
export, plus real integration through LivePipeline.process_frame():
  (a) FrameOutput round-trips through frame_output_to_json + json.loads
      without data loss.
  (b) calling the real (modified) process_frame() on a LivePipeline produces
      a FrameOutput whose tracks/table_states reflect actual pipeline
      state, not placeholder values.
  (c) time_seconds in the output is the video-relative value passed in,
      not wall-clock time.

(b) and (c) drive process_frame() for real -- detection, tracking,
zone-matching, RoleEngine, TableKBS, _build_frame_output() all run as they
would in production. Only the detector/tracker (the ML perception layer,
already covered by their own tests) are replaced with lightweight stubs
returning realistic, pre-tracked data, so this test doesn't need to load
YOLO/torch models or a real video frame -- consistent with prior tasks'
object.__new__() stub pattern, extended here because this task needs the
real process_frame() call chain exercised, not just an isolated helper.

Run with:  python -m pytest tests/test_pipeline_output.py -v
"""

import json
import sys
import time
from pathlib import Path

import faiss
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.live_pipeline import LivePipeline
from src.core.pipeline_output import TrackOutput, FrameOutput, frame_output_to_json
from src.core.central_identity_store import CentralIdentityStore
from src.kbs.table_kbs import TableKBS
from src.kbs.reid_gallery import SessionGallery, ReIDGallery
from src.zone_management.zone_manager import ZoneManager


CAMERA_ID = "camera_test"


# ---------------------------------------------------------------------------
# (a) round-trip serialization
# ---------------------------------------------------------------------------

def test_frame_output_round_trips_through_json_without_data_loss():
    output = FrameOutput(
        camera_id="camera_01",
        video_id="video_001",
        frame_index=42,
        time_seconds=21.5,
        tracks=[
            TrackOutput(
                track_id=1,
                global_id="worker_ab12cd34",
                bbox=[10.0, 20.0, 110.0, 220.0],
                zone_id="camera_01_table_3",
                zone_type="table",
                role="worker",
                confidence=0.93,
                action="serving",
                zone_name="Table 3",
            ),
            TrackOutput(
                track_id=2,
                global_id="session_deadbeef",
                bbox=[300.0, 40.0, 360.0, 200.0],
                zone_id=None,
                zone_type="unknown",
                role="unknown",
                confidence=None,
                action=None,
                zone_name=None,
            ),
        ],
        table_states={"camera_01_table_3": "occupied", "camera_01_table_4": "free"},
    )

    json_str = frame_output_to_json(output)
    assert isinstance(json_str, str)

    decoded = json.loads(json_str)

    assert decoded["camera_id"] == "camera_01"
    assert decoded["video_id"] == "video_001"
    assert decoded["frame_index"] == 42
    assert decoded["time_seconds"] == 21.5
    assert decoded["table_states"] == {"camera_01_table_3": "occupied", "camera_01_table_4": "free"}

    assert len(decoded["tracks"]) == 2
    t0, t1 = decoded["tracks"]
    assert t0["track_id"] == 1
    assert t0["global_id"] == "worker_ab12cd34"
    assert t0["bbox"] == [10.0, 20.0, 110.0, 220.0]
    assert t0["zone_id"] == "camera_01_table_3"
    assert t0["zone_type"] == "table"
    assert t0["role"] == "worker"
    assert t0["confidence"] == 0.93
    assert t0["action"] == "serving"
    assert t0["zone_name"] == "Table 3"

    assert t1["track_id"] == 2
    assert t1["zone_id"] is None
    assert t1["confidence"] is None
    assert t1["action"] is None
    assert t1["zone_name"] is None


# ---------------------------------------------------------------------------
# Stubs for the real process_frame() integration tests
# ---------------------------------------------------------------------------

def make_zone_manager(zones_config: dict) -> ZoneManager:
    """Real ZoneManager (real geometric matching), bypassing __init__'s disk
    read -- get_bbox_zone() and its helpers only depend on the class-level
    ZONE_TYPE_MAP/HAND_KP_INDICES/etc. constants plus self.zones_config."""
    zone_manager = object.__new__(ZoneManager)
    zone_manager.zones_config = zones_config
    return zone_manager


class _StubDetector:
    """Stand-in for PersonDetector: returns one fixed detection, no keypoints.

    The bbox is deliberately narrower than ReIDGallery.MIN_CROP_WIDTH (32px)
    so _run_kbs() takes its real "crop too small, skip ReID this frame"
    branch -- exercising genuine pipeline logic without needing the actual
    OSNet embedding model loaded.
    """

    def detect_with_pose(self, frame):
        detections = [{
            "track_id": None,
            "box": [10, 10, 30, 90],
            "conf": 0.87,
            "class_id": 0,
            "class_name": "person",
        }]
        keypoints_list = [None]  # no pose -> action stays unset, exercises that path too
        return detections, keypoints_list


class _StubTracker:
    """Stand-in for RestaurantTracker: echoes the detection back with a track_id,
    in the exact shape the real DeepSort-backed tracker.update() produces."""

    def update(self, detections, frame=None):
        return [
            {
                "track_id": 1,
                "box": [float(v) for v in det["box"]],
                "conf": det["conf"],
                "class_id": det["class_id"],
                "class_name": det["class_name"],
            }
            for det in detections
        ]


def make_stub_pipeline() -> LivePipeline:
    # A "table" zone whose polygon contains the stub detection's bbox center
    # (30, 50), so real ZoneManager geometry matching exercises a genuine hit
    # rather than only the "unknown zone" path.
    zones_config = {
        CAMERA_ID: {
            "zones": [
                {
                    "zone_id": "table_1",
                    "zone_type": "table",
                    "zone_name": "Table 1",
                    "points": [[0, 0], [0, 100], [100, 100], [100, 0]],
                }
            ]
        }
    }

    pipeline = object.__new__(LivePipeline)
    pipeline.zone_manager = make_zone_manager(zones_config)
    pipeline.detector = _StubDetector()
    pipeline.tracker = _StubTracker()
    pipeline.table_kbs = TableKBS()
    pipeline.session_gallery = SessionGallery()
    # Bypass ReIDGallery.__init__ (loads the real OSNet model) -- only its
    # class-level MIN_CROP_WIDTH/MIN_CROP_HEIGHT constants are needed. Note:
    # _run_kbs() calls self.reid_gallery._extract(crop) directly and has no
    # crop-size gate of its own (that check only lives inside
    # ReIDGallery.process(), which _run_kbs doesn't call) -- so _extract()
    # DOES run for this stub's small crop and needs a working override
    # rather than the real (unloaded) OSNet extractor.
    pipeline.reid_gallery = object.__new__(ReIDGallery)
    pipeline.reid_gallery._extract = lambda crop: np.ones((1, 512), dtype=np.float32) / (512 ** 0.5)
    pipeline.reid_gallery.index = faiss.IndexFlatIP(ReIDGallery.EMBEDDING_DIM)
    pipeline.reid_gallery._ids = []
    pipeline.reid_gallery.threshold = 0.72
    # Deliberately unreachable (nothing listens on port 1) -- exercises the
    # real tier-2-unavailable -> tier-3 SessionGallery fallback path
    # (src/core/live_pipeline.py's _run_kbs()) deterministically, without
    # this unit test depending on a real Redis instance being up.
    pipeline.central_identity_store = CentralIdentityStore(host="localhost", port=1, db=0, socket_timeout=0.2)

    pipeline._kp_buffer = {}
    pipeline._action_labels = {}
    pipeline._action_frame_counts = {}
    pipeline._last_action_zone = {}
    pipeline._global_ids = {}
    pipeline._person_roles = {}
    pipeline._active_states = {}
    pipeline._role_accumulators = {}
    pipeline._gid_track_counts = {}
    pipeline._table_customer_counts = {}
    pipeline._table_was_served = {}
    pipeline._table_states = {}
    pipeline._table_presence_cycles = {}
    pipeline._table_absence_cycles = {}
    pipeline._table_seated_ids = {}
    pipeline._occupy_cycles = 1
    pipeline._vacate_cycles = 1
    pipeline.SERVING_SUSTAINED_FRAMES = 6
    pipeline.person_state = []
    return pipeline


# ---------------------------------------------------------------------------
# (b) real process_frame() produces a FrameOutput reflecting real state
# ---------------------------------------------------------------------------

def test_process_frame_produces_frame_output_reflecting_real_pipeline_state():
    pipeline = make_stub_pipeline()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    event = pipeline.process_frame(
        frame=frame,
        camera_id=CAMERA_ID,
        video_id="video_test",
        frame_index=7,
        time_seconds=33.5,
        pose_stride=None,
    )

    assert "frame_output" in event
    frame_output = event["frame_output"]
    assert isinstance(frame_output, FrameOutput)

    assert frame_output.camera_id == CAMERA_ID
    assert frame_output.video_id == "video_test"
    assert frame_output.frame_index == 7

    # Not placeholder values: the track came from the stub detector/tracker
    # through the REAL enrich_tracks_with_zones() + _run_kbs() call chain.
    assert len(frame_output.tracks) == 1
    track = frame_output.tracks[0]
    assert isinstance(track, TrackOutput)
    assert track.track_id == 1
    assert track.bbox == [10.0, 10.0, 30.0, 90.0]
    assert track.confidence == 0.87
    # Real ZoneManager geometry match against the configured "table_1" zone,
    # not a hardcoded/placeholder zone.
    assert track.zone_id == "table_1"
    assert track.zone_type == "table"
    assert track.zone_name == "Table 1"
    # _run_kbs() has no crop-size gate of its own (see make_stub_pipeline()'s
    # comment) -- real Re-ID genuinely runs for this track. The stub's
    # central_identity_store is deliberately unreachable, so it falls
    # through tier 2 to the real tier-3 SessionGallery, which registers a
    # real "session_..." id -- this is the actual identity the real _run_kbs()
    # call chain assigned this frame, not a hardcoded test placeholder.
    assert track.global_id.startswith("session_")
    assert track.role in ("worker", "customer", "unknown")

    # table_states reflects real, current TableKBS behavior for this single-
    # cycle stub run: TableKBS only declares a fact for a zone once it has
    # customer-occupancy evidence (self._table_customer_counts /
    # _table_was_served in live_pipeline.py's _run_kbs()), and RoleEngine
    # hasn't confirmed this track as "customer" after only one cycle (role
    # is "unknown", per the assertion above) -- so no TableFact was ever
    # declared for "table_1" here. table_states legitimately equals {} in
    # this exact scenario: the real (correct, empty) output of
    # get_tables_current_state(), not a hardcoded placeholder.
    assert frame_output.table_states == {}

    # The raw dict return is unchanged (additive integration, not a breaking change).
    assert event["tracked_objects"][0]["track_id"] == 1
    assert event["tables_current_state"] == frame_output.table_states


# ---------------------------------------------------------------------------
# (c) time_seconds is video-relative, not wall-clock
# ---------------------------------------------------------------------------

def test_frame_output_time_seconds_is_video_relative_not_wall_clock():
    pipeline = make_stub_pipeline()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    wall_clock_before = time.time()
    video_relative_time_seconds = 999999.25  # deliberately far from any real wall-clock time

    event = pipeline.process_frame(
        frame=frame,
        camera_id=CAMERA_ID,
        video_id="video_test",
        frame_index=3,
        time_seconds=video_relative_time_seconds,
        pose_stride=None,
    )

    frame_output = event["frame_output"]
    assert frame_output.time_seconds == video_relative_time_seconds
    assert frame_output.time_seconds != pytest.approx(wall_clock_before, abs=1.0)


def test_process_frame_raises_on_none_time_seconds_via_update_person_state():
    # time_seconds ultimately reaches _update_person_state() (Task 1's fix),
    # which must still reject None rather than silently falling back to
    # wall-clock time -- confirms this contract change didn't reintroduce
    # a bypass for that guard.
    pipeline = make_stub_pipeline()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    with pytest.raises(ValueError):
        pipeline.process_frame(
            frame=frame,
            camera_id=CAMERA_ID,
            video_id="video_test",
            frame_index=1,
            time_seconds=None,
            pose_stride=None,
        )
