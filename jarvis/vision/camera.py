# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/vision/camera.py
# Purpose : RTSP capture thread keeping only the latest frame, with auto-reconnect
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""RTSP frame acquisition.

The camera stream is read continuously on a dedicated thread and only the most
recent frame is kept: consumers never process a backlog of stale frames, so the
vision loop always works on "now" even when it runs slower than the camera.
Connection loss is handled transparently with an exponential reconnect backoff.
"""

from __future__ import annotations

import logging
import os
import threading

import numpy as np

from jarvis.config.settings import CameraConfig

log = logging.getLogger(__name__)

# Force RTSP over TCP (no UDP packet loss / smearing) and a 5 s socket timeout so
# a dead camera makes read() fail instead of blocking forever.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")


class RtspCamera(threading.Thread):
    """Background thread that reads an RTSP stream and publishes the latest frame.

    Attributes:
        cfg: Camera configuration (URL, hardware acceleration, reconnect delay).
        connected: True while the stream is open and delivering frames.
    """

    def __init__(self, cfg: CameraConfig, url: str | None = None):
        """Initialize the capture thread (not started).

        Args:
            cfg: Camera configuration.
            url: Stream URL with credentials (see ``rtsp_url_with_credentials``); defaults to
                ``cfg.rtsp_url``. Kept out of ``cfg`` so that it never shows up in a settings dump.
        """
        super().__init__(name="camera", daemon=True)
        self.cfg = cfg
        self._url = url or cfg.rtsp_url
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._cond = threading.Condition()
        self._halt = threading.Event()
        self.connected = False

    def _open(self):
        """Open the RTSP stream through the OpenCV FFmpeg backend.

        Returns:
            A ``cv2.VideoCapture`` instance (check ``isOpened()``).
        """
        import cv2

        params = []
        if self.cfg.hw_accel:  # VAAPI (H.264 only on the Intel HD 4600 GPU)
            params = [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY]
        cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG, params)
        # Minimal internal buffer: we want the freshest frame, not a queue.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def run(self) -> None:
        """Capture loop: connect, read frames, reconnect with exponential backoff."""
        delay = self.cfg.reconnect_delay_s
        while not self._halt.is_set():
            cap = self._open()
            if not cap.isOpened():
                log.warning("RTSP stream unavailable, retrying in %.0fs", delay)
                self.connected = False
                self._halt.wait(delay)
                delay = min(delay * 2, 30)
                continue
            log.info("RTSP stream connected")
            self.connected, delay = True, self.cfg.reconnect_delay_s
            while not self._halt.is_set():
                ok, frame = cap.read()
                if not ok:
                    log.warning("RTSP stream lost")
                    break
                with self._cond:
                    self._frame, self._seq = frame, self._seq + 1
                    self._cond.notify_all()
            cap.release()
            self.connected = False
            self._halt.wait(1.0)  # avoid a tight loop if the camera drops right after connecting

    def latest(self, after_seq: int = 0, timeout: float = 1.0) -> tuple[int, np.ndarray | None]:
        """Wait for a frame newer than ``after_seq`` and return it.

        Args:
            after_seq: Sequence number of the last frame the caller processed.
            timeout: Maximum wait in seconds.

        Returns:
            ``(sequence number, frame)``. On timeout or shutdown the current
            (possibly unchanged or ``None``) frame is returned.
        """
        with self._cond:
            self._cond.wait_for(lambda: self._seq > after_seq or self._halt.is_set(), timeout)
            return self._seq, self._frame

    def stop(self) -> None:
        """Request thread shutdown and wake up any waiting consumer."""
        self._halt.set()
        with self._cond:
            self._cond.notify_all()
