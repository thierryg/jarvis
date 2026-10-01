# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_features.py
# Purpose : Tests for sightings, unknown clustering, access rules and notifications
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Sighting tracking, unknown-visitor clustering, face search, access rules,
watchlist, adaptive enrollment and notifications."""

import io
import json
import queue
import sqlite3
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from PIL import Image

import jarvis.vision.pipeline as vision
from jarvis.core.access import access_allowed
from jarvis.web.app import create_app
from jarvis.config.settings import FaceConfig, NotificationsConfig, Settings
from jarvis.storage.database import Database, Person
from jarvis.core.decision import DecisionEngine
from jarvis.core.events import FaceRecognized, Intent, UnknownFaceSeen, VoiceCommand
from jarvis.vision.faces import EmbeddingGallery, IdentityResolver
from jarvis.hardware.devices import Hardware, MockOutput, NoSensor
from jarvis.core.notify import WebhookNotifier

H = {"X-Jarvis": "1"}


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def vec(i, dim=512, noise=0.0, seed=0):
    """Embedding for "person i": unit axis i plus optional noise."""
    v = np.zeros(dim, np.float32)
    v[i] = 1
    if noise:
        v += np.random.default_rng(seed).normal(0, noise, dim).astype(np.float32)
    return unit(v)


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


# --- Access rules ---------------------------------------------------------------------------

def person(**kw):
    base = dict(id=1, first_name="A", last_name="", can_open_garage=True, created_at=0)
    return Person(**(base | kw))


MON_10H = datetime(2026, 9, 28, 10, 0)   # Monday
SUN_10H = datetime(2026, 10, 4, 10, 0)   # Sunday


@pytest.mark.parametrize("kw,when,expected", [
    ({}, MON_10H, (True, "ok")),
    ({"can_open_garage": False}, MON_10H, (False, "not_authorized")),
    ({"watchlist": True}, MON_10H, (False, "watchlist")),
    ({"access_days": "12345"}, MON_10H, (True, "ok")),
    ({"access_days": "12345"}, SUN_10H, (False, "day_not_allowed")),
    ({"access_start": "08:00", "access_end": "18:00"}, MON_10H, (True, "ok")),
    ({"access_start": "12:00", "access_end": "18:00"}, MON_10H, (False, "outside_hours")),
    ({"access_start": "22:00", "access_end": "06:00"}, datetime(2026, 9, 28, 23, 30), (True, "ok")),
    ({"access_start": "22:00", "access_end": "06:00"}, datetime(2026, 9, 28, 5, 59), (True, "ok")),
    ({"access_start": "22:00", "access_end": "06:00"}, MON_10H, (False, "outside_hours")),
    ({"valid_until": MON_10H.timestamp() - 1}, MON_10H, (False, "expired")),
    ({"valid_until": MON_10H.timestamp() + 1}, MON_10H, (True, "ok")),
])
def test_access_rules(kw, when, expected):
    assert access_allowed(person(**kw), when) == expected


# --- Database -----------------------------------------------------------------------------------

def test_migration_from_first_schema(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE persons (id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL DEFAULT '',
                              can_open_garage INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
        CREATE TABLE unknown_faces (id INTEGER PRIMARY KEY, embedding BLOB NOT NULL, image_path TEXT NOT NULL,
                                    det_score REAL, created_at REAL NOT NULL);
        INSERT INTO persons(first_name, can_open_garage, created_at) VALUES ('Alice', 1, 0);
    """)
    con.close()
    db = Database(path)
    db.init()
    db.init()  # idempotent
    p = db.get_person(1)
    assert p.first_name == "Alice" and p.watchlist is False and p.valid_until is None
    db.update_person(1, access_days="67", watchlist=True)
    assert db.get_person(1).access_days == "67" and db.get_person(1).watchlist


def test_delete_person_erases_sightings(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init()
    pid = db.add_person("Alice")
    db.add_sighting("known", 1, pid, 0.8, 0.5, "/x/s.jpg", vec(0))
    files = db.delete_person(pid)
    assert "/x/s.jpg" in files and db.list_sightings() == []


# --- Unknown-visitor clustering ------------------------------------------------------------------

@pytest.fixture
def no_jpeg(monkeypatch):
    monkeypatch.setattr(vision, "save_jpeg", lambda *a, **k: None)


def make_store(tmp_path, clock):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    db = Database(s.storage.db_path)
    db.init()
    return vision.UnknownStore(s, db, clock=clock), db


def test_unknowns_dedupe_then_cluster_across_days(tmp_path, no_jpeg):
    clock = Clock(1_000_000)
    store, db = make_store(tmp_path, clock)
    crop = np.zeros((4, 4, 3), np.uint8)
    uid1, c1 = store.save(vec(0, noise=0.02, seed=1), crop, 0.9)
    assert uid1 is not None and c1 == uid1                     # starts its own cluster
    assert store.save(vec(0, noise=0.02, seed=2), crop, 0.9) == (None, c1)   # same visit: deduplicated
    clock.t += 2 * 86400                                        # comes back two days later
    uid2, c2 = store.save(vec(0, noise=0.02, seed=3), crop, 0.9)
    assert uid2 not in (None, uid1) and c2 == c1                # new visit, same cluster
    uid3, c3 = store.save(vec(5), crop, 0.9)                    # different visitor
    assert c3 == uid3 != c1
    clusters = {c["cluster_id"]: c for c in db.list_unknown_clusters()}
    assert clusters[c1]["faces"] == 2 and clusters[c3]["faces"] == 1
    store2, _ = make_store(tmp_path, clock)                     # reloaded from the database
    assert store2.nearest(vec(0))[1] == c1


# --- Decision: watchlist, schedules, notifications ----------------------------------------------

class Harness:
    def __init__(self, tmp_path, now=datetime(2026, 9, 28, 10, 0)):
        self.s = Settings()
        self.db = Database(tmp_path / "t.db")
        self.db.init()
        self.clock = Clock(now.timestamp())
        self.hw = Hardware(self.s.hardware, MockOutput("g"), MockOutput("green"), MockOutput("red"), NoSensor(),
                           clock=Clock(0), sleep=lambda _s: None)
        self.said, self.notified = [], []
        self.engine = DecisionEngine(self.s, self.db, self.hw, self.said.append, None, queue.Queue(),
                                     clock=self.clock, today=lambda: date(2026, 9, 28),
                                     notify=lambda t, **p: self.notified.append((t, p)))

    def face(self, pid):
        self.engine.handle(FaceRecognized(1, pid, "", 0.8, ts=self.clock.t, sighting_id=7))

    def open(self):
        self.engine.handle(VoiceCommand("ouvre le garage", Intent.OPEN_GARAGE))
        return self.hw.garage.history.count(True)


def test_watchlist_person_triggers_alert_never_access(tmp_path):
    h = Harness(tmp_path)
    pid = h.db.add_person("Eve", can_open_garage=True, watchlist=True)
    h.face(pid)
    assert h.hw.red.state and not h.hw.green.state
    assert h.said == []                                       # no greeting
    assert h.notified[0][0] == "watchlist_seen" and h.notified[0][1]["sighting_id"] == 7
    assert h.open() == 0


def test_outside_schedule_is_recognized_but_not_authorized(tmp_path):
    h = Harness(tmp_path)                                     # Monday 10:00
    pid = h.db.add_person("Artisan", can_open_garage=True, access_start="14:00", access_end="18:00")
    h.face(pid)
    assert h.said == ["Bonjour Artisan"] and h.hw.green.state
    assert h.db.list_events(type_="access_denied_schedule")[0]["details"]["reason"] == "outside_hours"
    assert h.open() == 0
    assert [t for t, _ in h.notified] == ["face_recognized", "access_denied_schedule", "voice_denied"]


def test_within_schedule_opens_and_notifies(tmp_path):
    h = Harness(tmp_path)
    pid = h.db.add_person("Artisan", can_open_garage=True, access_days="12345", access_start="08:00",
                          access_end="18:00")
    h.face(pid)
    assert h.open() == 1
    assert h.notified[-1][0] == "garage_pulse" and h.notified[-1][1]["person_id"] == pid


def test_unknown_notifies_with_cluster(tmp_path):
    h = Harness(tmp_path)
    h.engine.handle(UnknownFaceSeen(3, 12, cluster_id=4, sighting_id=9))
    assert h.notified == [("face_unknown", {"unknown_id": 12, "cluster_id": 4, "sighting_id": 9})]


# --- Identification: the decision carries the best capture -------------------------------------

def test_known_decision_carries_best_capture():
    r = IdentityResolver(FaceConfig(votes_required=2), clock=Clock())
    crops = [np.full((2, 2, 3), i, np.uint8) for i in range(2)]
    assert r.observe(1, 5, 0.7, vec(1), crops[0], 0.4) is None
    d = r.observe(1, 5, 0.6, vec(2), crops[1], 0.9)
    assert d.kind == "known" and d.score == 0.7 and d.quality == 0.4
    assert (d.crop == crops[0]).all() and (d.embedding == vec(1)).all()


# --- Adaptive enrollment -----------------------------------------------------------------------------

def test_adaptive_enrollment_rules(tmp_path, no_jpeg):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.faces.adaptive_max_per_person = 2
    db = Database(s.storage.db_path)
    db.init()
    pid = db.add_person("Alice")
    db.add_face_embedding(pid, vec(0), None)
    gallery = EmbeddingGallery()
    enroller = vision.AdaptiveEnroller(s, db, gallery)
    crop = np.zeros((4, 4, 3), np.uint8)
    novel = unit(vec(0) + vec(1))                                  # similarity 0.71: novel
    assert enroller.consider(pid, 0.55, 0.9, novel, crop) is None  # match score too low
    assert enroller.consider(pid, 0.8, 0.1, novel, crop) is None   # quality too low
    assert enroller.consider(pid, 0.8, 0.9, vec(0), crop) is None  # nothing new
    assert enroller.consider(pid, 0.8, 0.9, novel, crop) is not None
    assert len(gallery) == 2                                       # gallery hot-reloaded
    assert enroller.consider(pid, 0.8, 0.9, unit(vec(0) + vec(2)), crop) is not None
    assert enroller.consider(pid, 0.8, 0.9, unit(vec(0) + vec(3)), crop) is None   # per-person cap reached
    assert db.count_faces(pid, "auto") == 2
    s.faces.adaptive_enabled = False
    assert vision.AdaptiveEnroller(s, db, gallery).consider(pid, 0.9, 0.9, unit(vec(0) + vec(4)), crop) is None


# --- API: sightings, CSV, statistics, search, clusters ---------------------------------------------------

class FakeCore:
    def __init__(self):
        self.calls, self.embedding = [], vec(0)

    def call(self, cmd, **params):
        self.calls.append(cmd)
        if cmd == "embed_face":
            return {"ok": True, "embedding": self.embedding.tolist(), "det_score": 0.9}
        return {"ok": True}


@pytest.fixture
def api(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("admin", PasswordHasher().hash("correct horse battery"))
    core = FakeCore()
    client = TestClient(create_app(s, db, core))
    assert client.post("/api/login", json={"username": "admin", "password": "correct horse battery"},
                       headers=H).status_code == 200
    return client, db, core, tmp_path


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 80, 60)).save(buf, "JPEG")
    return buf.getvalue()


def test_sightings_list_filters_csv_stats(api):
    client, db, _, _ = api
    alice = db.add_person("Alice")
    day1, day2 = datetime(2026, 9, 28, 9, 15).timestamp(), datetime(2026, 9, 29, 18, 40).timestamp()
    db.add_sighting("known", 1, alice, 0.81, 0.6, None, vec(0), ts=day1)
    db.add_sighting("unknown", 2, None, 0.1, 0.5, None, vec(3), cluster_id=4, ts=day2)
    assert len(client.get("/api/sightings").json()) == 2
    only = client.get("/api/sightings", params={"start": "2026-09-29", "end": "2026-09-29"}).json()
    assert [r["status"] for r in only] == ["unknown"]
    assert client.get("/api/sightings", params={"person_id": alice}).json()[0]["first_name"] == "Alice"
    assert client.get("/api/sightings", params={"start": "29/09/2026"}).status_code == 422
    r = client.get("/api/sightings.csv")
    lines = r.text.lstrip("﻿").splitlines()
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    assert lines[0].startswith("id;date;time;status;person")
    assert "2026-09-28;09:15:00;known;Alice;0.81" in lines[2]
    stats = client.get("/api/sightings/stats", params={"days": 3650}).json()
    assert {(x["day"], x["status"], x["n"]) for x in stats} == {("2026-09-28", "known", 1),
                                                               ("2026-09-29", "unknown", 1)}


def test_face_search_ranks_matches_and_keeps_no_photo(api):
    client, db, core, tmp_path = api
    alice = db.add_person("Alice")
    s_near = db.add_sighting("known", 1, alice, 0.8, 0.6, None, vec(0, noise=0.01))
    db.add_sighting("unknown", 2, None, 0.1, 0.5, None, vec(7))
    r = client.post("/api/search/face", files={"file": ("q.jpg", jpeg(), "image/jpeg")}, headers=H)
    assert r.status_code == 200
    hits = r.json()
    assert [h["id"] for h in hits] == [s_near] and hits[0]["similarity"] > 0.9
    assert "embed_face" in core.calls
    assert not list((tmp_path / "tmp").glob("*"))              # search photo deleted


def test_label_cluster_identifies_all_visits_retroactively(api):
    client, db, core, tmp_path = api
    u1 = db.add_unknown(vec(0), str(tmp_path / "u1.jpg"), 0.9)
    u2 = db.add_unknown(vec(0, noise=0.01), str(tmp_path / "u2.jpg"), 0.8, cluster_id=u1)
    (tmp_path / "u1.jpg").write_bytes(jpeg())
    db.add_sighting("unknown", 1, None, 0.1, 0.5, None, vec(0), unknown_id=u1, cluster_id=u1)
    db.add_sighting("unknown", 2, None, 0.1, 0.5, None, vec(0), unknown_id=None, cluster_id=u1)  # deduplicated
    clusters = client.get("/api/unknowns/clusters").json()
    assert clusters[0]["cluster_id"] == u1 and clusters[0]["faces"] == 2 and clusters[0]["sightings"] == 2
    r = client.post(f"/api/unknowns/clusters/{u1}/label", json={"new_person": {"first_name": "Facteur"}},
                    headers=H)
    assert r.status_code == 200 and r.json()["faces"] == 2 and r.json()["sightings_relabeled"] == 2
    pid = r.json()["person_id"]
    assert db.count_faces(pid) == 2 and db.list_unknowns() == []
    assert {x["status"] for x in db.list_sightings(person_id=pid)} == {"labeled"}
    assert "reload_faces" in core.calls
    assert db.unknown_ids_in_cluster(u2) == []


def test_person_access_fields_validated(api):
    client, db, _, _ = api
    r = client.post("/api/persons", json={"first_name": "Artisan", "can_open_garage": True, "access_days": "12345",
                                          "access_start": "08:00", "access_end": "18:00",
                                          "valid_until": 1_900_000_000}, headers=H)
    pid = r.json()["id"]
    p = db.get_person(pid)
    assert (p.access_days, p.access_start, p.valid_until) == ("12345", "08:00", 1_900_000_000)
    assert client.post("/api/persons", json={"first_name": "X", "access_start": "25:00"}, headers=H).status_code == 422
    assert client.post("/api/persons", json={"first_name": "X", "access_days": "8"}, headers=H).status_code == 422
    client.patch(f"/api/persons/{pid}", json={"clear_valid_until": True, "watchlist": True}, headers=H)
    p = db.get_person(pid)
    assert p.valid_until is None and p.watchlist and p.access_days == "12345"


# --- Webhook notifications ----------------------------------------------------------------------------

def test_webhook_notifier_posts_json_and_filters():
    received = []

    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    n = WebhookNotifier(NotificationsConfig(webhook_url=f"http://127.0.0.1:{srv.server_port}/hook",
                                            events=["face_unknown"]))
    n.start()
    n("garage_pulse", actor="x")                       # not subscribed: ignored
    n("face_unknown", cluster_id=4)
    n.stop()
    n.join(5)
    srv.shutdown()
    assert len(received) == 1
    assert received[0]["type"] == "face_unknown" and received[0]["cluster_id"] == 4 and "datetime" in received[0]
    assert not WebhookNotifier(NotificationsConfig()).enabled


# --- Regression: fixed camera ----------------------------------------------------------------------

def test_fixed_camera_never_blocks_face_recognition():
    from jarvis.vision.ptz import NullPTZ, PTZTracker
    from jarvis.config.settings import PTZConfig

    tr = PTZTracker(NullPTZ(), PTZConfig())
    tr.update((0, 0, 100, 300), 1280, 720)   # target far off-center: a PTZ camera would move
    assert tr.moving is False


def test_thread_subclasses_do_not_shadow_thread_internals():
    """Regression: ``_stop`` and then ``_name`` shadowed internal attributes of threading.Thread."""
    import re
    import threading
    from pathlib import Path

    internal = set(vars(threading.Thread())) | {a for a in dir(threading.Thread)
                                                  if a.startswith("_") and not a.startswith("__")}
    for f in Path(__file__).parent.parent.joinpath("jarvis").rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"class (\w+)\(threading\.Thread\):", src):
            body = src[m.end():]
            nxt = re.search(r"^class ", body, re.M)
            body = body[:nxt.start()] if nxt else body
            names = set(re.findall(r"self\.(_[a-z]\w*)\s*=", body)) | set(re.findall(r"def (_[a-z]\w*)\(", body))
            assert not names & internal, f"{f.name}:{m.group(1)} masque {names & internal}"
