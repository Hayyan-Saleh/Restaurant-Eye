"""
test_central_identity_store.py
--------------------------------
Tests for src/core/central_identity_store.py (Task 8's cross-process shared
identity store) and its wiring into LivePipeline's Re-ID tiers:

  (a) two dissimilar embeddings resolve to two different global_ids.
  (b) two near-identical embeddings resolve to the same global_id.
  (c) a real distributed lock, exercised by real OS threads hitting a real
      local Redis instance, prevents concurrent near-simultaneous
      registrations of the same person from producing duplicate global_ids.
  (d) LivePipeline falls back to the local SessionGallery -- no exception
      propagating to the caller -- when Redis is unreachable, driven
      through the real process_frame()/_run_kbs() call chain (not just the
      store class in isolation).

(a)-(c) require a real local Redis instance reachable at
CENTRAL_STORE_TEST_REDIS_HOST/PORT (defaults to localhost:6379) and use a
dedicated db (15) that is flushed before/after each test, kept separate
from db 0 which run_all_cameras.py / production config use. They are
skipped automatically if that Redis instance isn't reachable.

(d) deliberately points the store at an unreachable port and needs no real
Redis instance running.

Run with:  python -m pytest tests/test_central_identity_store.py -v
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np
import pytest
import redis as redis_lib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.central_identity_store import CentralIdentityStore, CentralIdentityStoreError
from src.core.live_pipeline import LivePipeline
from src.kbs.reid_gallery import ReIDGallery, SessionGallery
from src.kbs.table_kbs import TableKBS
from src.zone_management.zone_manager import ZoneManager

import faiss


TEST_REDIS_HOST = "localhost"
TEST_REDIS_PORT = 6379
TEST_REDIS_DB   = 15  # dedicated to this test file -- never db 0


def _redis_reachable() -> bool:
    try:
        client = redis_lib.Redis(
            host=TEST_REDIS_HOST, port=TEST_REDIS_PORT, db=TEST_REDIS_DB,
            socket_timeout=1.0, socket_connect_timeout=1.0,
        )
        return bool(client.ping())
    except Exception:
        return False


requires_real_redis = pytest.mark.skipif(
    not _redis_reachable(),
    reason=f"no reachable Redis at {TEST_REDIS_HOST}:{TEST_REDIS_PORT} (needed for real cross-process tests)",
)


def _unit_vector(index: int, dim: int = 512) -> np.ndarray:
    """Deterministic, orthogonal-by-construction embedding -- avoids any
    flakiness from random-vector cosine similarity assumptions."""
    v = np.zeros(dim, dtype=np.float32)
    v[index] = 1.0
    return v


@pytest.fixture()
def store():
    s = CentralIdentityStore(host=TEST_REDIS_HOST, port=TEST_REDIS_PORT, db=TEST_REDIS_DB)
    s._redis.flushdb()
    yield s
    s._redis.flushdb()


# ---------------------------------------------------------------------------
# (a) dissimilar embeddings -> different global_ids
# ---------------------------------------------------------------------------

@requires_real_redis
def test_dissimilar_embeddings_get_different_global_ids(store):
    e1 = _unit_vector(0)
    e2 = _unit_vector(1)  # cosine(e1, e2) == 0.0 by construction -- unambiguously dissimilar

    id1 = store.resolve_identity(e1)
    id2 = store.resolve_identity(e2)

    assert id1 != id2
    assert store._redis.hlen(store.HASH_KEY) == 2


# ---------------------------------------------------------------------------
# (b) near-identical embeddings -> same global_id
# ---------------------------------------------------------------------------

@requires_real_redis
def test_near_identical_embeddings_get_same_global_id(store):
    base = _unit_vector(5)
    noisy = base.copy()
    noisy[400] = 0.02  # cosine(base, noisy) ~= 0.9998 -- well above threshold+margin

    id1 = store.resolve_identity(base)
    id2 = store.resolve_identity(noisy)

    assert id1 == id2
    assert store._redis.hlen(store.HASH_KEY) == 1


# ---------------------------------------------------------------------------
# (c) distributed lock prevents duplicate ids under real concurrent load
# ---------------------------------------------------------------------------

@requires_real_redis
def test_lock_prevents_duplicate_ids_for_concurrent_registrations(store):
    n_threads = 8
    embedding = _unit_vector(9)

    barrier = threading.Barrier(n_threads)
    results: list[str | None] = [None] * n_threads
    errors: list[Exception] = []

    def worker(slot: int) -> None:
        barrier.wait()  # maximize the chance all threads hit resolve_identity at once
        try:
            results[slot] = store.resolve_identity(embedding.copy())
        except Exception as exc:  # noqa: BLE001 -- captured for the assertion below, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"unexpected errors from concurrent resolve_identity calls: {errors}"
    assert all(r is not None for r in results)
    assert len(set(results)) == 1, (
        f"expected every concurrent caller to converge on ONE shared global_id, got {set(results)}"
    )
    assert store._redis.hlen(store.HASH_KEY) == 1, (
        "lock failed to prevent a duplicate registration -- more than one identity was stored"
    )


# ---------------------------------------------------------------------------
# Typed-error contract
# ---------------------------------------------------------------------------

def test_resolve_identity_raises_typed_error_when_redis_unreachable():
    unreachable_store = CentralIdentityStore(
        host="localhost", port=1, db=0, socket_timeout=0.5,
    )
    with pytest.raises(CentralIdentityStoreError):
        unreachable_store.resolve_identity(_unit_vector(0))


def test_construction_does_not_raise_when_redis_unreachable():
    # redis.Redis() connects lazily -- constructing the store must never
    # fail just because the server isn't there yet.
    store = CentralIdentityStore(host="localhost", port=1, db=0, socket_timeout=0.5)
    assert store is not None


# ---------------------------------------------------------------------------
# (d) LivePipeline falls back to SessionGallery when Redis is unreachable,
#     driven through the real process_frame()/_run_kbs() call chain.
# ---------------------------------------------------------------------------

CAMERA_ID = "camera_test_reid"


class _StubDetector:
    """One fixed detection, no keypoints -- avoids needing the real pose
    model (_extract_pose_from_detections short-circuits on kpts is None)."""

    def detect_with_pose(self, frame):
        detections = [{
            "track_id": None,
            "box": [10, 10, 80, 220],
            "conf": 0.9,
            "class_id": 0,
            "class_name": "person",
        }]
        keypoints_list = [None]
        return detections, keypoints_list


class _StubTracker:
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


def _make_zone_manager() -> ZoneManager:
    zm = object.__new__(ZoneManager)
    zm.zones_config = {}
    return zm


def _make_reid_gallery_stub() -> ReIDGallery:
    """Bypasses ReIDGallery.__init__ (loads the real OSNet model). Only the
    faiss index + _extract override are needed for _run_kbs()'s tier-1
    permanent-gallery check and embedding extraction."""
    gallery = object.__new__(ReIDGallery)
    gallery.index = faiss.IndexFlatIP(ReIDGallery.EMBEDDING_DIM)
    gallery._ids = []
    gallery.threshold = 0.72
    # Real model inference is out of scope for this test -- a fixed,
    # deterministic embedding is enough to exercise the tier-2/tier-3
    # routing logic under test.
    gallery._extract = lambda crop: _unit_vector(3).reshape(1, -1)
    return gallery


def make_bare_pipeline_with_unreachable_redis() -> LivePipeline:
    pipeline = object.__new__(LivePipeline)

    pipeline.zone_manager = _make_zone_manager()
    pipeline.detector = _StubDetector()
    pipeline.tracker = _StubTracker()
    pipeline.reid_gallery = _make_reid_gallery_stub()
    pipeline.session_gallery = SessionGallery(ttl_seconds=1800)
    # Deliberately unreachable: nothing listens on port 1, and a short
    # socket_timeout keeps the test fast instead of hanging.
    pipeline.central_identity_store = CentralIdentityStore(
        host="localhost", port=1, db=0, socket_timeout=0.5,
    )

    pipeline._kp_buffer = {}
    pipeline._action_labels = {}
    pipeline._action_frame_counts = {}
    pipeline._last_action_zone = {}
    pipeline._global_ids = {}
    pipeline._person_roles = {}
    pipeline._active_states = {}
    pipeline._role_accumulators = {}
    pipeline._worker_states = {}
    pipeline._table_customer_counts = {}
    pipeline._table_was_served = {}
    pipeline._table_states = {}
    pipeline._table_presence_cycles = {}
    pipeline._table_absence_cycles = {}
    pipeline._table_seated_ids = {}
    pipeline._occupy_cycles = 1
    pipeline._vacate_cycles = 1
    pipeline.SERVING_SUSTAINED_FRAMES = 6

    return pipeline


def test_pipeline_falls_back_to_session_gallery_when_redis_unreachable(capsys):
    pipeline = make_bare_pipeline_with_unreachable_redis()
    frame = np.zeros((240, 240, 3), dtype=np.uint8)

    # The critical assertion: this call must complete without raising, even
    # though the central identity store cannot reach Redis at all.
    event = pipeline.process_frame(
        frame=frame,
        camera_id=CAMERA_ID,
        video_id="video_test",
        frame_index=1,
        time_seconds=5.0,
        pose_stride=None,
    )

    assert event["tracked_objects"][0]["track_id"] == 1

    # Fell through tier 2 (central store, unreachable) to tier 3 (local
    # SessionGallery) -- confirmed by the id prefix actually assigned, not
    # just by "it didn't crash".
    gid = pipeline._global_ids[1]
    assert gid.startswith("session_"), f"expected a SessionGallery fallback id, got {gid!r}"
    assert gid in pipeline.session_gallery.embeddings

    stdout = capsys.readouterr().out
    assert "central identity store unavailable" in stdout
    assert "falling back to local session gallery" in stdout


def test_pipeline_process_frame_twice_stays_on_fallback_without_crashing():
    """A second frame (same track already assigned) must not re-attempt the
    dead Redis connection and must not crash either -- exercises the 'touch'
    branch of the Re-ID pre-check with a fallback-tier id already assigned."""
    pipeline = make_bare_pipeline_with_unreachable_redis()
    frame = np.zeros((240, 240, 3), dtype=np.uint8)

    pipeline.process_frame(
        frame=frame, camera_id=CAMERA_ID, video_id="video_test",
        frame_index=1, time_seconds=5.0, pose_stride=None,
    )
    first_gid = pipeline._global_ids[1]

    event2 = pipeline.process_frame(
        frame=frame, camera_id=CAMERA_ID, video_id="video_test",
        frame_index=2, time_seconds=5.5, pose_stride=None,
    )

    assert event2["tracked_objects"][0]["track_id"] == 1
    assert pipeline._global_ids[1] == first_gid
