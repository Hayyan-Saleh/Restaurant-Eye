"""
central_identity_store.py
--------------------------
Cross-process shared person-identity store, backed by Redis.

Since Task 5, each camera runs as a fully separate OS subprocess (see
src/run_all_cameras.py), so the in-memory galleries in
src/kbs/reid_gallery.py (ReIDGallery, SessionGallery) are only visible
within the one process that built them -- the same person seen by
camera_01's process and later by camera_02's process previously got two
unrelated global_ids, since neither process could see the other's gallery.

CentralIdentityStore closes that gap for people not yet confirmed as known
staff (the permanent worker gallery is unaffected -- see live_pipeline.py's
tier ordering): given a Re-ID embedding, it matches against embeddings
registered by *any* camera's process and returns the shared global_id, or
registers a new one -- all under a distributed lock so two processes
seeing the same person at the same instant can't each register a separate
identity.

Design choices (mirrored from src/kbs/reid_gallery.py for consistency):
  - threshold: 0.65, matching SessionGallery's default -- NOT ReIDGallery's
    stricter 0.72 permanent-gallery threshold, since misclassifying a
    session-level person here is far cheaper to correct than misclassifying
    a confirmed worker.
  - ambiguity margin: reuses ReIDGallery.MIN_MATCH_MARGIN (0.05) -- a match
    that doesn't clearly beat the runner-up is rejected in favor of
    registering a new identity, same reasoning as reid_gallery.py.

Storage: embeddings live in a single Redis hash (global_id -> JSON list of
floats) plus an INCR counter for id issuance, guarded by a single
redis.lock() around the whole read-compare-write sequence of each call.
Every public method raises CentralIdentityStoreError on any failure
(Redis unreachable, lock timeout, corrupt stored data) rather than
swallowing it -- callers (LivePipeline) are expected to catch this
specific exception and fall back to the local SessionGallery.
"""

from __future__ import annotations

import json

import numpy as np
import redis

from src.kbs.reid_gallery import ReIDGallery


class CentralIdentityStoreError(Exception):
    """Raised by every public CentralIdentityStore method on failure.

    Covers: Redis unreachable/timed out, lock not acquired in time, or
    corrupt data found in the store. Never raised for "no match found" --
    that's a normal outcome (resolve_identity() just registers a new id).
    """


class CentralIdentityStore:
    """
    Cross-process shared gallery of person embeddings, backed by Redis.

    Parameters
    ----------
    host, port, db  : Redis connection target.
    threshold       : cosine similarity threshold to consider a match (0-1).
                       Defaults to 0.65, matching SessionGallery (see module
                       docstring) since this store takes over its role for
                       people not yet confirmed as permanent workers.
    socket_timeout  : seconds before a Redis call is treated as unreachable.
                       Kept short so an unreachable Redis fails fast at the
                       point of use instead of hanging LivePipeline's frame
                       loop.
    """

    EMBEDDING_DIM = 512

    THRESHOLD = 0.65

    LOCK_KEY               = "central_identity_store:lock"
    LOCK_TIMEOUT_SECONDS    = 10   # auto-release if the holding process dies mid-op
    LOCK_BLOCKING_SECONDS   = 10   # how long a caller waits to acquire it

    HASH_KEY    = "central_identity_store:embeddings"
    COUNTER_KEY = "central_identity_store:counter"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        threshold: float | None = None,
        socket_timeout: float = 2.0,
    ):
        self.threshold = threshold if threshold is not None else self.THRESHOLD

        # redis.Redis() does not connect eagerly -- constructing this client
        # never touches the network, so an unreachable Redis server cannot
        # fail LivePipeline construction. The first real connection attempt
        # (and thus the first possible failure) happens inside resolve_identity()
        # / remove(), which wrap it in CentralIdentityStoreError.
        try:
            self._redis = redis.Redis(
                host=host,
                port=port,
                db=db,
                decode_responses=True,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_timeout,
            )
        except Exception as exc:  # redis.Redis() itself can raise on bad args
            raise CentralIdentityStoreError(
                f"Failed to construct Redis client for {host}:{port} db={db}: {exc}"
            ) from exc

    # -- Public API ------------------------------------------------------

    def resolve_identity(self, embedding: np.ndarray | list[float]) -> str:
        """
        Main entry point. Match `embedding` against every identity any
        camera process has registered so far; return its global_id if a
        confident match is found, else register it as a new identity and
        return the new global_id.

        The whole match-then-register sequence runs under a single Redis
        lock so two processes resolving the same person at the same instant
        cannot both register it as two different new identities.

        Raises CentralIdentityStoreError if Redis is unreachable, the lock
        can't be acquired in time, or stored data is corrupt.
        """
        vector = self._to_vector_list(embedding)

        try:
            lock = self._redis.lock(
                self.LOCK_KEY,
                timeout=self.LOCK_TIMEOUT_SECONDS,
                blocking_timeout=self.LOCK_BLOCKING_SECONDS,
            )
            acquired = lock.acquire(blocking=True)
        except redis.exceptions.RedisError as exc:
            raise CentralIdentityStoreError(
                f"Redis error acquiring '{self.LOCK_KEY}': {exc}"
            ) from exc

        if not acquired:
            raise CentralIdentityStoreError(
                f"Timed out acquiring '{self.LOCK_KEY}' after {self.LOCK_BLOCKING_SECONDS}s"
            )

        try:
            global_id = self._match_locked(vector)
            if global_id is None:
                global_id = self._register_locked(vector)
            return global_id
        except redis.exceptions.RedisError as exc:
            raise CentralIdentityStoreError(
                f"Redis error during resolve_identity: {exc}"
            ) from exc
        finally:
            try:
                lock.release()
            except redis.exceptions.LockError as exc:
                # Lock already expired/auto-released (e.g. this call ran
                # right up against LOCK_TIMEOUT_SECONDS). Correctness is
                # still protected by the lock's own timeout; this is just
                # visibility that release-on-exit didn't complete cleanly.
                print(f"[CentralIdentityStore][WARNING] lock release failed: {exc}")

    def remove(self, global_id: str) -> None:
        """Delete a stored identity (used when a session-level person from
        this store gets promoted to the permanent worker gallery)."""
        try:
            self._redis.hdel(self.HASH_KEY, global_id)
        except redis.exceptions.RedisError as exc:
            raise CentralIdentityStoreError(
                f"Redis error removing {global_id}: {exc}"
            ) from exc

    def ping(self) -> bool:
        """Explicit reachability check. Raises CentralIdentityStoreError if
        Redis cannot be reached -- callers that only need a health check
        (rather than resolve_identity's full side effects) can use this."""
        try:
            return bool(self._redis.ping())
        except redis.exceptions.RedisError as exc:
            raise CentralIdentityStoreError(f"Redis unreachable: {exc}") from exc

    # -- Internal (must be called with the lock already held) ------------

    def _match_locked(self, vector: list[float]) -> str | None:
        all_identities = self._redis.hgetall(self.HASH_KEY)
        if not all_identities:
            return None

        query = np.asarray(vector, dtype=np.float32)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return None
        query = query / query_norm

        candidate_ids: list[str] = []
        similarities: list[float] = []

        for global_id, stored_json in all_identities.items():
            try:
                stored_vec = np.asarray(json.loads(stored_json), dtype=np.float32)
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                raise CentralIdentityStoreError(
                    f"Corrupt embedding stored under global_id={global_id!r}: {exc}"
                ) from exc

            stored_norm = np.linalg.norm(stored_vec)
            if stored_norm == 0:
                continue

            similarity = float(np.dot(query, stored_vec / stored_norm))
            candidate_ids.append(global_id)
            similarities.append(similarity)

        if not similarities:
            return None

        order = sorted(range(len(similarities)), key=lambda i: similarities[i], reverse=True)
        best_idx = order[0]
        best_sim = similarities[best_idx]
        best_id  = candidate_ids[best_idx]

        if best_sim < self.threshold:
            return None

        if len(order) > 1:
            runner_up_sim = similarities[order[1]]
            margin = best_sim - runner_up_sim
            if margin < ReIDGallery.MIN_MATCH_MARGIN:
                print(
                    f"[CentralIdentityStore] ambiguous match rejected: "
                    f"top={best_sim:.3f} runner_up={runner_up_sim:.3f} "
                    f"margin={margin:.3f} < {ReIDGallery.MIN_MATCH_MARGIN} "
                    f"-- registering as new identity instead"
                )
                return None

        # Refresh the stored embedding with the latest observed appearance,
        # mirroring SessionGallery.update_seen()'s drift-tracking behavior.
        self._redis.hset(self.HASH_KEY, best_id, json.dumps(vector))
        return best_id

    def _register_locked(self, vector: list[float]) -> str:
        new_id_number = self._redis.incr(self.COUNTER_KEY)
        global_id = f"central_{new_id_number}"
        self._redis.hset(self.HASH_KEY, global_id, json.dumps(vector))
        return global_id

    def _to_vector_list(self, embedding: np.ndarray | list[float]) -> list[float]:
        flat = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if flat.shape[0] != self.EMBEDDING_DIM:
            raise CentralIdentityStoreError(
                f"Expected a {self.EMBEDDING_DIM}-dim embedding, got shape {flat.shape}"
            )
        return flat.tolist()
