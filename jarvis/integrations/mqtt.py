# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/integrations/mqtt.py
# Purpose : MQTT bridge for smart-home platforms (Home Assistant discovery, openHAB, Domoticz, Jeedom, Node-RED)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""MQTT bridge: the common integration layer for smart-home controllers.

Topics (``<base>`` = ``mqtt.base_topic``, ``jarvis`` by default):

=============================  ========  ===================================================
``<base>/status``              retained  ``online`` / ``offline`` (Last Will and Testament)
``<base>/state``               retained  JSON: camera, fps, tracks, door, authorized, last_person
``<base>/state/<field>``       retained  one plain value per field (for simple controllers)
``<base>/event/<type>``        not ret.  JSON event: face_recognized, face_unknown, watchlist_seen,
                                         garage_pulse, voice_denied, access_denied_schedule…
``<base>/cmd/<command>``       inbound   garage_pulse, ptz_home, say — only with
                                         ``mqtt.allow_commands`` and the command token
=============================  ========  ===================================================

Home Assistant **MQTT discovery** (``<prefix>/<component>/<node>/<object>/config``) creates the device
and its entities automatically: camera connectivity, analysis rate, people tracked, door, last
recognized person, an ``event`` entity for face events and, when commands are enabled, the
buttons. openHAB (MQTT binding), Domoticz (MQTT Auto Discovery), Jeedom (jMQTT) and Node-RED consume
the same topics.

Security: commands are disabled by default; when enabled, every command payload must carry the
``secrets.mqtt_command_token`` (``{"token": "..."}``), on top of the broker authentication, TLS and
ACLs. A garage command goes through the same decision engine (cooldown, audit) as the web button.
"""

from __future__ import annotations

import json
import logging
import secrets as _secrets
import socket
import threading
import time
from typing import Callable

from jarvis import __version__
from jarvis.config.settings import MqttConfig, SecretsConfig

log = logging.getLogger(__name__)

STATE_FIELDS = ("camera_connected", "vision_fps", "tracks", "door", "authorized", "last_person")


def node_id(cfg: MqttConfig) -> str:
    """Stable identifier of this Jarvis instance in topics and discovery (host name based)."""
    return (cfg.client_id or f"jarvis_{socket.gethostname()}").replace("-", "_").replace(".", "_").lower()


def discovery_messages(cfg: MqttConfig) -> list[tuple[str, dict]]:
    """Home Assistant discovery payloads ``(topic, config)`` for every Jarvis entity (pure function)."""
    base, nid = cfg.base_topic, node_id(cfg)
    device = {"identifiers": [nid], "name": "Jarvis", "manufacturer": "Jarvis", "model": "Smart gatekeeper",
              "sw_version": __version__}
    common = {"availability_topic": f"{base}/status", "device": device}

    def entity(component: str, object_id: str, name: str, **extra) -> tuple[str, dict]:
        cfg_payload = {"name": name, "unique_id": f"{nid}_{object_id}", "object_id": f"{nid}_{object_id}",
                       **common, **extra}
        return f"{cfg.discovery_prefix}/{component}/{nid}/{object_id}/config", cfg_payload

    state = f"{base}/state"
    msgs = [
        entity("binary_sensor", "camera", "Camera", state_topic=state, value_template="{{ value_json.camera_connected }}",
               payload_on=True, payload_off=False, device_class="connectivity"),
        entity("sensor", "vision_fps", "Analysis rate", state_topic=state, value_template="{{ value_json.vision_fps }}",
               unit_of_measurement="fps", state_class="measurement", icon="mdi:speedometer"),
        entity("sensor", "tracks", "People tracked", state_topic=state, value_template="{{ value_json.tracks }}",
               state_class="measurement", icon="mdi:account-multiple"),
        entity("sensor", "authorized", "Authorized people", state_topic=state,
               value_template="{{ value_json.authorized }}", icon="mdi:shield-account"),
        entity("sensor", "last_person", "Last recognized person", state_topic=state,
               value_template="{{ value_json.last_person }}", icon="mdi:face-recognition"),
        entity("sensor", "door", "Garage door", state_topic=state, value_template="{{ value_json.door }}",
               icon="mdi:garage"),
        entity("event", "face", "Face identification", state_topic=f"{base}/event/face",
               event_types=["face_recognized", "face_unknown", "watchlist_seen"], icon="mdi:face-recognition"),
        entity("event", "access", "Access", state_topic=f"{base}/event/access",
               event_types=["garage_pulse", "voice_denied", "access_denied_schedule"], icon="mdi:door"),
    ]
    if cfg.allow_commands:
        # The token is embedded in the payload template stored by Home Assistant: HA sends it back.
        msgs += [
            entity("button", "garage_pulse", "Garage door pulse", command_topic=f"{base}/cmd/garage_pulse",
                   payload_press='{"token": "{{TOKEN}}"}', icon="mdi:garage-open"),
            entity("button", "ptz_home", "Camera home position", command_topic=f"{base}/cmd/ptz_home",
                   payload_press='{"token": "{{TOKEN}}"}', icon="mdi:cctv"),
        ]
    return msgs


# Event types -> event entity (face / access) for Home Assistant.
EVENT_GROUPS = {"face_recognized": "face", "face_unknown": "face", "watchlist_seen": "face",
                "garage_pulse": "access", "voice_denied": "access", "access_denied_schedule": "access"}


class MqttBridge(threading.Thread):
    """Keeps the MQTT connection, publishes states and events, dispatches authorized commands."""

    def __init__(self, cfg: MqttConfig, secrets: SecretsConfig, status: Callable[[], dict],
                 commands: dict[str, Callable[[dict], None]]):
        """Create the bridge (not started).

        Args:
            cfg: MQTT settings.
            secrets: Credentials (``mqtt_username``, ``mqtt_password``, ``mqtt_command_token``).
            status: Callable returning the core status (same shape as the ``status`` control command).
            commands: Command handlers by name (``garage_pulse``, ``ptz_home``, ``say``).
        """
        super().__init__(name="mqtt", daemon=True)
        self.cfg, self.secrets, self.status, self.commands = cfg, secrets, status, commands
        self._halt = threading.Event()
        self._connected = threading.Event()
        self.last_person = ""
        self.client = None

    # --- public ---------------------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def publish_event(self, event_type: str, **payload) -> None:
        """Publish a notification (same events as the webhook); called by the decision engine."""
        if event_type == "face_recognized" and payload.get("name"):
            self.last_person = payload["name"]
        msg = {"event_type": event_type, "ts": time.time(), **payload}
        self._publish(f"{self.cfg.base_topic}/event/{event_type}", msg, retain=False)
        group = EVENT_GROUPS.get(event_type)
        if group:  # Home Assistant event entities
            self._publish(f"{self.cfg.base_topic}/event/{group}", msg, retain=False)

    def stop(self) -> None:
        self._halt.set()
        if self.client is not None:
            self._publish(f"{self.cfg.base_topic}/status", "offline", retain=True)
            self.client.disconnect()
            self.client.loop_stop()

    # --- thread ----------------------------------------------------------------------------------
    def run(self) -> None:
        import paho.mqtt.client as mqtt

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=node_id(self.cfg), protocol=mqtt.MQTTv311)
        if self.secrets.mqtt_username:
            c.username_pw_set(self.secrets.mqtt_username, self.secrets.mqtt_password or None)
        if self.cfg.tls:
            c.tls_set(ca_certs=self.cfg.ca_file or None)
            c.tls_insecure_set(self.cfg.tls_insecure)
        c.will_set(f"{self.cfg.base_topic}/status", "offline", qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = lambda *a, **k: (self._connected.clear(), log.warning("MQTT disconnected"))
        c.on_message = self._on_message
        c.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client = c
        while not self._halt.is_set():
            try:
                c.connect(self.cfg.host, self.cfg.port, keepalive=60)
                break
            except OSError as exc:
                log.warning("MQTT broker %s:%d unreachable (%s), retrying in 15 s", self.cfg.host, self.cfg.port, exc)
                self._halt.wait(15)
        c.loop_start()  # paho handles the reconnections from now on
        while not self._halt.wait(self.cfg.state_interval_s):
            if self.connected:
                self.publish_state()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.error("MQTT connection refused: %s", reason_code)
            return
        self._connected.set()
        log.info("MQTT connected to %s:%d", self.cfg.host, self.cfg.port)
        self._publish(f"{self.cfg.base_topic}/status", "online", retain=True)
        if self.cfg.ha_discovery:
            token = self.secrets.mqtt_command_token
            for topic, payload in discovery_messages(self.cfg):
                raw = json.dumps(payload).replace("{{TOKEN}}", token)
                client.publish(topic, raw, qos=1, retain=True)
        if self.cfg.allow_commands:
            client.subscribe(f"{self.cfg.base_topic}/cmd/#", qos=1)
        self.publish_state()

    def _on_message(self, client, userdata, msg):
        command = msg.topic.rsplit("/", 1)[-1]
        handler = self.commands.get(command)
        try:
            payload = json.loads(msg.payload or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            payload = {"text": msg.payload.decode(errors="replace")}
        token = self.secrets.mqtt_command_token
        if not (self.cfg.allow_commands and handler and token and
                _secrets.compare_digest(str(payload.get("token", "")), token)):
            log.warning("MQTT command %r rejected (commands disabled, unknown command or bad token)", command)
            self.publish_event("mqtt_command_rejected", command=command)
            return
        log.info("MQTT command %s accepted", command)
        handler(payload)

    def publish_state(self) -> None:
        """Publish the retained state as one JSON document and one topic per field."""
        st = self.status() or {}
        state = {"camera_connected": bool(st.get("camera_connected")), "vision_fps": st.get("vision_fps", 0),
                 "tracks": st.get("tracks", 0), "door": st.get("door") or "unknown",
                 "authorized": len(st.get("authorized_persons") or []), "last_person": self.last_person}
        self._publish(f"{self.cfg.base_topic}/state", state, retain=True)
        for k in STATE_FIELDS:
            self._publish(f"{self.cfg.base_topic}/state/{k}", state[k], retain=True)

    def _publish(self, topic: str, payload, retain: bool) -> None:
        if self.client is None:
            return
        raw = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        self.client.publish(topic, raw, qos=self.cfg.qos, retain=retain)
