# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/vision/faces.py
# Purpose : Face detection/embedding, embedding gallery and per-track identity voting
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Face detection and recognition (InsightFace: SCRFD + ArcFace, ONNX on CPU).

- ``FaceEngine``: detects faces and computes L2-normalized 512-d ArcFace embeddings.
- ``EmbeddingGallery`` (alias ``FaceGallery``): in-memory store of known
  embeddings with cosine-similarity matching.
- ``IdentityResolver``: votes across several frames of the same tracking ID
  before deciding "known" / "unknown", so a single blurry frame can never
  trigger an action on its own.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from jarvis.config.settings import FaceConfig


@dataclass
class DetectedFace:
    """A face found in an image.

    Attributes:
        bbox: ``(x1, y1, x2, y2)`` in pixel coordinates of the analyzed image.
        det_score: SCRFD detection confidence in [0, 1].
        embedding: L2-normalized ArcFace embedding (float32, 512-d).
    """

    bbox: tuple[int, int, int, int]
    det_score: float
    embedding: np.ndarray


def sharpness(gray: np.ndarray) -> float:
    """Compute a focus measure as the variance of the Laplacian.

    Values below roughly 30 indicate a blurry image (motion blur, PTZ moving).

    Args:
        gray: Single-channel grayscale image.

    Returns:
        Laplacian variance (higher is sharper).
    """
    import cv2

    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class FaceEngine:
    """Thread-safe wrapper around InsightFace ``FaceAnalysis`` (detection + recognition only).

    Attributes:
        cfg: Face configuration (model, detector input size, quality thresholds).
        app: The prepared InsightFace ``FaceAnalysis`` instance.
    """

    def __init__(self, cfg: FaceConfig):
        """Load and prepare the SCRFD detector and ArcFace recognizer.

        Args:
            cfg: Face configuration.
        """
        from insightface.app import FaceAnalysis

        self.cfg = cfg
        # ONNX Runtime sessions are shared between the vision thread and enrollment calls.
        self._lock = threading.Lock()
        providers = [p for p in cfg.providers if p != "auto"] or ["CPUExecutionProvider"]
        self.app = FaceAnalysis(name=cfg.model_name, root=cfg.model_root, providers=providers,
                                allowed_modules=["detection", "recognition"])
        self.app.prepare(ctx_id=-1, det_size=(cfg.det_size, cfg.det_size))

    def detect(self, bgr: np.ndarray) -> list[DetectedFace]:
        """Detect every face in an image and compute its embedding.

        Args:
            bgr: BGR image.

        Returns:
            All detected faces, unfiltered.
        """
        with self._lock:
            faces = self.app.get(bgr)
        out = []
        for f in faces:
            x1, y1, x2, y2 = (int(v) for v in f.bbox)
            out.append(DetectedFace((x1, y1, x2, y2), float(f.det_score),
                                    np.asarray(f.normed_embedding, dtype=np.float32)))
        return out

    def best_face(self, bgr: np.ndarray, check_quality: bool = True) -> DetectedFace | None:
        """Return the largest face in the image that passes the quality checks.

        Args:
            bgr: BGR image.
            check_quality: Apply ``quality_ok`` filtering (disabled for enrollment photos).

        Returns:
            The largest qualifying face, or ``None``.
        """
        candidates = []
        for face in self.detect(bgr):
            if check_quality and not self.quality_ok(bgr, face):
                continue
            x1, y1, x2, y2 = face.bbox
            candidates.append(((x2 - x1) * (y2 - y1), face))
        return max(candidates, key=lambda c: c[0])[1] if candidates else None

    def quality_ok(self, bgr: np.ndarray, face: DetectedFace) -> bool:
        """Check minimum size, detection score and sharpness of a face.

        Args:
            bgr: Image the face was detected in.
            face: Detected face.

        Returns:
            True if the face is good enough to be matched.
        """
        import cv2

        x1, y1, x2, y2 = face.bbox
        if min(x2 - x1, y2 - y1) < self.cfg.min_face_px or face.det_score < self.cfg.min_det_score:
            return False
        # SCRFD can return boxes that slightly overflow the image: clip before cropping.
        crop = bgr[max(y1, 0):max(y2, 0), max(x1, 0):max(x2, 0)]
        if crop.size == 0:
            return False
        return sharpness(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)) >= self.cfg.min_sharpness

    def embed_image_file(self, path: str) -> DetectedFace | None:
        """Extract the main face of an arbitrary photo, for enrollment.

        The image is downscaled so its longest side is at most 960 px before detection.

        Args:
            path: Image file path.

        Returns:
            The largest face (no quality filtering), or ``None`` if none was found.

        Raises:
            ValueError: If the file cannot be read as an image.
        """
        import cv2

        img = cv2.imread(path)
        if img is None:
            raise ValueError("Unreadable image")
        scale = 960 / max(img.shape[:2])
        if scale < 1:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return self.best_face(img, check_quality=False)


class EmbeddingGallery:
    """In-memory matrix of known embeddings (N x dim) with cosine-similarity matching.

    Hot-reloaded through the control socket. Used for faces (ArcFace, 512-d) as
    well as voices (ECAPA, 192-d).

    Attributes:
        dim: Embedding dimension.
    """

    def __init__(self, dim: int = 512):
        """Create an empty gallery.

        Args:
            dim: Embedding dimension.
        """
        self.dim = dim
        self._lock = threading.Lock()
        self._matrix = np.zeros((0, dim), dtype=np.float32)
        self._person_ids = np.zeros((0,), dtype=np.int64)

    def load(self, rows: list[tuple[int, np.ndarray]]) -> None:
        """Replace the gallery content atomically.

        Rows are re-normalized so that a dot product equals cosine similarity.

        Args:
            rows: ``(person_id, embedding)`` pairs; a person may have several rows.
        """
        with self._lock:
            if rows:
                mat = np.stack([r[1] for r in rows]).astype(np.float32)
                mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
                self._matrix = mat
                self._person_ids = np.array([r[0] for r in rows], dtype=np.int64)
            else:
                self._matrix = np.zeros((0, self.dim), dtype=np.float32)
                self._person_ids = np.zeros((0,), dtype=np.int64)

    def __len__(self) -> int:
        """Return the number of stored embeddings (not persons)."""
        return len(self._person_ids)

    def match(self, embedding: np.ndarray) -> tuple[int | None, float]:
        """Find the nearest stored embedding (1-NN, cosine similarity).

        No threshold is applied here; callers compare the score against their own.

        Args:
            embedding: Query embedding (normalized internally).

        Returns:
            ``(person_id, similarity)`` of the best candidate, or ``(None, 0.0)``
            if the gallery is empty.
        """
        with self._lock:
            if len(self._person_ids) == 0:
                return None, 0.0
            emb = embedding / (np.linalg.norm(embedding) + 1e-9)
            sims = self._matrix @ emb
            idx = int(np.argmax(sims))
            return int(self._person_ids[idx]), float(sims[idx])


FaceGallery = EmbeddingGallery


@dataclass
class _TrackState:
    """Per-track accumulated evidence used by ``IdentityResolver``.

    Attributes:
        votes: Number of above-threshold matches per person ID.
        best_score: Highest similarity seen per person ID.
        misses: Number of observations that matched nobody.
        last_check: Clock time of the last observation.
        known: Person ID once the track is confirmed, else ``None``.
        unknown_reported: True once an "unknown" decision has been emitted.
        best_unknown: ``(quality, embedding, crop)`` of the best non-matching observation.
        best_known: ``person_id -> (score, quality, embedding, crop)`` of the best
            matching observation, reported with the "known" decision.
    """

    votes: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    best_score: dict[int, float] = field(default_factory=dict)
    misses: int = 0
    last_check: float = 0.0
    known: int | None = None
    unknown_reported: bool = False
    best_unknown: tuple[float, np.ndarray, np.ndarray] | None = None  # (quality, embedding, crop)
    best_known: dict[int, tuple[float, float, np.ndarray, np.ndarray | None]] = field(default_factory=dict)
    # person_id -> (score, quality, embedding, crop) of the best matching observation


@dataclass
class Decision:
    """Final identity decision for a track, emitted at most once per kind.

    Attributes:
        kind: ``"known"`` or ``"unknown"``.
        track_id: Tracker ID of the person.
        person_id: Recognized person (``None`` for unknown).
        score: Best cosine similarity (known) or last similarity (unknown).
        embedding: Embedding of the best observation.
        crop: Face crop of the best observation.
        quality: Quality score of the best observation.
    """

    kind: str  # "known" | "unknown"
    track_id: int
    person_id: int | None = None
    score: float = 0.0
    embedding: np.ndarray | None = None
    crop: np.ndarray | None = None
    quality: float = 0.0


class IdentityResolver:
    """Accumulate face observations per track and emit a single decision.

    A track becomes "known" once one person collects ``votes_required``
    above-threshold matches. It is reported "unknown" after
    ``unknown_after_observations`` misses with no vote at all for anyone; it
    can still be upgraded to "known" later if matches arrive.

    Attributes:
        cfg: Face configuration (thresholds, vote counts, recheck interval).
    """

    def __init__(self, cfg: FaceConfig, clock=time.monotonic):
        """Initialize the resolver.

        Args:
            cfg: Face configuration.
            clock: Monotonic time source (injectable for tests).
        """
        self.cfg, self._clock = cfg, clock
        self._tracks: dict[int, _TrackState] = {}

    def needs_check(self, track_id: int) -> bool:
        """Tell whether a track should be run through face recognition now.

        Args:
            track_id: Tracker ID.

        Returns:
            True for new tracks and for unresolved tracks whose last check is
            older than ``recheck_interval_s``; False once the track is known.
        """
        st = self._tracks.get(track_id)
        if st is None:
            return True
        if st.known is not None:
            return False
        return self._clock() - st.last_check >= self.cfg.recheck_interval_s

    def label(self, track_id: int) -> str | int | None:
        """Return the current label of a track.

        Args:
            track_id: Tracker ID.

        Returns:
            The person ID if known, ``"unknown"`` if reported unknown, else ``None``.
        """
        st = self._tracks.get(track_id)
        if st is None:
            return None
        if st.known is not None:
            return st.known
        return "unknown" if st.unknown_reported else None

    def observe(self, track_id: int, person_id: int | None, score: float, embedding: np.ndarray,
                crop: np.ndarray | None, quality: float) -> Decision | None:
        """Record one face observation for a track.

        Args:
            track_id: Tracker ID.
            person_id: Best gallery match (``None`` if the gallery is empty).
            score: Cosine similarity of that match.
            embedding: Face embedding.
            crop: Face crop (for storage).
            quality: Observation quality score.

        Returns:
            A ``Decision`` when the track just became known or unknown, else ``None``.
        """
        st = self._tracks.setdefault(track_id, _TrackState())
        st.last_check = self._clock()
        if st.known is not None:
            return None
        if person_id is not None and score >= self.cfg.match_threshold:
            st.votes[person_id] += 1
            st.best_score[person_id] = max(score, st.best_score.get(person_id, 0.0))
            prev = st.best_known.get(person_id)
            if prev is None or score > prev[0]:
                st.best_known[person_id] = (score, quality, embedding, crop)
            if st.votes[person_id] >= self.cfg.votes_required:
                st.known = person_id
                best_score, best_q, best_emb, best_crop = st.best_known[person_id]
                return Decision("known", track_id, person_id, best_score, best_emb, best_crop, best_q)
            return None
        st.misses += 1
        if st.best_unknown is None or quality > st.best_unknown[0]:
            st.best_unknown = (quality, embedding, crop)
        # Any vote for someone means "maybe known, keep looking": never report unknown then.
        if (not st.unknown_reported and st.misses >= self.cfg.unknown_after_observations
                and sum(st.votes.values()) == 0):
            st.unknown_reported = True
            best_q, emb, best_crop = st.best_unknown
            return Decision("unknown", track_id, None, score, emb, best_crop, best_q)
        return None

    def prune(self, active_ids: set[int]) -> None:
        """Forget tracks that are no longer active.

        Args:
            active_ids: Tracker IDs still alive.
        """
        for tid in list(self._tracks):
            if tid not in active_ids:
                del self._tracks[tid]

    def reset(self) -> None:
        """Drop all track state so current tracks are re-evaluated (after a gallery reload)."""
        self._tracks.clear()
