"""
pipeline_output.py
-------------------
Typed, exportable data contract for LivePipeline's per-frame output.

TrackOutput / FrameOutput formalize the shape that process_frame() has
always informally returned (see LivePipeline.process_frame() and
LivePipeline._run_kbs() in src/core/live_pipeline.py) so downstream
consumers (Event Engine, a future Redis publisher, tests) have a stable,
strictly-typed contract instead of a loosely-shaped dict. This module only
defines the contract and JSON export; it does not publish anywhere.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class TrackOutput:
    """One tracked person, for one processed frame.

    Fields track_id/global_id/bbox/zone_id/zone_type/role/confidence are the
    task's required floor. action and zone_name are additional fields the
    pipeline already computes per-track and that are directly meaningful to
    downstream consumers (see docs/EVENT_ENGINE.md's ActionObserved /
    ZoneOccupancy raw-event rows) -- included per the "floor, not ceiling"
    instruction, defaulted to None since they are enrichments rather than
    required identity/geometry fields.
    """

    track_id: int
    global_id: str
    bbox: list[float]
    zone_id: str | None
    zone_type: str | None
    role: str
    confidence: float | None
    action: str | None = None
    zone_name: str | None = None


@dataclass
class FrameOutput:
    """One processed frame's structured output.

    time_seconds is the video-relative value computed upstream in
    run_live.py (frame_index / actual_video_fps) and threaded through
    process_frame() as of Task 1 -- it is passed through unchanged here,
    never re-derived from wall-clock time.
    """

    camera_id: str
    video_id: str
    frame_index: int
    time_seconds: float
    tracks: list[TrackOutput]
    table_states: dict[str, str]


def frame_output_to_json(output: FrameOutput) -> str:
    """Serialize a FrameOutput (including its nested TrackOutputs) to JSON.

    Export-readiness only -- no network/publishing code here.
    """
    return json.dumps(asdict(output))
