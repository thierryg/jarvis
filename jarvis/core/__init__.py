# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/__init__.py
# Purpose : Core service package (orchestration, decision, access, control)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Core service package.

Groups everything that runs inside the privileged ``jarvis-core`` process:
orchestration, the event bus, the GO / NO_GO decision engine, per-person access
rules, notifications, the local control channel used by the API, logging setup,
health monitoring and systemd readiness/watchdog notification.
"""
