# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/vision/recorder.py
# Purpose : Event-triggered video clips, periodic time-lapse snapshots and FIFO storage rotation
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Event-triggered recording and time-lapse capture.

Design:

- :class:`Recorder` is a thread that samples the camera's latest frame at ``recording.fps``,
  independently of the (slower, variable) vision loop. Every sample is JPEG-encoded into a small
  ring buffer covering ``pre_seconds``: this is the pre-roll written at the start of each clip.
- :meth:`Recorder.trigger` (called by the vision pipeline on a detection or an identification)
  starts a clip, or extends the current one, until ``post_seconds`` after the last trigger or
  ``max_clip_seconds`` in total. Frames are piped to an FFmpeg subprocess that encodes browser-
  playable MP4 (H.264, yuv420p, faststart); the MJPEG input keeps the pipe light.
- Every ``timelapse.interval_s`` a down-scaled snapshot is stored under
  ``timelapse/YYYYMMDD/HHMMSS.jpg``; the file name *is* the timestamp, so listing a period needs no
  database.
- :func:`enforce_quota` implements the FIFO rotation (like the log rotation): the oldest files go
  first whenever the folder exceeds its size quota or files exceed the maximum age.
"""

from __future__ import annotations

import collections
import logging
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Callable

import numpy as np

from jarvis.config.settings import RecordingConfig, TimelapseConfig

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------------- image helpers

def encode_jpeg(frame: np.ndarray, quality: int = 80, width: int | None = None) -> bytes:
    """Encode a BGR frame as JPEG, optionally down-scaled to ``width`` pixels.

    Uses OpenCV when available (production) and falls back to Pillow (tests, tooling).
    """
    try:
        import cv2

        if width and frame.shape[1] > width:
            scale = width / frame.shape[1]
            frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise ValueError("JPEG encoding failed")
        return buf.tobytes()
    except ImportError:
        import io

        from PIL import Image

        img = Image.fromarray(frame[:, :, ::-1])  # BGR -> RGB
        if width and img.width > width:
            img = img.resize((width, round(img.height * width / img.width)))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality)
        return out.getvalue()


# --------------------------------------------------------------------------------- FIFO rotation

def enforce_quota(root: Path, max_bytes: int, max_age_s: float, pattern: str = "**/*",
                  on_delete: Callable[[Path], None] | None = None) -> list[Path]:
    """Delete the oldest files under ``root`` until the size quota and the age limit are met.

    Args:
        root: Folder to police (recursively).
        max_bytes: Size quota; ``0`` disables the size limit.
        max_age_s: Maximum file age in seconds; ``0`` disables the age limit.
        pattern: Glob selecting the managed files.
        on_delete: Callback run for each deleted file (e.g. to delete its database row).

    Returns:
        The deleted paths, oldest first.
    """
    if not root.exists():
        return []
    files = sorted((p for p in root.glob(pattern) if p.is_file()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    now = time.time()
    deleted = []
    for p in files:
        too_old = max_age_s and now - p.stat().st_mtime > max_age_s
        too_big = max_bytes and total > max_bytes
        if not (too_old or too_big):
            break  # files are sorted by age: everything after is newer and the quota is met
        size = p.stat().st_size
        p.unlink(missing_ok=True)
        total -= size
        deleted.append(p)
        if on_delete:
            on_delete(p)
    for d in sorted((d for d in root.glob("**/*") if d.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()  # drop the empty day folders left behind
    return deleted


# --------------------------------------------------------------------------------- time-lapse index

def timelapse_frames(root: Path, start_ts: float | None, end_ts: float | None, time_from: str | None,
                     time_to: str | None, step: int = 1, limit: int = 5000) -> list[tuple[float, Path]]:
    """List time-lapse snapshots of a period and a daily time window, oldest first.

    Args:
        root: Time-lapse root folder (``<data>/timelapse``).
        start_ts: First instant (inclusive), or None.
        end_ts: Last instant (exclusive), or None.
        time_from: Start of the daily window ``HH:MM`` (local time), or None.
        time_to: End of the daily window; the window may wrap past midnight (22:00 -> 06:00).
        step: Keep one frame out of ``step`` (sub-sampling for long periods).
        limit: Maximum number of frames returned (evenly sub-sampled when exceeded).

    Returns:
        ``(timestamp, path)`` tuples.
    """
    t0 = dtime.fromisoformat(time_from) if time_from else None
    t1 = dtime.fromisoformat(time_to) if time_to else None
    out = []
    if not root.exists():
        return out
    for day in sorted(root.iterdir()):
        if not (day.is_dir() and len(day.name) == 8 and day.name.isdigit()):
            continue
        day_start = datetime.strptime(day.name, "%Y%m%d").timestamp()
        if (end_ts and day_start >= end_ts) or (start_ts and day_start + 86400 <= start_ts):
            continue
        for f in sorted(day.glob("*.jpg")):
            try:
                dt = datetime.strptime(day.name + f.stem, "%Y%m%d%H%M%S")
            except ValueError:
                continue
            ts = dt.timestamp()
            if (start_ts and ts < start_ts) or (end_ts and ts >= end_ts):
                continue
            if t0 or t1:
                now = dt.time()
                a, b = t0 or dtime(0, 0), t1 or dtime(23, 59, 59)
                inside = a <= now <= b if a <= b else (now >= a or now <= b)
                if not inside:
                    continue
            out.append((ts, f))
    out = out[::max(1, step)]
    if len(out) > limit:
        stride = len(out) / limit
        out = [out[int(i * stride)] for i in range(limit)]
    return out


# --------------------------------------------------------------------------------- recorder

@dataclass
class Clip:
    """A clip being written."""

    started: float
    path: Path
    thumb: Path
    proc: subprocess.Popen
    last_trigger: float
    frames: int = 0
    triggers: set[str] = field(default_factory=set)
    persons: set[int] = field(default_factory=set)
    sighting_ids: list[int] = field(default_factory=list)


class Recorder(threading.Thread):
    """Samples the camera, keeps the pre-roll buffer, writes clips and time-lapse snapshots.

    Attributes:
        clip: The clip being recorded, or None.
    """

    def __init__(self, rec: RecordingConfig, tl: TimelapseConfig, data_dir: Path, latest_frame, db,
                 clock=time.time):
        """Initialize the recorder thread (not started).

        Args:
            rec: Clip recording settings (read live: hot-reloadable).
            tl: Time-lapse settings (read live).
            data_dir: Data root; clips go to ``recordings/``, snapshots to ``timelapse/``.
            latest_frame: ``callable(after_seq, timeout) -> (seq, frame)`` (``RtspCamera.latest``).
            db: :class:`jarvis.storage.database.Database` (clip index).
            clock: Wall clock, injectable for tests.
        """
        super().__init__(name="recorder", daemon=True)
        self.rec, self.tl = rec, tl
        self.rec_dir, self.tl_dir = Path(data_dir) / "recordings", Path(data_dir) / "timelapse"
        self.latest_frame, self.db, self._clock = latest_frame, db, clock
        self._ring: collections.deque[tuple[float, bytes]] = collections.deque()
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._last_snapshot = 0.0
        self._last_quota = 0.0
        self.clip: Clip | None = None

    # --- public API (thread-safe) -------------------------------------------------------------
    def trigger(self, reason: str, person_id: int | None = None, sighting_id: int | None = None) -> None:
        """Start or extend a clip because of ``reason`` (``person``, ``known``, ``unknown``, ``watchlist``)."""
        if not self.rec.enabled or reason not in self.rec.triggers or self._halt.is_set():
            return  # also ignore late triggers while the core is shutting down
        with self._lock:
            now = self._clock()
            if self.clip is None:
                self.clip = self._open_clip(now)
                if self.clip is None:
                    return
            self.clip.last_trigger = now
            self.clip.triggers.add(reason)
            if person_id is not None:
                self.clip.persons.add(person_id)
            if sighting_id is not None:
                self.clip.sighting_ids.append(sighting_id)

    def stop(self) -> None:
        self._halt.set()

    # --- thread loop -------------------------------------------------------------------------------
    def run(self) -> None:
        seq = 0
        while not self._halt.is_set():
            period = 1 / max(self.rec.fps, 1)
            t0 = time.monotonic()
            seq, frame = self.latest_frame(seq, timeout=1.0)
            if frame is not None:
                try:
                    self._on_frame(frame)
                except Exception:
                    log.exception("Recorder error")
            if self._clock() - self._last_quota > 60:
                self._last_quota = self._clock()
                self.rotate()
            self._halt.wait(max(0.0, period - (time.monotonic() - t0)))
        with self._lock:
            if self.clip:
                self._close_clip(self.clip)
                self.clip = None

    def _on_frame(self, frame: np.ndarray) -> None:
        now = self._clock()
        if self.tl.enabled and now - self._last_snapshot >= self.tl.interval_s:
            self._last_snapshot = now
            self._snapshot(frame, now)
        if not self.rec.enabled:
            self._ring.clear()
            return
        jpg = encode_jpeg(frame, self.rec.jpeg_quality, self.rec.width)
        with self._lock:
            clip = self.clip
            if clip is None:  # keep only the pre-roll window
                self._ring.append((now, jpg))
                while self._ring and now - self._ring[0][0] > self.rec.pre_seconds:
                    self._ring.popleft()
                return
            try:
                clip.proc.stdin.write(jpg)
                clip.frames += 1
            except (BrokenPipeError, OSError):
                log.warning("FFmpeg pipe closed, clip aborted")
                self._close_clip(clip)
                self.clip = None
                return
            idle = now - clip.last_trigger > self.rec.post_seconds
            too_long = now - clip.started > self.rec.max_clip_seconds
            if idle or too_long:
                self._close_clip(clip)
                self.clip = None

    # --- clips -----------------------------------------------------------------------------------------
    def _open_clip(self, now: float) -> Clip | None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            log.error("ffmpeg not found: recording disabled")
            return None
        day = self.rec_dir / datetime.fromtimestamp(now).strftime("%Y%m%d")
        day.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.fromtimestamp(now):%H%M%S}-{uuid.uuid4().hex[:6]}"
        path, thumb = day / f"{stem}.mp4", day / f"{stem}.jpg"
        cmd = [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-f", "image2pipe", "-c:v", "mjpeg",
               "-framerate", str(self.rec.fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", str(self.rec.crf), "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
               "-movflags", "+faststart", str(path)]
        # Own session: a SIGTERM/SIGINT aimed at the service's process group must not kill the encoder;
        # the recorder closes its stdin on shutdown so that FFmpeg writes a valid MP4 trailer.
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                start_new_session=True)
        pre = list(self._ring)
        self._ring.clear()
        started = pre[0][0] if pre else now
        for _, jpg in pre:
            proc.stdin.write(jpg)
        frames = len(pre)
        if pre:
            thumb.write_bytes(pre[-1][1])  # frame closest to the trigger
        log.info("Recording started: %s (%d pre-roll frames)", path.name, len(pre))
        return Clip(started, path, thumb, proc, now, frames=frames)

    def _close_clip(self, clip: Clip) -> None:
        try:
            clip.proc.stdin.close()
        except OSError:
            pass
        try:
            clip.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            clip.proc.kill()
        end = self._clock()
        if clip.frames < max(2, int(self.rec.fps)):  # less than ~1 s of footage: not worth keeping
            clip.path.unlink(missing_ok=True)
            clip.thumb.unlink(missing_ok=True)
            log.info("Recording discarded (too short): %s", clip.path.name)
            return
        if clip.proc.returncode != 0 or not clip.path.exists():
            err = clip.proc.stderr.read().decode(errors="replace")[-300:] if clip.proc.stderr else ""
            log.error("FFmpeg failed for %s: %s", clip.path.name, err)
            clip.path.unlink(missing_ok=True)
            clip.thumb.unlink(missing_ok=True)
            return
        rid = self.db.add_recording(clip.started, end, str(clip.path),
                                    str(clip.thumb) if clip.thumb.exists() else None, clip.path.stat().st_size,
                                    sorted(clip.triggers), sorted(clip.persons), clip.sighting_ids)
        log.info("Recording saved: %s (%.0f s, #%d)", clip.path.name, end - clip.started, rid)

    # --- time-lapse --------------------------------------------------------------------------------------
    def _snapshot(self, frame: np.ndarray, now: float) -> None:
        dt = datetime.fromtimestamp(now)
        day = self.tl_dir / dt.strftime("%Y%m%d")
        day.mkdir(parents=True, exist_ok=True)
        (day / f"{dt:%H%M%S}.jpg").write_bytes(encode_jpeg(frame, 75, self.tl.width))

    # --- rotation -----------------------------------------------------------------------------------------
    def rotate(self) -> None:
        """FIFO rotation of clips (with their index rows) and of time-lapse snapshots."""
        def drop_row(p: Path) -> None:
            if p.suffix == ".mp4":
                thumb = self.db.delete_recording_by_path(str(p))
                if thumb:
                    Path(thumb).unlink(missing_ok=True)

        removed = enforce_quota(self.rec_dir, int(self.rec.max_total_gb * 1024 ** 3), self.rec.max_age_days * 86400,
                                "**/*.mp4", drop_row)
        removed += enforce_quota(self.tl_dir, int(self.tl.max_total_gb * 1024 ** 3), self.tl.max_age_days * 86400,
                                 "**/*.jpg")
        if removed:
            log.info("Recording rotation: %d file(s) removed", len(removed))
