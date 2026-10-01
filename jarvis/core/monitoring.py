# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/monitoring.py
# Purpose : Prometheus metrics and node_exporter / Promtail / Alloy management
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Monitoring: Jarvis Prometheus metrics and control of the node_exporter / Promtail / Alloy agents.

Privilege separation:
- the (unprivileged) API writes the **desired state** to ``<state_dir>/desired.json``;
- the ``jarvis-monitoring.path`` unit detects the change and starts
  ``jarvis-monitoring-apply.service`` (root, oneshot), which runs
  ``jarvis monitoring-apply``: validation, log shipper configuration generation, and
  enabling/disabling of the services;
- the outcome is written to ``<state_dir>/status.json`` and shown on the Settings page.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from jarvis import __version__
from jarvis.config.settings import Settings

NODE_EXPORTER = "prometheus-node-exporter.service"
SHIPPERS = {
    "promtail": {"service": "promtail.service", "config": "/etc/promtail/config.yml",
                 "template": "promtail/config.yml.tmpl", "package": "promtail"},
    "alloy": {"service": "alloy.service", "config": "/etc/alloy/config.alloy",
              "template": "alloy/config.alloy.tmpl", "package": "alloy"},
}
# Strict http(s) URL whitelist: the value ends up in a root-written config file.
URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-:\[\]]+(/[A-Za-z0-9._~/\-]*)?$")
TEMPLATES = Path(__file__).resolve().parents[1] / "provisioning" / "templates"


# --- Desired state / actual state (exchange files between the API and the root service) -------

def desired_state(settings: Settings) -> dict:
    """Build the desired monitoring state from the current settings.

    Args:
        settings: Application settings.

    Returns:
        JSON-serializable dict written to ``desired.json``.
    """
    m = settings.monitoring
    return {"node_exporter": m.node_exporter_enabled, "log_shipper": m.log_shipper, "loki_url": m.loki_url,
            "loki_username": settings.secrets.loki_username, "loki_password": settings.secrets.loki_password,
            "log_path": str(Path(settings.logging.file_path).parent), "hostname": os.uname().nodename,
            "requested_at": time.time()}


def write_monitoring_state(settings: Settings) -> Path:
    """Atomically write ``desired.json``, which triggers the systemd ``.path`` unit.

    Args:
        settings: Application settings.

    Returns:
        Path of the written ``desired.json``.
    """
    d = Path(settings.monitoring.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "desired.json.tmp"
    # 0600: the file may carry the Loki credentials (read by the root apply service only).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(desired_state(settings), indent=2))
    os.replace(tmp, d / "desired.json")
    return d / "desired.json"


def read_monitoring_status(settings: Settings) -> dict:
    """Read the last apply report from ``status.json``.

    Args:
        settings: Application settings.

    Returns:
        The report dict, or a placeholder with ``applied_at=None`` if it was never applied.
    """
    try:
        return json.loads((Path(settings.monitoring.state_dir) / "status.json").read_text())
    except (OSError, ValueError):
        return {"applied_at": None, "message": "never applied (is the jarvis-monitoring service installed?)"}


# --- Apply (run as root by jarvis-monitoring-apply.service) --------------------------------------

def _systemctl(*args: str) -> str:
    """Run ``systemctl`` and return its combined, stripped stdout and stderr."""
    r = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr).strip()


def _unit_exists(unit: str) -> bool:
    """Return whether systemd knows the given unit."""
    return bool(subprocess.run(["systemctl", "cat", unit], capture_output=True, timeout=30).returncode == 0)


SAFE_CRED = re.compile(r"^[^\s\"'\\{}]*$")  # no whitespace, quotes, backslash or braces: cannot break the config


def basic_auth_block(kind: str, username: str, password: str) -> str:
    """Basic-auth snippet for the Loki client of the given agent, or an empty string."""
    if not username:
        return ""
    if not (SAFE_CRED.match(username) and SAFE_CRED.match(password)):
        raise ValueError("Loki credentials contain forbidden characters")
    if kind == "promtail":
        return f"    basic_auth:\n      username: {username}\n      password: {password}\n"
    return f'    basic_auth {{\n      username = "{username}"\n      password = "{password}"\n    }}\n'


def render_shipper_config(kind: str, loki_url: str, log_path: str, hostname: str, username: str = "",
                          password: str = "") -> str:  # nosec B107
    """Render the log shipper configuration from its template.

    Args:
        kind: Shipper name, a key of :data:`SHIPPERS`.
        loki_url: Loki push URL (must already be validated).
        log_path: Directory containing the Jarvis logs.
        hostname: Host label; characters outside ``[A-Za-z0-9.-]`` are stripped.

    Returns:
        The rendered configuration text.
    """
    tpl = (TEMPLATES / SHIPPERS[kind]["template"]).read_text()
    return (tpl.replace("{{LOKI_URL}}", loki_url).replace("{{LOG_PATH}}", log_path)
               .replace("{{BASIC_AUTH}}", basic_auth_block(kind, username, password))
               .replace("{{HOSTNAME}}", re.sub(r"[^A-Za-z0-9.\-]", "", hostname)))


def apply(state_dir: str) -> dict:
    """Apply ``desired.json`` and write the resulting ``status.json``.

    ``desired.json`` is written by the unprivileged API, so no field is trusted without
    being validated again here (this function runs as root).

    Args:
        state_dir: Directory holding ``desired.json`` and ``status.json``.

    Returns:
        The report also written to ``status.json`` (per-agent state, ``errors`` list and
        a summary ``message``).
    """
    d = Path(state_dir)
    desired = json.loads((d / "desired.json").read_text())
    report: dict = {"applied_at": time.time(), "node_exporter": {}, "log_shipper": {}, "errors": []}

    want_ne = bool(desired.get("node_exporter"))
    if _unit_exists(NODE_EXPORTER):
        _systemctl("enable" if want_ne else "disable", "--now", NODE_EXPORTER)
        report["node_exporter"] = {"installed": True, "wanted": want_ne,
                                   "active": _systemctl("is-active", NODE_EXPORTER)}
    else:
        report["node_exporter"] = {"installed": False, "wanted": want_ne}
        if want_ne:
            report["errors"].append("node_exporter missing: sudo apt install prometheus-node-exporter")

    kind = desired.get("log_shipper", "none")
    url = str(desired.get("loki_url") or "")
    log_path = str(desired.get("log_path") or "/var/log/jarvis")
    for name, meta in SHIPPERS.items():
        present = _unit_exists(meta["service"])
        # Only one shipper may run at a time: disable every other one.
        if name != kind:
            if present:
                _systemctl("disable", "--now", meta["service"])
            continue
        if not present:
            report["errors"].append(f"{name} missing: install the \"{meta['package']}\" package (apt.grafana.com repository)")
            continue
        if not URL_RE.match(url):
            report["errors"].append("invalid Loki URL: the agent was not enabled")
            continue
        if not log_path.startswith("/var/log/"):
            report["errors"].append("log path rejected")
            continue
        cfg = Path(meta["config"])
        cfg.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = render_shipper_config(name, url, log_path, str(desired.get("hostname", "")),
                                            str(desired.get("loki_username") or ""),
                                            str(desired.get("loki_password") or ""))
        except ValueError as exc:
            report["errors"].append(str(exc))
            continue
        cfg.touch(mode=0o600)
        cfg.chmod(0o600)  # may contain the Loki password
        cfg.write_text(content)
        _systemctl("enable", "--now", meta["service"])
        _systemctl("restart", meta["service"])
        report["log_shipper"] = {"kind": name, "active": _systemctl("is-active", meta["service"]), "loki_url": url}
    if kind == "none":
        report["log_shipper"] = {"kind": "none"}
    report["message"] = "; ".join(report["errors"]) or "applied"
    tmp = d / "status.json.tmp"
    tmp.write_text(json.dumps(report, indent=2))
    os.replace(tmp, d / "status.json")
    # Hand the report back to the service user so the API can read it.
    shutil.chown(d / "status.json", "jarvis", "jarvis") if _user_exists("jarvis") else None
    return report


def _user_exists(name: str) -> bool:
    """Return whether the local system user ``name`` exists."""
    import pwd

    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


# --- Prometheus metrics (text exposition format 0.0.4) ---------------------------------------------

# Cache for the (costly) full audit chain verification.
_verify_cache: dict = {"ts": 0.0, "result": None}


def _metric(lines: list[str], name: str, help_: str, kind: str, samples: list[tuple[dict, float]]) -> None:
    """Append one metric family (HELP, TYPE and samples) to ``lines``.

    Args:
        lines: Output buffer.
        name: Metric name.
        help_: HELP text.
        kind: Prometheus metric type (``gauge``, ``counter``...).
        samples: ``(labels, value)`` pairs; double quotes are stripped from label values.
    """
    lines.append(f"# HELP {name} {help_}")
    lines.append(f"# TYPE {name} {kind}")
    for labels, value in samples:
        lab = ",".join(f'{k}="{str(v).replace(chr(34), "")}"' for k, v in labels.items())
        lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")


def render_metrics(settings: Settings, db, core_status: dict | None) -> str:
    """Render the Jarvis metrics in the Prometheus text format.

    The ``*_last_24h`` series are gauges recomputed on every scrape, not counters.

    Args:
        settings: Application settings.
        db: :class:`~jarvis.storage.database.Database` instance.
        core_status: Status returned by the core, or ``None`` if it is unreachable.

    Returns:
        The metrics payload.
    """
    now = time.time()
    lines: list[str] = []
    st = core_status or {}
    _metric(lines, "jarvis_info", "Jarvis version", "gauge", [({"version": __version__}, 1)])
    _metric(lines, "jarvis_core_up", "Core service reachable from the API", "gauge", [({}, 1 if st.get("ok") else 0)])
    _metric(lines, "jarvis_camera_connected", "RTSP stream connected", "gauge",
            [({}, 1 if st.get("camera_connected") else 0)])
    _metric(lines, "jarvis_vision_fps", "Frames analyzed per second", "gauge", [({}, st.get("vision_fps") or 0)])
    _metric(lines, "jarvis_tracks", "Tracked people", "gauge", [({}, st.get("tracks") or 0)])
    _metric(lines, "jarvis_known_embeddings", "Enrolled faces in the gallery", "gauge",
            [({}, st.get("known_embeddings") or 0)])
    _metric(lines, "jarvis_authorized_persons", "People within the authorization window", "gauge",
            [({}, len(st.get("authorized_persons") or []))])
    door = st.get("door")
    _metric(lines, "jarvis_door_open", "Door open (1), closed (0), unknown (-1)", "gauge",
            [({}, 1 if door == "open" else (0 if door == "closed" else -1))])
    with db.connect() as c:
        persons = c.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        sightings = c.execute("SELECT status, COUNT(*) FROM sightings WHERE ts >= ? GROUP BY status",
                              (now - 86400,)).fetchall()
        events = c.execute("SELECT type, COUNT(*) FROM events WHERE ts >= ? GROUP BY type", (now - 86400,)).fetchall()
        sessions = c.execute("SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL").fetchone()[0]
        unknown = c.execute("SELECT COUNT(DISTINCT cluster_id) FROM unknown_faces").fetchone()[0]
    _metric(lines, "jarvis_persons", "Enrolled people", "gauge", [({}, persons)])
    _metric(lines, "jarvis_unknown_visitors", "Retained unknown visitor clusters", "gauge", [({}, unknown)])
    _metric(lines, "jarvis_sightings_last_24h", "Sightings over the last 24 h by status", "gauge",
            [({"status": s}, n) for s, n in sightings] or [({"status": "known"}, 0)])
    _metric(lines, "jarvis_events_last_24h", "Events over the last 24 h by type", "gauge",
            [({"type": t}, n) for t, n in events])
    _metric(lines, "jarvis_web_sessions_active", "Open web sessions", "gauge", [({}, sessions)])
    if now - _verify_cache["ts"] > 300:  # full chain verification at most every 5 minutes
        _verify_cache.update(ts=now, result=db.verify_events())
    v = _verify_cache["result"]
    _metric(lines, "jarvis_audit_chain_ok", "Audit hash chain integrity", "gauge", [({}, 1 if v["ok"] else 0)])
    _metric(lines, "jarvis_audit_sealed_events", "Verified sealed events", "gauge", [({}, v["checked"])])
    data = Path(settings.storage.data_dir)
    db_size = sum(p.stat().st_size for p in data.glob("jarvis.db*") if p.is_file())
    _metric(lines, "jarvis_db_size_bytes", "SQLite database size (including WAL)", "gauge", [({}, db_size)])
    _metric(lines, "jarvis_data_disk_free_bytes", "Free space on the data volume", "gauge",
            [({}, shutil.disk_usage(data).free)])
    backups = sorted(Path("/var/backups/jarvis").glob("jarvis-*.tar.gz")) if os.access("/var/backups/jarvis", os.R_OK) else []
    _metric(lines, "jarvis_backups", "Backup archives present", "gauge", [({}, len(backups))])
    _metric(lines, "jarvis_backup_last_timestamp_seconds", "Timestamp of the latest backup", "gauge",
            [({}, backups[-1].stat().st_mtime if backups else 0)])
    logf = Path(settings.logging.file_path)
    _metric(lines, "jarvis_log_file_size_bytes", "Current log file size", "gauge",
            [({}, logf.stat().st_size if logf.exists() else 0)])
    return "\n".join(lines) + "\n"
