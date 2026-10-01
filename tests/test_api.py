# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_api.py
# Purpose : Web API tests against a fake core service
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Web API tests against a fake core service."""

import io
import time

import numpy as np
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.web.app import create_app
from jarvis.config.settings import Settings
from jarvis.core.control import ControlClient, ControlServer, CoreUnavailable
from jarvis.storage.database import Database

H = {"X-Jarvis": "1"}


class FakeCore:
    def __init__(self, db):
        self.db, self.calls = db, []

    def call(self, cmd, **params):
        self.calls.append((cmd, params))
        if cmd == "enroll_face":
            fid = self.db.add_face_embedding(params["person_id"], np.ones(512, np.float32), params["image_path"])
            return {"ok": True, "face_id": fid}
        return {"ok": True}


@pytest.fixture
def ctx(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False  # TestClient uses plain HTTP
    s.vision.frame_path = str(tmp_path / "frame.jpg")
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("admin", PasswordHasher().hash("correct horse battery"))
    core = FakeCore(db)
    client = TestClient(create_app(s, db, core))
    return client, db, core, tmp_path


def login(client):
    r = client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}, headers=H)
    assert r.status_code == 200


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, "JPEG")
    return buf.getvalue()


def test_requires_auth(ctx):
    client, *_ = ctx
    assert client.get("/api/persons").status_code == 401
    assert client.get("/").status_code == 200  # static login page


def test_login_rate_limit(ctx):
    client, db, *_ = ctx
    for _ in range(5):
        assert client.post("/api/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "correct horse battery"}).status_code == 429
    assert len(db.list_events(type_="login_failed")) == 5


def test_csrf_header_required(ctx):
    client, *_ = ctx
    login(client)
    assert client.post("/api/persons", json={"first_name": "A"}).status_code == 403
    assert client.post("/api/persons", json={"first_name": "A"}, headers=H).status_code == 201


def test_person_face_lifecycle(ctx):
    client, db, core, tmp = ctx
    login(client)
    pid = client.post("/api/persons", json={"first_name": "Alice", "can_open_garage": True}, headers=H).json()["id"]
    r = client.post(f"/api/persons/{pid}/faces", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}, headers=H)
    assert r.status_code == 201, r.text
    faces = client.get(f"/api/persons/{pid}/faces").json()
    assert len(faces) == 1
    img = client.get("/api/media", params={"path": faces[0]["image_path"]})
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get("/api/media", params={"path": "/etc/passwd"}).status_code == 404
    assert client.get("/api/media", params={"path": str(tmp / "faces/../jarvis.db")}).status_code == 404

    assert client.delete(f"/api/persons/{pid}", headers=H).status_code == 200
    assert db.list_persons() == []
    assert not (tmp / "faces" / str(pid)).exists()
    assert ("reload_faces", {}) in core.calls
    types = [e["type"] for e in db.list_events()]
    assert {"person_created", "face_added", "person_deleted"} <= set(types)


def test_invalid_image_rejected(ctx):
    client, *_ = ctx
    login(client)
    pid = client.post("/api/persons", json={"first_name": "A"}, headers=H).json()["id"]
    r = client.post(f"/api/persons/{pid}/faces", files={"file": ("x.jpg", b"not an image", "image/jpeg")}, headers=H)
    assert r.status_code == 415


def test_label_unknown_as_new_person(ctx):
    client, db, core, tmp = ctx
    login(client)
    src = tmp / "unknown" / "20260929" / "u.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(jpeg_bytes())
    uid = db.add_unknown(np.ones(512, np.float32), str(src), 0.9)
    r = client.post(f"/api/unknowns/{uid}/label", json={"new_person": {"first_name": "Chloé"}}, headers=H)
    assert r.status_code == 200, r.text
    pid = r.json()["person_id"]
    assert db.list_unknowns() == []
    assert not src.exists()
    assert len(db.all_face_embeddings()) == 1 and db.all_face_embeddings()[0][0] == pid


def test_garage_pulse_goes_through_core(ctx):
    client, db, core, _ = ctx
    login(client)
    assert client.post("/api/garage/pulse", headers=H).status_code == 200
    assert core.calls[-1] == ("garage_pulse", {"actor": "admin"})


def test_logout_invalidates_session(ctx):
    client, *_ = ctx
    login(client)
    assert client.post("/api/logout", headers=H).status_code == 200
    assert client.get("/api/me").status_code == 401


def test_control_socket_roundtrip(tmp_path):
    path = str(tmp_path / "core.sock")
    server = ControlServer(path, {"echo": lambda req: {"got": req["x"]}, "boom": lambda req: 1 / 0})
    server.start()
    for _ in range(50):
        if (tmp_path / "core.sock").exists():
            break
        time.sleep(0.02)
    client = ControlClient(path, timeout=2)
    assert client.call("echo", x=3) == {"ok": True, "got": 3}
    assert client.call("boom")["ok"] is False
    assert client.call("nope")["ok"] is False
    server.stop()
    with pytest.raises(CoreUnavailable):
        client.call("echo", x=1)
