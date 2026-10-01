# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/logs.py
# Purpose : Logging setup (journald, shared log file, remote syslog) and rotation
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Logging: console (journald), ``/var/log/jarvis/jarvis.log`` file and remote syslog export.

- Both processes (core and API) write to the **same file** through a
  ``WatchedFileHandler``: every line is appended with O_APPEND, and the file is
  reopened automatically after a rotation.
- **Rotation** is performed by the core only (``rotate_if_needed``, called every
  minute): a single rotator means no race between processes. Modes are size-based or
  daily, with numbered archives ``jarvis.log.1[.gz]`` ... ``jarvis.log.N[.gz]``.
- **Syslog export** (RFC 3164 over UDP or TCP) to an external rsyslog/syslog-ng server
  can be enabled and reconfigured live from the Settings page.
"""

from __future__ import annotations

import gzip
import logging
import logging.handlers
import os
import shutil
import socket
import sys
import time
from datetime import date
from pathlib import Path

from jarvis.config.settings import LoggingConfig

FORMAT = "%(asctime)s %(levelname)-7s [%(process_name)s] %(name)s: %(message)s"
SYSLOG_FORMAT = "jarvis-%(process_name)s[%(process)d]: %(levelname)s %(name)s: %(message)s"
FACILITIES = {name: getattr(logging.handlers.SysLogHandler, f"LOG_{name.upper()}")
              for name in ("user", "daemon", "local0", "local1", "local2", "local3", "local4", "local5",
                           "local6", "local7")}


class _ProcessName(logging.Filter):
    """Log filter that stamps each record with the Jarvis process name (``core``/``api``).

    Attributes:
        process_name: Value injected as ``record.process_name``.
    """

    def __init__(self, name: str):
        super().__init__()
        self.process_name = name

    def filter(self, record: logging.LogRecord) -> bool:
        record.process_name = self.process_name
        return True


def configure(level: str, cfg: LoggingConfig, process: str) -> list[str]:
    """(Re)configure log destinations on the root logger.

    Idempotent and safe to call at runtime: handlers previously installed by this
    function are removed and closed before new ones are added. Handlers installed by
    third parties are left untouched.

    Args:
        level: Root log level name (e.g. ``"INFO"``).
        cfg: Logging settings (file and syslog destinations).
        process: Process name shown in every line (``"core"`` or ``"api"``).

    Returns:
        Warnings about destinations that could not be set up (also logged).
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        if getattr(h, "_jarvis", False):
            root.removeHandler(h)
            h.close()
    tag = _ProcessName(process)
    warnings = []

    def add(handler: logging.Handler, fmt: str) -> None:
        # Mark the handler as ours so that the next call can remove it.
        handler._jarvis = True
        handler.addFilter(tag)
        handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)

    add(logging.StreamHandler(sys.stdout), FORMAT)  # stdout -> journald
    if cfg.file_enabled:
        try:
            Path(cfg.file_path).parent.mkdir(parents=True, exist_ok=True)
            add(logging.handlers.WatchedFileHandler(cfg.file_path, encoding="utf-8"), FORMAT)
        except OSError as exc:
            warnings.append(f"log file unavailable ({cfg.file_path}): {exc}")
    if cfg.syslog_enabled and cfg.syslog_host:
        try:
            socktype = socket.SOCK_STREAM if cfg.syslog_protocol == "tcp" else socket.SOCK_DGRAM
            h = logging.handlers.SysLogHandler(address=(cfg.syslog_host, cfg.syslog_port),
                                               facility=FACILITIES[cfg.syslog_facility], socktype=socktype)
            if cfg.syslog_protocol == "tcp":
                h.append_nul = False  # over TCP, rsyslog expects newline-terminated frames
                h.format = lambda record, _f=h.format: _f(record) + "\n"
            add(h, SYSLOG_FORMAT)
        except OSError as exc:
            warnings.append(f"remote syslog {cfg.syslog_host}:{cfg.syslog_port} unreachable: {exc}")
    for w in warnings:
        logging.getLogger(__name__).warning(w)
    return warnings


def _archives(path: Path) -> list[Path]:
    """Return the existing archives of ``path`` sorted by rotation number (``.1`` first)."""
    return sorted(path.parent.glob(path.name + ".*"), key=lambda p: int(p.name.split(".")[-2 if p.suffix == ".gz" else -1]))


def rotate(cfg: LoggingConfig) -> Path | None:
    """Rotate the log file unconditionally.

    ``jarvis.log`` becomes ``jarvis.log.1`` (gzip-compressed if enabled), older
    archives are shifted by one, and archives beyond ``rotate_keep`` are deleted.

    Args:
        cfg: Logging settings.

    Returns:
        Path of the new ``.1`` archive, or ``None`` if the log file is missing or empty.
    """
    path = Path(cfg.file_path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    # Shift from the highest number down so that no archive is overwritten.
    for old in reversed(_archives(path)):
        n = int(old.name[len(path.name) + 1:].split(".")[0])
        if n >= cfg.rotate_keep:
            old.unlink(missing_ok=True)
            continue
        old.rename(path.with_name(f"{path.name}.{n + 1}{'.gz' if old.suffix == '.gz' else ''}"))
    first = path.with_name(path.name + ".1")
    os.replace(path, first)            # WatchedFileHandlers will reopen a fresh jarvis.log
    path.touch(mode=0o640)
    if cfg.rotate_compress:
        with first.open("rb") as src, gzip.open(first.with_name(first.name + ".gz"), "wb") as dst:
            shutil.copyfileobj(src, dst)
        first.unlink()
        first = first.with_name(first.name + ".gz")
    return first


def rotate_if_needed(cfg: LoggingConfig, state: dict) -> Path | None:
    """Rotate the log file if the configured mode requires it.

    In ``size`` mode, rotates once the file reaches ``rotate_max_mb``. In daily mode,
    rotates on the first call after the day changes.

    Args:
        cfg: Logging settings.
        state: Mutable dict kept by the caller between calls; stores the day of the
            last daily check under ``"day"``.

    Returns:
        Path of the new archive if a rotation happened, otherwise ``None``.
    """
    path = Path(cfg.file_path)
    if not cfg.file_enabled or not path.exists():
        return None
    if cfg.rotate_mode == "size":
        return rotate(cfg) if path.stat().st_size >= cfg.rotate_max_mb * 1024 * 1024 else None
    today = date.today()
    # On the first call, infer the day from the file's mtime so a restart does not skip a rotation.
    last = state.get("day") or date.fromtimestamp(path.stat().st_mtime if path.stat().st_size else time.time())
    state["day"] = today
    return rotate(cfg) if last != today else None
