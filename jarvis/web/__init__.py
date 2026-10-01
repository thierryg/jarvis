# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/web/__init__.py
# Purpose : Web interface package (REST API, static files, translations)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Web interface package.

Contains the FastAPI REST API (:mod:`jarvis.web.app`), the static single-page UI
(``static/``) and its translations. The API runs as an unprivileged service behind Nginx and
delegates hardware/ML operations to the core service over its control socket.
"""
