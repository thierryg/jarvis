# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_version_cert.py
# Purpose : Central version file, bump tool and the jarvis-cert TLS manager (local CA mode)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""The version has one source (jarvis/VERSION) and every consumer agrees; bump rules; jarvis-cert
issue/status/renew/revoke in local mode, with the documented exit codes."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

import jarvis
from jarvis.config.settings import Settings
from jarvis.integrations.mqtt import discovery_messages
from jarvis.config.settings import MqttConfig
from jarvis.storage.database import Database
from jarvis.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
CERT = ROOT / "scripts" / "jarvis-cert.sh"


def load_bump():
    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts" / "jarvis-bump-version.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_single_version_source():
    version = (ROOT / "jarvis" / "VERSION").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", version)
    assert jarvis.__version__ == version
    assert 'dynamic = ["version"]' in (ROOT / "pyproject.toml").read_text()
    assert discovery_messages(MqttConfig())[0][1]["device"]["sw_version"] == version
    assert "0.1.0" not in (ROOT / "jarvis" / "web" / "static" / "index.html").read_text()
    assert f"## [{version}]" in (ROOT / "CHANGELOG.md").read_text()


def test_version_served_to_authenticated_users_only(tmp_path):
    s = Settings()
    s.storage.data_dir = str(tmp_path)
    s.api.cookie_secure = False
    db = Database(s.storage.db_path)
    db.init()
    db.add_user("root", PasswordHasher().hash("correct horse battery"))
    c = TestClient(create_app(s, db, type("Core", (), {"call": lambda self, cmd, **k: {"ok": True}})()))
    assert "version" not in c.get("/api/ui").json()
    c.post("/api/login", json={"username": "root", "password": "correct horse battery"}, headers={"X-Jarvis": "1"})
    assert c.get("/api/me").json()["version"] == jarvis.__version__


def test_bump_rules():
    bv = load_bump()
    assert bv.next_version("0.2.0", "patch") == "0.2.1"
    assert bv.next_version("0.2.0", "minor") == "0.3.0"
    assert bv.next_version("0.2.9", "major") == "1.0.0"
    assert bv.next_version("1.0.0-rc.1", "patch") == "1.0.0"        # release the pre-release
    assert bv.sort_key("1.0.0-rc.1") < bv.sort_key("1.0.0")
    with pytest.raises(ValueError):
        bv.next_version("0.2.0", "1.2")
    text = "## [Unreleased]\n\n### Added\n- x\n\n[Unreleased]: https://h/r/compare/v0.2.0...HEAD\n"
    out = bv.roll_changelog(text, "0.3.0", "2026-10-01")
    assert "## [Unreleased]\n\n## [0.3.0] - 2026-10-01\n\n### Added" in out
    assert "[Unreleased]: https://h/r/compare/v0.3.0...HEAD" in out
    assert "[0.3.0]: https://h/r/compare/v0.2.0...v0.3.0" in out
    with pytest.raises(ValueError):
        bv.roll_changelog(out, "0.3.0", "2026-10-01")


needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None or shutil.which("bash") is None,
                                   reason="openssl and bash required")


def cert(tls, *args):
    env = {**os.environ, "JARVIS_TLS_DIR": str(tls), "JARVIS_CERT_NO_RELOAD": "1", "NO_COLOR": "1"}
    return subprocess.run(["bash", str(CERT), *args], capture_output=True, text=True, env=env, timeout=60)


@needs_openssl
def test_cert_local_lifecycle(tmp_path):
    tls = tmp_path / "tls"
    r = cert(tls, "issue", "--mode", "local", "--name", "jarvis.local", "--ip", "192.168.1.20")
    assert r.returncode == 0, r.stderr
    assert oct((tls / "jarvis.key").stat().st_mode & 0o777) == "0o600"
    assert oct((tls / "ca" / "jarvis-ca.key").stat().st_mode & 0o777) == "0o600"
    st = json.loads(cert(tls, "status", "--json").stdout)
    assert st["state"] == "VALID" and st["mode"] == "local" and st["key"] == "ok"
    assert st["revocation"]["status"] == "good" and st["chain"].startswith("ok")
    assert "DNS:jarvis.local" in st["san"] and "IP Address:192.168.1.20" in st["san"]
    assert 396 <= st["days_left"] <= 397
    assert cert(tls, "status", "--warn-days", "400").returncode == 8           # expiring soon
    assert "nothing to do" in cert(tls, "renew").stderr
    assert cert(tls, "revoke", "--yes").returncode == 0
    st = json.loads(cert(tls, "status", "--json").stdout)
    assert st["state"] == "REVOKED" and st["revocation"]["date"] and st["exit_code"] == 7
    assert cert(tls, "renew", "--force").returncode == 0                       # reissued
    assert cert(tls, "status").returncode == 0


@needs_openssl
def test_cert_rejects_bad_input(tmp_path):
    tls = tmp_path / "tls"
    assert cert(tls, "issue", "--mode", "local", "--name", "jarvis.example.com").returncode == 5
    assert cert(tls, "issue", "--mode", "local", "--ip", "8.8.8.8").returncode == 5
    assert cert(tls, "issue", "--mode", "letsencrypt", "--domain", "jarvis.local", "--email", "a@b.org").returncode == 5
    assert cert(tls, "frobnicate").returncode == 2
    assert cert(tls, "status").returncode == 4                                   # no certificate yet
    r = cert(tls, "--version")
    assert r.returncode == 0 and jarvis.__version__ in r.stdout
