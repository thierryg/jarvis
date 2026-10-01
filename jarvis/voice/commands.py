# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/voice/commands.py
# Purpose : Pure voice-command parser: normalization, exact phrases, keyword fallback
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Voice command parsing (pure, side-effect free, unit-testable).

Transcripts are normalized, then matched against the configured phrases
exactly; failing that, a keyword fallback tuned for the French speech model
is applied. The configured phrases also form the Vosk restricted grammar.
"""

from __future__ import annotations

import unicodedata

from jarvis.config.settings import CommandsConfig
from jarvis.core.events import Intent


def normalize(text: str) -> str:
    """Normalize a transcript for matching.

    Lowercases, strips diacritics (NFKD + drop combining marks), turns
    apostrophes into spaces (``"l'ouvre"`` -> ``"l ouvre"``) and collapses whitespace.

    Args:
        text: Raw text.

    Returns:
        Normalized text.
    """
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.replace("'", " ").split())


class CommandParser:
    """Map recognized text to an ``Intent``.

    Attributes:
        phrases: Normalized phrase -> intent (open/close garage, cancel).
    """

    def __init__(self, cfg: CommandsConfig):
        """Build the phrase table.

        Args:
            cfg: Command phrases configuration.
        """
        self.strict = cfg.strict
        self.phrases: dict[str, Intent] = {}
        for intent, phrases in ((Intent.OPEN_GARAGE, cfg.open_phrases),
                                (Intent.CLOSE_GARAGE, cfg.close_phrases),
                                (Intent.CANCEL, cfg.cancel_phrases)):
            for p in phrases:
                self.phrases[normalize(p)] = intent

    @property
    def grammar(self) -> list[str]:
        """Vosk grammar: all known phrases plus ``[unk]`` to absorb out-of-grammar speech."""
        return sorted(self.phrases) + ["[unk]"]

    def parse(self, text: str) -> Intent | None:
        """Parse a transcript.

        Exact phrase match first. In strict mode (``commands.strict``, default) nothing else
        is accepted. Otherwise, if the text mentions a target ("garage" or "porte"), a word
        starting with "ouvr" (ouvrir, ouvre...) means open and one starting with "ferm"
        (fermer, ferme...) means close.

        Args:
            text: Transcript from the speech recognizer.

        Returns:
            The intent, or ``None`` if nothing matched.
        """
        t = normalize(text)
        if not t or t == "[unk]":
            return None
        if t in self.phrases:
            return self.phrases[t]
        if self.strict:
            return None
        words = set(t.split())
        target = "garage" in words or "porte" in words
        if target and any(w.startswith("ouvr") for w in words):
            return Intent.OPEN_GARAGE
        if target and any(w.startswith("ferm") for w in words):
            return Intent.CLOSE_GARAGE
        return None
