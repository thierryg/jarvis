# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/service.py
# Purpose : Core process: wires camera, vision, voice, decision, hardware, control
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""The ``core`` process: assembles camera, vision, voice, decision, hardware and the control socket.

:class:`Core` owns every hardware resource and ML model. It starts the pipeline threads,
exposes privileged operations to the API through the control socket, reports readiness
and health to systemd (``Type=notify`` with watchdog), and runs background housekeeping
(GDPR data retention, log rotation).
"""

from __future__ import annotations

import logging
import queue
import signal
import threading
import time
from pathlib import Path

from jarvis.vision.camera import RtspCamera
from jarvis.vision.ptz import PTZTracker, build_ptz
from jarvis.config.settings import Settings, resolve_credentials, rtsp_url_with_credentials
from jarvis.core.control import ControlServer
from jarvis.storage.database import Database
from jarvis.core.decision import DecisionEngine
from jarvis.core.events import ManualGarage
from jarvis.vision.faces import FaceEngine, FaceGallery
from jarvis.hardware.devices import Hardware
from jarvis.core import logs
from jarvis.core.notify import WebhookNotifier
from jarvis.core.sdnotify import notify as sd_notify
from jarvis.core.sdnotify import watchdog_interval_s
from jarvis.config.catalog import BY_KEY, CATALOG, apply_overrides, get_path, set_path

log = logging.getLogger(__name__)


def remove_files(paths: list[str]) -> None:
    """Delete the given files, ignoring missing ones and logging other failures.

    Args:
        paths: File paths to delete.
    """
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            log.warning("Unable to delete: %s", p)


class Core:
    """Top-level orchestrator of the core process.

    Attributes:
        s: Effective settings (``config.yaml`` plus database overrides), mutated in
            place on hot reload.
        base: Pristine copy of the ``config.yaml`` settings, without overrides.
        stop_event: Set to request a graceful shutdown.
        events: Queue feeding the decision engine.
        db: Database instance.
        hw: Hardware facade (relay, LEDs, door sensor).
        gallery: In-memory face embedding gallery.
        notifier: Outgoing webhook notifier.
        tts, voice, speaker, vision, control, decision, camera, ptz: Components
            created by :meth:`start` (``None`` until then or when disabled).
    """

    def __init__(self, settings: Settings):
        """Open the database, apply setting overrides, configure logging and load the face gallery.

        Args:
            settings: Settings loaded from ``config.yaml``.
        """
        self.s = settings
        self.stop_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.db = Database(settings.storage.db_path)
        self.db.init()
        self.base = settings.model_copy(deep=True)       # config.yaml values, without overrides
        ignored = apply_overrides(self.s, self.db.get_settings_overrides())
        resolve_credentials(self.s)  # secrets -> PTZ account, Porcupine key (after the overrides)
        # Hardware detection and automatic tuning, before any model is loaded.
        from jarvis.config.settings import VALUE_SOURCES
        from jarvis.core.sysinfo import apply_auto_performance, detect_capabilities, write_capabilities_snapshot

        self.capabilities = detect_capabilities()
        user_keys = set(VALUE_SOURCES) | set(self.db.get_settings_overrides())
        self.performance = apply_auto_performance(self.s, self.capabilities, user_keys)
        cpu = self.capabilities["cpu"]
        log.info("Hardware: %s, %d cores / %d threads, flags %s, OpenVINO %s, ONNX %s, VAAPI %s",
                 cpu["model"], cpu["physical_cores"], cpu["logical_cpus"], ",".join(cpu["flags"]),
                 list(self.capabilities["openvino_devices"]) or "-", self.capabilities["onnx_providers"] or "-",
                 self.capabilities["vaapi_decode"] or "-")
        for reason in self.performance["why"]:
            log.info("Performance: %s", reason)
        # Shared with the SSH MOTD (jarvis-motd) and other local tools.
        write_capabilities_snapshot(Path(self.s.control.socket_path).parent / "capabilities.json",
                                    self.capabilities, self.performance)
        if self.performance["applied"]:
            log.info("Performance settings applied: %s", self.performance["applied"])
        logs.configure(self.s.log_level, self.s.logging, "core")  # includes logging overrides
        if ignored:
            log.warning("Ignored setting overrides: %s", ignored)
        self.hw = Hardware.from_config(settings.hardware)
        self.gallery = FaceGallery()
        self.gallery.load(self.db.all_face_embeddings())
        log.info("%d face embeddings loaded", len(self.gallery))
        self.tts = self.voice = self.speaker = self.vision = self.recorder = self.mqtt = None
        self.control = self.decision = self.camera = self.ptz = None
        self.notifier = WebhookNotifier(settings.notifications, settings.secrets)

    # --- Startup -----------------------------------------------------------------------
    def start(self) -> None:
        """Create and start every component thread, then the control socket and housekeeping."""
        # Heavy imports (OpenVINO, audio stack) are deferred so that importing this module stays cheap.
        from jarvis.vision.pipeline import PersonDetector, VisionPipeline
        from jarvis.voice.assistant import VoiceAssistant
        from jarvis.voice.speaker import SpeakerVerifier
        from jarvis.voice.tts import PiperTTS

        s = self.s
        if self.notifier.enabled:
            self.notifier.start()
        self.tts = PiperTTS(s.tts, s.audio.output_device)
        self.tts.start()

        self.faces = FaceEngine(s.faces)
        self.camera = RtspCamera(s.camera, url=rtsp_url_with_credentials(s))
        self.camera.start()
        self.ptz = build_ptz(s.ptz)
        self.tracker = PTZTracker(self.ptz, s.ptz)
        detector = PersonDetector(s.detector, s.plates.vehicle_classes if s.plates.enabled else None)
        self.vision = VisionPipeline(s, self.camera, detector, self.faces, self.gallery,
                                     self.tracker, self.db, self.events)
        if s.plates.enabled:
            from jarvis.vision.plates import PlateReader

            self.vision.plates = PlateReader(s.plates, s.storage.plates_dir)
            log.info("License plate recognition enabled (%s, %s)", s.plates.detector_model, s.plates.ocr_model)
        from jarvis.vision.recorder import Recorder

        self.recorder = Recorder(s.recording, s.timelapse, s.storage.data_dir, self.camera.latest, self.db)
        self.vision.recorder = self.recorder
        self.recorder.start()
        if s.mqtt.enabled:
            from jarvis.integrations.mqtt import MqttBridge

            self.mqtt = MqttBridge(s.mqtt, s.secrets, status=lambda: {"ok": True, **self._status({})}, commands={
                "garage_pulse": lambda p: self.events.put(ManualGarage(actor="mqtt")),
                "ptz_home": lambda p: self._ptz_home({}),
                "say": lambda p: self.tts.say(str(p.get("text", ""))[:200]) if p.get("text") else None,
            })
            self.mqtt.start()
        self.vision.start()

        if s.audio.enabled:
            if s.speaker.enabled:
                self.speaker = SpeakerVerifier(s.speaker)
                self.speaker.gallery.load(self.db.all_voice_profiles())
            self.voice = VoiceAssistant(s, self.events, self.tts, self.speaker, on_sound=self.tracker.on_sound)
            self.voice.start()

        self.decision = DecisionEngine(s, self.db, self.hw, self.tts.say,
                                       self.voice.open_listening if self.voice else None, self.events,
                                       notify=self._notify)
        if self.voice:
            self.decision.activate_voice = self.voice.activate   # recognition window -> voice on
        # Plate automation closes only when nobody (person or vehicle) is in the scene.
        self.decision.scene_clear = lambda: self.vision.active_tracks == 0 and self.vision.active_vehicles == 0
        self.decision.start()

        self.control = ControlServer(s.control.socket_path, {
            "status": self._status,
            "reload_faces": self._reload_faces,
            "reload_voices": self._reload_voices,
            "enroll_face": self._enroll_face,
            "embed_face": self._embed_face,
            "enroll_voice": self._enroll_voice,
            "garage_pulse": self._garage_pulse,
            "ptz_home": self._ptz_home,
            "reload_settings": self._reload_settings,
            "system": lambda _req: {"capabilities": self.capabilities, "performance": self.performance},
            "restart": self._restart,
            "say": lambda req: self.tts.say(str(req["text"])[:200]),
        })
        self.control.start()
        threading.Thread(target=self._maintenance, name="maintenance", daemon=True).start()
        threading.Thread(target=self._log_rotation, name="log-rotate", daemon=True).start()
        self.db.log_event("core_started", actor="system")
        log.info("Core started")

    def healthy(self) -> tuple[bool, str]:
        """Health check for the systemd watchdog: vital threads alive and vision loop running.

        A disconnected camera is not a service failure (the stream reconnects on its
        own): only an internal stall (dead thread, frozen vision loop) should make
        systemd restart the service.

        Returns:
            ``(healthy, reason)``; ``reason`` is ``"ok"`` when healthy.
        """
        for name, th in (("vision", self.vision), ("decision", self.decision), ("control", self.control),
                         ("camera", self.camera), ("tts", self.tts)):
            if th is not None and not th.is_alive():
                return False, f"{name} thread stopped"
        if self.vision is not None and time.monotonic() - self.vision.heartbeat > 60:
            return False, "vision loop stalled"
        return True, "ok"

    def run_forever(self) -> None:
        """Start the core and supervise it until SIGTERM/SIGINT or a restart request.

        Sends ``READY=1`` once started, then periodically updates ``STATUS`` and pings
        the watchdog while healthy. When unhealthy, pings are withheld so that systemd
        restarts the service. Always shuts down cleanly on exit.
        """
        signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())
        try:
            self.start()
            sd_notify("READY=1")
            period = watchdog_interval_s() or 10.0
            while not self.stop_event.wait(period):
                ok, why = self.healthy()
                cam = "camera connected" if self.camera and self.camera.connected else "camera disconnected"
                sd_notify(f"STATUS={cam}, vision {self.vision.fps:.1f} fps, {why}" if self.vision else "STATUS=starting")
                if ok:
                    sd_notify("WATCHDOG=1")
                else:
                    log.error("Health degraded (%s): the systemd watchdog will restart the service", why)
        finally:
            sd_notify("STOPPING=1")
            self.shutdown()

    def shutdown(self) -> None:
        """Stop every component, wait for native-code threads, and release hardware."""
        log.info("Stopping core")
        for comp in (self.control, self.vision, self.voice, self.decision, self.tts, self.recorder, self.mqtt,
                     self.notifier if self.notifier.is_alive() else None):
            if comp is not None:
                comp.stop()
        if self.camera:
            self.camera.stop()
        # Wait for threads running native code (OpenVINO, FFmpeg): exiting the interpreter
        # mid-inference triggers "terminate called..." and a core dump.
        for comp in (self.vision, self.recorder, self.camera, self.voice):
            if isinstance(comp, threading.Thread) and comp.is_alive():
                comp.join(timeout=5)
        if self.ptz:
            self.ptz.close()
        self.hw.close()
        self.db.log_event("core_stopped", actor="system")

    # --- Housekeeping (GDPR retention) -------------------------------------------------
    def _maintenance(self) -> None:
        """Purge expired unknown faces, events and sightings every 6 hours."""
        while True:
            now = time.time()
            st = self.s.storage
            files = self.db.purge(now - st.unknown_retention_days * 86400, now - st.event_retention_days * 86400,
                                  now - st.sighting_retention_days * 86400,
                                  now - st.plate_read_retention_days * 86400)
            remove_files(files)
            if files:
                log.info("Retention: %d files (unknown faces, sightings, plate reads) deleted", len(files))
            if self.stop_event.wait(6 * 3600):
                return

    def _notify(self, event_type: str, **payload) -> None:
        """Fan out a decision-engine notification to the webhook and the MQTT bridge."""
        self.notifier(event_type, **payload)
        if self.mqtt is not None:
            self.mqtt.publish_event(event_type, **payload)

    def _log_rotation(self) -> None:
        """Rotate ``/var/log/jarvis/jarvis.log``, checking once a minute.

        The core is the only rotator even though both processes write to the file,
        which avoids inter-process races.
        """
        state: dict = {}
        while not self.stop_event.wait(60):
            try:
                archived = logs.rotate_if_needed(self.s.logging, state)
                if archived:
                    log.info("Log rotated: %s", archived.name)
            except OSError:
                log.exception("Log rotation failed")

    # --- Control commands --------------------------------------------------------------
    def _status(self, _req: dict) -> dict:
        """Return a runtime status snapshot (camera, vision, voice, door, authorizations)."""
        return {
            "camera_connected": self.camera.connected,
            "vision_fps": round(self.vision.fps, 1) if self.vision else 0,
            "tracks": self.vision.active_tracks if self.vision else 0,
            "target_id": self.vision.target_id if self.vision else None,
            "known_embeddings": len(self.gallery),
            "voice_state": self.voice.state if self.voice else "disabled",
            "voice_window_s": round(self.voice.window_remaining_s) if self.voice else 0,
            "plates_enabled": bool(self.s.plates.enabled),
            "vehicles": self.vision.active_vehicles if self.vision else 0,
            "door": self.hw.door_state(),
            "authorized_persons": sorted(self.decision.authorized_persons()),
            "performance": {"detector_device": self.s.detector.device, "face_providers": self.s.faces.providers,
                            "hw_decode": self.s.camera.hw_accel, "process_fps": self.s.vision.process_fps},
        }

    def _reload_faces(self, _req: dict | None = None) -> dict:
        """Reload the face gallery from the database and reset vision tracks.

        Returns:
            ``{"known_embeddings": <count>}``.
        """
        self.gallery.load(self.db.all_face_embeddings())
        if self.vision:
            # Existing tracks carry identities resolved against the old gallery.
            self.vision.request_reset()
        return {"known_embeddings": len(self.gallery)}

    def _reload_voices(self, _req: dict | None = None) -> dict:
        """Reload voice profiles from the database (no-op if speaker verification is off)."""
        if self.speaker:
            self.speaker.gallery.load(self.db.all_voice_profiles())
        return {}

    def _enroll_face(self, req: dict) -> dict:
        """Enroll a face from an uploaded photo.

        Args:
            req: Request with ``person_id`` and ``image_path``.

        Returns:
            ``{"face_id": ..., "det_score": ...}``.

        Raises:
            ValueError: If no face is detected in the photo.
        """
        person_id, path = int(req["person_id"]), str(req["image_path"])
        face = self.faces.embed_image_file(path)
        if face is None:
            raise ValueError("No face detected in the photo")
        face_id = self.db.add_face_embedding(person_id, face.embedding, path, source="upload")
        self._reload_faces()
        return {"face_id": face_id, "det_score": round(face.det_score, 3)}

    def _embed_face(self, req: dict) -> dict:
        """Compute the embedding of the largest face in a photo, for search-by-face.

        Nothing is stored.

        Args:
            req: Request with ``image_path``.

        Returns:
            ``{"embedding": [...], "det_score": ...}``.

        Raises:
            ValueError: If no face is detected in the photo.
        """
        face = self.faces.embed_image_file(str(req["image_path"]))
        if face is None:
            raise ValueError("No face detected in the photo")
        return {"embedding": [round(float(v), 6) for v in face.embedding], "det_score": round(face.det_score, 3)}

    def _enroll_voice(self, req: dict) -> dict:
        """Enroll a voice profile from an uploaded recording.

        Args:
            req: Request with ``person_id`` and ``audio_path``.

        Returns:
            ``{"profile_id": ...}``.

        Raises:
            ValueError: If speaker verification is disabled or the recording is too short.
        """
        if self.speaker is None:
            raise ValueError("Speaker verification is disabled (speaker.enabled: false)")
        from jarvis.voice.audio import load_audio_file

        person_id, path = int(req["person_id"]), str(req["audio_path"])
        wav = load_audio_file(path)
        if len(wav) < 3 * 16000:  # 16 kHz mono samples
            raise ValueError("Recording too short (3 s minimum, 10 s recommended)")
        profile_id = self.db.add_voice_profile(person_id, self.speaker.embed(wav), path)
        self._reload_voices()
        return {"profile_id": profile_id}

    def _reload_settings(self, _req: dict | None = None) -> dict:
        """Hot-apply the settings changed from the web UI (database overrides).

        The effective settings are recomputed from the pristine ``config.yaml`` copy
        plus the current overrides, and only the differing catalog keys are written
        into the live settings object.

        Returns:
            ``{"changed": [keys], "restart_required": [keys that are not hot-reloadable]}``.
        """
        effective = self.base.model_copy(deep=True)
        apply_overrides(effective, self.db.get_settings_overrides())
        changed = []
        for prm in CATALOG:
            new = get_path(effective, prm.key)
            if get_path(self.s, prm.key) != new:
                set_path(self.s, prm.key, new)
                changed.append(prm.key)
        if any(k.startswith("commands.") for k in changed) and self.voice:
            from jarvis.voice.commands import CommandParser

            self.voice.parser = CommandParser(self.s.commands)  # rebuilds the Vosk grammar
        if any(k == "log_level" or k.startswith("logging.") for k in changed):
            logs.configure(self.s.log_level, self.s.logging, "core")
        if changed:
            log.info("Settings hot-applied: %s", ", ".join(changed))
        return {"changed": changed, "restart_required": [k for k in changed if not BY_KEY[k].hot]}

    def _restart(self, _req: dict) -> dict:
        """Schedule a graceful shutdown; systemd restarts the service (``Restart=always``).

        The short delay lets the control response reach the caller first.
        """
        log.warning("Restart requested from the web UI")
        threading.Timer(0.5, self.stop_event.set).start()
        return {}

    def _garage_pulse(self, req: dict) -> dict:
        """Queue a manual garage pulse on behalf of ``req["actor"]`` (defaults to ``"web"``)."""
        self.events.put(ManualGarage(actor=str(req.get("actor", "web"))))
        return {}

    def _ptz_home(self, _req: dict) -> dict:
        """Move the PTZ camera to its configured home preset, if any."""
        if self.s.ptz.home_preset:
            self.ptz.goto_preset(self.s.ptz.home_preset)
        return {}
