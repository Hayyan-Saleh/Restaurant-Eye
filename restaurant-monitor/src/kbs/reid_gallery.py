"""
reid_gallery.py
---------------
Person Re-ID gallery using OSNet-x0.25 for embedding extraction
and faiss for fast CPU similarity search.

Workflow
--------
1. Worker crop arrives from live_pipeline
2. extract_embedding(crop) → 512-dim vector
3. match(embedding) → global_id if similarity > threshold, else None
4. register(embedding) → new global_id stored in gallery
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import cv2
import numpy as np
import faiss
import torch
import torchreid


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------

class ReIDGallery:
    """
    Stateful gallery of known worker embeddings.

    Parameters
    ----------
    model_name      : OSNet variant — 'osnet_x0_25' is smallest/fastest
    threshold       : cosine similarity threshold to consider a match (0–1)
    device          : 'cpu' always for MX350-constrained inference
    weights_dir     : where to cache downloaded model weights
    """

    EMBEDDING_DIM = 512

    # Crops smaller than this are too low-res/blurry for a trustworthy
    # embedding — accepting them into the gallery is exactly what causes
    # worker X's identity to get handed to unknown person Y.
    MIN_CROP_WIDTH = 32
    MIN_CROP_HEIGHT = 64

    # A match must not just clear `threshold` — it must beat the runner-up
    # by at least this much. A "confidently wrong" match (0.66 vs 0.65) is
    # far more dangerous than an honest "no match" (which just registers a
    # new id). This margin check is what actually catches those.
    MIN_MATCH_MARGIN = 0.05

    def __init__(
        self,
        model_name : str   = "osnet_x0_25",
        threshold  : float = 0.72,
        device     : str   = "cpu",
        weights_dir: str   = "models/reid",
    ):
        self.threshold   = threshold
        self.device      = device
        self.weights_dir = Path(weights_dir)
        self.weights_dir.mkdir(parents=True, exist_ok=True)

        # -- Load OSNet feature extractor ------------------------------------
        self.extractor = torchreid.utils.FeatureExtractor(
            model_name=model_name,
            model_path=str(self.weights_dir / f"{model_name}.pth"),
            device=device,
        )

        # -- Faiss flat index (cosine via L2 on normalized vectors) ----------
        self.index = faiss.IndexFlatIP(self.EMBEDDING_DIM)  # inner product = cosine when normalized

        # global_id list parallel to faiss index rows
        self._ids: list[str] = []

    # -- Public API ----------------------------------------------------------

    def process(self, track_id: int, crop: np.ndarray) -> str:
        """
        Main entry point called by live_pipeline for every worker crop.
        Returns the global_id (existing match or newly registered).
        """
        h, w = crop.shape[:2]
        if w < self.MIN_CROP_WIDTH or h < self.MIN_CROP_HEIGHT:
            # Crop too small/low-res to trust — do NOT attempt a match against
            # the gallery (this is exactly the scenario that hands worker X's
            # identity to a blurry unrelated person Y). Caller should treat
            # a None return as "skip ReID this frame, keep previous id".
            print(f"[ReIDGallery] track {track_id}: crop too small ({w}x{h}, min {self.MIN_CROP_WIDTH}x{self.MIN_CROP_HEIGHT}) — skipping match, not registering")
            return None

        embedding = self._extract(crop)
        global_id = self._match(embedding)

        if global_id is None:
            global_id = self._register(embedding)

        return global_id

    # -- Internal ------------------------------------------------------------

    def _extract(self, crop: np.ndarray) -> np.ndarray:
        """Extract L2-normalized 512-dim embedding from a BGR crop."""
        # torchreid expects RGB, resize to 256x128 (standard Re-ID input)
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (128, 256))

        embedding = self.extractor([resized])           # returns (1, 512) tensor
        vec = embedding.cpu().numpy().astype(np.float32)
        faiss.normalize_L2(vec)                         # in-place L2 norm → cosine via IP
        return vec                                      # shape (1, 512)

    def _match(self, embedding: np.ndarray) -> str | None:
        """Return global_id of closest gallery entry if above threshold AND
        clearly ahead of the runner-up, else None.
        """
        if self.index.ntotal == 0:
            return None

        k = min(2, self.index.ntotal)
        similarities, indices = self.index.search(embedding, k=k)
        sim   = float(similarities[0][0])
        idx   = int(indices[0][0])

        if sim < self.threshold:
            return None

        if k > 1:
            runner_up_sim = float(similarities[0][1])
            margin = sim - runner_up_sim
            if margin < self.MIN_MATCH_MARGIN:
                print(f"[ReIDGallery] ambiguous match rejected: top={sim:.3f} runner_up={runner_up_sim:.3f} margin={margin:.3f} < {self.MIN_MATCH_MARGIN} — registering as new identity instead")
                return None

        return self._ids[idx]

    def _register(self, embedding: np.ndarray) -> str:
        """Add new embedding to gallery, return assigned global_id."""
        global_id = f"worker_{uuid.uuid4().hex[:8]}"
        self.index.add(embedding)
        self._ids.append(global_id)
        return global_id

    # -- Persistence ---------------------------------------------------------

    def save(self, path: str = "models/reid/gallery.index") -> None:
        """Persist faiss index + id list to disk."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(p))
        np.save(str(p.with_suffix(".ids.npy")), np.array(self._ids))

    def load(self, path: str = "models/reid/gallery.index") -> None:
        """Load persisted gallery from disk."""
        p = Path(path)
        if not p.exists():
            return
        self.index = faiss.read_index(str(p))
        self._ids  = np.load(str(p.with_suffix(".ids.npy")), allow_pickle=True).tolist()


# ---------------------------------------------------------------------------
# Session Gallery (Short-Term / Ephemeral)
# ---------------------------------------------------------------------------

class SessionGallery:
    """
    Short-term, in-memory gallery for tracking unknown persons/customers
    during an active session. Purges old embeddings automatically to save memory.
    Connects fragmented DeepSORT tracks when a camera glitches.
    """
    def __init__(self, threshold: float = 0.65, ttl_seconds: float = 1800):
        self.threshold = threshold
        self.ttl = ttl_seconds
        
        # global_id -> {"vector": np.ndarray, "last_seen": float}
        self.embeddings: dict[str, dict] = {}
        
        self.index = faiss.IndexFlatIP(512)
        self._ids: list[str] = []

    def match(self, embedding: np.ndarray) -> str | None:
        """Find the closest matching session ID if above threshold AND clearly ahead of runner-up."""
        if self.index.ntotal == 0:
            return None

        k = min(2, self.index.ntotal)
        similarities, indices = self.index.search(embedding, k=k)
        sim = float(similarities[0][0])
        idx = int(indices[0][0])

        if sim < self.threshold:
            return None

        if k > 1:
            runner_up_sim = float(similarities[0][1])
            if (sim - runner_up_sim) < ReIDGallery.MIN_MATCH_MARGIN:
                return None

        return self._ids[idx]

    def register(self, embedding: np.ndarray) -> str:
        """Add a new embedding and generate a new session ID."""
        global_id = f"session_{uuid.uuid4().hex[:8]}"
        self.update_seen(global_id, embedding)
        return global_id

    def update_seen(self, global_id: str, embedding: np.ndarray) -> None:
        """Update the embedding vector and reset the TTL timer."""
        self.embeddings[global_id] = {
            "vector": embedding,
            "last_seen": time.time()
        }
        self._rebuild_index()

    def touch(self, global_id: str) -> None:
        """Reset the TTL timer without changing the embedding."""
        if global_id in self.embeddings:
            self.embeddings[global_id]["last_seen"] = time.time()

    def remove(self, global_id: str) -> None:
        """Delete an embedding manually (used when promoted to worker)."""
        if global_id in self.embeddings:
            del self.embeddings[global_id]
            self._rebuild_index()

    def purge_old(self) -> None:
        """Garbage collect embeddings older than TTL (30 mins)."""
        now = time.time()
        to_delete = [gid for gid, data in self.embeddings.items() if now - data["last_seen"] > self.ttl]
        
        if to_delete:
            for gid in to_delete:
                del self.embeddings[gid]
            self._rebuild_index()
            print(f"[SessionGallery] Purged {len(to_delete)} stale IDs: {to_delete}")

    def _rebuild_index(self) -> None:
        """Rebuild the Faiss index from the in-memory dictionary."""
        self.index.reset()
        self._ids = list(self.embeddings.keys())
        if self._ids:
            vectors = np.vstack([self.embeddings[gid]["vector"] for gid in self._ids])
            self.index.add(vectors)