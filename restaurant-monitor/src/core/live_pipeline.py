from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from src.ai_models.detector import PersonDetector
from src.ai_models.tracker import RestaurantTracker
from src.zone_management.zone_manager import ZoneManager
# from src.kbs.action_kbs import ActionKBS, PersonFact
# from src.kbs.worker_kbs import WorkerKBS, WorkerFact
from src.kbs.table_kbs import TableKBS, TableFact
from src.kbs.reid_gallery import ReIDGallery, SessionGallery
from src.kbs.role_engine import (
    RoleEngine, ObservationFact, ScoreAccumulator, MergeRequest, RoleDecision,
)
from src.core.classifiers import PoseLSTM
from src.core.central_identity_store import CentralIdentityStore, CentralIdentityStoreError
from src.core.pipeline_output import FrameOutput, TrackOutput


class LivePipeline:
    ZONE_TYPE_MAP: dict[str, str] = {
        "table_area"           : "table",
        "table"                 : "table",
        "service_path"         : "walk",
        "hallway"              : "walk",
        "corridor"             : "walk",
        "open_area"            : "walk",
        "mixed_area"           : "walk",
        "entrance"             : "walk",
        "bathroom_entrance"    : "walk",
        "prayer_room_entrance" : "walk",
        "staff_area"           : "staff",
        "buffet"               : "staff",
        "cashier"              : "staff",
    }

    KEYPOINT_NAMES = [
        "nose", "left_eye", "right_eye", "left_ear", "right_ear",
        "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
        "left_wrist", "right_wrist", "left_hip", "right_hip",
        "left_knee", "right_knee", "left_ankle", "right_ankle"
    ]

    ROLE_COLORS = {
        "unknown":  (255, 255, 255),  # white
        "customer": (255, 100,   0),  # blue  (BGR)
        "worker":   (  0, 140, 255),  # orange (BGR)
    }

    def __init__(self, config_path: str = "config/config.json"):
        self.project_dir = Path.cwd()
        self.config_path = self.project_dir / config_path

        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        # -- Detector -------------------------------------------------------
        detector_config = self.config["detector"]
        self.detector = PersonDetector(
            model_name=detector_config.get("model"),
            conf_threshold=detector_config.get("conf_threshold"),
            imgsz=detector_config.get("imgsz"),
            min_box_width=detector_config.get("min_box_width"),
            min_box_height=detector_config.get("min_box_height"),
            device=detector_config.get("device"),
        )

        # -- Tracker --------------------------------------------------------
        tracker_config = self.config.get("tracker", {})
        self.tracker = RestaurantTracker(
            max_age=tracker_config.get("max_age"),
            n_init=tracker_config.get("n_init"),
            nms_max_overlap=tracker_config.get("nms_max_overlap"),
        )

        # -- Action model ---------------------------------------------------
        model_path = self.project_dir / "models/pose_lstm.pth"
        le_path    = self.project_dir / "models/label_encoder.pkl"

        self.action_model = PoseLSTM()
        self.action_model.load_state_dict(torch.load(model_path, map_location="cpu"))
        self.action_model.eval()

        with open(le_path, "rb") as f:
            self.label_encoder = pickle.load(f)

        # -- Pose buffers ---------------------------------------------------
        self._kp_buffer:    dict[int, list] = {}  # track_id -> [(frame_id, kp_dict)]
        self._action_labels: dict[int, str] = {}  # track_id -> latest label

        # -- Zone manager ---------------------------------------------------
        self.zone_manager = ZoneManager()

        # -- KBS engines (instantiated once, reset each frame) --------------
        # self.action_kbs   = ActionKBS()
        # self.worker_kbs   = WorkerKBS()
        # self.table_kbs    = TableKBS()
        self.reid_gallery = ReIDGallery()
        self.session_gallery = SessionGallery(ttl_seconds=1800)  # 30 mins TTL

        # -- Cross-process shared identity store (Task 8) --------------------
        # Optional "redis" config section, backward-compatible with every
        # existing config.json that predates this task (defaults below match
        # central_state.py's/run_all_cameras.py's existing localhost:6379/db0
        # convention). Constructing CentralIdentityStore never touches the
        # network (see its docstring), so an unreachable Redis server cannot
        # fail pipeline construction -- failures surface only at the point of
        # use in _run_kbs(), where they're caught and fall back to
        # self.session_gallery (tier 3).
        redis_config = self.config.get("redis", {})
        self.central_identity_store = CentralIdentityStore(
            host=redis_config.get("host", "localhost"),
            port=redis_config.get("port", 6379),
            db=redis_config.get("db", 0),
        )

        # KBS engines are re-instantiated each cycle in _run_kbs to avoid Experta lru_cache deadlock

        # -- Per-track state ------------------------------------------------
        self._person_roles:        dict[int, str]   = {}  # track_id -> role
        self._active_states:       dict[int, str]   = {}  # track_id -> active_state
        self._global_ids:          dict[int, str]   = {}  # track_id -> global_id
        self._action_frame_counts: dict[int, int]   = {}  # track_id -> consecutive frames
        self._last_action_zone:    dict[int, tuple] = {}  # track_id -> (action, zone_type)
        self._worker_states:       dict[str, str]   = {}  # global_id -> worker state

        # -- RoleEngine persistent state ------------------------------------
        self._role_accumulators: dict[str, dict]  = {}  # global_id -> accumulator dict

        # -- Table state ----------------------------------------------------
        self._table_customer_counts: dict[str, int]  = {}  # zone_id -> count
        self._table_was_served:      dict[str, bool] = {}  # zone_id -> served flag
        self._table_states:          dict[str, str]  = {}  # zone_id -> "free"|"occupied"|"dirty"

        # -- Seated-id hysteresis (mirrors ZoneManager's own occupy/vacate
        # timers, which were previously declared but never wired in) -------
        self._table_presence_cycles: dict[tuple[str, str], int] = {}  # (zone_id, gid) -> consecutive present cycles
        self._table_absence_cycles:  dict[tuple[str, str], int] = {}  # (zone_id, gid) -> consecutive absent cycles
        self._table_seated_ids:      dict[str, set[str]]        = {}  # zone_id -> confirmed seated global_ids

        # Occupy/vacate cycle counts depend on the ACTUAL per-camera cadence
        # _run_kbs runs at (run_live.py hardcodes pose_stride=1, so that's the
        # video's native fps, not config.process_fps) -- computed later via
        # set_frame_rate() once the capture's real fps is known, not here.
        self._occupy_cycles = 1
        self._vacate_cycles = 1
        self.SERVING_SUSTAINED_FRAMES = 6  # mirrors RoleEngine's W_SERVING_SUSTAINED bar (pfc > 6)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def process_frame(
        self,
        frame,
        camera_id,
        video_id,
        frame_index,
        time_seconds,
        pose_stride=None,
    ):
        detections, keypoints_list = self.detector.detect_with_pose(frame)
        tracked_objects = self.tracker.update(detections, frame=frame)
        tracked_objects = self.enrich_tracks_with_zones(
            camera_id,
            tracked_objects,
            detections,
            keypoints_list,
        )
        person_crops    = self.crop_persons(frame, tracked_objects)

        if pose_stride is None or frame_index % pose_stride == 0:
            self._extract_pose_from_detections(
                frame, video_id, frame_index,
                detections, keypoints_list, tracked_objects,
            )
            self._run_kbs(tracked_objects, person_crops)

        tables_current_state = self.get_tables_current_state(camera_id)
        frame_output = self._build_frame_output(
            camera_id, video_id, frame_index, time_seconds,
            tracked_objects, tables_current_state,
        )

        return {
            "camera_id":      camera_id,
            "video_id":       video_id,
            "frame_index":    frame_index,
            "time_seconds":   time_seconds,
            "detections":     detections,
            "tracked_objects": tracked_objects,
            "person_crops":   person_crops,
            "tables_current_state": tables_current_state,
            "frame_output":   frame_output,
        }

    def _build_track_output(self, obj: dict[str, Any]) -> TrackOutput:
        """Map one post-enrichment tracked_objects[i] dict (see
        enrich_tracks_with_zones()) plus this instance's per-track Re-ID/role
        state into Task 4's TrackOutput contract. Mirrors the external
        mapping already established and verified as correct by Task 6's
        real-pipeline smoke test (docs/TASK6_REPORT.md §10), reused here
        rather than re-derived from scratch, with one refinement: zone_id is
        None when the zone was unmatched (zone.get("matched") is falsy) --
        "unknown" there is ZoneManager's not-found sentinel, not a real zone
        identity (see zone_manager.py's _unknown_zone()) -- consistent with
        this repo's own already-documented design intent for this field
        (docs/TASK4_REPORT.md) and with what EventEngine.ZONE_OCCUPANCY_CHANGE
        (src/events/event_engine.py) already assumes: it only counts tracks
        with a non-None zone_id as occupying a real zone.
        """
        tid = obj.get("track_id")
        zone = obj.get("zone") or {}

        return TrackOutput(
            track_id=tid,
            global_id=self._global_ids.get(tid, f"local_{tid}"),
            bbox=[float(v) for v in obj.get("box", [0, 0, 0, 0])],
            zone_id=zone.get("zone_id") if zone.get("matched") else None,
            zone_type=obj.get("zone_type_normalized"),
            role=self._person_roles.get(tid, "unknown"),
            confidence=obj.get("conf"),
            action=self._action_labels.get(tid),
            zone_name=obj.get("zone_name"),
        )

    def _build_frame_output(
        self,
        camera_id: str,
        video_id: str,
        frame_index: int,
        time_seconds: float,
        tracked_objects: list[dict[str, Any]],
        tables_current_state: dict[str, str],
    ) -> FrameOutput:
        """Build this frame's FrameOutput. time_seconds is passed straight
        through, unchanged -- never re-derived from wall-clock time (Task 1's
        fix, still honored here)."""
        return FrameOutput(
            camera_id=camera_id,
            video_id=video_id,
            frame_index=frame_index,
            time_seconds=time_seconds,
            tracks=[self._build_track_output(obj) for obj in tracked_objects],
            table_states=tables_current_state,
        )

    def enrich_tracks_with_zones(
        self,
        camera_id: str,
        tracked_objects: list[dict[str, Any]],
        detections: list[dict[str, Any]] | None = None,
        keypoints_list: list[np.ndarray] | None = None,
    ) -> list[dict[str, Any]]:
        enriched_tracks: list[dict[str, Any]] = []

        pose_points_by_track_id: dict[int, list[int]] = {}
        keypoints_by_track_id = {}
        if detections and keypoints_list:
            for det, kpts in zip(detections, keypoints_list):
                if kpts is None:
                    continue

                track_id = self._match_to_track(det.get("box", [0, 0, 0, 0]), tracked_objects)
                if track_id is None:
                    continue

                keypoints_by_track_id[track_id] = kpts

        for obj in tracked_objects:
            track_id = obj.get("track_id")
            box = obj.get("box", [0, 0, 0, 0])
            kpts = keypoints_by_track_id.get(track_id) if keypoints_list else None

            zone = self.zone_manager.get_bbox_zone(
                camera_id=camera_id,
                box=box,
                keypoints=kpts,
            )
            if track_id in (1, 2, 6):  # adjust to tracks you know are sitting
                hand_conf = None
                if kpts is not None:
                    hand_conf = [(int(i), round(float(kpts[i][2]), 3)) for i in (9, 10) if i < len(kpts)]
                print(f"[DEBUG zone] tid={track_id} kpts_present={kpts is not None} hand_conf(idx,conf)={hand_conf} zone_type={zone.get('zone_type')} method={zone.get('method')} reason={zone.get('reason')}")
            enriched_obj = dict(obj)
            enriched_obj["zone"] = zone
            raw_zone_type = zone.get("zone_type", "unknown") if zone else "unknown"
            enriched_obj["zone_type_normalized"] = self.ZONE_TYPE_MAP.get(raw_zone_type, "unknown")
            enriched_obj["zone_name"] = zone.get("zone_name", "Unknown") if zone else "Unknown"
            enriched_obj["zone_specificity"] = zone.get("zone_specificity") if zone else None
            enriched_obj["zone_point"] = kpts
            enriched_obj["active_state"] = self._active_states.get(track_id, "active")
            enriched_tracks.append(enriched_obj)

        return enriched_tracks

    def crop_persons(
        self,
        frame,
        tracked_objects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        crops: list[dict[str, Any]] = []
        height, width = frame.shape[:2]

        for obj in tracked_objects:
            box = obj.get("box", [0, 0, 0, 0])
            x1, y1, x2, y2 = [int(v) for v in box]

            x1 = max(0, min(width,  x1))
            x2 = max(0, min(width,  x2))
            y1 = max(0, min(height, y1))
            y2 = max(0, min(height, y2))

            if x1 >= x2 or y1 >= y2:
                continue

            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            crops.append({
                "track_id": obj.get("track_id"),
                "zone":     obj.get("zone", {}),
                "box":      [x1, y1, x2, y2],
                "crop":     crop.copy(),
            })

        return crops

    def draw_zones(self, frame, camera_id: str) -> None:
        camera_zones = (
            self.zone_manager.zones_config
            .get(camera_id, {})
            .get("zones", [])
        )

        for zone in camera_zones:
            points = zone.get("points", [])
            if len(points) < 3:
                continue

            polygon = np.array(points, dtype=np.int32)
            cv2.polylines(frame, [polygon], isClosed=True, color=(0, 255, 0), thickness=2)

    def draw_tracks(self, frame, tracked_objects: list[dict[str, Any]]) -> None:
        for obj in tracked_objects:
            box = obj.get("box", [0, 0, 0, 0])
            x1, y1, x2, y2 = map(int, box)
            zone_type = obj.get("zone_type_normalized", "unknown")
            zone_name = obj.get("zone_name", "Unknown")
            track_id  = obj.get("track_id", "?")
            action    = self._action_labels.get(track_id, "...")
            role      = self._person_roles.get(track_id, "unknown")
            gid       = self._global_ids.get(track_id)
            active_state = self._active_states.get(track_id, "active")
            color     = self.ROLE_COLORS.get(role, (255, 255, 255))

            box_color = color
            if role == "worker" and active_state == "inactive":
                box_color = (128, 128, 128)  # grey out inactive workers

            cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)

            label = f"{action} | {role} | {zone_type}"
            if role == "worker":
                label += f" | {active_state}"
            cv2.putText(
                frame, label,
                (x1, max(35, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2,
            )

            cv2.putText(
                frame, f"id:{track_id}",
                (x1 + 4, y1 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2,
            )

            if gid:
                gid_short = gid.replace("worker_", "")
                cv2.putText(
                    frame, f"gid:{gid_short}",
                    (x1 + 4, y1 + 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 105, 255), 2,
                )

    def get_tables_current_state(self, camera_id: str) -> dict[str, str]:
        """Return {zone_id: state} for all tables tracked by TableKBS so far.

        camera_id is accepted for API symmetry with run_live.py's draw call;
        zone_ids are already unique across the pipeline's single-process
        state so no per-camera filtering is needed here.
        """
        return dict(self._table_states)
   
    def set_frame_rate(self, fps: float) -> None:
        """Recompute occupy/vacate hysteresis windows for this camera's actual
        per-frame cadence. Must be called once after the capture's real fps
        is known -- e.g. from run_live.py right after cv2.VideoCapture opens --
        since _run_kbs runs every raw frame (pose_stride is hardcoded to 1),
        not at config.pipeline.process_fps.
        """
        if not fps or fps <= 0:
            return
        self._occupy_cycles = max(1, round(self.zone_manager.TIME_TO_OCCUPY * fps))
        self._vacate_cycles = max(1, round(self.zone_manager.TIME_TO_VACATE * fps))
      
    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _extract_pose_from_detections(
        self,
        frame,
        video_id,
        frame_index,
        detections,
        keypoints_list,
        tracked_objects,
    ) -> None:
        h, w = frame.shape[:2]

        for det, kpts in zip(detections, keypoints_list):
            if kpts is None:
                continue

            track_id = self._match_to_track(det["box"], tracked_objects)
            if track_id is None:
                continue

            kp_dict = {
                self.KEYPOINT_NAMES[j]: {
                    "x":    round(float(kpts[j][0] / w), 4),
                    "y":    round(float(kpts[j][1] / h), 4),
                    "conf": round(float(kpts[j][2]),     4),
                }
                for j in range(17)
            }

            self._predict_action(track_id, frame_index, kp_dict)

    def _estimate_zone_point(
        self,
        box: list[int | float],
        kpts: np.ndarray | None,
        conf_threshold: float = 0.25,
    ) -> list[int]:
        x1, y1, x2, y2 = [int(v) for v in box]

        def visible_point(index: int) -> tuple[float, float] | None:
            if kpts is None or index >= len(kpts):
                return None
            x, y, conf = kpts[index]
            if float(conf) < conf_threshold:
                return None
            return float(x), float(y)

        def average_visible_points(indices: tuple[int, ...]) -> list[int] | None:
            points = [visible_point(index) for index in indices]
            visible_points = [point for point in points if point is not None]
            if visible_points:
                avg_x = sum(point[0] for point in visible_points) / len(visible_points)
                avg_y = sum(point[1] for point in visible_points) / len(visible_points)
                return [int(round(avg_x)), int(round(avg_y))]
            return None

        # Explicit priority order:
        # 1) ankles visible
        # 2) hips visible
        # 3) shoulders visible
        # 4) upper body only -> use the box center
        ankles_point = average_visible_points((15, 16))
        if ankles_point is not None:
            return ankles_point

        hips_point = average_visible_points((11, 12))
        if hips_point is not None:
            return hips_point

        shoulders_point = average_visible_points((5, 6))
        if shoulders_point is not None:
            return shoulders_point

        return [int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))]

    def _match_to_track(self, pose_box, tracked_objects) -> int | None:
        best_iou, best_id = 0.0, None
        px1, py1, px2, py2 = pose_box

        for obj in tracked_objects:
            tx1, ty1, tx2, ty2 = obj["box"]
            ix1 = max(px1, tx1)
            iy1 = max(py1, ty1)
            ix2 = min(px2, tx2)
            iy2 = min(py2, ty2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if inter == 0:
                continue
            union = (px2 - px1) * (py2 - py1) + (tx2 - tx1) * (ty2 - ty1) - inter
            iou = inter / union if union > 0 else 0
            if iou > best_iou:
                best_iou, best_id = iou, obj["track_id"]

        return best_id if best_iou > 0.4 else None

    def _predict_action(self, track_id: int, frame_id: int, kp_dict: dict) -> None:
        buf = self._kp_buffer.setdefault(track_id, [])
        buf.append((frame_id, kp_dict))

        # Drop frames with large gaps
        while len(buf) > 1 and buf[-1][0] - buf[-2][0] >= 200:
            buf.pop(0)

        if len(buf) > 3:
            buf.pop(0)

        if len(buf) < 3:
            return

        def normalize_kp(kp):
            lh = kp.get("left_hip",       {"x": 0, "y": 0, "conf": 0})
            rh = kp.get("right_hip",      {"x": 0, "y": 0, "conf": 0})
            ls = kp.get("left_shoulder",  {"x": 0, "y": 0, "conf": 0})
            rs = kp.get("right_shoulder", {"x": 0, "y": 0, "conf": 0})
            cx = (lh["x"] + rh["x"]) / 2
            cy = (lh["y"] + rh["y"]) / 2
            shoulder_y = (ls["y"] + rs["y"]) / 2
            torso_h    = abs(cy - shoulder_y)
            scale      = torso_h if torso_h > 1e-5 else 1.0
            return {
                name: {
                    "x":    (pt["x"] - cx) / scale,
                    "y":    (pt["y"] - cy) / scale,
                    "conf": pt["conf"],
                }
                for name, pt in kp.items()
            }

        def flatten(kp):
            kp = normalize_kp(kp)
            return [v for pt in kp.values() for v in (pt["x"], pt["y"], pt["conf"])]

        x = np.array([flatten(buf[i][1]) for i in range(3)], dtype=np.float32)
        x = torch.tensor(x).unsqueeze(0)  # (1, 3, 51)

        with torch.no_grad():
            pred = self.action_model(x).argmax(1).item()

        predicted_label = self.label_encoder.classes_[pred]
        self._action_labels[track_id] = predicted_label

    def _run_kbs(self, tracked_objects: list[dict], person_crops: list[dict]) -> None:
        crop_by_id = {
            c["track_id"]: c["crop"]
            for c in person_crops
            if c.get("crop") is not None
        }

        # -- Update action_frame_counts (reset if action or zone changed) ---
        for obj in tracked_objects:
            tid       = obj["track_id"]
            action    = self._action_labels.get(tid, "unknown")
            zone_type = obj.get("zone_type_normalized", "unknown")
            key       = (action, zone_type)

            if self._last_action_zone.get(tid) != key:
                self._action_frame_counts[tid] = 0
                self._last_action_zone[tid]    = key
            else:
                self._action_frame_counts[tid] = self._action_frame_counts.get(tid, 0) + 1

        # -- Re-ID pre-check: match unknowns against existing gallery --------
        self.session_gallery.purge_old()

        for obj in tracked_objects:
            tid  = obj["track_id"]
            
            # If we already linked this track ID in a previous frame to a session ID, just touch it
            if tid in self._global_ids:
                gid = self._global_ids[tid]
                if gid.startswith("session_"):
                    self.session_gallery.touch(gid)
                continue

            crop = crop_by_id.get(tid)
            if crop is None:
                continue
                
            embedding = self.reid_gallery._extract(crop)

            # 1. Permanent Worker Gallery (in-process, unchanged) ------------
            matched_worker = self.reid_gallery._match(embedding)
            if matched_worker is not None:
                self._person_roles[tid] = "worker"
                self._global_ids[tid]   = matched_worker
                print(f"[ReID] track {tid} matched permanent worker → {matched_worker}")
                continue

            # 2. Central cross-process identity store (primary path for -----
            #    anyone not matched locally as a permanent worker). Any
            #    failure here (Redis unreachable, lock timeout, ...) is
            #    caught and logged -- it must never propagate out of
            #    _run_kbs() -- and falls through to tier 3 below.
            central_gid = None
            try:
                central_gid = self.central_identity_store.resolve_identity(embedding)
            except CentralIdentityStoreError as exc:
                print(
                    f"[ReID] central identity store unavailable ({exc}); "
                    f"falling back to local session gallery for track {tid}"
                )

            if central_gid is not None:
                self._global_ids[tid] = central_gid
                print(f"[ReID] track {tid} resolved via central identity store → {central_gid}")
                continue

            # 3. Local SessionGallery -- only reached when Redis is ----------
            #    unavailable (tier 2 above raised/returned nothing).
            matched_session = self.session_gallery.match(embedding)
            if matched_session is not None:
                self._global_ids[tid] = matched_session
                self.session_gallery.update_seen(matched_session, embedding)
                print(f"[ReID] track {tid} matched active session (fallback) → {matched_session}")
                continue

            new_session = self.session_gallery.register(embedding)
            self._global_ids[tid] = new_session
            print(f"[ReID] track {tid} registered as new session (fallback) → {new_session}")

        # -- RoleEngine: classify all persons via point-based evidence ------
        engine = RoleEngine()
        engine.reset()

        # Re-inject persistent accumulators from previous cycle
        for gid, acc in self._role_accumulators.items():
            engine.declare(ScoreAccumulator(
                global_id=gid,
                worker_score=acc["worker_score"],
                customer_score=acc["customer_score"],
                confirmed_role=acc["confirmed_role"],
                cycle_count=acc["cycle_count"],
                visited_tables=acc.get("visited_tables", ()),
                visited_staff=acc.get("visited_staff", False),
            ))

        # Declare one ObservationFact per tracked person
        for obj in tracked_objects:
            tid = obj["track_id"]
            gid = self._global_ids.get(tid, f"local_{tid}")
            
            zone = obj.get("zone") or {}
            zone_id = zone.get("zone_id", "unknown")

            engine.declare(ObservationFact(
                track_id         = tid,
                global_id        = gid,
                action           = self._action_labels.get(tid, "unknown"),
                zone_type        = obj.get("zone_type_normalized", "unknown"),
                zone_id          = zone_id,
                pose_frame_count = self._action_frame_counts.get(tid, 0),
                appearance_count = 0,
                previous_role    = self._person_roles.get(tid, "unknown"),
            ))

        engine.run()

        # Read RoleEngine decisions
        for tid, decision in engine.get_decisions().items():
            role = decision["role"]
            active_state = decision.get("active_state", "active")
            if role != "unknown":
                self._person_roles[tid] = role
            self._active_states[tid] = active_state

            # Log scores for debugging
            print(
                f"  [RoleEngine] track {tid}: "
                f"role={decision['role']}  "
                f"active_state={active_state}  "
                f"conf={decision['confidence']:.2f}  "
                f"ws={decision['worker_score']:.1f}  "
                f"cs={decision['customer_score']:.1f}"
            )

            # Re-ID Promotion: register newly confirmed workers in permanent gallery
            # (a session_-prefixed id came from tier 3's local fallback, a
            # central_-prefixed one from tier 2's shared store -- either can
            # be promoted).
            gid = self._global_ids.get(tid)
            if role == "worker" and gid and (gid.startswith("session_") or gid.startswith("central_")):
                crop = crop_by_id.get(tid)
                if crop is not None:
                    embedding = self.reid_gallery._extract(crop)
                    new_worker_gid = self.reid_gallery._register(embedding)

                    # Rename in our track mappings
                    self._global_ids[tid] = new_worker_gid

                    # Rename in the accumulators dict so RoleEngine continues next cycle
                    if gid in self._role_accumulators:
                        acc = self._role_accumulators.pop(gid)
                        acc["global_id"] = new_worker_gid
                        self._role_accumulators[new_worker_gid] = acc

                    # Remove from whichever gallery/store issued the old id
                    if gid.startswith("session_"):
                        self.session_gallery.remove(gid)
                    else:
                        try:
                            self.central_identity_store.remove(gid)
                        except CentralIdentityStoreError as exc:
                            print(
                                f"[ReID][WARNING] failed to remove {gid} from central "
                                f"identity store during promotion: {exc}"
                            )
                    print(f"[ReID] Promoted {gid} to permanent worker → {new_worker_gid}")

        # Persist accumulators for next cycle
        self._role_accumulators = engine.get_accumulators()

        for obj in tracked_objects:
            tid = obj["track_id"]
            obj["active_state"] = self._active_states.get(tid, "active")

        # -- Update table seated_ids (id-set with occupy/vacate hysteresis) --
        # Only zone_specificity == "specific" observations count -- a
        # table_area-only match is zone-ambiguous and must not move any
        # single table's occupancy.
        raw_present: dict[str, set[str]] = {}
        for obj in tracked_objects:
            tid  = obj["track_id"]
            role = self._person_roles.get(tid, "unknown")
            if role != "customer":
                continue
            zone = obj.get("zone") or {}
            if zone.get("zone_type") != "table" or obj.get("zone_specificity") != "specific":
                continue
            zone_id = zone.get("zone_id")
            gid = self._global_ids.get(tid, f"local_{tid}")
            if zone_id:
                raw_present.setdefault(zone_id, set()).add(gid)

        all_pair_zone_ids = (
            set(raw_present)
            | {zid for (zid, _gid) in self._table_presence_cycles}
            | {zid for (zid, _gid) in self._table_absence_cycles}
            | set(self._table_seated_ids)
        )

        for zone_id in all_pair_zone_ids:
            present_gids = raw_present.get(zone_id, set())
            seated = self._table_seated_ids.setdefault(zone_id, set())

            # Confirm-to-occupy: consecutive presence must cross the threshold
            for gid in present_gids:
                key = (zone_id, gid)
                self._table_presence_cycles[key] = self._table_presence_cycles.get(key, 0) + 1
                self._table_absence_cycles.pop(key, None)
                if gid not in seated and self._table_presence_cycles[key] >= self._occupy_cycles:
                    seated.add(gid)

            # Confirm-to-vacate: consecutive absence must cross the threshold
            for gid in list(seated):
                if gid in present_gids:
                    continue
                key = (zone_id, gid)
                self._table_absence_cycles[key] = self._table_absence_cycles.get(key, 0) + 1
                self._table_presence_cycles.pop(key, None)
                if self._table_absence_cycles[key] >= self._vacate_cycles:
                    seated.discard(gid)
                    self._table_absence_cycles.pop(key, None)
         
        zone_customer_counts = {zid: len(gids) for zid, gids in self._table_seated_ids.items()}

        # -- Check for serving events: "serve" (occupied) vs "clean" (dirty) -
        # No dedicated cleaning label exists, so a sustained serving action on
        # an empty, dirty table IS treated as the cleaning event. Both
        # branches require zone_specificity == "specific" for the same reason
        # as the occupancy count above.
        for obj in tracked_objects:
            tid     = obj["track_id"]
            role    = self._person_roles.get(tid, "unknown")
            action  = self._action_labels.get(tid, "unknown")
            zone    = obj.get("zone") or {}
            zone_id = zone.get("zone_id")
            pfc     = self._action_frame_counts.get(tid, 0)

            if not (
                role == "worker"
                and action == "standing"
                and zone.get("zone_type") == "table"
                and obj.get("zone_specificity") == "specific"
                and zone_id
                and pfc > self.SERVING_SUSTAINED_FRAMES
            ):
                continue
            if zone_customer_counts.get(zone_id, 0) > 0:
                self._table_was_served[zone_id] = True
            elif self._table_states.get(zone_id) == "dirty":
                self._table_was_served[zone_id] = False
       
        # -- TableKBS ---------------------------------------------------------
        all_zone_ids = (
            set(zone_customer_counts)
            | set(self._table_customer_counts)
            | set(self._table_was_served)
        )

        self.table_kbs = TableKBS()
        self.table_kbs.reset()
        for zone_id in all_zone_ids:
            self.table_kbs.declare(TableFact(
                zone_id        = zone_id,
                customer_count = zone_customer_counts.get(zone_id, 0),
                was_served     = self._table_was_served.get(zone_id, False),
            ))
        self.table_kbs.run()
        self._table_customer_counts = zone_customer_counts

        # -- Read TableKBS results, clear was_served once a table goes free ---
        for fact in self.table_kbs.facts.values():
            if not isinstance(fact, TableFact):
                continue
            self._table_states[fact["zone_id"]] = fact["state"]
            if fact["state"] == "free":
                self._table_was_served[fact["zone_id"]] = False
  
                
        # # -- WorkerKBS ------------------------------------------------------
        # self.worker_kbs = WorkerKBS()
        # self.worker_kbs.reset()
        # for obj in tracked_objects:
        #     tid  = obj["track_id"]
        #     role = self._person_roles.get(tid, "unknown")
        #     if role != "worker":
        #         continue
        #     global_id = self._global_ids.get(tid, f"local_{tid}")
        #     self.worker_kbs.declare(WorkerFact(
        #         global_id        = global_id,
        #         action           = self._action_labels.get(tid, "unknown"),
        #         zone_type        = obj.get("zone_type_normalized", "unknown"),
        #         camera_type      = "customer_facing",
        #         pose_frame_count = self._action_frame_counts.get(tid, 0),
        #         re_id_ready      = False,
        #     ))
        # self.worker_kbs.run()

        # # -- Read WorkerKBS results -----------------------------------------
        # for fact in self.worker_kbs.facts.values():
        #     if not isinstance(fact, WorkerFact):
        #         continue
        #     self._worker_states[fact["global_id"]] = fact["state"]