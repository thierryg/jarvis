# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_scripts.py
# Purpose : Smoke tests of the operator scripts: hardware inventory, SSH MOTD, deploy wrapper
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""The bash tools run on any Linux host, honor their documented exit codes and never fail on a
missing optional probe (hwinfo, MOTD), and the shared library reports errors with a location."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or not Path("/proc/stat").exists(),
                                reason="Linux with bash required")
ENV = {**os.environ, "NO_COLOR": "1"}


def run(*cmd, timeout=60):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=ENV, timeout=timeout)


def test_hwinfo_json_and_verdict():
    r = run("bash", ROOT / "scripts/jarvis-hwinfo.sh", "--json", "--section", "cpu", "--section", "memory")
    assert r.returncode in (0, 8), r.stderr
    data = json.loads(r.stdout)
    assert {"cpu", "memory", "verdict"} <= set(data)
    assert data["cpu"]["Model"] and "Topology" in data["cpu"]
    assert all(v["level"] in ("ok", "warn", "fail") for v in data["verdict"])
    assert "\x1b[" not in r.stdout                        # no color codes in JSON


def test_hwinfo_usage_errors():
    assert run("bash", ROOT / "scripts/jarvis-hwinfo.sh", "--section", "bogus").returncode == 2
    assert run("bash", ROOT / "scripts/jarvis-hwinfo.sh", "--frobnicate").returncode == 2
    assert "Usage:" in run("bash", ROOT / "scripts/jarvis-hwinfo.sh", "--help").stdout


def test_motd_renders_without_jarvis_installed():
    r = run("bash", ROOT / "deploy/motd/jarvis-motd", "--no-logo", "--no-color", "--interval", "0.1")
    assert r.returncode == 0, r.stderr
    for heading in ("System metrics", "Hardware support", "Jarvis services", "Commands"):
        assert heading in r.stdout
    assert run("bash", ROOT / "deploy/motd/jarvis-motd", "--interval", "x").returncode == 2


def test_banner_is_80_columns():
    lines = (ROOT / "deploy/motd/issue.net").read_text().splitlines()
    assert lines and all(len(line) == 80 for line in lines)
    assert "RESTRICTED" in lines[2]


def test_deploy_wrapper_usage():
    r = run("bash", ROOT / "deploy/ansible/jarvis-deploy.sh", "--bogus")
    assert r.returncode == 2
    assert "Commands:" in run("bash", ROOT / "deploy/ansible/jarvis-deploy.sh", "--help").stdout


def test_common_library_reports_failures(tmp_path):
    script = tmp_path / "t.sh"
    script.write_text(f'. "{ROOT}/scripts/lib/jarvis-common.sh"\nf() {{ false; }}\nf\necho unreachable\n')
    r = run("bash", script)
    assert r.returncode == 1 and "unreachable" not in r.stdout
    assert "command failed" in r.stderr and "false" in r.stderr
    script.write_text(f'. "{ROOT}/scripts/lib/jarvis-common.sh"\ndie "bad input" "$E_CONFIG"\n')
    r = run("bash", script)
    assert r.returncode == 5 and "bad input" in r.stderr and "command failed" not in r.stderr


@pytest.mark.parametrize("script", ["jarvis-start.sh", "jarvis-status.sh", "jarvis-stop.sh", "jarvis-clean.sh"])
def test_docker_scripts_usage(script):
    path = ROOT / "docker" / script
    assert "Usage:" in run("bash", path, "--help").stdout
    assert run("bash", path, "--frobnicate").returncode == 2


def _fake_docker(tmp_path, compose_ok=True):
    """PATH with a stub ``docker``: ``compose version`` succeeds or not, ``info`` always fails."""
    stub = tmp_path / "bin" / "docker"
    stub.parent.mkdir()
    stub.write_text("#!/bin/sh\n"
                    f'[ "$1 $2" = "compose version" ] && exit {0 if compose_ok else 1}\n'
                    'echo "Cannot connect to the Docker daemon" >&2; exit 1\n')
    stub.chmod(0o755)
    return {**ENV, "PATH": f"{stub.parent}:{os.environ['PATH']}"}


def test_docker_scripts_diagnose_missing_compose(tmp_path):
    r = subprocess.run(["bash", str(ROOT / "docker" / "jarvis-status.sh")], capture_output=True, text=True,
                       env=_fake_docker(tmp_path, compose_ok=False), timeout=60)
    assert r.returncode == 4 and "Compose v2 is missing" in r.stderr


def test_docker_scripts_diagnose_stopped_daemon(tmp_path):
    env = {**_fake_docker(tmp_path), "DOCKER_HOST": f"unix://{tmp_path}/none.sock"}
    r = subprocess.run(["bash", str(ROOT / "docker" / "jarvis-start.sh")], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 7 and "not running" in r.stderr and "systemctl enable --now docker" in r.stderr


def test_every_bash_script_is_named_jarvis():
    scripts = [p for p in ROOT.rglob("*.sh") if not {".git", "collections", ".ansible"} & set(p.parts)]
    assert scripts and all(p.name.startswith("jarvis-") for p in scripts), [str(p) for p in scripts if not p.name.startswith("jarvis-")]
    py = list((ROOT / "scripts").glob("*.py"))
    assert py and all(p.name.startswith("jarvis-") for p in py), [p.name for p in py]

