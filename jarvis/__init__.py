# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/__init__.py
# Purpose : Package root: project description and version number (read from jarvis/VERSION)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Jarvis: PTZ camera surveillance, face recognition and voice control of the garage door.

The package is split into a privileged ``core`` service (camera, vision, voice, decision
logic, hardware), an unprivileged web API (:mod:`jarvis.web`), an administration shell
(:mod:`jarvis.cli`) and a local appliance console (:mod:`jarvis.console`).
"""

from pathlib import Path as _Path

#: Single source of truth for the software version: the plain-text ``jarvis/VERSION`` file
#: (Semantic Versioning ``MAJOR.MINOR.PATCH[-pre]``). pyproject.toml reads the same file
#: (``[tool.setuptools.dynamic]``), so the wheel metadata, the API, the UI, the shell, the
#: console, MQTT discovery, Prometheus ``jarvis_info``, the SBOM and the PDFs never diverge.
#: Bump it with ``scripts/jarvis-bump-version.py`` (or ``make bump PART=patch|minor|major|x.y.z``), never by hand.
VERSION_FILE = _Path(__file__).with_name("VERSION")
__version__ = VERSION_FILE.read_text(encoding="utf-8").strip()
