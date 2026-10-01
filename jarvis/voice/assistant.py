# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/voice/assistant.py
# Purpose : Voice assistant thread: wake word, Vosk STT, speaker ID, VoiceCommand events
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Voice assistant.

Processing chain: 16 kHz microphone -> wake word -> Vosk (restricted grammar)
-> speaker verification -> ``VoiceCommand`` event.

The assistant runs half-duplex: microphone input is discarded while the
speaker is playing, so Jarvis never hears (and reacts to) its own voice.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

from jarvis.config.settings import Settings
from jarvis.core.events import VoiceCommand
from jarvis.voice.audio import BLOCK, SAMPLE_RATE, Framer
from jarvis.voice.commands import CommandParser
from jarvis.voice.speaker import SpeakerVerifier
from jarvis.voice.tts import PiperTTS
from jarvis.voice.wakeword import build_wakeword

log = logging.getLogger(__name__)


class VoiceAssistant(threading.Thread):
    """Microphone capture and command recognition thread.

    Attributes:
        s: Application settings.
        events: Queue receiving ``VoiceCommand`` events.
        tts: TTS engine (its ``speaking`` flag gates the microphone).
        speaker: Optional speaker verifier.
        on_sound: Optional callback fired on loud sounds (e.g. to aim the PTZ).
        parser: Command parser (also provides the Vosk grammar).
        state: ``"idle"`` or ``"listening"`` (exposed for status/UI).
    """

    def __init__(self, settings: Settings, events: queue.Queue, tts: PiperTTS,
                 speaker: SpeakerVerifier | None = None, on_sound: Callable[[], None] | None = None):
        """Initialize the assistant (models are loaded in ``run``).

        Args:
            settings: Application settings.
            events: Queue receiving ``VoiceCommand`` events.
            tts: TTS engine.
            speaker: Optional speaker verifier.
            on_sound: Optional callback for the sound trigger.
        """
        super().__init__(name="voice", daemon=True)
        self.s, self.events, self.tts, self.speaker, self.on_sound = settings, events, tts, speaker, on_sound
        self.parser = CommandParser(settings.commands)
        # Bounded: ~16 s of 80 ms blocks; beyond that we drop audio (see _callback).
        self._audio: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._halt = threading.Event()
        self._pending_listen: float | None = None
        self._listen_lock = threading.Lock()
        self._last_sound = 0.0
        self._active_until = 0.0     # monotonic end of the recognition window (voice enabled)
        self.state = "idle"

    def open_listening(self, seconds: float) -> None:
        """Listen directly, without the wake word, as soon as the TTS has finished.

        Used after a greeting so the visitor can answer right away. Repeated
        calls keep the longest requested window.

        Args:
            seconds: Listening window length.
        """
        with self._listen_lock:
            self._pending_listen = max(seconds, self._pending_listen or 0)

    def activate(self, seconds: float) -> None:
        """Enable the voice commands for ``seconds`` (the recognition window of the decision engine).

        Outside the window, with ``decision.voice_only_after_recognition``, the microphone is
        ignored (no wake word detection at all). A new call extends the window, never shortens it.

        Args:
            seconds: Window length from now.
        """
        with self._listen_lock:
            until = time.monotonic() + seconds
            if until > self._active_until:
                self._active_until = until
                log.info("Voice commands enabled for %.0f s (say the wake word, then the command)", seconds)

    @property
    def window_remaining_s(self) -> float:
        """Seconds left in the recognition window (0 when closed)."""
        return max(0.0, self._active_until - time.monotonic())

    def stop(self) -> None:
        """Request thread shutdown."""
        self._halt.set()

    def _callback(self, indata, frames, time_info, status) -> None:
        """sounddevice input callback: copy the raw int16 block into the queue."""
        try:
            self._audio.put_nowait(np.frombuffer(indata, dtype=np.int16).copy())
        except queue.Full:
            pass  # processing is behind: drop audio rather than accumulate latency

    def run(self) -> None:
        """Load Vosk and the wake word engine, then run the capture loop (restarts on audio errors)."""
        import sounddevice as sd
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)
        self.vosk = Model(self.s.stt.vosk_model_path)
        self.wake = build_wakeword(self.s.wakeword)
        self.framer = Framer(self.wake.frame_length)
        log.info("Voice assistant ready (%s)", self.s.wakeword.engine)
        while not self._halt.is_set():
            try:
                with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK, dtype="int16", channels=1,
                                       device=self.s.audio.input_device, callback=self._callback):
                    self._loop()
            except Exception:
                log.exception("Audio input error, restarting in 5 s")
                self._halt.wait(5)

    def _next_chunk(self) -> np.ndarray | None:
        """Return the next audio block, or ``None`` after 0.5 s without audio."""
        try:
            return self._audio.get(timeout=0.5)
        except queue.Empty:
            return None

    def _loop(self) -> None:
        """Idle loop: mute while speaking, sleep outside the recognition window, serve pending
        direct listens, detect the wake word."""
        while not self._halt.is_set():
            chunk = self._next_chunk()
            if chunk is None:
                continue
            if self.tts.speaking.is_set():
                # Half-duplex: discard our own voice and any partial wake word state.
                self.framer.clear()
                self.wake.reset()
                continue
            self._check_sound(chunk)   # sound trigger (PTZ) works even while voice is asleep
            if self.s.decision.voice_only_after_recognition and time.monotonic() >= self._active_until:
                if self.state != "sleeping":
                    if self.state == "idle" and self._active_until:
                        log.info("Voice commands disabled: recognition window closed")
                    self.state = "sleeping"
                self.framer.clear()
                self.wake.reset()
                continue
            if self.state == "sleeping":
                self.state = "idle"
            with self._listen_lock:
                pending, self._pending_listen = self._pending_listen, None
            if pending:
                self._listen(pending, via_wakeword=False)
                continue
            for frame in self.framer.push(chunk):
                if self.wake.process(frame):
                    log.info("Wake word detected")
                    self.wake.reset()
                    self.framer.clear()
                    self.tts.beep()
                    # Drop audio captured before/while beeping so it does not reach the recognizer.
                    self._drain()
                    self._listen(self.s.stt.listen_timeout_s, via_wakeword=True)
                    break

    def _drain(self) -> None:
        """Discard all queued audio."""
        while not self._audio.empty():
            self._audio.get_nowait()

    def _check_sound(self, chunk: np.ndarray) -> None:
        """Fire ``on_sound`` when block RMS reaches ``sound_trigger_rms`` (at most every 10 s).

        Args:
            chunk: int16 audio block.
        """
        thr = self.s.audio.sound_trigger_rms
        if not thr or not self.on_sound:
            return
        rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768) ** 2)))
        now = time.monotonic()
        if rms >= thr and now - self._last_sound > 10:
            self._last_sound = now
            self.on_sound()

    def _listen(self, seconds: float, via_wakeword: bool) -> None:
        """Recognize one command within a time window and emit a ``VoiceCommand``.

        Vosk is constrained to the command grammar (plus ``[unk]``). Listening
        stops at the first utterance that parses to an intent. After a wake
        word, any non-empty utterance also ends listening (it was addressed to
        Jarvis, even if not understood) and is reported with ``intent=None``;
        in direct-listen mode, unparsed speech is treated as background
        conversation and ignored. The utterance audio (last
        ``max_utterance_s`` seconds) is then passed to speaker verification.

        Args:
            seconds: Listening window length.
            via_wakeword: True if triggered by the wake word, False for direct listening.
        """
        from vosk import KaldiRecognizer

        self.state = "listening"
        rec = KaldiRecognizer(self.vosk, SAMPLE_RATE, json.dumps(self.parser.grammar, ensure_ascii=False))
        deadline = time.monotonic() + seconds
        utterance: list[np.ndarray] = []
        max_samples = int(self.s.stt.max_utterance_s * SAMPLE_RATE)
        text = ""
        try:
            while time.monotonic() < deadline and not self._halt.is_set():
                chunk = self._next_chunk()
                if chunk is None:
                    continue
                utterance.append(chunk)
                if rec.AcceptWaveform(chunk.tobytes()):
                    text = json.loads(rec.Result()).get("text", "")
                    if self.parser.parse(text) is not None:
                        break
                    if text and via_wakeword:
                        break  # utterance addressed to Jarvis but not understood
                    text, utterance = "", []  # noise / background conversation: keep listening
            else:
                text = text or json.loads(rec.FinalResult()).get("text", "")
        finally:
            self.state = "idle"
        intent = self.parser.parse(text)
        if intent is None and not (via_wakeword and text):
            return
        pid, score = None, None
        if self.speaker is not None and utterance:
            wav = np.concatenate(utterance)[-max_samples:].astype(np.float32) / 32768
            pid, score = self.speaker.identify(wav)
        log.info("Voice command %r -> %s (speaker %s, %.2f)", text, intent, pid, score or 0)
        self.events.put(VoiceCommand(text, intent, pid, score, via_wakeword))
