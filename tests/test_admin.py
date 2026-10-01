# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_admin.py
# Purpose : Tests for runtime settings, chained audit log, sessions and filters
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Administration: editable settings, hash-chained audit log, session tracking, date/time filters,
systemd notification."""

import socket
import time
from datetime import datetime

import numpy as np
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from jarvis.config.catalog import BY_KEY, apply_overrides, coerce, diff, validate_changes
from jarvis.config.settings import Settings
from jarvis.core.sdnotify import notify, watchdog_interval_s
from jarvis.storage.database import Database
from jarvis.web.app import create_app

H = {"X-Jarvis": "1"}
PW = "correct horse battery"


class FakeCore:
    def __init__(self):
        self.calls = []

    def call(self, cmd, **params):
        self.calls.append(cmd)
        return {"ok": True, "restart_required": []}


@pytest.fixture
def env(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("admin", PasswordHasher().hash(PW))
    core = FakeCore()
    return s, db, core


def client(env, ua="pytest-agent"):
    s, db, core = env
    c = TestClient(create_app(s, db, core), headers={"User-Agent": ua})
    assert c.post("/api/login", json={"username": "admin", "password": PW}, headers=H).status_code == 200
    return c


# --- Settings catalog -------------------------------------------------------------------------

def test_catalog_coercion_and_bounds():
    assert coerce(BY_KEY["faces.match_threshold"], "0.5") == 0.5
    with pytest.raises(ValueError):
        coerce(BY_KEY["faces.match_threshold"], 2)
    with pytest.raises(ValueError):
        coerce(BY_KEY["detector.imgsz"], "500")
    assert coerce(BY_KEY["commands.open_phrases"], "open the garage\n\n open the door ") == [
        "open the garage", "open the door"]
    with pytest.raises(ValueError):
        coerce(BY_KEY["commands.open_phrases"], "  ")
    assert coerce(BY_KEY["decision.unknown_message"], "") is None
    with pytest.raises(ValueError):
        validate_changes(Settings(), {"ptz.password": "x"})          # secret: never editable
    with pytest.raises(ValueError):
        validate_changes(Settings(), {"notifications.webhook_url": "ftp://x"})


def test_overrides_apply_and_ignore_unknown_keys():
    s = Settings()
    ignored = apply_overrides(s, {"faces.votes_required": 5, "nope.key": 1, "faces.match_threshold": "bad"})
    assert s.faces.votes_required == 5 and sorted(ignored) == ["faces.match_threshold", "nope.key"]
    assert diff({"a": 1, "b": 2}, {"a": 1, "b": 3}) == [{"field": "b", "before": 2, "after": 3}]


def test_settings_api_hot_change_diff_audit_and_reset(env):
    s, db, core = env
    c = client(env)
    view = c.get("/api/settings").json()
    keys = {p["key"] for g in view["groups"] for p in g["params"]}
    assert "faces.match_threshold" in keys and "ptz.password" not in keys
    r = c.put("/api/settings", json={"changes": {"faces.match_threshold": 0.5, "vision.preview_fps": 8,
                                                 "decision.auth_window_s": 30}}, headers=H)
    body = r.json()
    assert r.status_code == 200 and {x["field"] for x in body["changes"]} == {"faces.match_threshold",
                                                                             "vision.preview_fps"}
    assert body["restart_required"] == ["vision.preview_fps"]         # auth_window unchanged: not in the diff
    assert s.faces.match_threshold == 0.5 and "reload_settings" in core.calls
    assert db.get_settings_overrides()["faces.match_threshold"] == 0.5
    ev = db.list_events(type_="settings_changed")[0]
    assert ev["actor"] == "admin" and ev["details"]["ip"] == "testclient"
    assert {"field": "faces.match_threshold", "before": 0.45, "after": 0.5} in ev["details"]["changes"]
    assert c.put("/api/settings", json={"changes": {"faces.match_threshold": 9}}, headers=H).status_code == 422
    c.post("/api/settings/reset", json={"keys": ["faces.match_threshold"]}, headers=H)
    assert s.faces.match_threshold == 0.45 and "faces.match_threshold" not in db.get_settings_overrides()
    s2 = Settings()                                                   # after a restart: overrides are reloaded
    s2.storage.data_dir = s.storage.data_dir
    create_app(s2, db, core)
    assert s2.vision.preview_fps == 8


def test_person_update_audited_with_diff(env):
    s, db, core = env
    c = client(env)
    pid = c.post("/api/persons", json={"first_name": "Karim", "can_open_garage": True}, headers=H).json()["id"]
    c.patch(f"/api/persons/{pid}", json={"access_start": "08:00", "access_end": "18:00", "watchlist": False},
            headers=H)
    ev = db.list_events(type_="person_updated")[0]
    fields = {x["field"]: (x["before"], x["after"]) for x in ev["details"]["changes"]}
    assert fields == {"access_start": ("", "08:00"), "access_end": ("", "18:00")}
    c.delete(f"/api/persons/{pid}", headers=H)
    ev = db.list_events(type_="person_deleted")[0]
    assert ev["details"]["values"]["first_name"] == "Karim" and "erased" in ev["details"]
    audit = c.get("/api/audit").json()
    assert {"person_created", "person_updated", "person_deleted", "login"} <= {e["type"] for e in audit}


# --- Chained audit log -----------------------------------------------------------------------------

def test_audit_chain_detects_tampering(env):
    s, db, core = env
    for i in range(5):
        db.log_event("say", actor="admin", text=f"n{i}")
    assert db.verify_events()["ok"]
    with db.connect() as con:
        con.execute("UPDATE events SET details = '{\"text\": \"forged\"}' WHERE id = 3")
    r = db.verify_events()
    assert not r["ok"] and r["broken_at"] == 3 and r["reason"] == "content modified"
    with db.connect() as con:
        con.execute("DELETE FROM events WHERE id = 3")
    assert db.verify_events()["reason"] == "missing or reordered link"


def test_audit_verify_endpoint_and_csv(env):
    c = client(env)
    assert c.get("/api/audit/verify").json()["ok"] is True
    lines = c.get("/api/audit.csv").text.lstrip("﻿").splitlines()
    assert lines[0] == "id;date;time;user;ip;action;person_id;details;sha256"
    assert any(";login;" in line for line in lines[1:])


# --- Sessions -------------------------------------------------------------------------------------

def test_login_logout_traced_with_ip_and_duration(env):
    s, db, core = env
    c = client(env, ua="Firefox/140")
    sess = c.get("/api/sessions").json()[0]
    assert sess["active"] and sess["current"] and sess["ip"] == "testclient" and sess["user_agent"] == "Firefox/140"
    c.post("/api/logout", headers=H)
    sess = db.list_sessions()[0]
    assert sess["end_reason"] == "logout" and sess["ended_at"] >= sess["created_at"]
    ev = db.list_events(type_="logout")[0]
    assert ev["details"]["ip"] == "testclient" and ev["details"]["duration_s"] >= 0
    assert db.list_events(type_="login")[0]["details"]["user_agent"] == "Firefox/140"
    assert c.get("/api/me").status_code == 401


def test_idle_timeout_logs_out_and_background_requests_do_not_extend(env):
    s, db, core = env
    s.api.idle_timeout_minutes = 30
    c = client(env)
    with db.connect() as con:                                          # last activity 31 minutes ago
        con.execute("UPDATE sessions SET last_seen = ?", (time.time() - 31 * 60,))
    r = c.get("/api/status", headers={"X-Jarvis-Background": "1"})
    assert r.status_code == 401 and "idle timeout" in r.json()["detail"]
    sess = db.list_sessions()[0]
    assert sess["end_reason"] == "idle_timeout"
    assert abs(sess["ended_at"] - (sess["last_seen"] + 30 * 60)) < 1   # exact logout time
    assert db.list_events(type_="session_timeout")[0]["details"]["reason"] == "idle_timeout"


def test_background_poll_does_not_touch_session(env):
    s, db, core = env
    c = client(env)
    old = time.time() - 120
    with db.connect() as con:
        con.execute("UPDATE sessions SET last_seen = ?", (old,))
    c.get("/api/status", headers={"X-Jarvis-Background": "1"})
    assert db.list_sessions()[0]["last_seen"] == pytest.approx(old)
    c.get("/api/persons")
    assert db.list_sessions()[0]["last_seen"] > old + 60


def test_revoke_other_session(env):
    s, db, core = env
    a, b = client(env), client(env)
    sid_b = [x for x in b.get("/api/sessions").json() if x["current"]][0]["id"]
    assert a.delete(f"/api/sessions/{sid_b}", headers=H).status_code == 200
    assert b.get("/api/me").status_code == 401 and a.get("/api/me").status_code == 200
    assert db.list_events(type_="session_revoked")[0]["details"]["revoked_session_id"] == sid_b


def test_sweep_closes_idle_sessions(env):
    s, db, core = env
    client(env)
    with db.connect() as con:
        con.execute("UPDATE sessions SET last_seen = ?", (time.time() - 3600,))
    closed = db.sweep_sessions(idle_timeout_s=1800)
    assert len(closed) == 1 and closed[0]["end_reason"] == "idle_timeout"


# --- Date-range and time-of-day filters ---------------------------------------------------------------

def test_time_of_day_filters_including_overnight(env):
    s, db, core = env
    c = client(env)
    for day, hh in [(28, 7), (28, 13), (29, 23), (30, 2), (30, 9)]:
        db.add_sighting("known", 1, None, 0.8, 0.5, None, np.zeros(512, np.float32),
                        ts=datetime(2026, 9, day, hh, 30).timestamp())
    q = lambda **p: [datetime.fromtimestamp(r["ts"]).hour for r in c.get("/api/sightings", params=p).json()]
    assert sorted(q(time_from="07:00", time_to="12:00")) == [7, 9]
    assert sorted(q(time_from="22:00", time_to="06:00")) == [2, 23]                  # wraps around midnight
    assert q(start="2026-09-29", end="2026-09-29", time_from="20:00", time_to="23:59") == [23]
    assert c.get("/api/sightings", params={"time_from": "25:00"}).status_code == 422
    stats = c.get("/api/sightings/stats", params={"days": 3650, "time_from": "00:00", "time_to": "12:00"}).json()
    assert sum(x["n"] for x in stats) == 3
    ev = c.get("/api/events", params={"start": datetime.now().strftime("%Y-%m-%d"), "time_from": "00:00",
                                      "time_to": "23:59"}).json()
    assert any(e["type"] == "login" for e in ev)


# --- systemd --------------------------------------------------------------------------------------

def test_sd_notify(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind("n.sock")
    monkeypatch.setenv("NOTIFY_SOCKET", "n.sock")
    monkeypatch.setenv("WATCHDOG_USEC", "60000000")
    assert notify("READY=1") and srv.recv(64) == b"READY=1"
    assert watchdog_interval_s() == 30
    monkeypatch.delenv("NOTIFY_SOCKET")
    assert notify("READY=1") is False
