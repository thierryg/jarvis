# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/voice/audio.py
# Purpose : Shared audio constants, fixed-size framing and ffmpeg-based decoding
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Shared audio constants and helpers: sample rate, block size, framing, file decoding."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
BLOCK = 1280  # 80 ms: openWakeWord's native frame size


class Framer:
    """Re-chunk an int16 PCM stream into fixed-size frames.

    Needed because wake word engines require a specific frame length
    (e.g. Porcupine's) that may differ from the capture block size.

    Attributes:
        size: Frame length in samples.
    """

    def __init__(self, size: int):
        """Create an empty framer.

        Args:
            size: Frame length in samples.
        """
        self.size = size
        self._buf = np.zeros(0, dtype=np.int16)

    def push(self, pcm: np.ndarray) -> list[np.ndarray]:
        """Append samples and return all complete frames; the remainder is kept.

        Args:
            pcm: int16 samples.

        Returns:
            Zero or more frames of exactly ``size`` samples.
        """
        self._buf = np.concatenate([self._buf, pcm])
        n = len(self._buf) // self.size
        frames = [self._buf[i * self.size:(i + 1) * self.size] for i in range(n)]
        self._buf = self._buf[n * self.size:]
        return frames

    def clear(self) -> None:
        """Drop buffered samples."""
        self._buf = np.zeros(0, dtype=np.int16)


def load_audio_file(path: str | Path) -> np.ndarray:
    """Decode any audio format (browser webm/ogg, wav, ...) to mono 16 kHz float32 via ffmpeg.

    Args:
        path: Audio file path.

    Returns:
        float32 samples in [-1, 1].

    Raises:
        subprocess.CalledProcessError: If ffmpeg fails to decode the file.
        subprocess.TimeoutExpired: If decoding takes more than 60 s.
    """
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "-"], capture_output=True, check=True, timeout=60)
    return np.frombuffer(out.stdout, dtype=np.float32).copy()
