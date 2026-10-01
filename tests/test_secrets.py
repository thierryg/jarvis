# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_secrets.py
# Purpose : Centralized configuration and write-only secrets
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Precedence parameter > environment > file, credential injection, write-only secrets in the API,
the shell and the audit log, legacy metrics-token migration and webhook bearer token."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from jarvis.cli.shell import run
from jarvis.config.settings import (VALUE_SOURCES, Settings, load_settings, resolve_credentials,
                                    rtsp_url_with_credentials)
from jarvis.core.notify import WebhookNotifier
from jarvis.config.settings import NotificationsConfig, SecretsConfig
from jarvis.storage.database import Database
from jarvis.web.app import create_app

H = {"X-Jarvis": "1"}
PW = "correct horse battery"


def write_cfg(tmp_path, body: str):
    f = tmp_path / "config.yaml"
    f.write_text(body)
    return f


def test_precedence_file_then_environment(tmp_path, monkeypatch):
    cfg = write_cfg(tmp_path, "faces:\n  match_threshold: 0.42\nsecrets:\n  camera_password: from-file\n")
    s = load_settings(cfg)
    assert s.faces.match_threshold == 0.42 and VALUE_SOURCES["faces.match_threshold"] == "file"
    monkeypatch.setenv("JARVIS__FACES__MATCH_THRESHOLD", "0.51")
    monkeypatch.setenv("JARVIS__SECRETS__CAMERA_PASSWORD", "0123")      # secrets stay strings
    s = load_settings(cfg)
    assert s.faces.match_threshold == 0.51 and VALUE_SOURCES["faces.match_threshold"] == "environment"
    assert s.secrets.camera_password == "0123"
    assert "0123" not in repr(s) and "from-file" not in repr(s)


def test_credentials_injected_into_rtsp_and_ptz(tmp_path):
    s = Settings()
    s.camera.rtsp_url = "rtsp://10.0.0.5:554/Streaming/Channels/102"
    s.secrets.camera_username, s.secrets.camera_password = "jarvis", "p@ss:w/rd"
    resolve_credentials(s)
    assert rtsp_url_with_credentials(s) == "rtsp://jarvis:p%40ss%3Aw%2Frd@10.0.0.5:554/Streaming/Channels/102"
    assert (s.ptz.username, s.ptz.password) == ("jarvis", "p@ss:w/rd")
    s.camera.rtsp_url = "rtsp://explicit:pw@10.0.0.5/x"                 # explicit URL credentials win
    assert rtsp_url_with_credentials(s) == "rtsp://explicit:pw@10.0.0.5/x"


@pytest.fixture
def api(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("admin", PasswordHasher().hash(PW))
    c = TestClient(create_app(s, db, type("Core", (), {"call": lambda self, cmd, **k: {"ok": True}})()))
    assert c.post("/api/login", json={"username": "admin", "password": PW}, headers=H).status_code == 200
    return c, s, db


def secret_entry(client, key):
    view = client.get("/api/settings").json()
    return next(p for g in view["groups"] for p in g["params"] if p["key"] == key)


def test_secret_is_write_only_and_masked_in_audit(api):
    c, s, db = api
    e = secret_entry(c, "secrets.camera_password")
    assert e["type"] == "secret" and e["value"] is None and e["is_set"] is False
    r = c.put("/api/settings", json={"changes": {"secrets.camera_password": "S3cr3t!"}}, headers=H)
    assert r.status_code == 200 and "S3cr3t!" not in r.text
    assert r.json()["changes"] == [{"field": "secrets.camera_password", "before": "(empty)", "after": "(set)"}]
    assert db.get_settings_overrides()["secrets.camera_password"] == "S3cr3t!"   # stored for real
    assert s.secrets.camera_password == "S3cr3t!"
    e = secret_entry(c, "secrets.camera_password")
    assert e["value"] is None and e["is_set"] is True and e["source"] == "parameter"
    ev = db.list_events(type_="settings_changed")[0]
    assert "S3cr3t!" not in json.dumps(ev) and ev["details"]["changes"][0]["after"] == "(set)"
    assert "S3cr3t!" not in c.get("/api/audit.csv").text


def test_legacy_metrics_token_is_migrated(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    db = Database(s.storage.db_path)
    db.init()
    db.set_settings_overrides({"_secret.metrics_token": "legacy-token"}, actor="system")
    create_app(s, db)
    overrides = db.get_settings_overrides()
    assert "_secret.metrics_token" not in overrides and overrides["secrets.metrics_token"] == "legacy-token"
    assert s.secrets.metrics_token == "legacy-token"


def test_metrics_token_from_secrets(api):
    c, s, db = api
    s.monitoring.metrics_enabled = True
    s.secrets.metrics_token = "tok-123"
    assert c.get("/metrics", headers={"X-Real-IP": "192.168.1.50"}).status_code == 401
    r = c.get("/metrics", headers={"X-Real-IP": "192.168.1.50", "Authorization": "Bearer tok-123"})
    assert r.status_code == 200 and "jarvis_info" in r.text


def test_webhook_sends_bearer_token():
    seen = []

    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.headers.get("Authorization"))
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    n = WebhookNotifier(NotificationsConfig(webhook_url=f"http://127.0.0.1:{srv.server_port}/", events=["face_unknown"]),
                        SecretsConfig(webhook_token="hook-secret"))
    n.start()
    n("face_unknown")
    n.stop()
    n.join(5)
    srv.shutdown()
    assert seen == ["Bearer hook-secret"]


def test_shell_masks_secrets(tmp_path, capsys):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.control.socket_path = str(tmp_path / "none.sock")
    assert run(s, ["set", "secrets.webhook_token", "abc-xyz"]) == 0
    out = capsys.readouterr().out
    assert "abc-xyz" not in out and "(set)" in out
    assert run(s, ["settings", "Secrets"]) == 0
    assert "abc-xyz" not in capsys.readouterr().out
