# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/voice/tts.py
# Purpose : Piper text-to-speech thread with disk cache and half-duplex "speaking" flag
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Piper text-to-speech (fr_FR voice).

Synthesized phrases are cached on disk as WAV (keyed by model and text), so
recurring greetings play instantly. The ``speaking`` event lets the voice
assistant mute the microphone while audio plays (half-duplex).
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np

from jarvis.config.settings import TTSConfig

log = logging.getLogger(__name__)


class PiperTTS(threading.Thread):
    """Serialized speech output thread.

    Attributes:
        cfg: TTS configuration (Piper model, cache directory).
        output_device: sounddevice output device (``None`` for default).
        cache: WAV cache directory.
        speaking: Set from ``say()`` until playback (plus echo tail) ends.
    """

    def __init__(self, cfg: TTSConfig, output_device=None):
        """Initialize the thread (the Piper voice is loaded lazily).

        Args:
            cfg: TTS configuration.
            output_device: sounddevice output device.
        """
        super().__init__(name="tts", daemon=True)
        self.cfg, self.output_device = cfg, output_device
        self.cache = Path(cfg.cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.speaking = threading.Event()
        self._q: queue.Queue[str | None] = queue.Queue()
        self._voice = None

    def say(self, text: str) -> None:
        """Queue a phrase for playback (non-blocking).

        Args:
            text: Text to speak.
        """
        # Set as soon as the phrase is queued: direct listening (open_listening) must
        # not start before the TTS thread has actually begun speaking.
        self.speaking.set()
        self._q.put(text)

    def stop(self) -> None:
        """Stop the thread after the queued phrases have been played."""
        self._q.put(None)

    def _synth(self, text: str) -> Path:
        """Synthesize ``text`` to a cached WAV file.

        Args:
            text: Text to synthesize.

        Returns:
            Path of the WAV file (reused from the cache when present).
        """
        key = hashlib.sha1(f"{self.cfg.piper_model}|{text}".encode(), usedforsecurity=False).hexdigest()
        path = self.cache / f"{key}.wav"
        if path.exists():
            return path
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(self.cfg.piper_model)
        # Write to a temp file then rename, so a crash never leaves a truncated cache entry.
        tmp = path.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as wf:
            if hasattr(self._voice, "synthesize_wav"):  # piper-tts >= 1.3
                self._voice.synthesize_wav(text, wf)
            else:                                       # piper-tts 1.2
                self._voice.synthesize(text, wf)
        tmp.replace(path)
        return path

    def _play(self, path: Path) -> None:
        """Play a WAV file and block until playback ends.

        Args:
            path: WAV file (int16 PCM).
        """
        import sounddevice as sd

        with wave.open(str(path), "rb") as wf:
            data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            rate = wf.getframerate()
        sd.play(data, rate, device=self.output_device)
        sd.wait()

    def beep(self) -> None:
        """Play a short 880 Hz acknowledgment tone (150 ms, Hann-windowed); blocking."""
        import sounddevice as sd

        t = np.linspace(0, 0.15, int(0.15 * 22050), endpoint=False)
        tone = (0.3 * np.sin(2 * np.pi * 880 * t) * np.hanning(len(t))).astype(np.float32)
        sd.play(tone, 22050, device=self.output_device)
        sd.wait()

    def run(self) -> None:
        """Playback loop: synthesize and play queued phrases until ``stop()``."""
        while (text := self._q.get()) is not None:
            try:
                path = self._synth(text)
                self.speaking.set()
                self._play(path)
                time.sleep(0.3)  # let echo / reverberation die out before reopening the mic
            except Exception:
                log.exception("TTS: failed for %r", text)
            finally:
                self.speaking.clear()
