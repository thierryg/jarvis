# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/voice/speaker.py
# Purpose : Speaker verification with ECAPA-TDNN voiceprints (SpeechBrain)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Speaker verification: 192-d ECAPA-TDNN voiceprints (SpeechBrain, VoxCeleb model).

Voiceprints are matched with the same cosine ``EmbeddingGallery`` used for faces.
"""

from __future__ import annotations

import threading

import numpy as np

from jarvis.config.settings import SpeakerConfig
from jarvis.vision.faces import EmbeddingGallery
from jarvis.voice.audio import SAMPLE_RATE


class SpeakerVerifier:
    """Compute voiceprints and identify enrolled speakers.

    Attributes:
        DIM: Embedding dimension (192 for ECAPA-TDNN).
        cfg: Speaker configuration (model dir, acceptance threshold).
        encoder: SpeechBrain ``EncoderClassifier`` running on CPU.
        gallery: Enrolled voiceprints.
    """

    DIM = 192

    def __init__(self, cfg: SpeakerConfig):
        """Load the ECAPA model (downloaded into ``model_dir`` on first use).

        Args:
            cfg: Speaker configuration.
        """
        from speechbrain.inference.speaker import EncoderClassifier

        self.cfg = cfg
        self.encoder = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                                      savedir=cfg.model_dir, run_opts={"device": "cpu"})
        self.gallery = EmbeddingGallery(self.DIM)
        self._lock = threading.Lock()

    def embed(self, wav: np.ndarray) -> np.ndarray:
        """Compute an L2-normalized voiceprint.

        Args:
            wav: Mono 16 kHz float32 samples.

        Returns:
            192-d float32 embedding.
        """
        import torch

        with self._lock, torch.no_grad():
            emb = self.encoder.encode_batch(torch.from_numpy(wav).unsqueeze(0)).squeeze().cpu().numpy()
        return (emb / (np.linalg.norm(emb) + 1e-9)).astype(np.float32)

    def identify(self, wav: np.ndarray) -> tuple[int | None, float]:
        """Identify the speaker of an utterance.

        Clips shorter than 0.5 s are rejected (too little speech for a reliable voiceprint).

        Args:
            wav: Mono 16 kHz float32 samples.

        Returns:
            ``(person_id, score)`` if the best match reaches ``cfg.threshold``,
            else ``(None, score)``; ``(None, 0.0)`` if the gallery is empty or
            the clip too short.
        """
        if len(self.gallery) == 0 or len(wav) < SAMPLE_RATE // 2:
            return None, 0.0
        pid, score = self.gallery.match(self.embed(wav))
        return (pid, score) if score >= self.cfg.threshold else (None, score)
