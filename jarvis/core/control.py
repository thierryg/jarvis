# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/control.py
# Purpose : API -> core control channel over a local Unix socket (JSON lines)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""API -> core control channel: a local Unix socket carrying one JSON request per line.

The core process is the only one that owns the hardware and the ML models. The API
process (exposed through Nginx) never touches them directly; it delegates every
privileged action to the core through this channel.

Protocol: the client sends ``{"cmd": "<name>", ...params}`` followed by a newline and
reads back a single JSON line. Responses always contain an ``ok`` boolean; failures
also carry an ``error`` message.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[dict], dict]


class CoreUnavailable(RuntimeError):
    """Raised by :class:`ControlClient` when the core service cannot be reached."""


class ControlServer(threading.Thread):
    """Background thread serving control commands on a Unix socket.

    Attributes:
        path: Filesystem path of the Unix socket.
        handlers: Mapping of command name to handler. A handler receives the full
            request dict and returns a dict merged into the ``{"ok": True}`` response.
        server: The underlying socket server, set once :meth:`run` has started.
    """

    def __init__(self, socket_path: str, handlers: dict[str, Handler]):
        """Initialize the server thread.

        Args:
            socket_path: Path of the Unix socket to create.
            handlers: Command name -> handler mapping.
        """
        super().__init__(name="control", daemon=True)
        self.path = Path(socket_path)
        self.handlers = handlers
        self.server: socketserver.ThreadingUnixStreamServer | None = None

    def dispatch(self, request: dict) -> dict:
        """Route a decoded request to its handler.

        Handler exceptions are logged and turned into an error response so that one
        faulty command never takes the server down.

        Args:
            request: Decoded JSON request; its ``cmd`` key selects the handler.

        Returns:
            ``{"ok": True, ...handler result}`` on success, or
            ``{"ok": False, "error": <message>}`` on failure.
        """
        cmd = request.get("cmd")
        handler = self.handlers.get(cmd)
        if handler is None:
            return {"ok": False, "error": f"unknown command: {cmd}"}
        try:
            return {"ok": True} | (handler(request) or {})
        except Exception as exc:
            log.exception("Control command %s failed", cmd)
            return {"ok": False, "error": str(exc)}

    def run(self) -> None:
        """Create the socket and serve requests until :meth:`stop` is called."""
        outer = self

        class _Req(socketserver.StreamRequestHandler):
            """Per-connection handler: reads JSON lines and writes one response per line."""

            def handle(self):
                for line in self.rfile:
                    try:
                        req = json.loads(line)
                    except json.JSONDecodeError:
                        resp = {"ok": False, "error": "invalid JSON"}
                    else:
                        resp = outer.dispatch(req)
                    self.wfile.write((json.dumps(resp, default=str) + "\n").encode())

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Remove a stale socket left over by a previous (crashed) run.
        if self.path.exists():
            self.path.unlink()
        old_umask = os.umask(0o007)  # socket is rw for the jarvis user and group only
        try:
            self.server = socketserver.ThreadingUnixStreamServer(str(self.path), _Req)
        finally:
            os.umask(old_umask)
        self.server.daemon_threads = True
        log.info("Control socket: %s", self.path)
        self.server.serve_forever()

    def stop(self) -> None:
        """Shut down the server and remove the socket file."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class ControlClient:
    """Client used by the API process to send commands to the core.

    Attributes:
        path: Path of the core's Unix socket.
        timeout: Socket timeout in seconds for connect, send and receive.
    """

    def __init__(self, socket_path: str, timeout: float = 30.0):
        """Initialize the client.

        Args:
            socket_path: Path of the core's Unix socket.
            timeout: Socket timeout in seconds.
        """
        self.path, self.timeout = socket_path, timeout

    def call(self, cmd: str, **params: Any) -> dict:
        """Send a command to the core and wait for its response.

        A new connection is opened for every call.

        Args:
            cmd: Command name.
            **params: Extra request fields passed to the handler.

        Returns:
            The decoded response dict (check its ``ok`` key).

        Raises:
            CoreUnavailable: If the socket cannot be reached or the core returns an
                empty response.
        """
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.path)
                s.sendall((json.dumps({"cmd": cmd, **params}) + "\n").encode())
                buf = b""
                # Responses are newline-terminated; read until the full line arrives.
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as exc:
            raise CoreUnavailable(f"core service unreachable: {exc}") from exc
        if not buf:
            raise CoreUnavailable("empty response from core service")
        return json.loads(buf)
