# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/cli/__init__.py
# Purpose : ``jarvis`` command entry point: global options, logging setup, shell dispatch
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""``jarvis`` entry point: cmd2 administration shell (interactive) or a single command.

Examples: ``jarvis`` (shell), ``jarvis core`` (service), ``jarvis -c other.yaml check-config``,
``jarvis sightings --start 2026-09-01 --end 2026-09-30 --from 07:00 --to 09:00``.

Only the global ``-c/--config`` option is parsed here; everything else is handed to the cmd2
shell (:mod:`jarvis.cli.shell`), which owns the per-command argument parsers.
"""

from __future__ import annotations

import argparse
import sys

SERVICE_COMMANDS = ("core", "api")


def setup_logging(settings, command: str) -> None:
    """Configure logging for the command about to run.

    Services (``core``, ``api``) log to journald plus the file and syslog outputs enabled in the
    configuration. Other commands (often run as root) log to the console only, so they never
    create ``/var/log/jarvis/jarvis.log`` with the wrong owner.

    Args:
        settings: Loaded settings.
        command: Normalized command name (``-`` replaced by ``_``); empty for the interactive shell.
    """
    from jarvis.core import logs

    cfg = settings.logging
    if command not in SERVICE_COMMANDS:
        cfg = cfg.model_copy(update={"file_enabled": False, "syslog_enabled": False})
    logs.configure(settings.log_level, cfg, command or "shell")


def main(argv: list[str] | None = None) -> None:
    """Parse the global ``-c/--config`` option, then delegate to the cmd2 shell.

    ``-h``/``--help`` is mapped to ``help -v`` (verbose, categorized command list). Exits the
    process with the command's exit code.

    Args:
        argv: Command-line arguments without the program name; defaults to ``sys.argv[1:]``.
    """
    from jarvis.cli.shell import run
    from jarvis.config.settings import load_settings

    argv = sys.argv[1:] if argv is None else argv
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("-c", "--config")
    opts, rest = pre.parse_known_args(argv)
    if rest[:1] in (["-h"], ["--help"]):
        rest = ["help", "-v"]
    settings = load_settings(opts.config)
    command = rest[0].replace("-", "_") if rest else ""
    setup_logging(settings, command)
    sys.exit(run(settings, rest))
