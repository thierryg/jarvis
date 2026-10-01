# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/vision/__init__.py
# Purpose : Vision package (RTSP capture, PTZ control, person tracking, faces)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Vision: RTSP acquisition, PTZ control, person detection/tracking and face recognition."""

import contextlib
import os


def ensure_yolo_config_dir() -> None:
    """Create ``$YOLO_CONFIG_DIR`` before Ultralytics is imported.

    Ultralytics only accepts a config directory whose parent already exists and is
    writable; otherwise it warns and falls back to ``/tmp/Ultralytics``, which is lost on
    restart. Creating it first (fresh data volume, first install) keeps its settings in the
    data directory. Errors are ignored: Ultralytics then falls back as before.
    """
    path = os.environ.get("YOLO_CONFIG_DIR")
    if path:
        with contextlib.suppress(OSError):
            os.makedirs(path, exist_ok=True)
