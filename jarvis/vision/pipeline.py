# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/vision/pipeline.py
# Purpose : Vision loop: person detection/tracking, PTZ targeting, face ID, preview
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Vision pipeline.

Per frame: person detection (YOLO11 exported to OpenVINO), multi-object
tracking (ByteTrack), target selection, PTZ control, face recognition on a
bounded number of tracks, and an annotated MJPEG preview.

Face results are routed to three sinks:

- ``UnknownStore``: deduplicates and clusters unknown faces per visitor.
- ``SightingRecorder``: audit trail of every identification.
- ``AdaptiveEnroller``: carefully grows a known person's gallery with new,
  high-confidence captures.

Decisions are published on the event queue as ``FaceRecognized`` /
``UnknownFaceSeen`` for the orchestrator.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from jarvis.vision.camera import RtspCamera
from jarvis.vision.ptz import PTZTracker
from jarvis.config.settings import Settings
from jarvis.storage.database import Database
from jarvis.core.events import FaceRecognized, UnknownFaceSeen
from jarvis.vision.faces import FaceEngine, FaceGallery, IdentityResolver, sharpness

log = logging.getLogger(__name__)

TRACK_GRACE_S = 5.0  # a track missing for longer than this is forgotten


@dataclass
class Track:
    """A tracked object in the current frame (a person, or a vehicle for plate reading).

    Attributes:
        id: Persistent ByteTrack ID.
        box: ``(x1, y1, x2, y2)`` in frame pixels.
        conf: Detection confidence.
        cls: COCO class (0 = person; 2, 3, 5, 7 = car, motorcycle, bus, truck).
    """

    id: int
    box: tuple[float, float, float, float]
    conf: float
    cls: int = 0


class PersonDetector:
    """YOLO person detector with built-in tracking (Ultralytics ``model.track``).

    Attributes:
        cfg: Detector configuration (model path, confidence, input size, tracker YAML).
        model: Ultralytics YOLO model.
    """

    def __init__(self, cfg, extra_classes: list[int] | None = None):
        """Load the detection model.

        Args:
            cfg: Detector configuration section.
            extra_classes: COCO classes tracked besides persons (the vehicles, when plate
                recognition is enabled).
        """
        from jarvis.vision import ensure_yolo_config_dir

        ensure_yolo_config_dir()
        from ultralytics import YOLO

        self.cfg = cfg
        self.classes = [0, *(extra_classes or [])]
        self.model = YOLO(cfg.model_path, task="detect")

    def track(self, frame: np.ndarray) -> list[Track]:
        """Detect and track persons (COCO class 0), and vehicles when enabled, in a frame.

        Args:
            frame: BGR frame.

        Returns:
            Tracks that have been assigned an ID (unconfirmed detections are dropped).
        """
        # persist=True keeps the tracker state between calls so IDs stay stable.
        device = None if self.cfg.device in ("auto", "intel:cpu") else self.cfg.device  # Ultralytics OpenVINO device
        res = self.model.track(frame, persist=True, classes=self.classes, conf=self.cfg.conf, imgsz=self.cfg.imgsz, device=device,
                               tracker=self.cfg.tracker, verbose=False)[0]
        boxes = res.boxes
        if boxes is None or boxes.id is None:
            return []
        ids = boxes.id.int().tolist()
        xyxy = boxes.xyxy.tolist()
        confs = boxes.conf.tolist()
        classes = boxes.cls.int().tolist()
        return [Track(i, tuple(b), c, k) for i, b, c, k in zip(ids, xyxy, confs, classes)]


def select_target(tracks: list[Track], frame_w: int, frame_h: int, previous_id: int | None) -> Track | None:
    """Choose which person the PTZ should follow.

    The previous target is kept while it is still tracked (avoids flip-flopping).
    Otherwise the score is ``0.6 * min(4 * area_ratio, 1) + 0.4 * centrality``,
    favoring the closest (largest) and most horizontally centered person.

    Args:
        tracks: Current tracks.
        frame_w: Frame width in pixels.
        frame_h: Frame height in pixels.
        previous_id: ID of the previous target, if any.

    Returns:
        The selected track, or ``None`` if there are no tracks.
    """
    if not tracks:
        return None
    for t in tracks:
        if t.id == previous_id:
            return t

    def score(t: Track) -> float:
        x1, y1, x2, y2 = t.box
        area = (x2 - x1) * (y2 - y1) / (frame_w * frame_h)
        cx = (x1 + x2) / 2 / frame_w
        centrality = 1 - min(abs(cx - 0.5) * 2, 1)
        return 0.6 * min(area * 4, 1) + 0.4 * centrality

    return max(tracks, key=score)


def face_region(box: tuple[float, float, float, float], frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Compute the search area for a face inside a person box.

    Upper ~55% of the person box, widened by 10% on each side and 5% upward,
    clipped to the frame.

    Args:
        box: Person box ``(x1, y1, x2, y2)``.
        frame_w: Frame width.
        frame_h: Frame height.

    Returns:
        Integer region ``(x1, y1, x2, y2)``.
    """
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    return (int(max(0, x1 - 0.1 * w)), int(max(0, y1 - 0.05 * h)),
            int(min(frame_w, x2 + 0.1 * w)), int(min(frame_h, y1 + 0.55 * h)))


def save_jpeg(path: Path, crop: np.ndarray, quality: int = 92) -> None:
    """Write an image as JPEG, creating parent directories.

    Args:
        path: Destination file.
        crop: BGR image.
        quality: JPEG quality (0-100).
    """
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), crop, [cv2.IMWRITE_JPEG_QUALITY, quality])


class UnknownStore:
    """Persist unknown faces and group them per visitor.

    - **Deduplication**: a face very close to an unknown seen within the
      dedupe window (one hour by default) is not stored again.
    - **Clustering**: a new unknown joins the cluster of the nearest stored
      unknown if similarity reaches ``unknown_cluster_similarity``; otherwise
      it starts a new cluster. Labeling a cluster identifies all its visits at
      once (and, retroactively, its sightings).

    Attributes:
        cfg: Face configuration (dedupe/cluster thresholds).
        dir: Root directory for unknown face crops.
        db: Database handle.
    """

    def __init__(self, settings: Settings, db: Database, clock=time.time):
        """Initialize the store and load existing unknown embeddings.

        Args:
            settings: Application settings.
            db: Database handle.
            clock: Wall-clock time source (injectable for tests).
        """
        self.cfg = settings.faces
        self.dir = settings.storage.unknown_dir
        self.db = db
        self._clock = clock
        self.reload()

    def reload(self) -> None:
        """Reload the unknown embedding cache from the database."""
        # (id, cluster_id, created_at, embedding); a few thousand at most given retention,
        # so a linear scan is fine.
        self._known = self.db.all_unknown_embeddings()

    def nearest(self, embedding: np.ndarray) -> tuple[int | None, int | None, float, float]:
        """Find the most similar stored unknown (linear scan, dot product).

        Args:
            embedding: Normalized query embedding.

        Returns:
            ``(unknown_id, cluster_id, similarity, created_at)``; IDs are ``None``
            and similarity is -1 when the store is empty.
        """
        best = (None, None, -1.0, 0.0)
        for uid, cid, ts, emb in self._known:
            sim = float(np.dot(emb, embedding))
            if sim > best[2]:
                best = (uid, cid, sim, ts)
        return best

    def save(self, embedding: np.ndarray, crop: np.ndarray, det_score: float) -> tuple[int | None, int | None]:
        """Store an unknown face unless it is a recent duplicate.

        Args:
            embedding: Normalized face embedding.
            crop: Face crop to save as JPEG.
            det_score: Detection confidence.

        Returns:
            ``(unknown_id, cluster_id)``; ``unknown_id`` is ``None`` when the face
            was deduplicated (``cluster_id`` is then the duplicate's cluster).
        """
        now = self._clock()
        uid, cid, sim, ts = self.nearest(embedding)
        if uid is not None and sim >= self.cfg.unknown_dedupe_similarity and now - ts < self.cfg.unknown_dedupe_window_s:
            return None, cid
        cluster = cid if uid is not None and sim >= self.cfg.unknown_cluster_similarity else None
        path = self.dir / datetime.fromtimestamp(now).strftime("%Y%m%d") / f"{uuid.uuid4().hex}.jpg"
        save_jpeg(path, crop)
        new_id = self.db.add_unknown(embedding, str(path), det_score, cluster_id=cluster)
        # A face that founds a cluster uses its own ID as the cluster ID.
        cluster = cluster or new_id
        self._known.append((new_id, cluster, now, embedding))
        return new_id, cluster


class SightingRecorder:
    """Audit trail: every identification (known, unknown, watchlist) becomes a timestamped sighting.

    Each sighting stores the face crop and its embedding, enabling search-by-face
    after the fact.

    Attributes:
        dir: Root directory for sighting crops.
        db: Database handle.
    """

    def __init__(self, settings: Settings, db: Database):
        """Initialize the recorder.

        Args:
            settings: Application settings.
            db: Database handle.
        """
        self.dir = settings.storage.sightings_dir
        self.db = db

    def record(self, status: str, track_id: int, person_id: int | None, score: float, quality: float,
               embedding: np.ndarray | None, crop: np.ndarray | None, unknown_id: int | None = None,
               cluster_id: int | None = None) -> int:
        """Persist one sighting.

        Args:
            status: ``"known"``, ``"watchlist"`` or ``"unknown"``.
            track_id: Tracker ID.
            person_id: Recognized person, if any.
            score: Match similarity.
            quality: Observation quality.
            embedding: Face embedding.
            crop: Face crop (saved as JPEG if non-empty).
            unknown_id: Linked unknown face, if any.
            cluster_id: Linked unknown cluster, if any.

        Returns:
            The new sighting ID.
        """
        path = None
        if crop is not None and crop.size:
            path = self.dir / datetime.now().strftime("%Y%m%d") / f"{uuid.uuid4().hex}.jpg"
            save_jpeg(path, crop, quality=85)
        return self.db.add_sighting(status, track_id, person_id, round(float(score), 3), round(float(quality), 3),
                                    str(path) if path else None, embedding, unknown_id, cluster_id)


class AdaptiveEnroller:
    """Grow a person's gallery with their best live captures, under strict conditions.

    A capture is added only if its score is well above the match threshold
    (``adaptive_min_score``), its quality is good (``adaptive_min_quality``),
    it is genuinely new (max similarity to existing embeddings at most
    ``adaptive_novelty_max_similarity``) and the per-person cap
    (``adaptive_max_per_person``) is not reached. Added entries use source
    ``"auto"`` and can be reviewed and deleted in the UI.

    Attributes:
        cfg: Face configuration.
        db: Database handle.
        gallery: Live face gallery, reloaded after each addition.
        dir: Root directory for enrolled face images.
    """

    def __init__(self, settings: Settings, db: Database, gallery: FaceGallery):
        """Initialize the enroller.

        Args:
            settings: Application settings.
            db: Database handle.
            gallery: Face gallery to refresh on enrollment.
        """
        self.cfg, self.db, self.gallery = settings.faces, db, gallery
        self.dir = settings.storage.faces_dir

    def consider(self, person_id: int, score: float, quality: float, embedding: np.ndarray | None,
                 crop: np.ndarray | None) -> int | None:
        """Enroll a capture if it meets all adaptive-learning criteria.

        Args:
            person_id: Recognized person.
            score: Match similarity.
            quality: Observation quality.
            embedding: Face embedding.
            crop: Face crop.

        Returns:
            The new face ID, or ``None`` if the capture was rejected.
        """
        c = self.cfg
        if not c.adaptive_enabled or embedding is None or crop is None or not crop.size:
            return None
        if score < c.adaptive_min_score or quality < c.adaptive_min_quality:
            return None
        if self.db.count_faces(person_id, source="auto") >= c.adaptive_max_per_person:
            return None
        existing = self.db.person_face_embeddings(person_id)
        # Near-duplicates add nothing and would bias matching toward one pose/lighting.
        if existing and max(float(np.dot(e, embedding)) for e in existing) > c.adaptive_novelty_max_similarity:
            return None
        path = self.dir / str(person_id) / f"auto_{uuid.uuid4().hex}.jpg"
        save_jpeg(path, crop)
        face_id = self.db.add_face_embedding(person_id, embedding, str(path), source="auto")
        self.gallery.load(self.db.all_face_embeddings())
        self.db.log_event("face_auto_enrolled", actor="vision", person_id=person_id, face_id=face_id,
                          score=round(score, 3), quality=round(quality, 3))
        log.info("Adaptive learning: capture added for person #%d", person_id)
        return face_id


class PreviewWriter:
    """Write the annotated frame to /run/jarvis (tmpfs); the API streams it as MJPEG.

    Attributes:
        path: Output JPEG path.
        width: Maximum output width (frames are downscaled, never upscaled).
        interval: Minimum seconds between two writes.
    """

    def __init__(self, path: str, width: int, fps: float):
        """Initialize the writer.

        Args:
            path: Output JPEG path.
            width: Maximum output width in pixels.
            fps: Maximum preview frame rate.
        """
        self.path, self.width = Path(path), width
        self.interval = 1 / fps
        self._last = 0.0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def maybe_write(self, frame: np.ndarray, tracks: list[Track], labels: dict[int, str],
                    target_id: int | None) -> None:
        """Annotate and write the frame if the preview rate allows it.

        Boxes are green for recognized persons, red for unknown faces and cyan
        for unresolved tracks; the PTZ target gets a thicker border.

        Args:
            frame: BGR frame (not modified).
            tracks: Current tracks.
            labels: Display label per track ID (``"?"``, ``"unknown"`` or a name).
            target_id: ID of the PTZ target, if any.
        """
        import cv2

        now = time.monotonic()
        if now - self._last < self.interval:
            return
        self._last = now
        img = frame.copy()
        for t in tracks:
            label = labels.get(t.id, "?")
            if t.cls != 0:                       # vehicle: orange, labeled with its plate
                color = (0, 140, 255)
            else:
                color = (0, 200, 0) if label not in ("?", "unknown") else ((0, 0, 230) if label == "unknown" else (200, 200, 0))
            x1, y1, x2, y2 = (int(v) for v in t.box)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 3 if t.id == target_id else 1)
            cv2.putText(img, f"#{t.id} {label}", (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(img, datetime.now().strftime("%H:%M:%S"), (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (255, 255, 255), 2)
        scale = self.width / img.shape[1]
        if scale < 1:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(buf.tobytes())
            os.replace(tmp, self.path)  # atomic swap: readers never see a partial JPEG


class VisionPipeline(threading.Thread):
    """Main vision thread tying detection, tracking, PTZ and face recognition together.

    Attributes:
        s: Application settings (re-read every cycle, so tunables apply live).
        resolver: Per-track identity voting.
        unknowns: Unknown face store.
        sightings: Sighting recorder.
        adaptive: Adaptive enroller.
        preview: MJPEG preview writer.
        target_id: Current PTZ target track ID.
        fps: Exponentially smoothed processing rate.
        active_tracks: Number of tracks in the last frame.
        heartbeat: Monotonic time of the last cycle start (systemd watchdog).
    """

    def __init__(self, settings: Settings, camera: RtspCamera, detector: PersonDetector,
                 faces: FaceEngine, gallery: FaceGallery, tracker: PTZTracker, db: Database,
                 events: queue.Queue):
        """Wire the pipeline components (thread not started).

        Args:
            settings: Application settings.
            camera: Frame source.
            detector: Person detector/tracker.
            faces: Face engine.
            gallery: Known face gallery.
            tracker: PTZ tracker.
            db: Database handle.
            events: Queue receiving ``FaceRecognized`` / ``UnknownFaceSeen`` events.
        """
        super().__init__(name="vision", daemon=True)
        self.s = settings
        self.camera, self.detector, self.faces, self.gallery = camera, detector, faces, gallery
        self.tracker, self.db, self.events = tracker, db, events
        self.resolver = IdentityResolver(settings.faces)
        self.unknowns = UnknownStore(settings, db)
        self.sightings = SightingRecorder(settings, db)
        self.adaptive = AdaptiveEnroller(settings, db, gallery)
        self.recorder = None  # optional jarvis.vision.recorder.Recorder, set by the core
        self.plates = None    # optional jarvis.vision.plates.PlateReader, set by the core
        self.active_vehicles = 0
        self.preview = PreviewWriter(settings.vision.frame_path, settings.vision.preview_width,
                                     settings.vision.preview_fps)
        self._halt = threading.Event()
        self._reset_requested = threading.Event()
        self._names: dict[int, str] = {}
        self._last_seen: dict[int, float] = {}
        self.target_id: int | None = None
        self.fps = 0.0
        self.active_tracks = 0
        self.heartbeat = time.monotonic()  # updated every cycle (systemd watchdog)

    def request_reset(self) -> None:
        """Ask the vision thread to drop identity state and reload caches on its next frame.

        Thread-safe; called after the gallery or the person list changed.
        """
        self._reset_requested.set()

    def stop(self) -> None:
        """Request thread shutdown."""
        self._halt.set()

    def run(self) -> None:
        """Main loop: fetch the latest frame, process it, throttle to ``process_fps``."""
        seq = 0
        while not self._halt.is_set():
            period = 1 / self.s.vision.process_fps  # re-read every cycle: tunable at runtime
            t0 = self.heartbeat = time.monotonic()
            seq, frame = self.camera.latest(seq, timeout=1.0)
            if frame is None:
                # No frame: still let the tracker stop the PTZ / return home.
                self.tracker.update(None, 1, 1)
                continue
            try:
                self._process(frame)
            except Exception:
                log.exception("Vision pipeline error")
                time.sleep(1)
            dt = time.monotonic() - t0
            self.fps = 0.9 * self.fps + 0.1 * (1 / max(dt, 1e-3))
            if dt < period:
                self._halt.wait(period - dt)

    def _process(self, frame: np.ndarray) -> None:
        """Run one full vision cycle on a frame.

        Args:
            frame: BGR frame.
        """
        if self._reset_requested.is_set():
            self._reset_requested.clear()
            self.resolver.reset()
            self.unknowns.reload()
            self._names = {p["id"]: p["first_name"] for p in self.db.list_persons()}
        h, w = frame.shape[:2]
        detections = self.detector.track(frame)
        # Persons drive PTZ tracking and face recognition; vehicles go to the plate reader.
        tracks = [t for t in detections if t.cls == 0]
        vehicles = [t for t in detections if t.cls != 0]
        self.active_vehicles = len(vehicles)
        now = time.monotonic()
        for t in tracks:
            self._last_seen[t.id] = now
        # Keep identity state through short occlusions; forget tracks gone for TRACK_GRACE_S.
        stale = {tid for tid, ts in self._last_seen.items() if now - ts > TRACK_GRACE_S}
        for tid in stale:
            del self._last_seen[tid]
        self.resolver.prune(set(self._last_seen))
        self.active_tracks = len(tracks)

        target = select_target(tracks, w, h, self.target_id)
        self.target_id = target.id if target else None
        self.tracker.update(target.box if target else None, w, h)
        if tracks and self.recorder is not None:
            self.recorder.trigger("person")  # only records when "person" is among recording.triggers

        # Recognition: target first, capped at max_faces_per_frame.
        if not self.tracker.moving:  # frames are blurry while the PTZ is moving
            ordered = sorted(tracks, key=lambda t: t.id != self.target_id)
            budget = self.s.faces.max_faces_per_frame
            for t in ordered:
                if budget == 0:
                    break
                if self.resolver.needs_check(t.id):
                    budget -= 1
                    self._recognize(frame, t)

        if vehicles:
            if self.recorder is not None:
                self.recorder.trigger("vehicle")  # only records when "vehicle" is among recording.triggers
            if self.plates is not None:
                for ev in self.plates.process(frame, vehicles):
                    self.events.put(ev)
        elif self.plates is not None:
            for ev in self.plates.process(frame, []):   # lets the tracker report the vehicles gone
                self.events.put(ev)

        labels = {}
        for t in tracks:
            lab = self.resolver.label(t.id)
            labels[t.id] = "unknown" if lab == "unknown" else (self._person_name(lab) if lab is not None else "?")
        for t in vehicles:
            st = self.plates.tracker.vehicles.get(t.id) if self.plates is not None else None
            labels[t.id] = (st.plate if st and st.plate else "vehicle") + (f" {st.direction}" if st and st.direction != "stationary" else "")
        self.preview.maybe_write(frame, tracks + vehicles, labels, self.target_id)

    def _person_name(self, person_id: int) -> str:
        """Return a person's first name (cached), or ``#<id>`` if the person no longer exists.

        Args:
            person_id: Person ID.

        Returns:
            Display name.
        """
        if person_id not in self._names:
            p = self.db.get_person(person_id)
            self._names[person_id] = p.first_name if p else f"#{person_id}"
        return self._names[person_id]

    def _recognize(self, frame: np.ndarray, t: Track) -> None:
        """Run face recognition on one track and act on the resulting decision.

        Quality is ``det_score * min(face_width / 100, 1) * min(sharpness / 100, 1)``.
        On a "known" decision a sighting is recorded, ``FaceRecognized`` is
        emitted and adaptive enrollment is attempted (not for watchlist
        persons). On an "unknown" decision the face is stored/clustered, a
        sighting is recorded and ``UnknownFaceSeen`` is emitted.

        Args:
            frame: BGR frame.
            t: Track to analyze.
        """
        import cv2

        h, w = frame.shape[:2]
        rx1, ry1, rx2, ry2 = face_region(t.box, w, h)
        region = frame[ry1:ry2, rx1:rx2]
        if region.shape[0] < 48 or region.shape[1] < 48:
            return
        face = self.faces.best_face(region)
        if face is None:
            return
        person_id, score = self.gallery.match(face.embedding)
        fx1, fy1, fx2, fy2 = face.bbox
        # 30% margin around the face for a more recognizable stored crop.
        mx, my = int((fx2 - fx1) * 0.3), int((fy2 - fy1) * 0.3)
        crop = region[max(0, fy1 - my):fy2 + my, max(0, fx1 - mx):fx2 + mx].copy()
        quality = face.det_score * min((fx2 - fx1) / 100, 1.0) * min(
            sharpness(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)) / 100, 1.0) if crop.size else 0.0
        decision = self.resolver.observe(t.id, person_id, score, face.embedding, crop, quality)
        if decision is None:
            return
        if decision.kind == "known":
            pid = decision.person_id
            name = self._person_name(pid)
            person = self.db.get_person(pid)
            status = "watchlist" if person and person.watchlist else "known"
            log.info("Track #%d recognized: %s (%.2f)", t.id, name, decision.score)
            sid = self.sightings.record(status, t.id, pid, decision.score, decision.quality, decision.embedding,
                                        decision.crop)
            self.events.put(FaceRecognized(t.id, pid, name, decision.score, sighting_id=sid))
            if self.recorder is not None:
                self.recorder.trigger(status, person_id=pid, sighting_id=sid)
            if status == "known":
                self.adaptive.consider(pid, decision.score, decision.quality, decision.embedding, decision.crop)
        else:
            unknown_id = cluster_id = None
            if decision.crop is not None and decision.crop.size:
                unknown_id, cluster_id = self.unknowns.save(decision.embedding, decision.crop, face.det_score)
            log.info("Track #%d: unknown face (stored: %s, cluster %s)", t.id, unknown_id, cluster_id)
            sid = self.sightings.record("unknown", t.id, None, decision.score, decision.quality, decision.embedding,
                                        decision.crop, unknown_id, cluster_id)
            self.events.put(UnknownFaceSeen(t.id, unknown_id, cluster_id=cluster_id, sighting_id=sid))
            if self.recorder is not None:
                self.recorder.trigger("unknown", sighting_id=sid)
