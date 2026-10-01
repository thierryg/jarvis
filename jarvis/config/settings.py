# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/config/settings.py
# Purpose : Configuration models, loading and validation (YAML + environment)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Configuration loading and validation (YAML + environment variables).

The configuration is a tree of Pydantic models rooted at :class:`Settings`; every field
has a sensible default so that an empty or missing ``config.yaml`` still yields a
runnable (mock hardware) configuration.

Configuration sources, highest precedence first:

1. **Parameter**: a value set from the web UI or the ``jarvis set`` shell command, stored as an
   override in the database (applied by :func:`jarvis.config.catalog.apply_overrides`);
2. **Environment**: ``JARVIS__<SECTION>__<KEY>`` variables override any key, for example
   ``JARVIS__SECRETS__CAMERA_PASSWORD`` or ``JARVIS__FACES__MATCH_THRESHOLD=0.5``;
3. **File**: the central ``/etc/jarvis/config.yaml`` (``${VAR}`` / ``${VAR:-default}`` references are
   expanded from the environment, typically ``/etc/jarvis/jarvis.env``);
4. **Default**: the model defaults below.

All credentials (camera, Picovoice, metrics/webhook tokens, Loki) live in the ``secrets`` section;
they are never returned by the API nor written in clear text in the audit log.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATHS = (Path("/etc/jarvis/config.yaml"), Path("config/config.yaml"))
ENV_PREFIX = "JARVIS__"

# Origin of the values that did not come from the defaults: dotted key -> "file" | "environment".
# Filled by load_settings(); "parameter" (database override) is resolved by the callers.
VALUE_SOURCES: dict[str, str] = {}

# Matches ${NAME} and ${NAME:-default}.
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class CameraConfig(BaseModel):
    """RTSP camera stream settings."""

    # Prefer the camera's substream (720p H.264) for analysis: continuously decoding
    # 4K H.265 saturates a Haswell-class CPU.
    # Credentials are injected from ``secrets.camera_username/password`` when the URL has none.
    rtsp_url: str = "rtsp://192.168.50.64:554/stream2"
    hw_accel: bool | Literal["auto"] = "auto"   # auto: VAAPI when the GPU decodes H.264 (performance.mode)
    reconnect_delay_s: float = 2.0


class PTZConfig(BaseModel):
    """ONVIF PTZ control and subject tracking (proportional pan/tilt/zoom) settings."""

    enabled: bool = True
    host: str = "192.168.50.64"
    port: int = 80
    # Empty: taken from ``secrets.camera_username`` / ``secrets.camera_password``.
    username: str = ""
    password: str = Field(default="", repr=False)
    profile_index: int = 0
    home_preset: str | None = "1"
    return_home_after_s: float = 30.0
    sound_preset: str | None = None
    pan_gain: float = 0.6
    tilt_gain: float = 0.6
    deadzone: float = 0.12
    max_speed: float = 0.5
    invert_tilt: bool = False
    zoom_enabled: bool = True
    target_height_ratio: float = 0.55
    zoom_gain: float = 0.5
    command_interval_s: float = 0.25


class DetectorConfig(BaseModel):
    """YOLO person detector and tracker settings."""

    # OpenVINO-exported model (see `jarvis setup-models`).
    model_path: str = "/var/lib/jarvis/models/yolo11n_openvino_model"
    imgsz: int = 480
    conf: float = 0.45
    tracker: str = "bytetrack.yaml"
    # OpenVINO device for YOLO: auto (hardware detection), intel:cpu, intel:gpu, intel:npu.
    device: Literal["auto", "intel:cpu", "intel:gpu", "intel:npu"] = "auto"


class FaceConfig(BaseModel):
    """Face detection, recognition, unknown-face handling and adaptive enrollment settings."""

    model_name: str = "buffalo_s"
    model_root: str = "/var/lib/jarvis/models/insightface"
    # ONNX Runtime providers; ["auto"] = chosen from the detected hardware (CUDA, OpenVINO, CPU).
    providers: list[str] = Field(default_factory=lambda: ["auto"])
    det_size: int = 320
    match_threshold: float = 0.45
    min_face_px: int = 40
    min_det_score: float = 0.6
    min_sharpness: float = 30.0
    votes_required: int = 3
    unknown_after_observations: int = 6
    recheck_interval_s: float = 0.4
    max_faces_per_frame: int = 2
    unknown_dedupe_similarity: float = 0.5
    unknown_dedupe_window_s: float = 3600.0
    # Unknown-face clustering: a new face joins the cluster of the closest unknown visitor.
    unknown_cluster_similarity: float = 0.5
    # Adaptive learning: the best captures of a confidently recognized person are added to
    # their gallery (new haircut, glasses, beard, aging).
    adaptive_enabled: bool = True
    adaptive_min_score: float = 0.6        # well above the recognition threshold (0.45)
    adaptive_min_quality: float = 0.35     # detection score x size x sharpness, 0..1
    adaptive_max_per_person: int = 5
    adaptive_novelty_max_similarity: float = 0.85  # above this, the capture adds nothing new
    # Search-by-face across the sightings history.
    search_threshold: float = 0.45


class VisionConfig(BaseModel):
    """Vision loop rate and live preview settings."""

    process_fps: float = 6.0
    preview_fps: float = 5.0
    preview_width: int = 960
    frame_path: str = "/run/jarvis/frame.jpg"


class DecisionConfig(BaseModel):
    """Decision engine settings: LEDs, spoken messages and the recognition window.

    The recognition window (``auth_window_s``) opens when an authorized person is recognized:
    the green LED stays on for its whole duration and turns off at its end, and voice commands
    are accepted only while it is open (``voice_only_after_recognition``), each one starting
    with the "Jarvis" wake word. A new recognition extends the window.
    """

    led_on_s: float = 5.0                       # other LED signals (pulse, unknown, watchlist)
    greeting: str = "Bonjour {first_name}"
    # Listening WITHOUT the wake word after the greeting: 0 (disabled) because every command
    # must start with the "Jarvis" token.
    listen_after_recognition_s: float = 0.0
    auth_window_s: float = 30.0                 # recognition window: green LED + voice commands
    voice_only_after_recognition: bool = True   # microphone ignored outside the window
    unknown_message: str | None = None
    watchlist_message: str | None = None   # spoken when a watchlisted person is recognized


class AudioConfig(BaseModel):
    """Audio input/output device settings."""

    enabled: bool = True
    input_device: str | int | None = None
    output_device: str | int | None = None
    sample_rate: int = 16000
    # Simple sound trigger (RMS level) used to point the PTZ camera; 0 = disabled.
    sound_trigger_rms: float = 0.0


class WakeWordConfig(BaseModel):
    """Wake word engine settings (openWakeWord or Porcupine)."""

    engine: Literal["openwakeword", "porcupine"] = "openwakeword"
    # Name of a pre-trained model (downloaded into oww_dir) or path to a custom-trained .onnx
    # (e.g. plain "jarvis", see the openWakeWord training notebook).
    oww_model: str = "hey_jarvis_v0.1"
    oww_dir: str = "/var/lib/jarvis/models/openwakeword"
    threshold: float = 0.5
    porcupine_access_key: str = Field(default="", repr=False)  # empty: secrets.picovoice_access_key
    porcupine_keyword: str = "jarvis"
    porcupine_sensitivity: float = 0.6


class STTConfig(BaseModel):
    """Vosk speech-to-text settings."""

    vosk_model_path: str = "/var/lib/jarvis/models/vosk-model-small-fr-0.22"
    listen_timeout_s: float = 6.0
    max_utterance_s: float = 6.0


class SpeakerConfig(BaseModel):
    """Speaker verification (ECAPA embeddings) settings."""

    enabled: bool = False
    threshold: float = 0.35
    model_dir: str = "/var/lib/jarvis/models/spkrec-ecapa-voxceleb"


class TTSConfig(BaseModel):
    """Piper text-to-speech settings."""

    piper_model: str = "/var/lib/jarvis/models/piper/fr_FR-siwis-medium.onnx"
    cache_dir: str = "/var/lib/jarvis/tts-cache"


class CommandsConfig(BaseModel):
    """Voice command phrases (in French, the spoken language) per intent.

    Each command is spoken after the "Jarvis" wake word: "Jarvis… ouvre la porte du garage".
    With ``strict`` (default) only these exact phrases are accepted (no keyword guessing).
    """

    open_phrases: list[str] = Field(default_factory=lambda: ["ouvre la porte du garage"])
    close_phrases: list[str] = Field(default_factory=lambda: ["ferme la porte du garage"])
    cancel_phrases: list[str] = Field(default_factory=list)
    strict: bool = True


class OutputConfig(BaseModel):
    """A digital output (relay or LED) and its driver backend."""

    backend: Literal["mock", "gpiod", "serial_lcus", "hid_dcttech"] = "mock"
    device: str | None = None  # /dev/gpiochip0, /dev/ttyUSB0...
    channel: int = 1  # GPIO line or relay number (1..n)
    active_low: bool = False


class InputConfig(BaseModel):
    """A digital input (door sensor) and its driver backend."""

    backend: Literal["none", "gpiod", "serial_cts"] = "none"
    device: str | None = None
    line: int = 0
    # True: closed (active) contact == door closed (reed switch at the closed end of travel).
    active_means_closed: bool = True


class HardwareConfig(BaseModel):
    """Garage relay, status LEDs and door sensor wiring, plus relay pulse timing."""

    garage: OutputConfig = Field(default_factory=OutputConfig)
    led_green: OutputConfig = Field(default_factory=lambda: OutputConfig(channel=2))
    led_red: OutputConfig = Field(default_factory=lambda: OutputConfig(channel=3))
    door_sensor: InputConfig = Field(default_factory=InputConfig)
    pulse_ms: int = 500
    cooldown_s: float = 5.0


class StorageConfig(BaseModel):
    """Data directory layout and GDPR retention periods."""

    data_dir: str = "/var/lib/jarvis"
    unknown_retention_days: int = 30
    event_retention_days: int = 180
    sighting_retention_days: int = 90
    plate_read_retention_days: int = 90

    @property
    def db_path(self) -> Path:
        """Path of the SQLite database."""
        return Path(self.data_dir) / "jarvis.db"

    @property
    def faces_dir(self) -> Path:
        """Directory holding enrolled face photos."""
        return Path(self.data_dir) / "faces"

    @property
    def unknown_dir(self) -> Path:
        """Directory holding unknown face snapshots."""
        return Path(self.data_dir) / "unknown"

    @property
    def voice_dir(self) -> Path:
        """Directory holding voice enrollment recordings."""
        return Path(self.data_dir) / "voice"

    @property
    def sightings_dir(self) -> Path:
        """Directory holding sighting snapshots."""
        return Path(self.data_dir) / "sightings"

    @property
    def simulation_dir(self) -> Path:
        """Directory holding the uploaded simulation media (videos, photos, recordings)."""
        return Path(self.data_dir) / "simulation"

    @property
    def plates_dir(self) -> Path:
        """Directory holding the vehicle snapshots of the plate reads."""
        return Path(self.data_dir) / "plates"


class PlatesConfig(BaseModel):
    """License plate recognition (ANPR) and vehicle-driven garage automation.

    Vehicles (COCO car, motorcycle, bus, truck) are tracked by the person detector; each
    vehicle is read by fast-alpr (plate detector + international OCR, ONNX, offline). A plate
    is confirmed after ``votes_required`` identical reads; its direction comes from the growth
    (approaching) or shrinkage (leaving) of the vehicle box. A known, enabled plate whose
    vehicle approaches opens the garage; with ``auto_close``, the garage closes once the known
    vehicle has left and the scene has stayed clear for ``close_delay_s``. Both automations
    require the door sensor (the single-button motor toggles: without the state, an "open"
    pulse could close the door on the car).
    """

    enabled: bool = False
    detector_model: str = "yolo-v9-t-384-license-plate-end2end"
    ocr_model: str = "cct-xs-v2-global-model"
    vehicle_classes: list[int] = Field(default_factory=lambda: [2, 3, 5, 7])  # car, motorcycle, bus, truck
    min_vehicle_px: int = 120          # minimum vehicle box width to attempt a read
    read_interval_s: float = 0.5       # per vehicle
    min_confidence: float = 0.80       # mean OCR character confidence
    votes_required: int = 2            # identical reads needed to confirm a plate
    motion_window_s: float = 2.0       # history used to estimate the direction
    approach_growth: float = 1.15      # box area ratio (newest / oldest) meaning "approaching"
    leave_shrink: float = 0.85         # box area ratio meaning "leaving"
    auto_open: bool = True
    auto_close: bool = False           # opt-in: an unattended closing door is a hazard
    close_delay_s: float = 20.0        # scene clear for this long before closing
    close_give_up_s: float = 300.0     # abandon a pending close if the scene never clears
    unknown_notify: bool = True
    save_images: bool = True


class SimulationConfig(BaseModel):
    """Test without a real camera or microphone (Settings > Simulation).

    The camera simulation plays an uploaded video or photo instead of the RTSP stream (until it
    is stopped or the core restarts); the voice simulation turns a typed command into speech
    (Piper) and runs the real speech recognition on it, or uses an uploaded recording. Garage
    pulses caused by a simulation are logged as ``garage_pulse_simulated`` and do NOT drive the
    relay unless ``drive_relay`` is set.
    """

    loop: bool = True                  # restart the simulated video at its end
    drive_relay: bool = False          # DANGER: let simulated events pulse the real relay
    require_window: bool = True        # apply the recognition window rule to simulated voice commands
    max_upload_mb: int = 100           # simulation videos / recordings (nginx allows the same)


class ApiConfig(BaseModel):
    """Web API server and session settings."""

    host: str = "127.0.0.1"
    port: int = 8000
    # Addresses whose X-Forwarded-* headers uvicorn trusts: the local nginx. In Docker, nginx runs
    # in another container: "*" is acceptable there because the API port is not published.
    forwarded_allow_ips: str = "127.0.0.1"
    session_hours: float = 12.0            # absolute maximum session lifetime
    idle_timeout_minutes: int = 30         # automatic logout after inactivity
    cookie_secure: bool = True
    max_upload_mb: int = 15


class NotificationsConfig(BaseModel):
    """Outgoing webhook notification settings."""

    # JSON POST to a local webhook: Home Assistant (/api/webhook/<id>), Node-RED, ntfy...
    webhook_url: str | None = None
    events: list[str] = Field(default_factory=lambda: [
        "face_unknown", "watchlist_seen", "access_denied_schedule", "voice_denied", "garage_pulse"])
    timeout_s: float = 5.0


class LoggingConfig(BaseModel):
    """Log file, rotation and remote syslog settings."""

    # Log file shared by the core and API processes; rotation is performed by the core.
    file_enabled: bool = True
    file_path: str = "/var/log/jarvis/jarvis.log"
    rotate_mode: Literal["size", "daily"] = "size"
    rotate_max_mb: int = 10
    rotate_keep: int = 10
    rotate_compress: bool = True
    # Export to an external syslog server (rsyslog, syslog-ng, Graylog...).
    syslog_enabled: bool = False
    syslog_host: str = ""
    syslog_port: int = 514
    syslog_protocol: Literal["udp", "tcp"] = "udp"
    syslog_facility: Literal["user", "daemon", "local0", "local1", "local2", "local3", "local4", "local5",
                             "local6", "local7"] = "local0"


class MonitoringConfig(BaseModel):
    """Prometheus metrics endpoint and monitoring agent settings."""

    # Prometheus /metrics endpoint of the API (Bearer token required except from 127.0.0.1).
    metrics_enabled: bool = False
    metrics_token: str = ""
    # System agents managed by jarvis-monitoring-apply (.path unit, runs as root):
    node_exporter_enabled: bool = False     # system metrics (CPU, RAM, disk, network), port 9100
    log_shipper: Literal["none", "promtail", "alloy"] = "none"   # ships logs to Loki
    loki_url: str = ""                      # e.g. http://grafana.lan:3100/loki/api/v1/push
    state_dir: str = "/var/lib/jarvis/monitoring"


class RecordingConfig(BaseModel):
    """Event-triggered video clips (pre-roll + post-roll), stored with a FIFO size/age rotation."""

    enabled: bool = True
    # Events that start or extend a clip: person (any person tracked), known, unknown, watchlist.
    triggers: list[Literal["person", "known", "unknown", "watchlist", "vehicle"]] = Field(
        default_factory=lambda: ["known", "unknown", "watchlist"])
    pre_seconds: float = 5.0          # footage kept in memory before the trigger
    post_seconds: float = 10.0        # recording continues this long after the last trigger
    max_clip_seconds: float = 120.0   # a clip never exceeds this length (a new one starts)
    fps: float = 8.0
    width: int = 1280                 # frames wider than this are down-scaled
    jpeg_quality: int = 80            # in-memory pre-roll quality
    crf: int = 28                     # H.264 quality (lower = better, larger files)
    max_total_gb: float = 20.0        # FIFO: oldest clips are deleted beyond this size
    max_age_days: int = 14            # FIFO: clips older than this are deleted


class TimelapseConfig(BaseModel):
    """Periodic snapshots used by the time-lapse viewer."""

    enabled: bool = True
    interval_s: float = 10.0
    width: int = 640
    max_total_gb: float = 5.0
    max_age_days: int = 30


class MqttConfig(BaseModel):
    """MQTT bridge to smart-home controllers (Home Assistant, openHAB, Domoticz, Jeedom, Node-RED)."""

    enabled: bool = False
    host: str = "localhost"
    port: int = 1883                      # 8883 with TLS
    tls: bool = False
    ca_file: str = ""                     # CA bundle of the broker (empty: system store)
    tls_insecure: bool = False            # accept a self-signed broker certificate (lab only)
    client_id: str = ""                   # empty: jarvis_<hostname>
    base_topic: str = "jarvis"
    qos: int = 1
    ha_discovery: bool = True             # publish Home Assistant MQTT discovery configs
    discovery_prefix: str = "homeassistant"
    state_interval_s: float = 10.0
    # Remote commands (garage pulse, PTZ home, say): off by default, and every payload must carry
    # secrets.mqtt_command_token.
    allow_commands: bool = False


class PerformanceConfig(BaseModel):
    """Automatic performance tuning from the hardware detected at start-up."""

    # auto: pick the inference devices, providers, decoding and rates that suit the hardware, for every
    # setting the operator did not set explicitly; manual: use the configuration as is.
    mode: Literal["auto", "manual"] = "auto"


class SecretsConfig(BaseModel):
    """Every credential of the system, in one place.

    Values can come from this section of ``config.yaml`` (possibly as ``${VAR}`` references), from
    ``JARVIS__SECRETS__<NAME>`` environment variables, or from a write-only parameter set in the
    web UI / shell. They are excluded from ``repr`` so they never end up in logs by accident.
    """

    camera_username: str = Field(default="", repr=False)
    camera_password: str = Field(default="", repr=False)
    picovoice_access_key: str = Field(default="", repr=False)
    metrics_token: str = Field(default="", repr=False)      # generated on first use when empty
    webhook_token: str = Field(default="", repr=False)      # sent as "Authorization: Bearer <token>"
    loki_username: str = Field(default="", repr=False)
    loki_password: str = Field(default="", repr=False)
    mqtt_username: str = Field(default="", repr=False)
    mqtt_password: str = Field(default="", repr=False)
    mqtt_command_token: str = Field(default="", repr=False)  # required in MQTT command payloads


class UIConfig(BaseModel):
    """Web UI settings."""

    # Default web UI language (can be changed from the Settings page).
    language: Literal["en-US", "fr-FR", "es-ES", "nl-NL", "de-DE", "it-IT", "ru-RU", "zh-CN", "id-ID", "ko-KR",
                      "ja-JP", "th-TH"] = "en-US"


class ConsoleConfig(BaseModel):
    """Local console (tty1) settings."""

    # Local console (tty1): require a web administrator account before showing the menu.
    require_login: bool = True


class ControlConfig(BaseModel):
    """Core control socket settings."""

    socket_path: str = "/run/jarvis/core.sock"


class Settings(BaseModel):
    """Root of the Jarvis configuration; one attribute per configuration section."""

    log_level: str = "INFO"
    camera: CameraConfig = Field(default_factory=CameraConfig)
    ptz: PTZConfig = Field(default_factory=PTZConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    faces: FaceConfig = Field(default_factory=FaceConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    decision: DecisionConfig = Field(default_factory=DecisionConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    wakeword: WakeWordConfig = Field(default_factory=WakeWordConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    speaker: SpeakerConfig = Field(default_factory=SpeakerConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    commands: CommandsConfig = Field(default_factory=CommandsConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    plates: PlatesConfig = Field(default_factory=PlatesConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    timelapse: TimelapseConfig = Field(default_factory=TimelapseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)


def expand_env(value):
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` references in strings.

    Dicts and lists are walked recursively; other values are returned unchanged.

    Args:
        value: A string, dict, list or scalar parsed from YAML.

    Returns:
        The same structure with environment references substituted.

    Raises:
        ValueError: If a referenced variable is unset and has no default.
    """
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ValueError(f"Missing environment variable: {name}")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def resolve_config_path(path: str | os.PathLike | None = None) -> Path | None:
    """Locate the configuration file.

    Resolution order: explicit ``path``, then ``$JARVIS_CONFIG``, then the first
    existing entry of :data:`DEFAULT_CONFIG_PATHS`.

    Args:
        path: Explicit configuration path, if any.

    Returns:
        The configuration path, or ``None`` if none was found.
    """
    if path:
        return Path(path)
    if env := os.environ.get("JARVIS_CONFIG"):
        return Path(env)
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return None


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """Load, expand and validate the configuration.

    Args:
        path: Explicit configuration path (see :func:`resolve_config_path`).

    Returns:
        Validated settings; defaults only if no configuration file was found.

    Raises:
        ValueError: If an environment reference cannot be resolved.
        pydantic.ValidationError: If the configuration is invalid.
    """
    VALUE_SOURCES.clear()
    resolved = resolve_config_path(path)
    raw = {}
    if resolved is not None:
        raw = expand_env(yaml.safe_load(resolved.read_text(encoding="utf-8")) or {})
        for key, value in _flatten(raw):
            if value not in ("", None):
                VALUE_SOURCES[key] = "file"
    apply_env_overrides(raw, os.environ)
    return Settings.model_validate(raw)


def _flatten(tree: dict, prefix: str = ""):
    """Yield (dotted key, value) for every leaf of a nested mapping."""
    for k, v in tree.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            yield from _flatten(v, f"{key}.")
        else:
            yield key, v


def apply_env_overrides(raw: dict, environ) -> list[str]:
    """Apply ``JARVIS__SECTION__KEY=value`` environment variables onto the raw configuration tree.

    Values are parsed as YAML scalars (``true``, ``0.5``, ``[a, b]``…) so that types match the model;
    secrets are always kept as strings.

    Args:
        raw: Raw configuration mapping, modified in place.
        environ: Environment mapping (usually ``os.environ``).

    Returns:
        The dotted keys that were overridden.
    """
    applied = []
    for name, value in environ.items():
        if not name.startswith(ENV_PREFIX) or len(name) <= len(ENV_PREFIX):
            continue
        parts = [p.lower() for p in name[len(ENV_PREFIX):].split("__") if p]
        node = raw
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                break
        else:
            parsed = value if parts[0] == "secrets" else _yaml_scalar(value)
            node[parts[-1]] = parsed
            key = ".".join(parts)
            VALUE_SOURCES[key] = "environment"
            applied.append(key)
    return applied


def _yaml_scalar(value: str):
    try:
        return yaml.safe_load(value) if value.strip() else value
    except yaml.YAMLError:
        return value


def resolve_credentials(settings: Settings) -> None:
    """Propagate ``secrets.*`` into the consumers that did not receive an explicit value.

    Called by the core and the API after the database overrides have been applied, so that the
    precedence parameter > environment > file holds for credentials too.
    """
    sec = settings.secrets
    if not settings.ptz.username:
        settings.ptz.username = sec.camera_username or "admin"
    if not settings.ptz.password:
        settings.ptz.password = sec.camera_password
    if not settings.wakeword.porcupine_access_key:
        settings.wakeword.porcupine_access_key = sec.picovoice_access_key


def rtsp_url_with_credentials(settings: Settings) -> str:
    """RTSP URL to open: ``camera.rtsp_url`` with the secrets injected when it carries no userinfo."""
    from urllib.parse import quote, urlsplit, urlunsplit

    url = settings.camera.rtsp_url
    parts = urlsplit(url)
    sec = settings.secrets
    if "@" in parts.netloc or not (sec.camera_username or sec.camera_password):
        return url
    userinfo = f"{quote(sec.camera_username, safe='')}:{quote(sec.camera_password, safe='')}@"
    return urlunsplit(parts._replace(netloc=userinfo + parts.netloc))
