# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_logic.py
# Purpose : Pure-logic unit tests (no models, no hardware required)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Pure-logic tests (no models, no hardware required)."""

import queue
from datetime import date

import numpy as np
import pytest

from jarvis.vision.ptz import PTZCommand, PTZTracker, compute_command
from jarvis.config.settings import FaceConfig, HardwareConfig, PTZConfig, Settings, expand_env
from jarvis.storage.database import Database
from jarvis.core.decision import DecisionEngine
from jarvis.core.events import FaceRecognized, Intent, ManualGarage, UnknownFaceSeen, VoiceCommand
from jarvis.vision.faces import EmbeddingGallery, IdentityResolver
from jarvis.hardware.devices import Hardware, MockOutput, dcttech_report, lcus_frame
from jarvis.vision.pipeline import Track, face_region, select_target
from jarvis.voice.audio import Framer
from jarvis.voice.commands import CommandParser, normalize


# --- Config -------------------------------------------------------------------------------

def test_expand_env(monkeypatch):
    monkeypatch.setenv("CAM_PW", "s3cret")
    assert expand_env({"a": ["rtsp://u:${CAM_PW}@h"], "b": "${MISSING:-x}"}) == {"a": ["rtsp://u:s3cret@h"], "b": "x"}
    with pytest.raises(ValueError):
        expand_env("${DEFINITELY_MISSING}")


# --- Voice commands -----------------------------------------------------------------------

@pytest.fixture
def parser():
    return CommandParser(Settings().commands)


# Strict mode (default): after the "Jarvis" wake word, only the two exact commands are accepted.
@pytest.mark.parametrize("text,intent", [
    ("ouvre la porte du garage", Intent.OPEN_GARAGE),
    ("Ouvre la porte du garage", Intent.OPEN_GARAGE),
    ("ferme la porte du garage", Intent.CLOSE_GARAGE),
    ("fermé la porte du garage", Intent.CLOSE_GARAGE),          # accents are normalized
    ("ouvre le garage", None),
    ("jarvis ouvre moi le garage", None),
    ("ferme la porte", None),
    ("annule", None),
    ("[unk]", None),
    ("il fait beau", None),
    ("ouvre la fenêtre", None),
])
def test_parse(parser, text, intent):
    assert parser.parse(text) == intent


def test_grammar_is_restricted_to_the_two_commands(parser):
    assert parser.grammar == ["ferme la porte du garage", "ouvre la porte du garage", "[unk]"]


def test_non_strict_mode_keeps_the_keyword_fallback():
    from jarvis.config.settings import CommandsConfig

    loose = CommandParser(CommandsConfig(strict=False, open_phrases=["ouvre le garage"], close_phrases=["ferme le garage"],
                                         cancel_phrases=["annule"]))
    assert loose.parse("jarvis ouvre moi le garage") == Intent.OPEN_GARAGE
    assert loose.parse("fermé la porte") == Intent.CLOSE_GARAGE
    assert loose.parse("annule") == Intent.CANCEL


def test_normalize():
    assert normalize("  Fermé  L'accès ") == "ferme l acces"


def test_framer():
    f = Framer(512)
    assert f.push(np.zeros(300, np.int16)) == []
    frames = f.push(np.zeros(800, np.int16))
    assert len(frames) == 2 and all(len(x) == 512 for x in frames)


# --- Relays -------------------------------------------------------------------------------

def test_lcus_frames():
    assert lcus_frame(1, True) == bytes.fromhex("A00101A2")
    assert lcus_frame(1, False) == bytes.fromhex("A00100A1")
    assert lcus_frame(2, True) == bytes.fromhex("A00201A3")


def test_dcttech_report():
    assert dcttech_report(1, True)[:3] == [0x00, 0xFF, 1]
    assert dcttech_report(2, False)[:3] == [0x00, 0xFD, 2]


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_hw(clock=None, door=None):
    class Door:
        def state(self):
            return door
    return Hardware(HardwareConfig(cooldown_s=5), MockOutput("garage"), MockOutput("green"), MockOutput("red"),
                    Door(), clock=clock or FakeClock(), sleep=lambda s: None)


def test_pulse_cooldown():
    clock = FakeClock()
    hw = make_hw(clock)
    assert hw.pulse_garage()
    assert hw.garage.history == [True, False]
    assert not hw.pulse_garage()
    clock.t += 6
    assert hw.pulse_garage()


# --- PTZ ----------------------------------------------------------------------------------

def test_ptz_centered_target_is_still():
    cfg = PTZConfig(zoom_enabled=False)
    # person centered (aim point at 30% of the box height = image center)
    assert compute_command((600, 250, 680, 1000), 1280, 1000, cfg).is_zero


def test_ptz_directions():
    cfg = PTZConfig(zoom_enabled=False)
    right = compute_command((1100, 400, 1200, 700), 1280, 720, cfg)
    assert right.pan > 0
    top = compute_command((600, 0, 680, 100), 1280, 720, cfg)
    assert top.tilt > 0  # ONVIF: positive y = up
    assert abs(right.pan) <= cfg.max_speed


def test_ptz_zoom_in_when_small_and_centered():
    cfg = PTZConfig()
    cmd = compute_command((620, 300, 660, 420), 1280, 720, cfg)
    assert cmd.zoom > 0


def test_ptz_tracker_returns_home():
    calls = []

    class Rec:
        def move(self, *a): calls.append(("move", a))
        def stop(self): calls.append(("stop",))
        def goto_preset(self, p): calls.append(("preset", p))

    clock = FakeClock(0)
    tr = PTZTracker(Rec(), PTZConfig(zoom_enabled=False, return_home_after_s=30), clock=clock)
    tr.update((1100, 300, 1200, 600), 1280, 720)
    assert calls[-1][0] == "move"
    clock.t = 1
    tr.update(None, 1280, 720)
    assert calls[-1] == ("stop",)
    clock.t = 40
    tr.update(None, 1280, 720)
    assert calls[-1] == ("preset", "1")
    tr.update(None, 1280, 720)
    assert calls.count(("preset", "1")) == 1


# --- Vision / faces ------------------------------------------------------------------------

def test_select_target_prefers_previous_then_large_central():
    small_edge = Track(1, (0, 0, 50, 100), 0.9)
    big_center = Track(2, (500, 100, 800, 700), 0.9)
    assert select_target([small_edge, big_center], 1280, 720, None).id == 2
    assert select_target([small_edge, big_center], 1280, 720, 1).id == 1
    assert select_target([], 1280, 720, 1) is None


def test_face_region_clipped():
    x1, y1, x2, y2 = face_region((0, 0, 100, 400), 1280, 720)
    assert x1 == 0 and y1 == 0 and y2 == int(0.55 * 400)


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def test_gallery_match():
    g = EmbeddingGallery(dim=3)
    assert g.match(unit([1, 0, 0])) == (None, 0.0)
    g.load([(7, unit([1, 0, 0])), (9, unit([0, 1, 0]))])
    pid, score = g.match(unit([0.9, 0.1, 0]))
    assert pid == 7 and score > 0.9


def test_resolver_votes_before_known():
    clock = FakeClock(0)
    r = IdentityResolver(FaceConfig(votes_required=3, match_threshold=0.45), clock=clock)
    emb = unit([1, 0, 0])
    assert r.observe(1, 5, 0.6, emb, None, 1.0) is None
    assert r.observe(1, 5, 0.3, emb, None, 1.0) is None  # below threshold: not counted
    assert r.observe(1, 5, 0.7, emb, None, 1.0) is None
    d = r.observe(1, 5, 0.65, emb, None, 1.0)
    assert d.kind == "known" and d.person_id == 5 and d.score == 0.7
    assert not r.needs_check(1)
    assert r.observe(1, 5, 0.9, emb, None, 1.0) is None  # decision is made only once


def test_resolver_unknown_keeps_best_crop():
    r = IdentityResolver(FaceConfig(unknown_after_observations=3), clock=FakeClock(0))
    crops = [np.full((2, 2, 3), i, np.uint8) for i in range(3)]
    for q, crop in zip([0.2, 0.9, 0.5], crops[:2] + crops[2:]):
        d = r.observe(4, None, 0.1, unit([0, 0, 1]), crop, q)
    assert d.kind == "unknown"
    assert (d.crop == crops[1]).all()
    r.prune(set())
    assert r.label(4) is None


# --- Decision -------------------------------------------------------------------------------

class Harness:
    def __init__(self, tmp_path, speaker=False, door=None):
        self.s = Settings()
        self.s.speaker.enabled = speaker
        self.db = Database(tmp_path / "t.db")
        self.db.init()
        self.clock = FakeClock(10_000)
        self.hw = make_hw(FakeClock(), door=door)
        self.said, self.listen, self.windows = [], [], []
        self.engine = DecisionEngine(self.s, self.db, self.hw, self.said.append, self.listen.append,
                                     queue.Queue(), clock=self.clock, today=lambda: date(2026, 9, 29))
        self.engine.activate_voice = self.windows.append
        self.alice = self.db.add_person("Alice", can_open_garage=True)
        self.bob = self.db.add_person("Bob", can_open_garage=False)

    def face(self, pid):
        self.engine.handle(FaceRecognized(1, pid, "", 0.8, ts=self.clock.t))

    def voice(self, intent, speaker=None, text="ouvre la porte du garage"):
        self.engine.handle(VoiceCommand(text, intent, speaker, 0.5 if speaker else None))

    def pulses(self):
        return self.hw.garage.history.count(True)


def test_greets_once_per_day(tmp_path):
    h = Harness(tmp_path)
    h.face(h.alice)
    h.face(h.alice)
    assert h.said == ["Bonjour Alice"]
    assert h.hw.green.state is True
    assert h.listen == []                  # no listening without the "Jarvis" wake word
    assert h.windows == [30.0, 30.0]       # voice window = recognition window, extended each time


def test_recognition_window_led_and_voice(tmp_path):
    h = Harness(tmp_path)
    h.s.decision.auth_window_s = 45
    h.face(h.bob)                          # recognized but not allowed to open: LED only, no voice
    assert h.hw.green.state is True and h.windows == []
    assert not h.db.list_events(type_="voice_window_opened")
    h.face(h.alice)
    assert h.windows == [45]
    ev = h.db.list_events(type_="voice_window_opened")[0]
    assert ev["person_id"] == h.alice and ev["details"]["seconds"] == 45
    h.voice(Intent.OPEN_GARAGE)
    assert h.pulses() == 1                 # within the window
    h.clock.t += 46
    h.voice(Intent.CLOSE_GARAGE)
    assert h.pulses() == 1                 # window over: refused


def test_unknown_lights_red(tmp_path):
    h = Harness(tmp_path)
    h.engine.handle(UnknownFaceSeen(3, 12))
    assert h.hw.red.state is True and h.hw.green.state is False
    assert h.db.list_events(type_="face_unknown")


def test_voice_without_face_is_denied(tmp_path):
    h = Harness(tmp_path)
    h.voice(Intent.OPEN_GARAGE)
    assert h.pulses() == 0
    assert "refusé" in h.said[-1]


def test_voice_after_authorized_face_opens(tmp_path):
    h = Harness(tmp_path)
    h.face(h.alice)
    h.voice(Intent.OPEN_GARAGE)
    assert h.pulses() == 1
    ev = h.db.list_events(type_="garage_pulse")[0]
    assert ev["person_id"] == h.alice and ev["details"]["intent"] == "open_garage"


def test_non_authorized_person_cannot_open(tmp_path):
    h = Harness(tmp_path)
    h.face(h.bob)
    h.voice(Intent.OPEN_GARAGE)
    assert h.pulses() == 0


def test_authorization_window_expires(tmp_path):
    h = Harness(tmp_path)
    h.face(h.alice)
    h.clock.t += h.s.decision.auth_window_s + 1
    h.voice(Intent.OPEN_GARAGE)
    assert h.pulses() == 0


def test_speaker_must_match_recognized_face(tmp_path):
    h = Harness(tmp_path, speaker=True)
    h.face(h.alice)
    h.voice(Intent.OPEN_GARAGE, speaker=None)
    h.voice(Intent.OPEN_GARAGE, speaker=h.bob)
    assert h.pulses() == 0
    h.voice(Intent.OPEN_GARAGE, speaker=h.alice)
    assert h.pulses() == 1


def test_door_sensor_prevents_toggle_in_wrong_direction(tmp_path):
    h = Harness(tmp_path, door="open")
    h.face(h.alice)
    h.voice(Intent.OPEN_GARAGE)
    assert h.pulses() == 0 and "déjà ouvert" in h.said[-1]
    h.voice(Intent.CLOSE_GARAGE, text="ferme le garage")
    assert h.pulses() == 1


def test_manual_pulse_logged(tmp_path):
    h = Harness(tmp_path)
    h.engine.handle(ManualGarage(actor="admin"))
    assert h.pulses() == 1
    assert h.db.list_events(type_="garage_pulse")[0]["actor"] == "admin"


def test_ptzcommand_zero():
    assert PTZCommand().is_zero and not PTZCommand(pan=0.1).is_zero


def test_voice_assistant_sleeps_outside_the_recognition_window():
    import threading
    import time as _time

    import numpy as np

    from jarvis.voice.assistant import VoiceAssistant

    class FakeWake:
        frame_length = 1280

        def process(self, frame):
            return True               # the wake word is "heard" on every frame

        def reset(self):
            pass

    class FakeTTS:
        speaking = threading.Event()

        def beep(self):
            pass

    va = VoiceAssistant(Settings(), queue.Queue(), FakeTTS(), None)
    va.wake = FakeWake()
    va.framer = __import__("jarvis.voice.audio", fromlist=["Framer"]).Framer(1280)
    listened = []
    va._listen = lambda seconds, via_wakeword: listened.append(via_wakeword)
    feeder = threading.Thread(target=va._loop, daemon=True)

    def feed(n):
        for _ in range(n):
            va._audio.put(np.zeros(1280, dtype=np.int16))
        _time.sleep(0.3)

    feeder.start()
    feed(5)
    assert listened == [] and va.state == "sleeping"          # no window: the microphone is ignored
    va.activate(30)
    assert 29 <= va.window_remaining_s <= 30
    feed(2)
    assert listened and listened[0] is True                     # window open: wake word -> command
    va.activate(5)
    assert va.window_remaining_s > 25                            # a shorter call never shortens it
    va._active_until = 0.0
    listened.clear()
    feed(5)
    assert listened == [] and va.state == "sleeping"
    va.stop()
    feeder.join(2)


def test_ensure_yolo_config_dir_creates_it(tmp_path, monkeypatch):
    from jarvis.vision import ensure_yolo_config_dir

    target = tmp_path / ".config" / "Ultralytics"
    monkeypatch.setenv("YOLO_CONFIG_DIR", str(target))
    ensure_yolo_config_dir()
    assert target.is_dir()
