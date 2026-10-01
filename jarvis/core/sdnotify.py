# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/sdnotify.py
# Purpose : Dependency-free systemd sd_notify client (READY, STATUS, WATCHDOG)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Dependency-free implementation of the systemd notification protocol (sd_notify).

Supports ``READY``, ``STATUS`` and ``WATCHDOG`` messages. It is only active when the
service is started by systemd with ``Type=notify`` (``NOTIFY_SOCKET`` is set);
otherwise every function is a no-op, so the code runs unchanged outside systemd.
"""

from __future__ import annotations

import os
import socket


def notify(message: str) -> bool:
    """Send a notification message to the systemd service manager.

    Args:
        message: Notification payload, e.g. ``"READY=1"`` or ``"WATCHDOG=1"``.

    Returns:
        ``True`` if the message was sent, ``False`` when not running under systemd or
        if the socket could not be reached.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):  # Linux abstract socket namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode())
        return True
    except OSError:
        return False


def watchdog_interval_s() -> float | None:
    """Return the recommended watchdog ping interval.

    Returns:
        Half of ``WatchdogSec`` in seconds, or ``None`` if the watchdog is disabled or
        targets another process (``WATCHDOG_PID`` mismatch).
    """
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec or os.environ.get("WATCHDOG_PID", str(os.getpid())) != str(os.getpid()):
        return None
    # Ping at half the timeout, as recommended by sd_watchdog_enabled(3).
    return int(usec) / 2_000_000
