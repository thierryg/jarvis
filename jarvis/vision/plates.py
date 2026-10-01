# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/vision/plates.py
# Purpose : License plate reading (fast-alpr) and vehicle direction tracking for the pipeline
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Automatic number plate recognition (ANPR) for vehicles tracked by the person detector.

Three layers:

- :class:`PlateEngine`: thin wrapper around ``fast_alpr.ALPR`` (YOLOv9 plate detector + CCT
  international OCR, ONNX Runtime on the CPU, fully offline once the models are cached by
  ``jarvis setup-models`` under ``~/.cache`` of the ``jarvis`` account). Loaded lazily, on the
  first vehicle, so a Jarvis without plate recognition pays nothing.
- :class:`VehicleTracker`: pure logic (no model, unit-tested): per-track box history to
  estimate the direction (the box grows while the vehicle approaches the camera and shrinks
  while it leaves), read voting (a plate is confirmed after ``votes_required`` identical
  normalized reads with enough confidence) and track end detection. It turns observations into
  :class:`~jarvis.core.events.VehicleEvent` objects.
- :class:`PlateReader`: glue used by the vision pipeline: chooses at most one vehicle to read per
  cycle (the largest one due for a read), crops it, runs the engine, saves the vehicle snapshot
  of the confirming read and feeds the tracker.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from jarvis.config.settings import PlatesConfig
from jarvis.core.events import VehicleEvent
from jarvis.core.plates import mean_confidence, normalize_plate

log = logging.getLogger(__name__)

# A vehicle missing for this long is considered gone (short occlusions are tolerated).
GONE_AFTER_S = 3.0


class PlateEngine:
    """Lazy fast-alpr instance (plate detection + OCR)."""

    def __init__(self, cfg: PlatesConfig):
        self.cfg = cfg
        self._alpr = None

    def _load(self):
        from fast_alpr import ALPR

        log.info("Loading plate models: %s + %s", self.cfg.detector_model, self.cfg.ocr_model)
        self._alpr = ALPR(detector_model=self.cfg.detector_model, ocr_model=self.cfg.ocr_model,
                          detector_providers=["CPUExecutionProvider"], ocr_device="cpu")
        return self._alpr

    def read(self, image: np.ndarray) -> list[tuple[str, float, str | None]]:
        """Plates found in a BGR image: ``[(normalized text, mean confidence, region), ...]``."""
        alpr = self._alpr or self._load()
        out = []
        for r in alpr.predict(image):
            if r.ocr is None or not r.ocr.text:
                continue
            text = normalize_plate(r.ocr.text)
            if text:
                out.append((text, mean_confidence(r.ocr.confidence, len(r.ocr.text)), r.ocr.region))
        return out


@dataclass
class _VehicleState:
    """What is known about one tracked vehicle."""

    first_seen: float
    last_seen: float
    history: deque = field(default_factory=lambda: deque(maxlen=64))    # (ts, box area)
    votes: Counter = field(default_factory=Counter)                      # normalized plate -> reads
    best: dict = field(default_factory=dict)                             # plate -> (confidence, region)
    plate: str | None = None                                             # confirmed plate
    direction: str = "stationary"
    announced: set = field(default_factory=set)                          # directions already emitted
    last_read: float = 0.0
    image_path: str | None = None


class VehicleTracker:
    """Pure vehicle logic: direction, read voting, track end (see the module docstring)."""

    def __init__(self, cfg: PlatesConfig):
        self.cfg = cfg
        self.vehicles: dict[int, _VehicleState] = {}

    def observe(self, boxes: dict[int, tuple[float, float, float, float]], now: float) -> list[VehicleEvent]:
        """Update the tracks with the current vehicle boxes.

        Returns:
            ``approaching`` / ``leaving`` events the first time a direction is established, and
            ``gone`` events for vehicles not seen for :data:`GONE_AFTER_S`.
        """
        events = []
        for tid, (x1, y1, x2, y2) in boxes.items():
            st = self.vehicles.setdefault(tid, _VehicleState(first_seen=now, last_seen=now))
            st.last_seen = now
            st.history.append((now, max(0.0, (x2 - x1) * (y2 - y1))))
            direction = self._direction(st, now)
            if direction != st.direction:
                st.direction = direction
                if direction != "stationary" and direction not in st.announced:
                    st.announced.add(direction)
                    events.append(VehicleEvent(direction, tid, st.plate, direction=direction, ts=now))
        for tid in [t for t, st in self.vehicles.items() if now - st.last_seen > GONE_AFTER_S]:
            st = self.vehicles.pop(tid)
            events.append(VehicleEvent("gone", tid, st.plate, direction=st.direction, ts=now))
        return events

    def _direction(self, st: _VehicleState, now: float) -> str:
        """``approaching`` if the box area grew by ``approach_growth`` over the motion window,
        ``leaving`` if it shrank to ``leave_shrink``, otherwise the previous direction."""
        recent = [a for ts, a in st.history if now - ts <= self.cfg.motion_window_s]
        if len(recent) < 3 or recent[0] <= 0:
            return st.direction
        ratio = recent[-1] / recent[0]
        if ratio >= self.cfg.approach_growth:
            return "approaching"
        if ratio <= self.cfg.leave_shrink:
            return "leaving"
        return st.direction

    def due_for_read(self, tid: int, now: float) -> bool:
        """True when the vehicle has no confirmed plate and its read interval has elapsed.

        A confirmed plate is read again only after 5 read intervals: the same vehicle seen
        leaving shows its rear plate, which confirms it is still the same one.
        """
        st = self.vehicles.get(tid)
        if st is None:
            return False
        interval = self.cfg.read_interval_s * (5 if st.plate else 1)
        return now - st.last_read >= interval

    def add_reads(self, tid: int, reads: list[tuple[str, float, str | None]], now: float,
                  image_path: str | None = None) -> VehicleEvent | None:
        """Account the plates read on a vehicle; returns a ``plate`` event when one gets confirmed."""
        st = self.vehicles.get(tid)
        if st is None:
            return None
        st.last_read = now
        for text, conf, region in reads:
            if conf < self.cfg.min_confidence:
                continue
            st.votes[text] += 1
            if conf >= st.best.get(text, (0.0, None))[0]:
                st.best[text] = (conf, region)
        if st.plate or not st.votes:
            return None
        text, n = st.votes.most_common(1)[0]
        if n < self.cfg.votes_required:
            return None
        st.plate, st.image_path = text, image_path
        conf, region = st.best[text]
        return VehicleEvent("plate", tid, text, confidence=round(conf, 3), region=region, direction=st.direction,
                            image_path=image_path, ts=now)


class PlateReader:
    """Pipeline glue: one plate read per cycle at most, snapshots, events."""

    def __init__(self, cfg: PlatesConfig, plates_dir: Path, engine: PlateEngine | None = None, clock=time.time):
        self.cfg = cfg
        self.dir = plates_dir
        self.engine = engine or PlateEngine(cfg)
        self.tracker = VehicleTracker(cfg)
        self._clock = clock

    def process(self, frame: np.ndarray, vehicles: list) -> list[VehicleEvent]:
        """Handle the vehicle tracks of one frame (``Track`` objects with ``id`` and ``box``)."""
        now = self._clock()
        events = self.tracker.observe({t.id: t.box for t in vehicles}, now)
        due = [t for t in vehicles if t.box[2] - t.box[0] >= self.cfg.min_vehicle_px
               and self.tracker.due_for_read(t.id, now)]
        if due:
            t = max(due, key=lambda v: (v.box[2] - v.box[0]) * (v.box[3] - v.box[1]))
            crop = self._crop(frame, t.box)
            try:
                reads = self.engine.read(crop)
            except Exception:
                log.exception("Plate reading failed")
                reads = []
            path = self._save(crop) if reads and self.cfg.save_images else None
            ev = self.tracker.add_reads(t.id, reads, now, path)
            if ev is not None:
                log.info("Plate %s confirmed on vehicle #%d (confidence %.2f, region %s, %s)",
                         ev.plate, ev.track_id, ev.confidence, ev.region or "?", ev.direction)
                events.append(ev)
            elif path:
                Path(path).unlink(missing_ok=True)       # only the confirming read keeps its snapshot
        return events

    @staticmethod
    def _crop(frame: np.ndarray, box) -> np.ndarray:
        """Vehicle box with a 5 % margin, clipped to the frame."""
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = box
        mx, my = 0.05 * (x2 - x1), 0.05 * (y2 - y1)
        return frame[int(max(0, y1 - my)):int(min(h, y2 + my)), int(max(0, x1 - mx)):int(min(w, x2 + mx))]

    def _save(self, crop: np.ndarray) -> str | None:
        """JPEG snapshot under ``plates/YYYYMMDD/``; returns its path (``None`` on failure)."""
        import cv2

        day = self.dir / datetime.now().strftime("%Y%m%d")
        try:
            day.mkdir(parents=True, exist_ok=True)
            path = day / f"{datetime.now().strftime('%H%M%S_%f')}.jpg"
            if cv2.imwrite(str(path), crop, [cv2.IMWRITE_JPEG_QUALITY, 90]):
                return str(path)
        except OSError:
            log.exception("Cannot save the plate snapshot")
        return None
