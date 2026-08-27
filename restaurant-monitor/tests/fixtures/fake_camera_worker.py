"""
fake_camera_worker.py
----------------------
Lightweight stand-in for src/run_live.py, used only by
tests/test_run_all_cameras.py. Accepts the same core flags the orchestrator
passes to a real camera process (--camera-id, --video-id, --source) so it's
a drop-in replacement of the orchestrator's `script_path`, but does none of
the real ML pipeline work -- it just prints a few identifiable lines and
sleeps, optionally crashing on command.

This lets orchestration tests (discovery, logging separation, crash
isolation, shutdown) run in milliseconds instead of minutes and without
needing real video files or a loaded YOLO/torch pipeline.

Crash behavior is controlled via the FAKE_CAMERA_CRASH_IDS environment
variable (comma-separated camera_ids) rather than a CLI flag, so the same
--extra-args (shared across every camera the orchestrator launches) can
still make exactly one camera crash and leave others alone.
"""

import argparse
import os
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--video-id", default="test_video")
    parser.add_argument("--source", default="0")
    parser.add_argument("--duration", type=float, default=3.0, help="seconds to run before exiting cleanly")
    parser.add_argument("--crash-after", type=float, default=0.5, help="seconds before crashing, if this camera_id is in FAKE_CAMERA_CRASH_IDS")
    args = parser.parse_args()

    crash_ids = {c.strip() for c in os.environ.get("FAKE_CAMERA_CRASH_IDS", "").split(",") if c.strip()}
    should_crash = args.camera_id in crash_ids

    print(f"STARTED camera_id={args.camera_id} source={args.source} video_id={args.video_id}", flush=True)

    start = time.time()
    tick = 0
    while True:
        elapsed = time.time() - start
        if should_crash and elapsed >= args.crash_after:
            print(f"CRASHING camera_id={args.camera_id} at {elapsed:.2f}s", flush=True)
            raise RuntimeError(f"Simulated crash for camera_id={args.camera_id}")
        if elapsed >= args.duration:
            break
        print(f"TICK camera_id={args.camera_id} t={tick}", flush=True)
        tick += 1
        time.sleep(0.1)

    print(f"STOPPED camera_id={args.camera_id}", flush=True)


if __name__ == "__main__":
    main()
