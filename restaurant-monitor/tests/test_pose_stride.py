"""
test_pose_stride.py
--------------------
Tests for wiring config.json's pipeline.process_fps to a dynamically
computed pose_stride in src/run_live.py:
  (a) pose_stride is computed correctly from a given process_fps + actual
      video FPS pair, covering native FPS > process_fps and native FPS <
      process_fps.
  (b) a missing/invalid process_fps in config raises a clear ValueError
      instead of silently defaulting.
  (c) the previous hardcoded `pose_stride = 1` no longer exists in main()'s
      code path, and pose_stride is still passed through to process_frame()
      downstream unchanged.

Run with:  python -m pytest tests/test_pose_stride.py -v
"""

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.run_live import resolve_process_fps, compute_pose_stride, main


# ---------------------------------------------------------------------------
# (a) pose_stride computation
# ---------------------------------------------------------------------------

def test_pose_stride_when_native_fps_higher_than_process_fps():
    # camera_01-style native fps (15.001) vs configured process_fps=2
    # -> round(15.001 / 2) = 8
    assert compute_pose_stride(15.001, 2) == 8


def test_pose_stride_when_native_fps_lower_than_process_fps():
    # native fps (1.0) below configured process_fps=2
    # -> round(1.0 / 2) = 0, floored to the minimum stride of 1
    assert compute_pose_stride(1.0, 2) == 1


def test_pose_stride_matches_videos_index_example_fps():
    # videos_index.json documents a camera at native fps 7.0
    # -> round(7.0 / 2) = 4
    assert compute_pose_stride(7.0, 2) == 4


def test_pose_stride_falls_back_visibly_when_fps_unavailable(capsys):
    assert compute_pose_stride(None, 2) == 1
    assert compute_pose_stride(0, 2) == 1
    assert compute_pose_stride(-5, 2) == 1

    captured = capsys.readouterr()
    assert captured.out.count("WARNING") == 3


# ---------------------------------------------------------------------------
# (b) missing/invalid process_fps raises a clear error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("config", [
    {},                                          # no "pipeline" key at all
    {"pipeline": {}},                             # no "process_fps" key
    {"pipeline": {"process_fps": 0}},              # zero
    {"pipeline": {"process_fps": -2}},             # negative
    {"pipeline": {"process_fps": "2"}},            # wrong type (string)
    {"pipeline": {"process_fps": None}},           # explicit null
    {"pipeline": {"process_fps": True}},           # bool is not a valid fps
])
def test_resolve_process_fps_raises_on_missing_or_invalid_config(config):
    with pytest.raises(ValueError, match="process_fps"):
        resolve_process_fps(config)


def test_resolve_process_fps_accepts_valid_config():
    assert resolve_process_fps({"pipeline": {"process_fps": 2}}) == 2.0


# ---------------------------------------------------------------------------
# (c) hardcoded pose_stride = 1 is gone; downstream usage unchanged
# ---------------------------------------------------------------------------

def test_hardcoded_pose_stride_removed_and_still_passed_downstream():
    source = inspect.getsource(main)
    assert "pose_stride = 1" not in source
    assert "compute_pose_stride(" in source
    # pose_stride must still flow into process_frame() unchanged after this fix
    assert "pose_stride=pose_stride" in source
