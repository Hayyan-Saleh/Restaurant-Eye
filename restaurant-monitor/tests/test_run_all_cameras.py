"""
test_run_all_cameras.py
------------------------
Tests for the multi-process camera orchestrator (src/run_all_cameras.py):
  (a) only "enabled": true cameras from camera_config.json are discovered
      and launched, disabled ones are not.
  (b) one subprocess crashing does not stop or affect other running
      subprocesses -- a real crash is simulated (an actual OS subprocess
      raises an unhandled exception and exits non-zero) and the others are
      observed to still be running afterward, not just asserted by
      inspection.
  (c) each camera's stdout/stderr ends up in its own distinguishable
      logs/<camera_id>.log file, with no cross-contamination between
      cameras.
  (d) triggering the orchestrator's shutdown path (the same handler
      signal.signal() would invoke on a real SIGINT) results in every
      still-running child subprocess actually being OS-terminated, none
      left orphaned.

All tests launch the real tests/fixtures/fake_camera_worker.py as the
`script_path` instead of the real src/run_live.py, via subprocess.Popen --
these are genuine, fully independent OS processes (not mocked), just a
lightweight stand-in for the real ML pipeline so tests run in milliseconds.

Run with:  python -m pytest tests/test_run_all_cameras.py -v
"""

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.run_all_cameras import CameraOrchestrator, load_enabled_cameras

FAKE_WORKER = Path(__file__).resolve().parent / "fixtures" / "fake_camera_worker.py"


def write_camera_config(path: Path, cameras: dict[str, bool]) -> None:
    """cameras: {camera_id: enabled_bool}"""
    data = {
        "cameras": {
            camera_id: {"type": "mixed", "location": "test", "enabled": enabled, "description": "test"}
            for camera_id, enabled in cameras.items()
        }
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def write_videos_index(path: Path, camera_ids: list[str]) -> None:
    records = [
        {"camera_id": cid, "video_id": "video_001", "path": f"fake_source_{cid}.mp4"}
        for cid in camera_ids
    ]
    path.write_text(json.dumps(records), encoding="utf-8")


def make_orchestrator(tmp_path: Path, cameras: dict[str, bool], indexed_camera_ids: list[str] | None = None, extra_args=None) -> CameraOrchestrator:
    camera_config_path = tmp_path / "camera_config.json"
    videos_index_path = tmp_path / "videos_index.json"
    write_camera_config(camera_config_path, cameras)
    write_videos_index(videos_index_path, indexed_camera_ids if indexed_camera_ids is not None else list(cameras.keys()))

    return CameraOrchestrator(
        camera_config_path=camera_config_path,
        videos_index_path=videos_index_path,
        logs_dir=tmp_path / "logs",
        python_executable=sys.executable,
        script_path=FAKE_WORKER,
        extra_args=extra_args,
        poll_interval=0.1,
    )


# ---------------------------------------------------------------------------
# (a) discovery: only enabled cameras launched
# ---------------------------------------------------------------------------

def test_load_enabled_cameras_excludes_disabled(tmp_path):
    camera_config_path = tmp_path / "camera_config.json"
    write_camera_config(camera_config_path, {"camera_01": True, "camera_02": False, "camera_03": True})

    enabled = load_enabled_cameras(camera_config_path)

    assert set(enabled.keys()) == {"camera_01", "camera_03"}


def test_orchestrator_only_launches_enabled_cameras(tmp_path):
    orchestrator = make_orchestrator(
        tmp_path,
        cameras={"camera_01": True, "camera_02": False, "camera_03": True},
        extra_args=["--duration", "0.5"],
    )
    try:
        orchestrator.launch_all()
        assert set(orchestrator.processes.keys()) == {"camera_01", "camera_03"}
        assert all(cp.popen.poll() is None or cp.popen.poll() == 0 for cp in orchestrator.processes.values())
    finally:
        orchestrator.shutdown()


# ---------------------------------------------------------------------------
# (b) crash isolation -- actually simulated, not just asserted
# ---------------------------------------------------------------------------

def test_one_crashing_camera_does_not_affect_others(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CAMERA_CRASH_IDS", "camera_crash")
    orchestrator = make_orchestrator(
        tmp_path,
        cameras={"camera_crash": True, "camera_survivor": True},
        extra_args=["--duration", "5.0", "--crash-after", "0.3"],
    )
    try:
        orchestrator.launch_all()

        # Wait past the crash point but well before the survivor's duration ends.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            orchestrator.poll_once()
            if orchestrator.processes["camera_crash"].popen.poll() is not None:
                break
            time.sleep(0.1)

        crash_proc = orchestrator.processes["camera_crash"]
        survivor_proc = orchestrator.processes["camera_survivor"]

        assert crash_proc.popen.poll() is not None, "crashed camera should have actually exited"
        assert crash_proc.popen.poll() != 0, "crashed camera should exit with a non-zero code"

        # The critical assertion: the OTHER subprocess is still alive and
        # running, observed directly via the OS (poll() returning None means
        # the process has not exited), not merely assumed.
        assert survivor_proc.popen.poll() is None, "surviving camera must still be running after the other crashed"
    finally:
        orchestrator.shutdown()

    # After shutdown, both must be gone (no orphan from the crash, none left over from termination).
    assert orchestrator.processes["camera_crash"].popen.poll() is not None
    assert orchestrator.processes["camera_survivor"].popen.poll() is not None


# ---------------------------------------------------------------------------
# (c) per-camera log destination, no cross-contamination
# ---------------------------------------------------------------------------

def test_each_camera_output_goes_to_its_own_log_file(tmp_path):
    orchestrator = make_orchestrator(
        tmp_path,
        cameras={"camera_a": True, "camera_b": True},
        extra_args=["--duration", "0.5"],
    )
    try:
        orchestrator.launch_all()

        deadline = time.time() + 5.0
        while time.time() < deadline and not orchestrator.all_exited():
            orchestrator.poll_once()
            time.sleep(0.1)
        orchestrator.poll_once()
    finally:
        orchestrator.shutdown()

    log_a = tmp_path / "logs" / "camera_a.log"
    log_b = tmp_path / "logs" / "camera_b.log"
    assert log_a.exists() and log_b.exists()
    assert log_a != log_b

    text_a = log_a.read_text(encoding="utf-8")
    text_b = log_b.read_text(encoding="utf-8")

    assert "camera_id=camera_a" in text_a
    assert "camera_id=camera_b" not in text_a  # no cross-contamination

    assert "camera_id=camera_b" in text_b
    assert "camera_id=camera_a" not in text_b  # no cross-contamination

    assert "STARTED" in text_a and "STOPPED" in text_a
    assert "STARTED" in text_b and "STOPPED" in text_b


# ---------------------------------------------------------------------------
# (d) clean shutdown terminates every child, none orphaned
# ---------------------------------------------------------------------------

def test_signal_triggers_termination_of_all_children_no_orphans(tmp_path):
    orchestrator = make_orchestrator(
        tmp_path,
        cameras={"camera_x": True, "camera_y": True},
        extra_args=["--duration", "30.0"],  # long-running: must still be alive when we "signal"
    )

    orchestrator.launch_all()
    assert len(orchestrator.processes) == 2

    # supervise() contains no signal-module calls, so it's safe to drive from
    # a background thread here (signal.signal() itself is main-thread-only).
    supervisor_thread = threading.Thread(target=orchestrator.supervise, daemon=True)
    supervisor_thread.start()

    # Give the children a moment to actually start (they print STARTED immediately).
    time.sleep(0.5)
    assert orchestrator.running_camera_ids() == ["camera_x", "camera_y"] or set(orchestrator.running_camera_ids()) == {"camera_x", "camera_y"}
    pids_before = {cid: cp.popen.pid for cid, cp in orchestrator.processes.items()}

    # Invoke the exact handler signal.signal(SIGINT, ...) would call on a
    # real Ctrl+C -- (signum, frame) is the interpreter's own calling
    # convention for signal handlers. Real cross-process console-signal
    # delivery is not reliably testable on Windows (see docs/TASK5_REPORT.md);
    # this exercises the real registered code path end-to-end instead.
    orchestrator._handle_signal(signal.SIGINT, None)

    supervisor_thread.join(timeout=15.0)
    assert not supervisor_thread.is_alive(), "supervise() must return after shutdown"

    for camera_id, pid in pids_before.items():
        exit_code = orchestrator.processes[camera_id].popen.poll()
        assert exit_code is not None, f"{camera_id} (pid={pid}) must have actually been terminated by the OS, not left running"
