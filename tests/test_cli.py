# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_cli.py
# Purpose : Tests for the cmd2 admin shell (one-shot commands, exit codes, audit)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""cmd2 admin shell: one-shot commands, exit codes, auditing of changes."""

from datetime import datetime

import numpy as np
import pytest

from jarvis.cli.shell import JarvisShell, run
from jarvis.config.settings import Settings
from jarvis.storage.database import Database


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.control.socket_path = str(tmp_path / "absent.sock")   # core not running: notifications are ignored
    s.detector.model_path = str(tmp_path / "absent-model")
    return s


def db_of(s):
    db = Database(s.storage.db_path)
    db.init()
    return db


def test_hyphenated_commands_and_exit_codes(settings, capsys):
    assert run(settings, ["check-config", "api"]) == 0          # dash accepted; the API loads no model
    assert run(settings, ["check-config"]) == 1                 # core: YOLO model missing
    assert "YOLO model missing" in capsys.readouterr().err
    assert run(settings, ["sightings", "--limit", "abc"]) == 2  # argument error
    assert run(settings, ["version"]) == 0


def test_person_create_update_diff_audited(settings, capsys):
    assert run(settings, ["person", "--first-name", "Karim", "--garage", "yes"]) == 0
    db = db_of(settings)
    pid = db.list_persons()[0]["id"]
    assert run(settings, ["person", str(pid), "--days", "12345", "--hours", "08:00-18:00", "--until", "2026-10-31"]) == 0
    p = db.get_person(pid)
    assert (p.access_days, p.access_start, p.access_end) == ("12345", "08:00", "18:00")
    assert datetime.fromtimestamp(p.valid_until).strftime("%Y-%m-%d %H:%M") == "2026-10-31 23:59"
    ev = db.list_events(type_="person_updated")[0]
    assert ev["actor"].startswith("cli") and ev["details"]["via"] == "cli"
    assert {c["field"] for c in ev["details"]["changes"]} == {"access_days", "access_start", "access_end", "valid_until"}
    assert run(settings, ["person", str(pid), "--days", "89"]) == 1
    assert run(settings, ["person-delete", str(pid), "--yes"]) == 0
    assert db.get_person(pid) is None and db.list_events(type_="person_deleted")


def test_settings_set_reset_and_validation(settings, capsys):
    assert run(settings, ["set", "faces.match_threshold", "0.52"]) == 0
    assert "0.45 → 0.52" in capsys.readouterr().out
    db = db_of(settings)
    assert db.get_settings_overrides()["faces.match_threshold"] == 0.52
    assert run(settings, ["set", "faces.match_threshold", "5"]) == 1          # out of range
    assert run(settings, ["set", "ptz.password", "x"]) == 1                    # secret, not editable
    assert run(settings, ["set", "faces.adaptive_enabled", "no"]) == 0
    assert run(settings, ["set", "commands.open_phrases", "open the garage; open the door"]) == 0
    assert db.get_settings_overrides()["commands.open_phrases"] == ["open the garage", "open the door"]
    assert db.list_events(type_="settings_changed")[0]["details"]["changes"][0]["field"] == "commands.open_phrases"
    assert run(settings, ["reset", "all"]) == 0 and db.get_settings_overrides() == {}


def test_sightings_time_window_and_csv(settings, tmp_path):
    db = db_of(settings)
    for hh in (6, 8, 20):
        db.add_sighting("unknown", 1, None, 0.1, 0.5, None, np.zeros(512, np.float32), cluster_id=3,
                        ts=datetime(2026, 9, 28, hh, 0).timestamp())
    out = tmp_path / "s.csv"
    assert run(settings, ["sightings", "--start", "2026-09-28", "--end", "2026-09-28", "--from", "07:00", "--to",
                          "21:00", "--csv", str(out)]) == 0
    lines = out.read_text().splitlines()
    assert lines[0].startswith("id;date;time") and len(lines) == 3 and "unknown #3" in lines[1]


def test_audit_verify_and_events_admin(settings, capsys):
    run(settings, ["set", "decision.led_on_s", "7"])
    assert run(settings, ["audit-verify"]) == 0
    assert run(settings, ["events", "--admin", "--limit", "5"]) == 0
    assert "led_on_s" in capsys.readouterr().out


def test_help_lists_categories(settings, capsys):
    JarvisShell(settings, interactive=False).onecmd_plus_hooks("help")
    out = capsys.readouterr().out
    for cat in ("Administration", "Traceability", "Settings", "Maintenance", "Services"):
        assert cat in out
