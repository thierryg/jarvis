# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/voice/simulate.py
# Purpose : Voice simulation: typed command or recording -> wake word + Vosk STT, without a microphone
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Voice simulation for Settings > Simulation > Voice (no microphone needed).

Two inputs, both going through the real recognition components:

- **text**: the "Jarvis" token is checked on the typed text (``"Jarvis, ouvre la porte du
  garage"``), then the command part is synthesized by Piper (the TTS voice of Jarvis) and the
  audio is transcribed by Vosk with the same restricted grammar as live listening. This tests
  the grammar and the recognizer, not only the parser.
- **audio**: an uploaded recording (any format FFmpeg reads). The configured wake word engine
  (openWakeWord / Porcupine) runs on it, then Vosk transcribes what follows the wake word.

The result describes each stage; the core then hands the command to the decision engine as a
*simulated* event (see :class:`jarvis.config.settings.SimulationConfig`).
"""

from __future__ import annotations

import json
import logging

import numpy as np

from jarvis.config.settings import Settings
from jarvis.voice.audio import SAMPLE_RATE, Framer, load_audio_file
from jarvis.voice.commands import CommandParser, normalize

log = logging.getLogger(__name__)

WAKE_TOKENS = ("hey jarvis", "jarvis")


def split_wake_token(text: str) -> tuple[bool, str]:
    """``(True, command)`` when the text starts with the "Jarvis" token, else ``(False, text)``.

    >>> split_wake_token("Jarvis, ouvre la porte du garage")
    (True, 'ouvre la porte du garage')
    """
    t = normalize(text.replace(",", " ").replace("…", " ").replace("...", " "))
    for token in WAKE_TOKENS:
        if t == token or t.startswith(token + " "):
            return True, t[len(token):].strip()
    return False, t


class VoiceSimulator:
    """Runs a simulated utterance through the wake word engine, Piper and Vosk (all lazy)."""

    def __init__(self, settings: Settings, tts=None):
        """Args: settings; tts: the core's PiperTTS (its WAV cache is reused), or ``None``."""
        self.s, self.tts = settings, tts
        self.parser = CommandParser(settings.commands)
        self._vosk = None

    # --- components -------------------------------------------------------------------------
    def _model(self):
        if self._vosk is None:
            from vosk import Model, SetLogLevel

            SetLogLevel(-1)
            self._vosk = Model(self.s.stt.vosk_model_path)
        return self._vosk

    def transcribe(self, audio: np.ndarray) -> str:
        """Vosk transcription of float32 16 kHz audio, restricted to the command grammar."""
        from vosk import KaldiRecognizer

        rec = KaldiRecognizer(self._model(), SAMPLE_RATE, json.dumps(self.parser.grammar, ensure_ascii=False))
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        texts = []
        for i in range(0, len(pcm), 8000):
            if rec.AcceptWaveform(pcm[i:i + 8000]):
                texts.append(json.loads(rec.Result()).get("text", ""))
        texts.append(json.loads(rec.FinalResult()).get("text", ""))
        words = [t for t in texts if t and t != "[unk]"]
        return " ".join(words).replace("[unk]", "").strip()

    def synthesize(self, text: str) -> np.ndarray:
        """Piper speech of ``text`` as float32 16 kHz (through the TTS cache)."""
        from jarvis.voice.tts import PiperTTS

        tts = self.tts or PiperTTS(self.s.tts)
        return load_audio_file(tts._synth(text))

    def detect_wake(self, audio: np.ndarray) -> int | None:
        """Sample index just after the wake word in ``audio``, or ``None`` if it is not heard."""
        from jarvis.voice.wakeword import build_wakeword

        wake = build_wakeword(self.s.wakeword)
        framer = Framer(wake.frame_length)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        pos = 0
        for i in range(0, len(pcm), 1280):
            chunk = pcm[i:i + 1280]
            for frame in framer.push(chunk):
                pos += len(frame)
                if wake.process(frame):
                    return pos
        return None

    # --- runs ---------------------------------------------------------------------------------
    def from_text(self, text: str) -> dict:
        """Simulate a spoken command typed as text (see the module docstring)."""
        wake, command = split_wake_token(text)
        res = {"input": "text", "text": text, "wake_word": wake, "transcript": "", "intent": None, "stt": ""}
        if not wake:
            res["stt"] = "not run: the command must start with the wake word (\"Jarvis…\")"
            return res
        try:
            res["transcript"] = self.transcribe(self.synthesize(command))
            res["stt"] = "vosk on piper speech"
        except Exception as exc:                       # Piper or Vosk model missing: parse the text
            log.warning("Voice simulation: speech chain unavailable (%s), parsing the text directly", exc)
            res["transcript"] = command
            res["stt"] = f"skipped ({exc.__class__.__name__}): text parsed directly"
        intent = self.parser.parse(res["transcript"])
        res["intent"] = intent.value if intent else None
        return res

    def from_audio(self, path: str) -> dict:
        """Simulate a recording: wake word engine, then Vosk on what follows it."""
        audio = load_audio_file(path)
        res = {"input": "audio", "seconds": round(len(audio) / SAMPLE_RATE, 1), "wake_word": False,
               "transcript": "", "intent": None, "stt": ""}
        try:
            end = self.detect_wake(audio)
        except Exception as exc:
            res["stt"] = f"wake word engine unavailable ({exc.__class__.__name__})"
            return res
        res["wake_word"] = end is not None
        if end is None:
            res["stt"] = "not run: the wake word was not heard in the recording"
            return res
        res["transcript"] = self.transcribe(audio[end:])
        res["stt"] = "vosk after the wake word"
        intent = self.parser.parse(res["transcript"])
        res["intent"] = intent.value if intent else None
        return res
