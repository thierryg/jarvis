# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_camera_logs.py
# Purpose : Camera stream settings and probe, log search, live tail and secret masking
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""camera.rtsp_url is editable but never carries credentials; the ffprobe-based stream test
reports codec/resolution/rate and Jarvis advice without leaking the password; the log viewer
parses multi-line entries, filters by text/regex/level/process/date, reads rotated files,
follows rotation and masks secrets."""

import asyncio
import gzip
import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from jarvis.config.catalog import check_rtsp_url
from jarvis.config.settings import Settings
from jarvis.storage.database import Database
from jarvis.web import logview, probe
from jarvis.web.app import create_app

H = {"X-Jarvis": "1"}
PW = "correct horse battery"


@pytest.fixture
def api(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    s.logging.file_path = str(tmp_path / "jarvis.log")
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("root", PasswordHasher().hash(PW))
    c = TestClient(create_app(s, db, type("Core", (), {"call": lambda self, cmd, **k: {"ok": True}})()))
    assert c.post("/api/login", json={"username": "root", "password": PW}, headers=H).status_code == 200
    return c, s, db, tmp_path


# --- camera stream setting -----------------------------------------------------------------
def test_rtsp_url_validation():
    assert check_rtsp_url("rtsp://192.168.50.64:554/Streaming/Channels/102")
    assert check_rtsp_url("rtsps://cam.lan/h264Preview_01_sub")
    assert check_rtsp_url("rtsp://[fe80::1]:554/s")
    for bad in ("http://cam/x", "rtsp://user:pw@cam/x", "rtsp://cam:99999/x", "rtsp:// cam", ""):
        with pytest.raises(ValueError):
            check_rtsp_url(bad)


def test_rtsp_url_setting_refuses_credentials(api):
    c, s, db, _ = api
    r = c.put("/api/settings", json={"changes": {"camera.rtsp_url": "rtsp://admin:S3cr3t@10.0.0.9/live"}}, headers=H)
    assert r.status_code in (400, 422) and "Secrets" in r.text and "S3cr3t" not in r.text
    r = c.put("/api/settings", json={"changes": {"camera.rtsp_url": "rtsp://10.0.0.9:554/live",
                                                 "ptz.host": "10.0.0.9", "ptz.port": 8000}}, headers=H)
    assert r.status_code == 200 and "camera.rtsp_url" in r.json()["restart_required"]
    groups = c.get("/api/settings").json()["groups"]
    assert groups[0]["name"] == "Camera stream"
    url = next(p for p in groups[0]["params"] if p["key"] == "camera.rtsp_url")
    assert url["value"] == "rtsp://10.0.0.9:554/live" and url["extra"]["placeholder"].startswith("rtsp://")


# --- stream probe --------------------------------------------------------------------------
def test_probe_advice_and_masking():
    assert probe.advice(None) == ["No video stream found at this URL."]
    assert probe.advice({"codec": "h264", "width": 1920, "height": 1080, "fps": 12.5}) == []
    w = " ".join(probe.advice({"codec": "hevc", "width": 640, "height": 360, "fps": 30}))
    assert "H.265" in w and "640 px" in w and "30 fps" in w
    assert probe.mask_credentials("rtsp://jarvis:p%40ss@10.0.0.9/x: 401") == "rtsp://***@10.0.0.9/x: 401"


def fake_ffprobe(tmp_path, monkeypatch, body: str, code: int = 0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "ffprobe"
    exe.write_text(f"#!/bin/sh\n{body}\nexit {code}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_probe_parses_ffprobe(tmp_path, monkeypatch):
    out = {"streams": [{"codec_type": "video", "codec_name": "h264", "profile": "Main", "width": 1920,
                        "height": 1080, "avg_frame_rate": "15/1"}, {"codec_type": "audio", "codec_name": "aac"}]}
    fake_ffprobe(tmp_path, monkeypatch, f"echo '{json.dumps(out)}'")
    r = asyncio.run(probe.probe("rtsp://u:p@cam/x"))
    assert r["ok"] and r["video"] == {"codec": "h264", "profile": "Main", "width": 1920, "height": 1080, "fps": 15.0}
    assert r["audio"] == {"codec": "aac"} and r["warnings"] == []


def test_probe_error_never_leaks_the_password(tmp_path, monkeypatch):
    fake_ffprobe(tmp_path, monkeypatch, "echo 'rtsp://jarvis:TopSecret@cam/x: Server returned 401 Unauthorized' >&2", 1)
    r = asyncio.run(probe.probe("rtsp://jarvis:TopSecret@cam/x"))
    assert not r["ok"] and "TopSecret" not in r["error"] and "Secrets" in r["error"]


def test_probe_endpoint_injects_secrets_and_audits_without_them(api, monkeypatch):
    c, s, db, _ = api
    seen = {}

    async def fake(url, timeout_s=15.0):
        seen["url"] = url
        return {"ok": True, "elapsed_s": 0.1, "video": {"codec": "h264", "width": 1920, "height": 1080, "fps": 10},
                "audio": None, "warnings": [], "error": None}

    monkeypatch.setattr(probe, "probe", fake)
    s.secrets.camera_username, s.secrets.camera_password = "jarvis", "TopSecret"
    r = c.post("/api/camera/probe", json={"url": "rtsp://10.0.0.9:554/live"}, headers=H)
    assert r.status_code == 200 and r.json()["url"] == "rtsp://10.0.0.9:554/live"
    assert seen["url"] == "rtsp://jarvis:TopSecret@10.0.0.9:554/live"          # injected for the probe only
    ev = db.list_events(type_="camera_probed")[0]
    assert "TopSecret" not in json.dumps(ev) and ev["details"]["ok"] is True
    assert c.post("/api/camera/probe", json={"url": "rtsp://a:b@10.0.0.9/x"}, headers=H).status_code == 422


# --- log viewer ------------------------------------------------------------------------------
LOG = """2026-09-30 10:00:00,001 INFO    [core] jarvis.core.service: Core started
2026-09-30 10:00:01,002 WARNING [core] jarvis.vision.camera: RTSP stream lost, reconnecting rtsp://jarvis:pw123@10.0.0.9/live
2026-09-30 10:00:02,003 ERROR   [api] jarvis.web.app: Unhandled error token=abcdef123456
Traceback (most recent call last):
  File "x.py", line 1, in <module>
ValueError: boom
2026-09-30 11:30:00,004 DEBUG   [core] jarvis.vision.faces: face score 0.71
"""


def test_parse_groups_tracebacks_and_masks(tmp_path):
    f = tmp_path / "jarvis.log"
    f.write_text(LOG)
    r = logview.search(f)
    assert [e["level"] for e in r["entries"]] == ["INFO", "WARNING", "ERROR", "DEBUG"]
    err = r["entries"][2]
    assert err["process"] == "api" and "ValueError: boom" in err["message"] and "Traceback" in err["message"]
    assert "pw123" not in r["entries"][1]["message"] and "rtsp://***@10.0.0.9" in r["entries"][1]["message"]
    assert "abcdef123456" not in err["message"] and "token=***" in err["message"]


def test_search_filters(tmp_path):
    f = tmp_path / "jarvis.log"
    f.write_text(LOG)
    assert [e["level"] for e in logview.search(f, level="WARNING")["entries"]] == ["WARNING", "ERROR"]
    assert len(logview.search(f, q="RTSP")["entries"]) == 1                          # case-insensitive
    assert len(logview.search(f, q=r"score 0\.\d+", regex=True)["entries"]) == 1
    assert [e["process"] for e in logview.search(f, process="api")["entries"]] == ["api"]
    start = datetime(2026, 9, 30, 11, 0).timestamp()
    assert [e["level"] for e in logview.search(f, start=start)["entries"]] == ["DEBUG"]
    r = logview.search(f, limit=2)
    assert len(r["entries"]) == 2 and r["total_matched"] == 4 and r["truncated"]
    with pytest.raises(ValueError):
        logview.compile_query("(", regex=True)


def test_search_reads_rotated_files_newest_last(tmp_path):
    f = tmp_path / "jarvis.log"
    f.write_text("2026-09-30 12:00:00,000 INFO    [core] jarvis.x: current\n")
    (tmp_path / "jarvis.log.1").write_text("2026-09-29 12:00:00,000 INFO    [core] jarvis.x: yesterday\n")
    with gzip.open(tmp_path / "jarvis.log.2.gz", "wt") as fh:
        fh.write("2026-09-28 12:00:00,000 INFO    [core] jarvis.x: two days ago\n")
    assert [e["message"] for e in logview.search(f)["entries"]] == ["current"]
    r = logview.search(f, history=True)
    assert [e["message"] for e in r["entries"]] == ["two days ago", "yesterday", "current"]
    assert r["files"] == ["jarvis.log", "jarvis.log.1", "jarvis.log.2.gz"]


def test_follower_appends_partial_lines_and_rotation(tmp_path):
    f = tmp_path / "jarvis.log"
    f.write_text("2026-09-30 10:00:00,000 INFO    [core] jarvis.x: old\n")
    fol = logview.Follower(f)                      # starts at the end: "old" is not replayed
    assert fol.poll() == []
    with f.open("a") as fh:
        fh.write("2026-09-30 10:00:01,000 INFO    [core] jarvis.x: new\n2026-09-30 10:00:02,000 INFO    [core] jarv")
    assert [e["message"] for e in fol.poll()] == ["new"]
    with f.open("a") as fh:
        fh.write("is.x: completed\n")
    assert [e["message"] for e in fol.poll()] == ["completed"]
    f.rename(tmp_path / "jarvis.log.1")            # rotation: a new file appears
    Path(f).write_text("2026-09-30 10:00:03,000 ERROR   [core] jarvis.x: after rotation password=hunter2\n")
    got = fol.poll()
    assert [e["level"] for e in got] == ["ERROR"] and "hunter2" not in got[0]["message"]


def test_logs_endpoint(api):
    c, s, db, tmp = api
    Path(s.logging.file_path).write_text(LOG)
    r = c.get("/api/logs", params={"q": "stream", "level": "INFO"})
    assert r.status_code == 200 and len(r.json()["entries"]) == 1 and "pw123" not in r.text
    assert c.get("/api/logs", params={"level": "LOUD"}).status_code == 422
    assert c.get("/api/logs", params={"q": "(", "regex": "true"}).status_code == 422
    assert c.get("/api/logs", params={"start": "2026-09-30T11:00"}).json()["entries"][0]["level"] == "DEBUG"
    c.post("/api/logout", headers=H)
    assert c.get("/api/logs").status_code == 401
