# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/__main__.py
# Purpose : Entry point for ``python -m jarvis``
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""``python -m jarvis``: same entry point as the ``jarvis`` command."""

from jarvis.cli import main

if __name__ == "__main__":
    main()
