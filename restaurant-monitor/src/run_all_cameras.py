"""
run_all_cameras.py
-------------------
Multi-process camera orchestrator. Replaces the manual "one terminal per
camera" workflow (`python src/run_live.py --camera-id camera_01`, repeated
by hand in N terminals) with a single supervising process that:

  1. Reads config/camera_config.json and launches one fully independent OS
     subprocess (`subprocess.Popen`, not `multiprocessing`) running
     `run_live.py` per camera with `"enabled": true`.
  2. Captures each subprocess's stdout/stderr into its own
     logs/<camera_id>.log file, so N cameras' output never interleaves on
     one shared terminal.
  3. Polls all subprocesses; if one exits (crash or otherwise) it is logged
     clearly (camera_id + exit code) and left stopped -- no restart, and no
     effect on any other camera's subprocess, which is the natural
     consequence of each camera being a fully separate OS process.
  4. On SIGINT/SIGTERM, terminates every still-running child cleanly before
     exiting itself, so no orphaned python processes are left behind.

This module deliberately does not modify src/run_live.py's per-camera
processing logic -- it only launches and supervises it.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import redis
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAMERA_CONFIG = PROJECT_ROOT / "config" / "camera_config.json"
DEFAULT_VIDEOS_INDEX = PROJECT_ROOT / "data" / "metadata" / "videos" / "videos_index.json"
DEFAULT_RUN_LIVE_SCRIPT = Path(__file__).resolve().parent / "run_live.py"
DEFAULT_LOGS_DIR = PROJECT_ROOT / "logs"
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


# ---------------------------------------------------------------------------
# Config discovery
# ---------------------------------------------------------------------------

def load_enabled_cameras(camera_config_path: Path | str) -> dict[str, dict]:
    """Read camera_config.json and return only entries with "enabled": true."""
    path = Path(camera_config_path)
    if not path.exists():
        raise FileNotFoundError(f"Camera config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cameras = data.get("cameras", {})
    return {
        camera_id: camera
        for camera_id, camera in cameras.items()
        if camera.get("enabled") is True
    }


def load_camera_video_source(camera_id: str, videos_index_path: Path | str) -> tuple[str, str] | None:
    """Look up the first (source_path, video_id) recorded for camera_id.

    Returns None if no video record exists for this camera_id -- callers must
    treat that as "skip this camera" rather than guessing a source.
    """
    path = Path(videos_index_path)
    if not path.exists():
        raise FileNotFoundError(f"Videos index not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    for record in records:
        if record.get("camera_id") == camera_id:
            return record["path"], record["video_id"]
    return None


def build_camera_command(
    camera_id: str,
    source: str,
    video_id: str,
    python_executable: str,
    script_path: Path | str,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the argv used to launch one camera's run_live.py subprocess."""
    cmd = [
        str(python_executable),
        str(script_path),
        "--camera-id", camera_id,
        "--video-id", video_id,
        "--source", source,
    ]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class CameraProcess:
    """Tracks one camera's subprocess and its dedicated log file handle."""

    def __init__(self, camera_id: str, popen: subprocess.Popen, log_path: Path, log_file):
        self.camera_id = camera_id
        self.popen = popen
        self.log_path = log_path
        self.log_file = log_file
        self.exit_logged = False


class CameraOrchestrator:
    """Launches and supervises one run_live.py subprocess per enabled camera."""

    def __init__(
        self,
        camera_config_path: Path | str = DEFAULT_CAMERA_CONFIG,
        videos_index_path: Path | str = DEFAULT_VIDEOS_INDEX,
        logs_dir: Path | str = DEFAULT_LOGS_DIR,
        python_executable: str = sys.executable,
        script_path: Path | str = DEFAULT_RUN_LIVE_SCRIPT,
        extra_args: list[str] | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ):
        self.camera_config_path = camera_config_path
        self.videos_index_path = videos_index_path
        self.logs_dir = Path(logs_dir)
        self.python_executable = python_executable
        self.script_path = script_path
        self.extra_args = extra_args
        self.poll_interval = poll_interval

        self.processes: dict[str, CameraProcess] = {}
        self._shutdown_requested = threading.Event()
        self._lock = threading.Lock()

    # -- Discovery + launch --------------------------------------------------

    def discover_cameras(self) -> dict[str, dict]:
        return load_enabled_cameras(self.camera_config_path)

    def launch_all(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        cameras = self.discover_cameras()
        if not cameras:
            print("[orchestrator] No enabled cameras found in camera config.")
            return
        for camera_id in cameras:
            self.launch_camera(camera_id)

    def launch_camera(self, camera_id: str) -> None:
        video_source = load_camera_video_source(camera_id, self.videos_index_path)
        if video_source is None:
            print(
                f"[orchestrator][WARNING] camera_id={camera_id!r} is enabled in "
                f"camera_config.json but has no matching video record in "
                f"{self.videos_index_path} -- skipping launch."
            )
            return
        source, video_id = video_source

        cmd = build_camera_command(
            camera_id=camera_id,
            source=source,
            video_id=video_id,
            python_executable=self.python_executable,
            script_path=self.script_path,
            extra_args=self.extra_args,
        )

        log_path = self.logs_dir / f"{camera_id}.log"
        log_file = open(log_path, "a", encoding="utf-8")
        log_file.write(
            f"\n===== orchestrator run started {datetime.now().isoformat()} "
            f"camera_id={camera_id} cmd={' '.join(cmd)} =====\n"
        )
        log_file.flush()

        # run_live.py's own sys.path setup only resolves `import src...` when
        # the project root is already importable (e.g. under `python -m` from
        # the project root); a bare `python src/run_live.py` subprocess -- the
        # exact manual workflow this orchestrator replaces -- fails with
        # ModuleNotFoundError otherwise (verified: see docs/TASK5_REPORT.md).
        # Fixing that is a supervision concern (launching the child correctly),
        # not a change to run_live.py's own code, so it's handled here via
        # cwd + PYTHONPATH rather than editing run_live.py.
        child_env = os.environ.copy()
        existing_pythonpath = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = (
            f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(PROJECT_ROOT)
        )

        # subprocess.Popen (not multiprocessing): each camera is a fully
        # independent OS process with its own interpreter, matching the
        # existing manual one-terminal-per-camera behavior.
        popen = subprocess.Popen(
            cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True, cwd=PROJECT_ROOT, env=child_env
        )

        with self._lock:
            self.processes[camera_id] = CameraProcess(camera_id, popen, log_path, log_file)

        print(f"[orchestrator] Launched camera_id={camera_id!r} pid={popen.pid} -> log={log_path}")

    # -- Supervision -----------------------------------------------------------

    def poll_once(self) -> None:
        """Check all tracked subprocesses once; log any that have newly exited.

        A camera exiting has no effect on any other tracked camera -- each is
        an independent OS process/Popen object, and this loop only ever reads
        that one process's own exit status.
        """
        with self._lock:
            items = list(self.processes.items())

        for camera_id, cam_proc in items:
            if cam_proc.exit_logged:
                continue
            exit_code = cam_proc.popen.poll()
            if exit_code is not None:
                cam_proc.exit_logged = True
                level = "INFO" if exit_code == 0 else "ERROR"
                print(
                    f"[orchestrator][{level}] camera_id={camera_id!r} subprocess exited "
                    f"(pid={cam_proc.popen.pid}, exit_code={exit_code}). "
                    f"Not restarting -- see {cam_proc.log_path}"
                )
                try:
                    cam_proc.log_file.close()
                except Exception:
                    pass

    def running_camera_ids(self) -> list[str]:
        with self._lock:
            items = list(self.processes.items())
        return [camera_id for camera_id, cam_proc in items if cam_proc.popen.poll() is None]

    def all_exited(self) -> bool:
        with self._lock:
            items = list(self.processes.values())
        return bool(items) and all(cam_proc.popen.poll() is not None for cam_proc in items)

    def supervise(self) -> None:
        """Poll until every camera has exited or shutdown() is requested.

        Contains no signal-module calls, so it is safe to run from a
        background thread (signal.signal() itself is main-thread-only).
        """
        try:
            while not self._shutdown_requested.is_set():
                self.poll_once()
                if self.all_exited():
                    print("[orchestrator] All camera subprocesses have exited.")
                    break
                time.sleep(self.poll_interval)
        finally:
            self.shutdown()

    # -- Shutdown --------------------------------------------------------------

    def shutdown(self, timeout: float = 10.0) -> None:
        """Terminate all still-running child subprocesses. Idempotent."""
        with self._lock:
            items = list(self.processes.items())

        still_running = [(cid, cp) for cid, cp in items if cp.popen.poll() is None]
        for camera_id, cam_proc in still_running:
            print(f"[orchestrator] Terminating camera_id={camera_id!r} (pid={cam_proc.popen.pid})...")
            cam_proc.popen.terminate()

        deadline = time.time() + timeout
        for camera_id, cam_proc in still_running:
            remaining = max(0.0, deadline - time.time())
            try:
                cam_proc.popen.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                print(f"[orchestrator][WARNING] camera_id={camera_id!r} did not exit in time -- killing.")
                cam_proc.popen.kill()
                cam_proc.popen.wait(timeout=5.0)

        self.poll_once()  # log final exit codes + close remaining log file handles

    def _handle_signal(self, signum, frame) -> None:
        print(f"[orchestrator] Received signal {signum}; shutting down all camera subprocesses...")
        self._shutdown_requested.set()

    def install_signal_handlers(self) -> None:
        """Register SIGINT/SIGTERM handlers. Must be called from the main thread."""
        signal.signal(signal.SIGINT, self._handle_signal)
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (AttributeError, ValueError):
            # SIGTERM isn't independently catchable on every platform/thread
            # (e.g. delivered as an unconditional TerminateProcess on Windows).
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch and supervise src/run_live.py as one OS subprocess per enabled camera."
    )
    parser.add_argument("--camera-config", default=str(DEFAULT_CAMERA_CONFIG))
    parser.add_argument("--videos-index", default=str(DEFAULT_VIDEOS_INDEX))
    parser.add_argument("--logs-dir", default=str(DEFAULT_LOGS_DIR))
    parser.add_argument("--run-live-script", default=str(DEFAULT_RUN_LIVE_SCRIPT))
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument(
        "--extra-args",
        default=None,
        help='Extra args forwarded verbatim to every camera\'s run_live.py invocation, '
             'e.g. --extra-args "--max-frames 0 --save-debug"',
    )
    return parser.parse_args()


def main() -> None:
    # --- إضافة كود تصفير الـ Redis هنا ---
    try:
        print("[orchestrator] جاري تصفير قاعدة بيانات Redis المشتركة لتبدأ الكاميرات بنظافة...")
        r = redis.Redis(host='localhost', port=6379, db=0)
        r.flushdb()
        print("[orchestrator] تم تصفير قاعدة البيانات بنجاح.")
    except Exception as e:
        print(f"[orchestrator][WARNING] لم نتمكن من الاتصال بـ Redis لتصفيرها: {e}")
    # -------------------------------------

    args = parse_args()
    extra_args = shlex.split(args.extra_args) if args.extra_args else None

    orchestrator = CameraOrchestrator(
        camera_config_path=args.camera_config,
        videos_index_path=args.videos_index,
        logs_dir=args.logs_dir,
        python_executable=args.python_executable,
        script_path=args.run_live_script,
        extra_args=extra_args,
        poll_interval=args.poll_interval,
    )
    orchestrator.install_signal_handlers()
    orchestrator.launch_all()
    orchestrator.supervise()


if __name__ == "__main__":
    main()
