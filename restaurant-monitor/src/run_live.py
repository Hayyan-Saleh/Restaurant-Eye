from __future__ import annotations
import collections.abc
collections.Mapping = collections.abc.Mapping
import argparse
import json
import logging
import signal
import sys
from pathlib import Path

import cv2
import numpy as np  # أضفنا numpy لرسم المضلعات
import redis

sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.core.live_pipeline import LivePipeline
from src.events.dispatch_bridge import EventDispatchBridge
from src.events.event_dispatcher import CAMERA_LIVE_CHANNEL

# Without this, EventDispatchBridge's INFO/WARNING/ERROR logs (dispatch
# failures, dropped-frame backpressure warnings, start/stop lifecycle) are
# silently swallowed by the root logger's default WARNING+-to-nowhere
# behavior -- this process has no other logging configuration.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,  # heavy ML imports above (ultralytics/absl/torch) already
    # attach their own root handlers on import, which makes a plain
    # basicConfig() a silent no-op -- force=True (3.8+) resets them so
    # EventDispatchBridge's own INFO/WARNING/ERROR logs are never swallowed.
)


class _GracefulShutdown(Exception):
    """Raised from the SIGTERM handler so the main loop's normal try/finally
    (which already flushes the EventDispatchBridge queue on any exception,
    same as a KeyboardInterrupt/SIGINT) runs before the process exits.

    Note: on Windows, sending SIGTERM (e.g. via Popen.terminate()/taskkill)
    invokes TerminateProcess() directly at the OS level -- it never reaches
    this handler, or any other Python code, at all. This handler only takes
    effect on platforms where SIGTERM is a real, catchable signal (POSIX).
    See docs/AI_BACKEND_WIRING.md for the verified behavior on this
    Windows/win32 host.
    """


def _install_signal_handlers() -> None:
    def _handle_sigterm(signum, frame):
        raise _GracefulShutdown()

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (AttributeError, ValueError):
        # SIGTERM isn't independently catchable on every platform/thread
        # (e.g. delivered as an unconditional TerminateProcess on Windows).
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the live in-memory pipeline")
    parser.add_argument("--source", default="0", help="Camera index or path to a video file")
    parser.add_argument("--camera-id", default="live_camera", help="Camera label used in analytics")
    parser.add_argument("--video-id", default="live_stream", help="Stream label used in analytics")
    parser.add_argument("--first-video", action="store_true", help="Use the first recorded video from videos_index.json")
    parser.add_argument("--videos-index", default="data/metadata/videos/videos_index.json", help="Path to videos_index.json")
    parser.add_argument("--config", default="config/config.json", help="Project config path")
    parser.add_argument("--max-frames", type=int, default=100, help="Optional frame limit for testing")
    parser.add_argument("--save-crops-dir", default="data/debug/crops", help="Optional directory to save person crops for RE-ID prep")
    parser.add_argument("--show", action="store_true", help="Show annotated frames in a window")
    parser.add_argument("--start-sec", type=float, default=None, help="Start processing from a specific time in seconds (e.g. 532)")
    parser.add_argument("--save-debug", action="store_true", help="Save crops to disk")
    parser.add_argument(
        "--no-event-dispatch",
        action="store_true",
        help="Disable the EventEngine/EventDispatcher background bridge (no Postgres/Redis/WebSocket writes)",
    )
    return parser.parse_args()


def publish_stream_lifecycle_event(
    client: "redis.Redis", camera_id: str, event_type: str
) -> None:
    """Publishes a CAMERA_STREAM_STARTED/CAMERA_STREAM_STOPPED envelope to
    CAMERA_LIVE_CHANNEL via the existing synchronous frame_publish_client --
    same channel, same envelope shape (event_type/camera_id/time_seconds/
    track_id/global_id/zone_id/details) as every event EventDispatcher
    publishes there, so vision_subscriber.py's generic, event_type-agnostic
    re-broadcast (backend/app/services/vision_subscriber.py) needs no
    changes to relay these two lifecycle events to dashboard clients same
    as any other. Never allowed to crash the pipeline on a Redis hiccup --
    same resilience posture as the frame-publish block below.
    """
    try:
        client.publish(
            CAMERA_LIVE_CHANNEL,
            json.dumps(
                {
                    "event_type": event_type,
                    "camera_id": camera_id,
                    "time_seconds": 0.0,
                    "track_id": None,
                    "global_id": None,
                    "zone_id": None,
                    "details": {},
                }
            ),
        )
    except Exception as e:
        print(f"[WARNING] failed to publish {event_type} to Redis: {e}")


def open_capture(source: str) -> cv2.VideoCapture:
    if source.isdigit():
        return cv2.VideoCapture(int(source))
    return cv2.VideoCapture(source)


def resolve_process_fps(config: dict) -> float:
    """Read and validate pipeline.process_fps from the already-loaded project config.

    Raises ValueError instead of silently defaulting so a missing/invalid config
    value fails loudly at startup rather than masking a config mistake.
    """
    process_fps = config.get("pipeline", {}).get("process_fps")
    if not isinstance(process_fps, (int, float)) or isinstance(process_fps, bool) or process_fps <= 0:
        raise ValueError(
            "config.json is missing a valid 'pipeline.process_fps' value "
            f"(got {process_fps!r}). Set 'pipeline.process_fps' to a positive number "
            "of frames-per-second in config/config.json."
        )
    return float(process_fps)


def compute_pose_stride(actual_fps: float | None, process_fps: float) -> int:
    """Compute how many frames to advance between pose/action inference passes.

    Targets `process_fps` regardless of the source video's native FPS (different
    cameras in videos_index.json have different native FPS, e.g. 15.001 vs 7.0,
    so a fixed stride would not hit the same target rate across cameras). Falls
    back to a stride of 1 (inference on every frame) if the source FPS is
    unavailable/non-positive — the same fallback condition already used elsewhere
    in this file for --start-sec frame seeking — and prints that fallback so it's
    never silent.
    """
    if actual_fps and actual_fps > 0:
        return max(1, round(actual_fps / process_fps))
    print(
        "[WARNING] Could not read a valid video FPS from the capture source; "
        "falling back to pose_stride=1 (pose/action inference on every frame)."
    )
    return 1


def load_first_video_path(videos_index_path: str) -> tuple[str, str, str]:
    index_path = Path(videos_index_path)
    if not index_path.exists():
        raise FileNotFoundError(f"Video index not found: {index_path}")

    with open(index_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not records:
        raise ValueError(f"No video records found in: {index_path}")

    first_record = records[1]
    return first_record["path"], first_record["camera_id"], first_record["video_id"]


# --- دالة الرسم المضافة لعرض حالة الطاولة حياً على الفريم ---
def draw_custom_table_states(frame: np.ndarray, camera_id: str, pipeline: LivePipeline) -> None:
    """تستعلم من الـ ZoneManager داخل البايبلاين لترسم حدود الطاولة بناء على حالتها الزمنية المستقرة."""
    if pipeline is None:
        return

    # جلب ملخص الحالات الحالي من طبقة الـ pipeline نفسها حتى تشمل dirty/occupied/empty
    tables_status = pipeline.get_tables_current_state(camera_id)
    zone_mgr = getattr(pipeline, "zone_manager", None)
    if zone_mgr is None:
        return

    camera_zones = zone_mgr.zones_config.get(camera_id, {}).get("zones", [])

    for zone in camera_zones:
        if zone.get("zone_type") in ("table", "table_area"):
            table_id = zone.get("zone_id")
            status = tables_status.get(table_id, "empty")

            # تحديد اللون بناءً على حالة الذاكرة المؤقتة
            if status == "occupied":
                color = (0, 0, 255)  # BGR أحمر
                text = f"{table_id}: Occupied"
            elif status == "dirty":
                color = (0, 165, 255)  # BGR برتقالي
                text = f"{table_id}: Dirty"
            else:
                color = (0, 255, 0)  # BGR أخضر
                text = f"{table_id}: Empty"

            points = zone.get("points", [])
            if len(points) >= 3:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                # رسم خطوط الطاولة
                cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)

                # كتابة النص التوضيحي للحالة فوق أول نقطة إحداثية للطاولة
                text_position = (int(points[0][0]), int(points[0][1] - 10))
                cv2.putText(
                    frame,
                    text,
                    text_position,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2,
                )


def main() -> None:
    _install_signal_handlers()

    args = parse_args()
    pipeline = LivePipeline(config_path=args.config)

    redis_config = pipeline.config.get("redis", {})

    bridge: EventDispatchBridge | None = None
    if not args.no_event_dispatch:
        bridge = EventDispatchBridge(
            config=pipeline.config,
            redis_host=redis_config.get("host", "localhost"),
            redis_port=redis_config.get("port", 6379),
            redis_db=redis_config.get("db", 0),
        )
        bridge.start()

    # Separate, plain-synchronous Redis client for publishing the annotated
    # live-detection frame (see the encode+publish block inside the frame
    # loop below) -- deliberately NOT the same client/machinery as
    # EventDispatchBridge:
    # that bridge exists because EventDispatcher is async and needs a
    # background event loop to call into from this synchronous loop. This
    # is just one SET call per frame -- a plain synchronous redis-py client
    # is the right-sized tool, no bridge/thread needed. socket_timeout
    # bounds how long a hung Redis connection could ever block this
    # process's main frame loop.
    frame_publish_client = redis.Redis(
        host=redis_config.get("host", "localhost"),
        port=redis_config.get("port", 6379),
        db=redis_config.get("db", 0),
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )

    process_fps = resolve_process_fps(pipeline.config)

    source = args.source
    camera_id = args.camera_id
    video_id = args.video_id

    if args.first_video:
        source, camera_id, video_id = load_first_video_path(args.videos_index)

    # Both frame_publish_client and bridge (if enabled) are constructed and
    # ready by this point, and camera_id is now fully resolved (including
    # the --first-video override above) -- published before the frame loop
    # begins so a WebSocket client always sees this before any detection
    # event for this camera_id.
    publish_stream_lifecycle_event(frame_publish_client, camera_id, "CAMERA_STREAM_STARTED")

    capture = open_capture(source)

    if not capture.isOpened():
        raise ValueError(f"Cannot open video source: {source}")

    fps = capture.get(cv2.CAP_PROP_FPS)
    pipeline.set_frame_rate(fps)

    # يتم اشتقاق pose_stride من الـ fps الفعلي للفيديو ومعدل المعالجة المطلوب (process_fps)
    # في config.json، بدل قيمة ثابتة، حتى نستهدف نفس معدل المعالجة بغض النظر عن fps الكاميرا الأصلي
    pose_stride = compute_pose_stride(fps, process_fps)

    # في العرض المباشر نحدّث الـ pose/action كل فريم لتسريع التعرف على العامل والأشخاص على الطاولات
    pose_stride = 1

    frame_index = 0
    if args.start_sec is not None and fps and fps > 0:
        frame_index = int(args.start_sec * fps)
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        print(f"Seeked to {args.start_sec}s (Frame: {frame_index})")

    crop_dir = Path(args.save_crops_dir) if args.save_debug else None
    if crop_dir is not None:
        crop_dir.mkdir(parents=True, exist_ok=True)

    frames_processed = 0
    try:
        while True:
            if args.max_frames is not None and frames_processed >= args.max_frames:
                break

            ret, frame = capture.read()
            if not ret:
                break

            time_seconds = frame_index / fps if fps and fps > 0 else None

            event = pipeline.process_frame(
                frame=frame,
                camera_id=camera_id,
                video_id=video_id,
                pose_stride=pose_stride,
                frame_index=frame_index,
                time_seconds=time_seconds,
            )

            if bridge is not None:
                bridge.submit(event["frame_output"])

            try:
                # 1. رسم المكونات الافتراضية للـ pipeline
                pipeline.draw_zones(frame, camera_id)
                pipeline.draw_tracks(frame, event["tracked_objects"])

                # 2. حقن التغيير هنا: تشغيل دالة التلوين المعتمدة على الذاكرة الزمنية المستقرة لحالة الطاولة
                draw_custom_table_states(frame, camera_id, pipeline)

            except Exception as e:
                import traceback
                print(f"[ERROR] draw crashed on frame {frame_index}: {e}")
                traceback.print_exc()

            # Publish the fully-annotated live-detection frame for
            # backend/app/api/v1/endpoints/video.py's streaming endpoint to
            # poll. Runs AFTER the draw block above, so `frame` here already
            # has zone outlines (draw_zones), track boxes (draw_tracks), and
            # table-state coloring (draw_custom_table_states) drawn into it
            # in place -- the exact same frame the --show window displays.
            # No .copy() or second drawing pass needed: cv2.rectangle/
            # polylines/putText already mutated `frame` directly, so this is
            # just an encode + publish of what already exists. Published
            # exactly once per processed frame with no artificial fps
            # throttling (the pipeline's own real, CPU-bound inference speed
            # -- see docs/AI_BACKEND_WIRING.md §9 -- is the honest frame
            # rate). A 5s TTL means the key self-expires if this process
            # stops or crashes, with no separate "pipeline died" signal
            # needed. Never allowed to crash the pipeline loop on a Redis
            # hiccup -- same resilience posture as every other Redis write
            # in this codebase (CentralIdentityStore, EventDispatchBridge).
            try:
                ok, jpeg = cv2.imencode(".jpg", frame)
                if ok:
                    frame_publish_client.set(
                        f"camera:{camera_id}:latest_frame", jpeg.tobytes(), ex=5
                    )
            except Exception as e:
                print(f"[WARNING] failed to publish annotated frame to Redis (frame {frame_index}): {e}")

            if crop_dir is not None:
                for crop_item in event["person_crops"]:
                    track_id = crop_item.get("track_id", "unknown")
                    crop_path = crop_dir / f"{camera_id}_{video_id}_frame_{frame_index:06d}_track_{track_id}.jpg"
                    cv2.imwrite(str(crop_path), crop_item["crop"])

            if args.show:
                cv2.namedWindow("Restaurant Live Pipeline", cv2.WINDOW_NORMAL)
                cv2.imshow("Restaurant Live Pipeline", frame)
                key = cv2.waitKey(1) & 0xFF
                if key != 255:
                    print(f"[DEBUG] key pressed: {key} (chr: {chr(key) if key < 128 else '?'})")
                if key == ord("q"):
                    break

            frame_index += 1
            frames_processed += 1
            print(f"Frame {frames_processed}/{args.max_frames if args.max_frames else '?'} done", flush=True)
    except _GracefulShutdown:
        print("[run_live] Received SIGTERM -- shutting down cleanly.")
    except KeyboardInterrupt:
        print("[run_live] Received KeyboardInterrupt -- shutting down cleanly.")
    finally:
        capture.release()
        cv2.destroyAllWindows()
        if bridge is not None:
            bridge.stop()
        # Mirror of the CAMERA_STREAM_STARTED publish above -- only reached
        # on a clean exit (normal EOF/--max-frames, SIGINT, or a caught
        # SIGTERM -- see _GracefulShutdown above); a hard kill (SIGKILL, or
        # SIGTERM on Windows, which bypasses Python entirely) never reaches
        # this finally: block at all, same already-documented limitation as
        # EventDispatchBridge.stop()'s own queue-flush guarantee.
        publish_stream_lifecycle_event(frame_publish_client, camera_id, "CAMERA_STREAM_STOPPED")
        frame_publish_client.close()


if __name__ == "__main__":
    main()