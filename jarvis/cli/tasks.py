# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/cli/tasks.py
# Purpose : Presentation-free operational tasks shared by the CLI shell and the console
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Operational tasks shared by the cmd2 shell (``jarvis ...``) and the local console (tty1).

Each function does one thing and prints nothing: it returns a structured result or raises an
explicit exception, and the caller is responsible for presentation. Functions that need to
report progress take a ``report`` callable instead of printing directly.

Actions performed here are written to the audit log with the author ``cli:<Unix user>``.
"""

from __future__ import annotations

import getpass
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jarvis.config.settings import Settings
from jarvis.storage.database import Database

MIN_PASSWORD = 12


def actor() -> str:
    """Return the audit author for command-line actions: ``cli:<Unix user>`` (``cli`` if unknown)."""
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:
        return "cli"


def open_db(settings: Settings) -> Database:
    """Open the database and make sure its schema is up to date."""
    db = Database(settings.storage.db_path)
    db.init()
    return db


# --- Services ---------------------------------------------------------------------------------

def run_core(settings: Settings) -> None:
    """Run the core service (vision, voice, decision, hardware) until stopped."""
    from jarvis.core.service import Core

    Core(settings).run_forever()


def run_api(settings: Settings) -> None:
    """Run the web API (uvicorn) until stopped."""
    from jarvis.web.app import run

    run(settings)


# --- Accounts ---------------------------------------------------------------------------------

def set_user_password(settings: Settings, username: str, password: str, via: str = "cli") -> None:
    """Create a web account or replace its password (hashed with Argon2id).

    Args:
        settings: Loaded settings.
        username: Account name.
        password: Clear-text password (at least ``MIN_PASSWORD`` characters).
        via: Origin recorded in the audit event (``cli``, ``console``...).

    Raises:
        ValueError: If the password is too short.
    """
    from argon2 import PasswordHasher

    if len(password) < MIN_PASSWORD:
        raise ValueError(f"at least {MIN_PASSWORD} characters required")
    db = open_db(settings)
    existed = db.get_user_by_name(username) is not None
    db.add_user(username, PasswordHasher().hash(password))
    db.log_event("user_password_reset" if existed else "user_created", actor=actor(), username=username, via=via)


# --- Configuration check ----------------------------------------------------------------------

@dataclass
class CheckResult:
    """Outcome of :func:`check_config`.

    Attributes:
        problems: Blocking issues; the service must not start.
        warnings: Non-blocking issues worth the operator's attention.
    """

    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when there is no blocking problem."""
        return not self.problems


def check_config(settings: Settings, target: str = "core") -> CheckResult:
    """Pre-start checks (systemd ``ExecStartPre``): models present, hardware configured, secrets replaced.

    Args:
        settings: Loaded settings.
        target: ``core`` also checks ML models; ``api`` only checks common items.

    Returns:
        The problems and warnings found.
    """
    r = CheckResult()
    if target == "core":
        if not Path(settings.detector.model_path).exists():
            r.problems.append(f"YOLO model missing: {settings.detector.model_path} (run \"jarvis setup-models\")")
        if not Path(settings.faces.model_root, "models", settings.faces.model_name).exists():
            r.problems.append(f"InsightFace models missing in {settings.faces.model_root}")
        if settings.audio.enabled:
            for path, what in ((settings.stt.vosk_model_path, "Vosk model"), (settings.tts.piper_model, "Piper voice")):
                if not Path(path).exists():
                    r.problems.append(f"{what} missing: {path}")
    for name in ("garage", "led_green", "led_red"):
        out = getattr(settings.hardware, name)
        if out.backend == "mock":
            r.warnings.append(f"hardware.{name} in \"mock\" mode (no relay driven)")
        elif out.device and not Path(out.device).exists():
            r.warnings.append(f"hardware.{name}: device {out.device} missing (falling back to \"mock\")")
    if "user:pass@" in settings.camera.rtsp_url:
        r.warnings.append("camera.rtsp_url still contains the example credentials")
    if settings.logging.syslog_enabled and not settings.logging.syslog_host:
        r.warnings.append("syslog export enabled without a server (logging.syslog_host)")
    return r


# --- Backup / restore -------------------------------------------------------------------------

@dataclass
class BackupResult:
    """Outcome of :func:`backup`.

    Attributes:
        archive: Path of the archive just created.
        size: Archive size, in bytes.
        removed: Older archives deleted by rotation.
    """

    archive: Path
    size: int
    removed: list[Path]


def backup(settings: Settings, dest: str | Path, keep: int = 14) -> BackupResult:
    """Hot backup: SQLite database + media, with rotation.

    The database is copied with SQLite's online backup API, which yields a consistent snapshot
    even in WAL mode while the services keep running. The archive is ``chmod 600`` since it
    contains biometric data.

    Args:
        settings: Loaded settings.
        dest: Destination directory (created if needed).
        keep: Number of archives to keep; ``0`` or less disables rotation.

    Returns:
        The archive path, its size and the archives removed by rotation.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    data = Path(settings.storage.data_dir)
    archive = dest / f"jarvis-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "jarvis.db"
        with sqlite3.connect(settings.storage.db_path) as src, sqlite3.connect(snap) as dst:
            src.backup(dst)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(snap, arcname="jarvis.db")
            for sub in ("faces", "voice", "unknown", "sightings"):
                if (data / sub).exists():
                    tar.add(data / sub, arcname=sub)
    archive.chmod(0o600)
    removed = sorted(dest.glob("jarvis-*.tar.gz"))[:-keep] if keep > 0 else []
    for f in removed:
        f.unlink()
    return BackupResult(archive, archive.stat().st_size, removed)


def list_backups(dest: str | Path = "/var/backups/jarvis") -> list[Path]:
    """List backup archives in ``dest``, oldest first (names embed a sortable timestamp)."""
    return sorted(Path(dest).glob("jarvis-*.tar.gz"))


def restore(settings: Settings, archive: str | Path) -> None:
    """Restore an archive into the data directory.

    The caller must stop the services beforehand. Stale WAL/SHM files are removed so SQLite
    does not replay them over the restored database.
    """
    data = Path(settings.storage.data_dir)
    with tarfile.open(archive) as tar:
        tar.extractall(data, filter="data")  # rejects absolute paths and ".."
    for p in (data / "jarvis.db-wal", data / "jarvis.db-shm"):
        p.unlink(missing_ok=True)


# --- Models, hardware, voice ------------------------------------------------------------------

def setup_models(settings: Settings) -> None:
    """Download and export the ML models (the only step that requires Internet access)."""
    from jarvis.provisioning.models import setup_models as _setup

    _setup(settings)


def say(settings: Settings, text: str) -> None:
    """Synthesize ``text`` with Piper and play it on the configured output device."""
    from jarvis.voice.tts import PiperTTS

    tts = PiperTTS(settings.tts, settings.audio.output_device)
    tts._play(tts._synth(text))


def test_hardware(settings: Settings, pulse: bool = False, report=print) -> None:
    """Read the door sensor and light each LED for 2 s; with ``pulse``, also trigger the door.

    The core must be stopped first, since it owns the devices.

    Args:
        settings: Loaded settings.
        pulse: Also send a pulse to the garage relay (audited as ``garage_pulse``).
        report: Callable receiving progress lines.
    """
    from jarvis.hardware.devices import Hardware

    hw = Hardware.from_config(settings.hardware)
    try:
        report(f"Door sensor: {hw.door_state()}")
        for color in ("green", "red"):
            report(f"{color} LED: 2 s")
            hw.indicate(color, 2)
            time.sleep(2.5)
        if pulse:
            report("Garage pulse")
            if hw.pulse_garage():
                open_db(settings).log_event("garage_pulse", actor=actor(), source="cli")
    finally:
        hw.close()


def enroll_face(settings: Settings, first_name: str, last_name: str, can_open_garage: bool, images: list[str],
                report=print) -> int:
    """Offline enrollment (without the core): create the person and encode each photo.

    Photos with no detectable face are skipped and reported.

    Args:
        settings: Loaded settings.
        first_name: First name of the new person.
        last_name: Last name (may be empty).
        can_open_garage: Whether the person may open the garage.
        images: Paths of the photos to enroll.
        report: Callable receiving one line per photo.

    Returns:
        The new person id.
    """
    import shutil
    import uuid

    from jarvis.vision.faces import FaceEngine

    db = open_db(settings)
    engine = FaceEngine(settings.faces)
    pid = db.add_person(first_name, last_name or "", can_open_garage)
    db.log_event("person_created", actor=actor(), person_id=pid, via="cli",
                 values={"first_name": first_name, "last_name": last_name, "can_open_garage": can_open_garage})
    for img in images:
        dest = settings.storage.faces_dir / str(pid) / f"{uuid.uuid4().hex}{Path(img).suffix or '.jpg'}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(img, dest)
        face = engine.embed_image_file(str(dest))
        if face is None:
            report(f"  {img}: no face detected, skipped")
            dest.unlink()
            continue
        db.add_face_embedding(pid, face.embedding, str(dest), source="cli")
        report(f"  {img}: OK (score {face.det_score:.2f})")
    notify_core(settings, "reload_faces")
    return pid


def monitoring_apply(settings: Settings) -> dict:
    """Apply the desired monitoring state published by the API (must run as root).

    Returns:
        ``{"message": str, "errors": ...}`` as returned by :func:`jarvis.core.monitoring.apply`.
    """
    from jarvis.core.monitoring import apply

    return apply(settings.monitoring.state_dir)


def notify_core(settings: Settings, cmd: str, **params) -> dict | None:
    """Send a command to the core over the control socket.

    Returns:
        The core's response, or None if the core is stopped (it re-reads everything at startup).
    """
    from jarvis.core.control import ControlClient, CoreUnavailable

    try:
        return ControlClient(settings.control.socket_path, timeout=10).call(cmd, **params)
    except CoreUnavailable:
        return None
