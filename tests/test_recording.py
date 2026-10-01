# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_recording.py
# Purpose : Event-triggered clips, time-lapse and FIFO storage rotation
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Recorder (pre-roll, post-roll, FFmpeg MP4), FIFO quota, time-lapse listing and the related API."""

import os
import shutil
import time
from datetime import datetime

import numpy as np
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from jarvis.config.settings import RecordingConfig, Settings, TimelapseConfig
from jarvis.storage.database import Database
from jarvis.vision.recorder import Recorder, enforce_quota, timelapse_frames
from jarvis.web.app import create_app

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
H = {"X-Jarvis": "1"}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def frame(i):
    f = np.zeros((120, 160, 3), np.uint8)
    f[:, : (i * 7) % 160] = (30, 120, 200)
    return f


def test_clip_has_preroll_and_closes_after_postroll(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init()
    clock = Clock(1_790_000_000.0)
    rec = Recorder(RecordingConfig(pre_seconds=2, post_seconds=3, fps=5, triggers=["unknown", "known"]),
                   TimelapseConfig(enabled=False), tmp_path, lambda *a, **k: (0, None), db, clock=clock)
    for i in range(20):                      # 4 s of history at 5 fps: only 2 s are kept as pre-roll
        clock.t += 0.2
        rec._on_frame(frame(i))
    assert len(rec._ring) <= 11
    rec.trigger("person")                    # not a configured trigger: ignored
    assert rec.clip is None
    rec.trigger("known", person_id=7, sighting_id=42)
    assert rec.clip is not None
    for i in range(30):                      # 6 s: closes 3 s after the last trigger
        clock.t += 0.2
        rec._on_frame(frame(100 + i))
    assert rec.clip is None
    rows = db.list_recordings()
    assert len(rows) == 1 and rows[0]["triggers"] == ["known"] and rows[0]["person_ids"] == [7]
    assert rows[0]["sighting_ids"] == [42] and os.path.getsize(rows[0]["path"]) > 0
    assert rows[0]["thumb_path"] and os.path.exists(rows[0]["thumb_path"])
    assert 4.5 <= rows[0]["duration_s"] <= 6


def test_max_clip_length_splits(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init()
    clock = Clock(1_790_000_000.0)
    rec = Recorder(RecordingConfig(pre_seconds=0, post_seconds=100, max_clip_seconds=10, fps=5), TimelapseConfig(enabled=False),
                   tmp_path, lambda *a, **k: (0, None), db, clock=clock)
    rec.trigger("unknown")
    for i in range(60):
        clock.t += 0.2
        rec._on_frame(frame(i))
        rec.trigger("unknown")
    assert len(db.list_recordings()) >= 1


def test_fifo_quota_and_age(tmp_path):
    root = tmp_path / "rec" / "20260930"
    root.mkdir(parents=True)
    now = time.time()
    for i in range(5):
        f = root / f"{i}.mp4"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (now - 1000 + i, now - 1000 + i))
    dropped = []
    removed = enforce_quota(tmp_path / "rec", max_bytes=2500, max_age_s=0, pattern="**/*.mp4", on_delete=dropped.append)
    assert [p.name for p in removed] == ["0.mp4", "1.mp4", "2.mp4"] and len(dropped) == 3   # oldest first
    os.utime(root / "4.mp4", (now - 90000, now - 90000))
    assert [p.name for p in enforce_quota(tmp_path / "rec", 0, 86400, "**/*.mp4")] == ["4.mp4"]


def make_snapshots(root, day, times):
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    for hms in times:
        (d / f"{hms}.jpg").write_bytes(b"jpg")


def test_timelapse_listing_window_and_overnight(tmp_path):
    root = tmp_path / "timelapse"
    make_snapshots(root, "20260928", ["060000", "080000", "120000", "230000"])
    make_snapshots(root, "20260929", ["010000", "090000"])
    start = datetime(2026, 9, 28).timestamp()
    end = datetime(2026, 9, 30).timestamp()
    hours = lambda fr: [datetime.fromtimestamp(ts).hour for ts, _ in fr]
    assert hours(timelapse_frames(root, start, end, "07:00", "12:00")) == [8, 12, 9]
    assert hours(timelapse_frames(root, start, end, "22:00", "02:00")) == [23, 1]
    assert len(timelapse_frames(root, start, end, None, None, limit=3)) == 3


@pytest.fixture
def api(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("admin", PasswordHasher().hash("correct horse battery"))
    c = TestClient(create_app(s, db, type("Core", (), {"call": lambda self, cmd, **k: {"ok": True}})()))
    c.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=H)
    return c, s, db, tmp_path


def test_api_recordings_range_and_timelapse_export(api):
    c, s, db, tmp = api
    clock = Clock(datetime(2026, 9, 29, 8, 30).timestamp())
    rec = Recorder(RecordingConfig(pre_seconds=1, post_seconds=1, fps=5), TimelapseConfig(interval_s=1, width=160),
                   tmp, lambda *a, **k: (0, None), db, clock=clock)
    rec.trigger("unknown")
    for i in range(20):
        clock.t += 0.2
        rec._on_frame(frame(i))
    rows = c.get("/api/recordings", params={"time_from": "08:00", "time_to": "09:00"}).json()
    assert len(rows) == 1
    assert c.get("/api/recordings", params={"time_from": "10:00", "time_to": "11:00"}).json() == []
    r = c.get(f"/api/recordings/{rows[0]['id']}/video", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers["content-type"] == "video/mp4"
    tl = c.get("/api/timelapse", params={"start": "2026-09-29", "end": "2026-09-29"}).json()
    assert len(tl["frames"]) >= 3
    assert c.get("/api/media", params={"path": tl["frames"][0]["path"]}).status_code == 200
    mp4 = c.get("/api/timelapse.mp4", params={"start": "2026-09-29", "end": "2026-09-29", "fps": 10})
    assert mp4.status_code == 200 and mp4.content[4:8] == b"ftyp"
    assert db.list_events(type_="timelapse_exported")
    assert c.delete(f"/api/recordings/{rows[0]['id']}", headers=H).status_code == 200
    assert not os.path.exists(rows[0]["path"]) and db.list_recordings() == []
