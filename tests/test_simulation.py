# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_simulation.py
# Purpose : Simulation (camera file, simulated face/voice) and the tabbed settings domains
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Simulated events never drive the relay unless simulation.drive_relay is on; the voice
simulation applies the wake word token and the strict grammar; the simulation API validates
uploads and file names; every settings group belongs to a domain tab."""

import io
import queue
from datetime import date

import numpy as np
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from jarvis.config.catalog import CATALOG, DOMAINS, GROUP_DOMAIN
from jarvis.config.settings import Settings
from jarvis.core.decision import DecisionEngine
from jarvis.core.events import FaceRecognized, Intent, VoiceCommand
from jarvis.storage.database import Database
from jarvis.voice.simulate import VoiceSimulator, split_wake_token
from jarvis.web.app import create_app
from test_logic import FakeClock, make_hw

H = {"X-Jarvis": "1"}


# --- settings tabs -------------------------------------------------------------------------
def test_every_group_has_a_domain():
    groups = list(dict.fromkeys(p.group for p in CATALOG))
    assert [g for g in groups if g not in GROUP_DOMAIN] == []
    assert [g for g in GROUP_DOMAIN if g not in groups] == []
    assert list(DOMAINS) == ["camera", "recognition", "access", "recording", "integrations", "system", "simulation"]


# --- voice simulation ----------------------------------------------------------------------
@pytest.mark.parametrize("text,wake,rest", [
    ("Jarvis, ouvre la porte du garage", True, "ouvre la porte du garage"),
    ("hey Jarvis… ferme la porte du garage", True, "ferme la porte du garage"),
    ("ouvre la porte du garage", False, "ouvre la porte du garage"),
    ("jarvisouvre", False, "jarvisouvre"),
])
def test_split_wake_token(text, wake, rest):
    assert split_wake_token(text) == (wake, rest)


def test_voice_simulation_text_chain(monkeypatch):
    sim = VoiceSimulator(Settings())
    heard = {}
    monkeypatch.setattr(sim, "synthesize", lambda text: heard.setdefault("synth", text) and np.zeros(1600, np.float32))
    monkeypatch.setattr(sim, "transcribe", lambda audio: "ouvre la porte du garage")
    r = sim.from_text("Jarvis, ouvre la porte du garage")
    assert r["wake_word"] and r["intent"] == "open_garage" and r["stt"] == "vosk on piper speech"
    assert heard["synth"] == "ouvre la porte du garage"             # the token is not synthesized
    r = sim.from_text("ouvre la porte du garage")                    # no "Jarvis": nothing runs
    assert not r["wake_word"] and r["intent"] is None and "wake word" in r["stt"]


def test_voice_simulation_falls_back_to_the_text(monkeypatch):
    sim = VoiceSimulator(Settings())

    def broken(text):
        raise FileNotFoundError("piper model")

    monkeypatch.setattr(sim, "synthesize", broken)
    r = sim.from_text("Jarvis, ferme la porte du garage")
    assert r["intent"] == "close_garage" and r["stt"].startswith("skipped")
    r = sim.from_text("Jarvis, ouvre le garage")                     # strict grammar still applies
    assert r["intent"] is None


# --- decision: simulated pulses ------------------------------------------------------------
def engine(tmp_path, drive_relay=False):
    s = Settings()
    s.simulation.drive_relay = drive_relay
    db = Database(tmp_path / "t.db")
    db.init()
    hw = make_hw(FakeClock(), door="closed")
    clock = FakeClock(10_000)
    eng = DecisionEngine(s, db, hw, lambda _t: None, None, queue.Queue(), clock=clock, today=lambda: date(2026, 10, 1))
    alice = db.add_person("Alice", can_open_garage=True)
    return eng, db, hw, alice, clock


def test_simulated_voice_does_not_drive_the_relay(tmp_path):
    eng, db, hw, alice, clock = engine(tmp_path)
    eng.handle(FaceRecognized(-1, alice, "Alice", 1.0, ts=clock.t, simulated=True))
    eng.handle(VoiceCommand("ouvre la porte du garage", Intent.OPEN_GARAGE, simulated=True))
    assert hw.garage.history.count(True) == 0                        # relay untouched
    ev = db.list_events(type_="garage_pulse_simulated")
    assert ev and ev[0]["details"]["intent"] == "open_garage" and not db.list_events(type_="garage_pulse")


def test_drive_relay_lets_simulations_pulse(tmp_path):
    eng, db, hw, alice, clock = engine(tmp_path, drive_relay=True)
    eng.handle(FaceRecognized(-1, alice, "Alice", 1.0, ts=clock.t, simulated=True))
    eng.handle(VoiceCommand("ouvre la porte du garage", Intent.OPEN_GARAGE, simulated=True))
    assert hw.garage.history.count(True) == 1 and db.list_events(type_="garage_pulse")


def test_camera_simulation_marks_events_simulated(tmp_path):
    eng, db, hw, alice, clock = engine(tmp_path)
    eng.camera_simulating = lambda: "/var/lib/jarvis/simulation/demo.mp4"
    eng.handle(FaceRecognized(1, alice, "Alice", 0.9, ts=clock.t))      # a real-looking event from the file
    eng.handle(VoiceCommand("ouvre la porte du garage", Intent.OPEN_GARAGE))
    assert hw.garage.history.count(True) == 0 and db.list_events(type_="garage_pulse_simulated")
    eng.camera_simulating = lambda: None                               # back to the camera: real again
    clock.t += 1
    eng.handle(FaceRecognized(1, alice, "Alice", 0.9, ts=clock.t))
    eng.handle(VoiceCommand("ouvre la porte du garage", Intent.OPEN_GARAGE))
    assert hw.garage.history.count(True) == 1


# --- API ------------------------------------------------------------------------------------
class FakeCore:
    def __init__(self):
        self.calls = []

    def call(self, cmd, **params):
        self.calls.append((cmd, params))
        if cmd == "status":
            return {"ok": True, "camera_simulation": None}
        return {"ok": True, "echo": cmd, "outcome": "handled", "input": "text", "transcript": "x", "intent": None}


@pytest.fixture
def api(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    s.simulation.max_upload_mb = 1
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("root", PasswordHasher().hash("correct horse battery"))
    core = FakeCore()
    c = TestClient(create_app(s, db, core))
    c.post("/api/login", json={"username": "root", "password": "correct horse battery"}, headers=H)
    return c, db, core, tmp_path


def test_settings_api_exposes_domains(api):
    c, *_ = api
    view = c.get("/api/settings").json()
    assert view["domains"][0] == "camera" and all(g["domain"] in view["domains"] for g in view["groups"])
    assert any(g["name"] == "Voice simulation" and g["domain"] == "simulation" for g in view["groups"])


def test_simulation_uploads_and_camera(api):
    c, db, core, tmp = api
    up = lambda name, data: c.post("/api/simulation/files", files={"file": (name, io.BytesIO(data))}, headers=H)
    assert up("clip.mp4", b"\x00" * 100).json() == {"name": "clip.mp4", "kind": "video", "size": 100}
    assert up("../../etc/evil.mp4", b"x").json()["name"] == "evil.mp4"           # no path traversal
    assert up("script.sh", b"x").status_code == 415
    assert up("big.mp4", b"x" * (1024 * 1024 + 1)).status_code == 413
    assert not (tmp / "simulation" / "big.mp4").exists()                         # partial upload removed
    assert up("cmd.webm", b"x").json()["kind"] == "audio"
    files = c.get("/api/simulation").json()["files"]
    assert {f["name"] for f in files} == {"clip.mp4", "evil.mp4", "cmd.webm"}
    assert c.post("/api/simulation/camera", json={"file": "clip.mp4"}, headers=H).status_code == 200
    assert core.calls[-1] == ("simulate_camera", {"path": str((tmp / "simulation" / "clip.mp4").resolve()), "actor": "root"})
    assert c.post("/api/simulation/camera", json={"file": "cmd.webm"}, headers=H).status_code == 422   # audio
    assert c.post("/api/simulation/camera", json={"file": "../jarvis.db"}, headers=H).status_code == 422
    assert c.post("/api/simulation/camera", json={"file": None}, headers=H).status_code == 200
    assert c.delete("/api/simulation/files/clip.mp4", headers=H).status_code == 200
    assert {e["type"] for e in db.list_events()} >= {"simulation_file_added", "simulation_camera", "simulation_file_deleted"}


def test_simulation_face_and_voice(api):
    c, db, core, tmp = api
    pid = db.add_person("Alice", can_open_garage=True)
    assert c.post("/api/simulation/face", json={"person_id": 999}, headers=H).status_code == 404
    assert c.post("/api/simulation/face", json={"person_id": pid}, headers=H).status_code == 200
    assert core.calls[-1][0] == "simulate_face"
    assert c.post("/api/simulation/voice", json={}, headers=H).status_code == 422
    r = c.post("/api/simulation/voice", json={"text": "Jarvis, ouvre la porte du garage"}, headers=H)
    assert r.status_code == 200 and core.calls[-1] == ("simulate_voice", {"actor": "root", "text": "Jarvis, ouvre la porte du garage"})
    assert db.list_events(type_="simulation_voice")
