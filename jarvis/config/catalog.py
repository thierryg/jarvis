# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/config/catalog.py
# Purpose : Catalog of settings editable from the web UI, with validation
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Settings editable from the web UI.

``config.yaml`` remains the reference configuration (read-only for the service).
Changes made in the UI are **overrides** stored in the database (``settings`` table)
and applied on top of the YAML, when both processes start and then live (through the
core's ``reload_settings`` command).

Only the keys listed in this catalog can be changed. Secrets (passwords, keys) and
filesystem paths stay in ``/etc/jarvis/config.yaml`` and ``/etc/jarvis/jarvis.env``.

Each :class:`Param` describes one key: its UI metadata (group, label, help text,
unit), its type and bounds used for validation by :func:`coerce`, and whether it can
be applied without restarting the core (``hot``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from jarvis.config.settings import Settings


@dataclass(frozen=True)
class Param:
    """Metadata and validation rules for one editable setting.

    Attributes:
        key: Dotted path into :class:`Settings`, e.g. ``"faces.match_threshold"``.
        group: UI section title.
        label: UI field label (also used as the prefix of validation errors).
        help: UI help text (may be empty).
        type: Value type: ``float``, ``int``, ``bool``, ``str``, ``text``, ``list``,
            ``enum`` or ``events``.
        min: Inclusive lower bound for numeric types.
        max: Inclusive upper bound for numeric types.
        step: UI input step for numeric types.
        unit: Unit displayed next to the field.
        choices: Allowed values for ``enum`` and ``events`` types.
        hot: ``False`` if a change requires restarting the core service.
        api: ``True`` if the setting is (also) used by the API process.
        extra: Additional UI hints.
    """

    key: str                 # dotted path, e.g. "faces.match_threshold"
    group: str
    label: str
    help: str
    type: str                # float | int | bool | str | text | list | enum | events
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    choices: tuple[str, ...] = ()
    hot: bool = True         # False: requires restarting the core service
    api: bool = False        # True: (also) used by the API process
    extra: dict = field(default_factory=dict)


NOTIFY_EVENTS = ("face_unknown", "watchlist_seen", "access_denied_schedule", "voice_denied", "garage_pulse")

CATALOG: tuple[Param, ...] = (
    # --- Camera stream (shown first: it is the first thing to set up)
    Param("camera.rtsp_url", "Camera stream", "RTSP stream URL",
          "rtsp://<camera>:554/<path> of the stream analyzed by Jarvis (H.264 recommended: the Haswell GPU "
          "does not decode H.265). Put the account in Secrets > Camera account / password, not in the URL: "
          "it is injected at connection time. Use \"Test the stream\" below before saving.",
          "str", hot=False, api=True, extra={"placeholder": "rtsp://192.168.50.64:554/Streaming/Channels/102"}),
    Param("camera.reconnect_delay_s", "Camera stream", "Reconnection delay",
          "Wait before reconnecting after the stream is lost.", "float", 0.5, 60, 0.5, "s", hot=False),
    # --- Face recognition
    Param("faces.match_threshold", "Face recognition", "Recognition threshold",
          "Minimum ArcFace cosine similarity to match a face to a person. 0.40 lenient, 0.45 "
          "balanced, 0.55 strict.", "float", 0.30, 0.80, 0.01),
    Param("faces.votes_required", "Face recognition", "Required votes",
          "Number of consistent observations of the same track before the person is considered recognized.",
          "int", 1, 10, 1),
    Param("faces.unknown_after_observations", "Face recognition", "Observations before \"unknown\"",
          "Number of unmatched observations before a face is declared unknown.", "int", 2, 30, 1),
    Param("faces.recheck_interval_s", "Face recognition", "Interval between track analyses",
          "Minimum delay between two face analyses of the same track.", "float", 0.1, 5.0, 0.1, "s"),
    Param("faces.max_faces_per_frame", "Face recognition", "Faces analyzed per frame",
          "CPU budget: maximum number of faces analyzed per frame (the target first).", "int", 1, 6, 1),
    Param("faces.min_face_px", "Face recognition", "Minimum face size",
          "Shortest side of the face in pixels; smaller captures are ignored.", "int", 20, 200, 1, "px"),
    Param("faces.min_det_score", "Face recognition", "Minimum detection score",
          "Minimum confidence of the SCRFD detector.", "float", 0.3, 0.95, 0.01),
    Param("faces.min_sharpness", "Face recognition", "Minimum sharpness",
          "Minimum variance of the Laplacian (blurry images are ignored).", "float", 0, 300, 1),
    # --- Unknown visitors
    Param("faces.unknown_dedupe_similarity", "Unknown visitors", "Deduplication threshold",
          "Above this, an unknown visitor seen again within the deduplication window is not stored again.",
          "float", 0.3, 0.9, 0.01),
    Param("faces.unknown_dedupe_window_s", "Unknown visitors", "Deduplication window",
          "Time during which the same unknown visitor is stored only once.", "float", 60, 86400, 60, "s"),
    Param("faces.unknown_cluster_similarity", "Unknown visitors", "Clustering threshold",
          "Above this, a new unknown face joins the cluster of the closest visitor.", "float", 0.3, 0.9, 0.01),
    # --- Adaptive learning
    Param("faces.adaptive_enabled", "Adaptive learning", "Enable adaptive learning",
          "Automatically adds the best captures of a confidently recognized person.", "bool"),
    Param("faces.adaptive_min_score", "Adaptive learning", "Minimum score",
          "Minimum similarity to learn a capture (well above the recognition threshold).",
          "float", 0.45, 0.95, 0.01),
    Param("faces.adaptive_min_quality", "Adaptive learning", "Minimum quality",
          "Minimum capture quality (detection x size x sharpness, from 0 to 1).", "float", 0, 1, 0.05),
    Param("faces.adaptive_max_per_person", "Adaptive learning", "Max. automatic captures per person",
          "Cap on automatic additions per person.", "int", 0, 50, 1),
    Param("faces.adaptive_novelty_max_similarity", "Adaptive learning", "Novelty threshold",
          "A capture closer than this to an existing photo adds nothing and is ignored.",
          "float", 0.5, 0.99, 0.01),
    Param("faces.search_threshold", "Search by face", "Search-by-face threshold",
          "Minimum similarity of the sightings returned by a photo search.", "float", 0.3, 0.9, 0.01, api=True),
    # --- Detection and vision
    Param("detector.conf", "Person detection", "Minimum YOLO confidence",
          "\"Person\" detections below this confidence are ignored.", "float", 0.1, 0.9, 0.01),
    Param("detector.imgsz", "Person detection", "Detector input size",
          "480 by default, 416 on a dual-core CPU (the OpenVINO model must be re-exported).", "enum",
          choices=("320", "416", "480", "640"), hot=False),
    Param("vision.process_fps", "Person detection", "Analyses per second",
          "Maximum rate of the vision pipeline.", "float", 1, 15, 0.5, "fps"),
    Param("vision.preview_fps", "Person detection", "Preview frame rate",
          "Frames per second of the Live tab stream.", "float", 1, 15, 1, "fps", hot=False, api=True),
    Param("vision.preview_width", "Person detection", "Preview width",
          "Width in pixels of the image streamed to the Live tab.", "int", 320, 1920, 10, "px", hot=False),
    Param("performance.mode", "Performance", "Performance tuning",
          "auto: at start-up, pick the inference devices, providers, hardware decoding and rates that suit the "
          "detected hardware (only for settings you did not set); manual: use the configuration as is.",
          "enum", choices=("auto", "manual"), hot=False),
    Param("detector.device", "Performance", "YOLO inference device",
          "auto follows the hardware detection; intel:gpu needs a Gen9+ Intel GPU (not the Haswell HD 4600).",
          "enum", choices=("auto", "intel:cpu", "intel:gpu", "intel:npu"), hot=False),
    Param("camera.hw_accel", "Performance", "VAAPI hardware decoding",
          "H.264 decoding on the Intel GPU (auto: when vainfo reports H.264 decoding).", "enum",
          choices=("auto", "true", "false"), hot=False),
    # --- PTZ
    Param("ptz.enabled", "PTZ camera", "PTZ enabled", "Disable for a fixed camera.", "bool", hot=False),
    Param("ptz.host", "PTZ camera", "ONVIF address",
          "IP address or host name of the camera ONVIF service (usually the camera itself).", "str", hot=False),
    Param("ptz.port", "PTZ camera", "ONVIF port", "HTTP port of the ONVIF service (80, 8000 or 8080 depending on the vendor).",
          "int", 1, 65535, 1, hot=False),
    Param("ptz.profile_index", "PTZ camera", "ONVIF media profile", "Index of the media profile carrying the PTZ configuration.",
          "int", 0, 15, 1, hot=False),
    Param("ptz.home_preset", "PTZ camera", "Home preset", "Number of the \"entrance view\" preset.", "str"),
    Param("ptz.return_home_after_s", "PTZ camera", "Return to preset after", "Delay without a target before returning.",
          "float", 5, 600, 5, "s"),
    Param("ptz.pan_gain", "PTZ camera", "Pan gain", "Horizontal tracking responsiveness.", "float", 0.1, 2, 0.05),
    Param("ptz.tilt_gain", "PTZ camera", "Tilt gain", "Vertical tracking responsiveness.", "float", 0.1, 2, 0.05),
    Param("ptz.zoom_gain", "PTZ camera", "Zoom gain", "Zoom responsiveness.", "float", 0, 2, 0.05),
    Param("ptz.deadzone", "PTZ camera", "Dead zone", "Tolerated offset around the center (fraction of the frame).",
          "float", 0, 0.5, 0.01),
    Param("ptz.max_speed", "PTZ camera", "Maximum speed", "Cap on PTZ speeds (ONVIF, 0 to 1).",
          "float", 0.05, 1, 0.05),
    Param("ptz.invert_tilt", "PTZ camera", "Invert tilt", "Camera mounted upside down.", "bool"),
    Param("ptz.zoom_enabled", "PTZ camera", "Automatic zoom", "Zoom in to enlarge the tracked person.", "bool"),
    Param("ptz.target_height_ratio", "PTZ camera", "Target person size",
          "Target height of the person, as a fraction of the frame.", "float", 0.2, 0.9, 0.05),
    Param("ptz.command_interval_s", "PTZ camera", "Interval between commands", "Maximum command rate.",
          "float", 0.1, 2, 0.05, "s"),
    # --- Decision
    Param("decision.auth_window_s", "Decision and access", "Recognition window",
          "After an authorized person is recognized, the green LED stays on for this time, then turns off; "
          "voice commands (\"Jarvis… ouvre la porte du garage\" / \"ferme la porte du garage\") are accepted "
          "only during this window. A new recognition extends it.", "float", 5, 600, 5, "s"),
    Param("decision.voice_only_after_recognition", "Decision and access", "Voice only after recognition",
          "Ignore the microphone outside the recognition window (recommended).", "bool"),
    Param("decision.led_on_s", "Decision and access", "Other LED signals",
          "On-time of the LED for a garage pulse, an unknown face or a watchlisted person.", "float", 1, 60, 1, "s"),
    Param("decision.greeting", "Decision and access", "Greeting",
          "Phrase spoken at the first recognition of the day; {first_name} is replaced by the first name.", "str"),
    Param("decision.unknown_message", "Decision and access", "Message for an unknown visitor",
          "Phrase spoken to an unknown visitor (empty = no message).", "str"),
    Param("decision.watchlist_message", "Decision and access", "Watchlist message",
          "Phrase spoken when a watchlisted person is recognized (empty = no message).", "str"),
    Param("hardware.pulse_ms", "Decision and access", "Garage pulse duration",
          "Contact closure time (the Novoferm manual calls for a short pulse).", "int", 100, 2000, 50, "ms"),
    Param("hardware.cooldown_s", "Decision and access", "Minimum delay between two pulses", "", "float", 1, 60, 1, "s"),
    # --- Voice
    Param("wakeword.threshold", "Voice", "Wake word sensitivity",
          "Minimum openWakeWord score; lower = more sensitive, more false triggers.",
          "float", 0.1, 0.95, 0.05),
    Param("stt.listen_timeout_s", "Voice", "Listening time after the wake word", "", "float", 2, 20, 1, "s"),
    Param("stt.max_utterance_s", "Voice", "Max. duration analyzed for the speaker", "", "float", 1, 15, 0.5, "s"),
    Param("speaker.enabled", "Voice", "Speaker verification",
          "Requires the voice to match the recognized person (the ECAPA model must be installed).", "bool", hot=False),
    Param("speaker.threshold", "Voice", "Speaker threshold", "Minimum ECAPA similarity.", "float", 0.1, 0.9, 0.01),
    Param("commands.open_phrases", "Voice", "Open phrases",
          "Spoken after the wake word, one phrase per line (Vosk grammar).", "list"),
    Param("commands.close_phrases", "Voice", "Close phrases", "One phrase per line.", "list"),
    Param("commands.strict", "Voice", "Exact phrases only",
          "Accept only the phrases above, spoken after the wake word (no keyword guessing).", "bool"),
    # --- Data
    # --- Recording (event-triggered clips) and time-lapse
    Param("recording.enabled", "Video recording", "Record clips on detection",
          "Writes an MP4 clip (with pre-roll) when one of the selected events occurs.", "bool"),
    Param("recording.triggers", "Video recording", "Recording triggers",
          "Events that start or extend a clip.", "multi", choices=("person", "known", "unknown", "watchlist", "vehicle")),
    Param("recording.pre_seconds", "Video recording", "Pre-roll", "Footage kept before the trigger.",
          "float", 0, 30, 1, "s"),
    Param("recording.post_seconds", "Video recording", "Post-roll", "Recording continues after the last trigger.",
          "float", 1, 120, 1, "s"),
    Param("recording.max_clip_seconds", "Video recording", "Maximum clip length", "", "float", 10, 900, 10, "s"),
    Param("recording.fps", "Video recording", "Recording frame rate", "", "float", 2, 25, 1, "fps"),
    Param("recording.width", "Video recording", "Recording width", "Wider frames are down-scaled.",
          "int", 320, 3840, 10, "px"),
    Param("recording.crf", "Video recording", "Video quality (CRF)", "18 = very good, 28 = compact, 35 = small.",
          "int", 16, 40, 1),
    Param("recording.max_total_gb", "Video recording", "Storage quota (FIFO)",
          "The oldest clips are deleted beyond this size.", "float", 0.5, 2000, 0.5, "GB"),
    Param("recording.max_age_days", "Video recording", "Maximum clip age", "", "int", 1, 365, 1, "days"),
    Param("timelapse.enabled", "Time-lapse", "Capture time-lapse snapshots", "", "bool"),
    Param("timelapse.interval_s", "Time-lapse", "Snapshot interval", "", "float", 1, 3600, 1, "s"),
    Param("timelapse.width", "Time-lapse", "Snapshot width", "", "int", 160, 1920, 10, "px"),
    Param("timelapse.max_total_gb", "Time-lapse", "Storage quota (FIFO)", "", "float", 0.1, 500, 0.1, "GB"),
    Param("timelapse.max_age_days", "Time-lapse", "Maximum snapshot age", "", "int", 1, 730, 1, "days"),
    Param("storage.unknown_retention_days", "Data retention (GDPR)", "Unknown faces",
          "Retention period for unknown faces.", "int", 1, 365, 1, "days"),
    Param("storage.plate_read_retention_days", "Data retention (GDPR)", "Plate reads",
          "Plate reads and their vehicle snapshots are deleted after this delay.", "int", 1, 3650, 1, "days"),
    Param("storage.sighting_retention_days", "Data retention (GDPR)", "Sightings",
          "Retention period for timestamped sightings and their photos.", "int", 1, 730, 1, "days"),
    Param("storage.event_retention_days", "Data retention (GDPR)", "Event log and audit",
          "Retention period for the event log and the audit trail.", "int", 7, 3650, 1, "days"),
    # --- License plates (ANPR)
    Param("plates.enabled", "License plates", "License plate recognition",
          "Track vehicles and read their plates (fast-alpr, offline). Needs the plate models: run "
          "\"jarvis setup-models\" once after enabling.", "bool", hot=False),
    Param("plates.auto_open", "License plates", "Open for known plates",
          "Open the garage when a vehicle with a known, enabled plate approaches and the door sensor reads "
          "\"closed\" (the plate's person must be allowed at that time).", "bool"),
    Param("plates.auto_close", "License plates", "Close after a known vehicle left",
          "DANGER: an unattended closing door can hurt. Closes only when the door sensor reads \"open\" and the "
          "camera saw nobody (no person, no vehicle) during the whole delay; the motor's obstacle detection "
          "and a photocell must work.", "bool"),
    Param("plates.close_delay_s", "License plates", "Clear scene before closing",
          "The scene must stay empty for this long; any presence restarts the countdown.", "float", 5, 300, 5, "s"),
    Param("plates.min_confidence", "License plates", "Minimum read confidence",
          "Mean OCR confidence required for a read to count.", "float", 0.5, 0.99, 0.01),
    Param("plates.votes_required", "License plates", "Identical reads to confirm",
          "Number of identical reads of the same vehicle before the plate is trusted.", "int", 1, 10, 1),
    Param("plates.read_interval_s", "License plates", "Interval between reads",
          "Per vehicle; lower = faster confirmation, more CPU.", "float", 0.2, 5, 0.1, "s"),
    Param("plates.min_vehicle_px", "License plates", "Minimum vehicle width",
          "Vehicles smaller than this in the image are not read (plate too small).", "int", 40, 1920, 10, "px"),
    Param("plates.approach_growth", "License plates", "Approach threshold",
          "Growth of the vehicle box over 2 s meaning \"approaching\" (1.15 = +15 %).", "float", 1.02, 2, 0.01),
    Param("plates.leave_shrink", "License plates", "Departure threshold",
          "Shrinkage of the vehicle box over 2 s meaning \"leaving\" (0.85 = -15 %).", "float", 0.3, 0.98, 0.01),
    Param("plates.unknown_notify", "License plates", "Notify unknown plates",
          "Send a notification (webhook, MQTT) for unknown, disabled or expired plates.", "bool"),
    # --- Simulation (test without a real camera or microphone)
    Param("simulation.loop", "Camera simulation", "Loop the video",
          "Restart the simulated video at its end (otherwise its last frame stays on screen).", "bool"),
    Param("simulation.drive_relay", "Camera simulation", "Simulations drive the relay",
          "DANGER: when on, a garage pulse decided during a simulation (simulated camera, face or voice) "
          "really drives the relay. Off: the pulse is only logged as garage_pulse_simulated.", "bool"),
    Param("simulation.require_window", "Voice simulation", "Apply the recognition window",
          "Simulated voice commands obey the live rule: a person must have been recognized within the "
          "recognition window (simulate one first). Off: test the voice chain alone.", "bool"),
    # --- Web UI
    Param("api.idle_timeout_minutes", "Web interface", "Automatic logout after inactivity",
          "A session with no user action during this delay is closed (automatic status refresh "
          "and the video stream do not count as activity).", "enum",
          choices=("5", "15", "30", "60", "120", "240", "480"), unit="min", api=True),
    Param("api.session_hours", "Web interface", "Maximum session duration",
          "Beyond this, the session is closed even if active.", "float", 0.5, 168, 0.5, "h", api=True),
    Param("api.max_upload_mb", "Web interface", "Max. upload file size", "", "int", 1, 15, 1, "MB", api=True),
    Param("ui.language", "Web interface", "Interface language",
          "Default language of the interface and the login screen (each user can change it "
          "for themselves from the menu).", "enum",
          choices=("en-US", "fr-FR", "es-ES", "nl-NL", "de-DE", "it-IT", "ru-RU", "zh-CN", "id-ID", "ko-KR",
                   "ja-JP", "th-TH"), api=True),
    # --- Logging
    Param("log_level", "Logging", "Log level",
          "DEBUG is very verbose; INFO is suitable for production.", "enum",
          choices=("DEBUG", "INFO", "WARNING", "ERROR"), api=True),
    Param("logging.file_enabled", "Logging", "Log file /var/log/jarvis/jarvis.log",
          "Writes the log of both services to a file, in addition to journald.", "bool", api=True),
    Param("logging.rotate_mode", "Logging", "Rotation mode",
          "\"size\": as soon as the file exceeds the maximum size; \"daily\": every day at midnight.",
          "enum", choices=("size", "daily")),
    Param("logging.rotate_max_mb", "Logging", "Maximum size before rotation", "", "int", 1, 500, 1, "MB"),
    Param("logging.rotate_keep", "Logging", "Archives kept",
          "Number of jarvis.log.N files kept.", "int", 1, 100, 1),
    Param("logging.rotate_compress", "Logging", "Compress archives (gzip)", "", "bool"),
    Param("logging.syslog_enabled", "Logging", "Export to an external syslog",
          "Also sends the log to an rsyslog / syslog-ng server (RFC 3164).", "bool", api=True),
    Param("logging.syslog_host", "Logging", "Syslog server", "Host name or IP address.", "str", api=True),
    Param("logging.syslog_port", "Logging", "Syslog port", "Usually 514.", "int", 1, 65535, 1, api=True),
    Param("logging.syslog_protocol", "Logging", "Syslog protocol", "", "enum", choices=("udp", "tcp"),
          api=True),
    Param("logging.syslog_facility", "Logging", "Syslog facility",
          "Category used to sort messages on the server side.", "enum",
          choices=("user", "daemon", "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7"),
          api=True),
    # --- Monitoring
    Param("monitoring.metrics_enabled", "Monitoring (Grafana)", "Prometheus /metrics endpoint",
          "Exposes Jarvis metrics (vision, camera, sightings, sessions, audit, disk). Network access requires "
          "a Bearer token.", "bool", api=True),
    Param("monitoring.node_exporter_enabled", "Monitoring (Grafana)", "node_exporter (system metrics)",
          "Enables the Prometheus node_exporter agent (CPU, memory, disk, network) on port 9100.", "bool", api=True),
    Param("monitoring.log_shipper", "Monitoring (Grafana)", "Ship logs to Loki",
          "Agent that pushes /var/log/jarvis/*.log and journald to Loki. Grafana has deprecated Promtail in "
          "favor of Alloy.", "enum", choices=("none", "promtail", "alloy"), api=True),
    Param("monitoring.loki_url", "Monitoring (Grafana)", "Loki push URL",
          "E.g. http://grafana.lan:3100/loki/api/v1/push", "str", api=True),
    # --- Notifications
    # --- Smart home (MQTT)
    Param("mqtt.enabled", "Smart home (MQTT)", "MQTT bridge",
          "Publishes states and events to an MQTT broker: Home Assistant, openHAB, Domoticz, Jeedom, Node-RED.",
          "bool", hot=False),
    Param("mqtt.host", "Smart home (MQTT)", "Broker host", "Name or IP address of the MQTT broker.", "str", hot=False),
    Param("mqtt.port", "Smart home (MQTT)", "Broker port", "1883, or 8883 with TLS.", "int", 1, 65535, 1, hot=False),
    Param("mqtt.tls", "Smart home (MQTT)", "TLS", "Encrypt the MQTT connection (recommended).", "bool", hot=False),
    Param("mqtt.base_topic", "Smart home (MQTT)", "Base topic", "Prefix of every Jarvis topic.", "str", hot=False),
    Param("mqtt.ha_discovery", "Smart home (MQTT)", "Home Assistant discovery",
          "Creates the Jarvis device and its entities in Home Assistant automatically.", "bool", hot=False),
    Param("mqtt.discovery_prefix", "Smart home (MQTT)", "Discovery prefix", "homeassistant by default.", "str",
          hot=False),
    Param("mqtt.state_interval_s", "Smart home (MQTT)", "State publishing interval", "", "float", 2, 600, 1, "s"),
    Param("mqtt.allow_commands", "Smart home (MQTT)", "Accept remote commands",
          "Garage pulse, PTZ home and speech from the smart-home controller. Every command must carry the MQTT "
          "command token; keep the broker authenticated, encrypted and restricted by ACLs.", "bool", hot=False),
    # --- Secrets (write-only: the API reports "set / not set" and the origin, never the value)
    Param("secrets.camera_username", "Secrets", "Camera account (ONVIF / RTSP)",
          "Account created on the camera for Jarvis.", "secret", hot=False),
    Param("secrets.camera_password", "Secrets", "Camera password", "", "secret", hot=False),
    Param("secrets.picovoice_access_key", "Secrets", "Picovoice access key",
          "Only needed for the Porcupine « jarvis » wake word.", "secret", hot=False),
    Param("secrets.metrics_token", "Secrets", "Prometheus metrics token",
          "Bearer token required on /metrics from the network (generated automatically when empty).",
          "secret", api=True),
    Param("secrets.webhook_token", "Secrets", "Webhook token",
          "Sent as « Authorization: Bearer » with each notification (ntfy, Node-RED…).", "secret"),
    Param("secrets.loki_username", "Secrets", "Loki user", "Basic authentication of the Loki push endpoint.",
          "secret", api=True),
    Param("secrets.loki_password", "Secrets", "Loki password", "", "secret", api=True),
    Param("secrets.mqtt_username", "Secrets", "MQTT user", "Account of Jarvis on the MQTT broker.", "secret", hot=False),
    Param("secrets.mqtt_password", "Secrets", "MQTT password", "", "secret", hot=False),
    Param("secrets.mqtt_command_token", "Secrets", "MQTT command token",
          "Shared secret that every MQTT command payload must carry: {\"token\": \"…\"}.", "secret"),
    Param("notifications.webhook_url", "Notifications", "Webhook URL",
          "URL called with a JSON POST (Home Assistant, Node-RED, ntfy...). Empty = disabled.", "str", hot=False),
    Param("notifications.events", "Notifications", "Notified events", "", "events", choices=NOTIFY_EVENTS),
    Param("notifications.timeout_s", "Notifications", "Send timeout", "", "float", 1, 30, 1, "s"),
)

BY_KEY = {p.key: p for p in CATALOG}


def get_path(obj: Any, key: str) -> Any:
    """Read a nested attribute by dotted path.

    Args:
        obj: Root object (typically :class:`Settings`).
        key: Dotted path such as ``"faces.match_threshold"``.

    Returns:
        The attribute value.

    Raises:
        AttributeError: If a path component does not exist.
    """
    for part in key.split("."):
        obj = getattr(obj, part)
    return obj


def set_path(obj: Any, key: str, value: Any) -> None:
    """Set a nested attribute by dotted path (in place, without validation).

    Args:
        obj: Root object (typically :class:`Settings`).
        key: Dotted path such as ``"faces.match_threshold"``.
        value: New value.

    Raises:
        AttributeError: If a parent path component does not exist.
    """
    *parents, last = key.split(".")
    for part in parents:
        obj = getattr(obj, part)
    setattr(obj, last, value)


def coerce(p: Param, value: Any) -> Any:
    """Convert and validate a user-supplied value for ``p``.

    Args:
        p: Catalog entry describing the setting.
        value: Raw value (from JSON or the CLI).

    Returns:
        The value converted to the setting's type. Optional message and URL settings
        return ``None`` for an empty string.

    Raises:
        ValueError: With a human-readable message prefixed by the setting label.
    """
    if p.type == "secret":
        s = "" if value is None else str(value)
        if any(ch in s for ch in "\r\n\0"):
            raise ValueError(f"{p.label}: control characters are not allowed")
        return s.strip()
    if p.type in ("float", "int"):
        try:
            v = float(value) if p.type == "float" else int(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"{p.label}: number expected") from None
        if (p.min is not None and v < p.min) or (p.max is not None and v > p.max):
            raise ValueError(f"{p.label}: value out of range [{p.min}, {p.max}]")
        return v
    if p.type == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(f"{p.label}: boolean expected")
    if p.type == "enum":
        if p.key == "camera.hw_accel":
            value = str(value).lower()
        if str(value) not in p.choices:
            raise ValueError(f"{p.label}: allowed values are {', '.join(p.choices)}")
        # These enums are rendered as string choices in the UI but stored as integers.
        if p.key == "camera.hw_accel":
            return {"auto": "auto", "true": True, "false": False}[str(value).lower()]
        return int(value) if p.key in ("detector.imgsz", "api.idle_timeout_minutes") else str(value)
    if p.type == "list":
        # Accept either a JSON list or a newline-separated text block.
        items = value if isinstance(value, list) else str(value).splitlines()
        items = [str(i).strip() for i in items if str(i).strip()]
        if not items:
            raise ValueError(f"{p.label}: at least one phrase is required")
        return items
    if p.type in ("events", "multi"):
        items = list(value or [])
        bad = [i for i in items if i not in p.choices]
        if bad:
            raise ValueError(f"{p.label}: unknown event {bad}")
        return items
    s = "" if value is None else str(value).strip()
    if p.key in ("decision.unknown_message", "decision.watchlist_message", "notifications.webhook_url"):
        return s or None
    if p.key == "monitoring.loki_url" and s and not s.startswith(("http://", "https://")):
        raise ValueError("Loki URL: http:// or https:// expected")
    if p.key == "logging.syslog_host" and s and not all(ch.isalnum() or ch in ".-:" for ch in s):
        raise ValueError("Syslog server: host name or IP address expected")
    if p.key == "notifications.webhook_url" and s and not s.startswith(("http://", "https://")):
        raise ValueError("Webhook URL: http:// or https:// expected")
    return s


def apply_overrides(settings: Settings, overrides: dict[str, Any]) -> list[str]:
    """Apply known, valid overrides to ``settings`` in place.

    Unknown keys and invalid values are skipped rather than raising, so that a stale
    or corrupted override never prevents the service from starting.

    Args:
        settings: Settings object to mutate.
        overrides: Key -> value mapping loaded from the database.

    Returns:
        The keys that were ignored.
    """
    ignored = []
    for key, value in overrides.items():
        p = BY_KEY.get(key)
        if p is None:
            ignored.append(key)
            continue
        try:
            set_path(settings, key, coerce(p, value))
        except (ValueError, AttributeError):
            ignored.append(key)
    return ignored


RTSP_URL = re.compile(r"rtsps?://(?P<userinfo>[^@/\s]+@)?(?P<host>\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::(?P<port>\d{1,5}))?(?:/\S*)?")


def check_rtsp_url(url: str) -> str:
    """Validate a camera stream URL: ``rtsp://`` or ``rtsps://``, a host, no embedded credentials.

    Credentials in the URL would be stored in clear text in the settings table and shown in the
    audit diffs: they belong to ``secrets.camera_username`` / ``secrets.camera_password``, which
    :func:`jarvis.config.settings.rtsp_url_with_credentials` injects at connection time.

    Args:
        url: Candidate URL.

    Returns:
        The URL, unchanged.

    Raises:
        ValueError: If the URL is malformed or carries a user name or password.
    """
    m = RTSP_URL.fullmatch(url or "")
    if not m:
        raise ValueError("RTSP stream URL: rtsp://host[:port]/path expected")
    if m["userinfo"]:
        raise ValueError("RTSP stream URL: remove the user name and password from the URL and set them "
                         "in Secrets > Camera account / Camera password")
    if m["port"] and not 1 <= int(m["port"]) <= 65535:
        raise ValueError("RTSP stream URL: invalid port")
    return url


# Settings page tabs: each group (sub-tab) belongs to one domain (main tab), in this order.
DOMAINS: dict[str, tuple[str, ...]] = {
    "camera": ("Camera stream", "PTZ camera", "Person detection", "Performance"),
    "recognition": ("Face recognition", "Unknown visitors", "Adaptive learning", "Search by face", "License plates"),
    "access": ("Decision and access", "Voice"),
    "recording": ("Video recording", "Time-lapse"),
    "integrations": ("Smart home (MQTT)", "Notifications", "Monitoring (Grafana)"),
    "system": ("Web interface", "Logging", "Data retention (GDPR)", "Secrets"),
    "simulation": ("Camera simulation", "Voice simulation"),
}
GROUP_DOMAIN = {group: domain for domain, groups in DOMAINS.items() for group in groups}


def validate_changes(base: Settings, changes: dict[str, Any]) -> dict[str, Any]:
    """Fully validate a batch of changes against the catalog and the Pydantic model.

    Args:
        base: Current settings; not modified (a deep copy is used for the trial).
        changes: Key -> raw value mapping submitted by the user.

    Returns:
        Key -> converted value mapping, ready to be stored as overrides.

    Raises:
        ValueError: If a key is not editable, a value is invalid, or the resulting
            configuration fails model validation.
    """
    out = {}
    for key, value in changes.items():
        p = BY_KEY.get(key)
        if p is None:
            raise ValueError(f"Setting not editable: {key}")
        out[key] = coerce(p, value)
    if "camera.rtsp_url" in out:
        check_rtsp_url(out["camera.rtsp_url"])
    if "ptz.host" in out and out["ptz.host"] and not re.fullmatch(r"[A-Za-z0-9.:\[\]-]{1,253}", out["ptz.host"]):
        raise ValueError("ONVIF address: host name or IP address expected")
    if "notifications.webhook_url" in out and out["notifications.webhook_url"]:
        if not out["notifications.webhook_url"].startswith(("http://", "https://")):
            raise ValueError("Webhook URL: http:// or https:// expected")
    # Validate the whole resulting configuration, not just individual fields.
    trial = base.model_copy(deep=True)
    for key, value in out.items():
        set_path(trial, key, value)
    try:
        Settings.model_validate(trial.model_dump())
    except ValidationError as exc:
        raise ValueError(str(exc)) from None
    return out


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a readable audit diff containing only the changed values.

    Args:
        before: Values before the change.
        after: Values after the change.

    Returns:
        ``[{"field": key, "before": old, "after": new}, ...]`` sorted by key.
    """
    return [{"field": k, "before": mask(k, before.get(k)), "after": mask(k, after.get(k))}
            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]


def is_secret(key: str) -> bool:
    """True for write-only settings, whose value must never be displayed nor audited."""
    p = BY_KEY.get(key)
    return bool(p and p.type == "secret")


def mask(key: str, value: Any) -> Any:
    """Audit-safe representation of a value: secrets become « (set) » / « (empty) »."""
    if not is_secret(key):
        return value
    return "(set)" if value else "(empty)"
