# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_plates.py
# Purpose : License plates: normalization, read voting, direction, garage automation rules
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""A known plate on an approaching vehicle opens the closed garage; every refusal (unknown,
disabled, expired, schedule, door state unknown, already open) is logged; the close after a
known vehicle left is opt-in, waits for a clear scene and needs the door to read "open"."""

import queue
from datetime import date

import pytest

from jarvis.config.settings import PlatesConfig, Settings
from jarvis.core.decision import DecisionEngine
from jarvis.core.events import VehicleEvent
from jarvis.core.plates import mean_confidence, normalize_plate, plate_status, validate_plate
from jarvis.storage.database import Database
from jarvis.vision.plates import GONE_AFTER_S, PlateReader, VehicleTracker
from jarvis.config.settings import HardwareConfig
from jarvis.hardware.devices import Hardware, MockOutput
from test_logic import FakeClock


class Door:
    """Door sensor whose state the test can change."""

    def __init__(self, value):
        self.value = value

    def state(self):
        return self.value


# --- pure rules -------------------------------------------------------------------------
@pytest.mark.parametrize("raw,norm", [
    ("ab-123-cd", "AB123CD"), (" B MW 1234 ", "BMW1234"), ("12·AB·34", "12AB34"), ("Б 123 ВГ", "Б123ВГ"),
    ("ＡＢ１２３", "AB123"),                       # full-width characters (NFKC)
])
def test_normalize_plate(raw, norm):
    assert normalize_plate(raw) == norm


def test_validate_and_status():
    assert validate_plate("ab 12") == "AB12"
    for bad in ("", "-", "A", "ABCDEFGHIJKLM"):
        with pytest.raises(ValueError):
            validate_plate(bad)
    assert plate_status(None) == "unknown"
    assert plate_status({"enabled": 0}) == "disabled"
    assert plate_status({"enabled": 1, "valid_until": 100.0}, now=200.0) == "expired"
    assert plate_status({"enabled": 1, "valid_until": None}) == "known"
    assert mean_confidence([0.9, 0.8, 0.1, 0.1], 2) == pytest.approx(0.85)   # padded slots ignored
    assert mean_confidence(0.7, 3) == 0.7


# --- vehicle tracker ---------------------------------------------------------------------
def box(side):
    return (100.0, 100.0, 100.0 + side, 100.0 + side * 0.6)


def test_direction_and_gone():
    vt = VehicleTracker(PlatesConfig(motion_window_s=2.0))
    evs = []
    for i, side in enumerate([200, 215, 230, 250]):                  # growing box: approaching
        evs += vt.observe({7: box(side)}, 10 + i * 0.5)
    assert [e.kind for e in evs] == ["approaching"]
    evs = []
    for i, side in enumerate([240, 210, 180, 150, 130]):            # shrinking box: leaving
        evs += vt.observe({7: box(side)}, 12 + i * 0.5)
    assert [e.kind for e in evs] == ["leaving"]
    assert [e.kind for e in vt.observe({}, 20 + GONE_AFTER_S)] == ["gone"]


def test_read_voting():
    vt = VehicleTracker(PlatesConfig(votes_required=2, min_confidence=0.8))
    vt.observe({3: box(200)}, 1.0)
    assert vt.add_reads(3, [("AB123CD", 0.95, "France")], 1.0) is None          # one vote
    assert vt.add_reads(3, [("AB123CO", 0.60, "France")], 1.5) is None          # too weak: ignored
    ev = vt.add_reads(3, [("AB123CD", 0.90, "France")], 2.0, "/tmp/x.jpg")
    assert ev.kind == "plate" and ev.plate == "AB123CD" and ev.confidence == 0.95 and ev.region == "France"
    assert vt.add_reads(3, [("AB123CD", 0.99, None)], 3.0) is None             # confirmed once only
    assert not vt.due_for_read(3, 3.1) and vt.due_for_read(3, 3.0 + 5 * 0.5)     # slower re-reads once confirmed


class FakeEngine:
    def __init__(self, reads):
        self.reads, self.calls = reads, 0

    def read(self, image):
        self.calls += 1
        return self.reads


class T:
    def __init__(self, tid, b):
        self.id, self.box, self.cls = tid, b, 2


def test_reader_reads_one_vehicle_per_cycle(tmp_path):
    import numpy as np

    clock = FakeClock(100.0)
    engine = FakeEngine([("AB123CD", 0.97, "France")])
    reader = PlateReader(PlatesConfig(votes_required=2, save_images=False, min_vehicle_px=50), tmp_path, engine, clock)
    frame = np.zeros((720, 1280, 3), np.uint8)
    small, big = T(1, (0, 0, 40, 30)), T(2, (100, 100, 500, 400))
    assert reader.process(frame, [small, big]) == [] and engine.calls == 1   # the big one only
    clock.t += 0.6
    evs = reader.process(frame, [small, big])
    assert [e.kind for e in evs] == ["plate"] and evs[0].track_id == 2


# --- decision engine -----------------------------------------------------------------------
class Harness:
    def __init__(self, tmp_path, door="closed", auto_close=False):
        self.s = Settings()
        self.s.plates.enabled = True
        self.s.plates.auto_close = auto_close
        self.s.plates.close_delay_s = 10
        self.db = Database(tmp_path / "t.db")
        self.db.init()
        self.clock = FakeClock(1_790_000_000.0)
        self.door = Door(door)
        self.hw = Hardware(HardwareConfig(cooldown_s=5), MockOutput("garage"), MockOutput("green"), MockOutput("red"),
                           self.door, clock=self.clock, sleep=lambda s: None)
        self.engine = DecisionEngine(self.s, self.db, self.hw, lambda _t: None, None, queue.Queue(),
                                     clock=self.clock, today=lambda: date(2026, 9, 30))
        self.clear = True
        self.engine.scene_clear = lambda: self.clear
        self.alice = self.db.add_person("Alice", can_open_garage=True)
        self.db.add_plate("AB123CD", "AB-123-CD", "France", "Clio", self.alice)

    def ev(self, kind, plate=None, direction="stationary", track=5):
        self.engine.handle(VehicleEvent(kind, track, plate, confidence=0.95, region="France", direction=direction,
                                        ts=self.clock.t))

    def pulses(self):
        return self.hw.garage.history.count(True)

    def refused(self, action="open"):
        return [e["details"]["reason"] for e in self.db.list_events(type_=f"plate_{action}_refused")]


def test_known_plate_approaching_opens_and_is_traced(tmp_path):
    h = Harness(tmp_path)
    h.ev("plate", "AB123CD", "approaching")
    assert h.pulses() == 1
    read = h.db.list_plate_reads()[0]
    assert read["status"] == "known" and read["action"] == "opened" and read["first_name"] == "Alice"
    pulse = h.db.list_events(type_="garage_pulse")[0]
    assert pulse["details"]["source"] == "plate" and pulse["details"]["plate"] == "AB123CD"
    assert h.db.list_events(type_="plate_read")[0]["details"]["status"] == "known"
    h.ev("approaching", track=5)
    assert h.pulses() == 1                                   # once per vehicle


def test_plate_first_then_approach(tmp_path):
    h = Harness(tmp_path)
    h.ev("plate", "AB123CD", "stationary")
    assert h.pulses() == 0
    h.ev("approaching")
    assert h.pulses() == 1


@pytest.mark.parametrize("setup,reason", [
    (lambda h: None, "plate_unknown"),
    (lambda h: h.db.update_plate(h.db.find_plate("AB123CD")["id"], enabled=False), "plate_disabled"),
    (lambda h: h.db.update_plate(h.db.find_plate("AB123CD")["id"], valid_until=h.clock.t - 1), "plate_expired"),
    (lambda h: h.db.update_person(h.alice, can_open_garage=False), "person_not_allowed"),
    (lambda h: setattr(h.s.plates, "auto_open", False), "auto_open_disabled"),
])
def test_refusals_are_logged(tmp_path, setup, reason):
    h = Harness(tmp_path)
    setup(h)
    h.ev("plate", "ZZ999ZZ" if reason == "plate_unknown" else "AB123CD", "approaching")
    assert h.pulses() == 0 and h.refused() == [reason]


def test_door_state_protects_the_single_button_motor(tmp_path):
    h = Harness(tmp_path, door=None)                      # no door sensor
    h.ev("plate", "AB123CD", "approaching")
    assert h.pulses() == 0 and h.refused() == ["door_state_unknown"]
    h2 = Harness(tmp_path / "b", door="open")
    h2.ev("plate", "AB123CD", "approaching")
    assert h2.pulses() == 0 and h2.refused() == ["already_open"]   # a pulse would CLOSE it


def test_close_is_opt_in(tmp_path):
    h = Harness(tmp_path, door="open")
    h.ev("plate", "AB123CD", "leaving")
    h.ev("gone", "AB123CD", "leaving")
    assert h.refused("close") == ["auto_close_disabled"] and h.pulses() == 0


def test_close_after_leaving_waits_for_a_clear_scene(tmp_path):
    h = Harness(tmp_path, door="open", auto_close=True)
    h.ev("plate", "AB123CD", "leaving")
    h.ev("gone", "AB123CD", "leaving")
    assert h.db.list_events(type_="garage_close_scheduled")
    h.clock.t += 5
    h.clear = False                                        # someone walks in: countdown restarts
    h.engine.tick()
    h.clock.t += 9
    h.clear = True
    h.engine.tick()
    assert h.pulses() == 0                                 # only 9 s clear since the reset
    h.clock.t += 2
    h.engine.tick()
    assert h.pulses() == 1
    assert h.db.list_events(type_="garage_closed_vehicle_left")
    assert h.db.list_plate_reads()[0]["action"] == "closed"


def test_close_needs_the_door_open(tmp_path):
    h = Harness(tmp_path, door="closed", auto_close=True)
    h.ev("plate", "AB123CD", "leaving")
    h.ev("gone", "AB123CD", "leaving")
    h.clock.t += 11
    h.engine.tick()
    assert h.pulses() == 0 and h.refused("close") == ["already_closed"]


def test_arrival_cancels_a_pending_close(tmp_path):
    h = Harness(tmp_path, door="open", auto_close=True)
    h.ev("plate", "AB123CD", "leaving", track=1)
    h.ev("gone", "AB123CD", "leaving", track=1)
    h.ev("plate", "AB123CD", "approaching", track=2)       # comes back before the close (door still open)
    assert h.db.list_events(type_="garage_close_cancelled")
    h.clock.t += 11
    h.engine.tick()
    assert h.pulses() == 0 and not h.db.list_events(type_="garage_closed_vehicle_left")


# --- API -------------------------------------------------------------------------------------
@pytest.fixture
def api(tmp_path):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from jarvis.web.app import create_app

    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("root", PasswordHasher().hash("correct horse battery"))
    c = TestClient(create_app(s, db, type("Core", (), {"call": lambda self, cmd, **k: {"ok": True}})()))
    c.post("/api/login", json={"username": "root", "password": "correct horse battery"}, headers={"X-Jarvis": "1"})
    return c, db


H = {"X-Jarvis": "1"}


def test_plate_registry_crud_is_audited(api):
    c, db = api
    alice = db.add_person("Alice", can_open_garage=True)
    r = c.post("/api/plates", json={"plate": "ab-123-cd", "country": "France", "label": "Clio", "person_id": alice}, headers=H)
    assert r.status_code == 200 and r.json()["plate"] == "AB123CD"
    pid = r.json()["id"]
    assert c.post("/api/plates", json={"plate": "AB 123 CD"}, headers=H).status_code == 409      # same plate
    assert c.post("/api/plates", json={"plate": "--"}, headers=H).status_code == 422
    assert c.post("/api/plates", json={"plate": "XY12", "person_id": 999}, headers=H).status_code == 422
    listed = c.get("/api/plates").json()
    assert listed[0]["display"] == "AB-123-CD" and listed[0]["status"] == "known" and listed[0]["first_name"] == "Alice"
    r = c.patch(f"/api/plates/{pid}", json={"enabled": False, "label": "Clio grise"}, headers=H)
    assert {x["field"] for x in r.json()["changes"]} == {"enabled", "label"}
    assert c.get("/api/plates").json()[0]["status"] == "disabled"
    c.patch(f"/api/plates/{pid}", json={"clear_person": True}, headers=H)
    assert db.get_plate(pid)["person_id"] is None
    assert c.delete(f"/api/plates/{pid}", headers=H).status_code == 200 and db.list_plates() == []
    types = [e["type"] for e in db.list_events()]
    assert {"plate_added", "plate_updated", "plate_deleted"} <= set(types)
    upd = db.list_events(type_="plate_updated")[-1]
    assert {"field": "label", "before": "Clio", "after": "Clio grise"} in upd["details"]["changes"]


def test_plate_reads_history_filters(api):
    c, db = api
    pid = db.add_plate("AB123CD", "AB-123-CD")
    db.add_plate_read("AB123CD", 0.97, "France", "known", pid, 1, "approaching", None, ts=1_790_000_000)
    db.add_plate_read("ZZ999ZZ", 0.91, "Germany", "unknown", None, 2, "leaving", None, ts=1_790_000_100)
    assert len(c.get("/api/plate-reads").json()) == 2
    assert [r["plate"] for r in c.get("/api/plate-reads", params={"status": "unknown"}).json()] == ["ZZ999ZZ"]
    assert [r["plate"] for r in c.get("/api/plate-reads", params={"plate": "ab-123"}).json()] == ["AB123CD"]
    assert c.get("/api/plate-reads", params={"status": "weird"}).status_code == 422
