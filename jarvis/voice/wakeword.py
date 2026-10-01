# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/voice/wakeword.py
# Purpose : Wake word engines (openWakeWord, Porcupine) behind a common interface
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Wake word detection: openWakeWord ("hey jarvis", ONNX, offline) or Porcupine ("jarvis")."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from jarvis.config.settings import WakeWordConfig
from jarvis.voice.audio import BLOCK


class WakeWord:
    """Wake word engine interface.

    Attributes:
        frame_length: Number of int16 samples expected by ``process``.
    """

    frame_length: int

    def process(self, pcm: np.ndarray) -> bool:
        """Feed one frame; return True if the wake word was detected."""
        ...

    def reset(self) -> None:
        """Clear internal state (e.g. after a detection or while muted)."""
        ...


class OpenWakeWord(WakeWord):
    """openWakeWord engine (ONNX inference, 80 ms frames).

    Attributes:
        threshold: Detection score threshold in [0, 1].
        model: openWakeWord ``Model``.
    """

    frame_length = BLOCK

    def __init__(self, cfg: WakeWordConfig):
        """Load the wake word model plus the shared melspectrogram/embedding models.

        Args:
            cfg: Wake word configuration; ``oww_model`` is a model name looked up
                in ``oww_dir`` or an explicit ``.onnx`` path.
        """
        from openwakeword.model import Model

        self.threshold = cfg.threshold
        d = Path(cfg.oww_dir)
        model = cfg.oww_model if cfg.oww_model.endswith(".onnx") else str(d / f"{cfg.oww_model}.onnx")
        self.model = Model(wakeword_models=[model], inference_framework="onnx",
                           melspec_model_path=str(d / "melspectrogram.onnx"),
                           embedding_model_path=str(d / "embedding_model.onnx"))

    def process(self, pcm: np.ndarray) -> bool:
        """Return True if any model score reaches the threshold."""
        scores = self.model.predict(pcm)
        return max(scores.values(), default=0.0) >= self.threshold

    def reset(self) -> None:
        """Reset the model's streaming buffers."""
        self.model.reset()


class PorcupineWakeWord(WakeWord):
    """Picovoice Porcupine engine (requires an access key).

    Attributes:
        handle: Porcupine instance.
        frame_length: Frame size imposed by Porcupine.
    """

    def __init__(self, cfg: WakeWordConfig):
        """Create the Porcupine instance.

        Args:
            cfg: Wake word configuration (access key, keyword, sensitivity).
        """
        import pvporcupine

        self.handle = pvporcupine.create(access_key=cfg.porcupine_access_key, keywords=[cfg.porcupine_keyword],
                                         sensitivities=[cfg.porcupine_sensitivity])
        self.frame_length = self.handle.frame_length

    def process(self, pcm: np.ndarray) -> bool:
        """Return True if Porcupine reports a keyword index (>= 0)."""
        return self.handle.process(pcm) >= 0

    def reset(self) -> None:
        """No-op: Porcupine exposes no reset API."""
        pass


def build_wakeword(cfg: WakeWordConfig) -> WakeWord:
    """Create the configured wake word engine.

    Args:
        cfg: Wake word configuration.

    Returns:
        ``PorcupineWakeWord`` if ``engine == "porcupine"``, otherwise ``OpenWakeWord``.
    """
    if cfg.engine == "porcupine":
        return PorcupineWakeWord(cfg)
    return OpenWakeWord(cfg)
