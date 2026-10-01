# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_mqtt.py
# Purpose : MQTT bridge against a real broker (amqtt), Home Assistant discovery, command security
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""End-to-end MQTT tests: availability, discovery, retained state, events and token-protected commands."""

import asyncio
import json
import socket
import threading
import time

import pytest

from jarvis.config.settings import MqttConfig, SecretsConfig
from jarvis.integrations.mqtt import MqttBridge, discovery_messages

amqtt = pytest.importorskip("amqtt.broker")
mqtt = pytest.importorskip("paho.mqtt.client")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def broker():
    port = free_port()
    loop = asyncio.new_event_loop()
    # amqtt calls asyncio.get_event_loop() internally: make this loop current (an earlier
    # asyncio.run() in the same process leaves no current loop behind).
    asyncio.set_event_loop(loop)
    cfg = {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}}, "sys_interval": 0,
           "auth": {"allow-anonymous": True, "plugins": ["auth_anonymous"]}, "topic-check": {"enabled": False}}
    b = amqtt.Broker(cfg, loop=loop)
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(b.start(), loop).result(10)
    yield port
    asyncio.run_coroutine_threadsafe(b.shutdown(), loop).result(10)
    loop.call_soon_threadsafe(loop.stop)


class Collector:
    def __init__(self, port):
        self.msgs = []
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.c.on_message = lambda c, u, m: self.msgs.append((m.topic, m.payload.decode(), m.retain))
        self.c.connect("127.0.0.1", port)
        self.c.subscribe("#", qos=1)
        self.c.loop_start()

    def wait(self, pred, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            hit = [m for m in self.msgs if pred(m)]
            if hit:
                return hit
            time.sleep(0.05)
        raise AssertionError(f"not received; got {[m[0] for m in self.msgs][:30]}")


def test_discovery_payloads_are_consistent():
    cfg = MqttConfig(client_id="jarvis-test", allow_commands=True)
    msgs = dict(discovery_messages(cfg))
    assert "homeassistant/binary_sensor/jarvis_test/camera/config" in msgs
    btn = msgs["homeassistant/button/jarvis_test/garage_pulse/config"]
    assert btn["command_topic"] == "jarvis/cmd/garage_pulse" and "{{TOKEN}}" in btn["payload_press"]
    assert all(p["availability_topic"] == "jarvis/status" for p in msgs.values())
    assert not any("button" in t for t, _ in discovery_messages(MqttConfig(client_id="x")))  # no commands


def test_bridge_end_to_end(broker):
    col = Collector(broker)
    pulses = []
    cfg = MqttConfig(host="127.0.0.1", port=broker, client_id="jarvis-e2e", allow_commands=True, state_interval_s=0.5)
    sec = SecretsConfig(mqtt_command_token="tok-42")
    bridge = MqttBridge(cfg, sec, status=lambda: {"camera_connected": True, "vision_fps": 6.5, "tracks": 2,
                                                 "door": "closed", "authorized_persons": [1]},
                        commands={"garage_pulse": pulses.append})
    bridge.start()
    col.wait(lambda m: m[0] == "jarvis/status" and m[1] == "online")
    col.wait(lambda m: m[0] == "homeassistant/sensor/jarvis_e2e/vision_fps/config")
    btn = col.wait(lambda m: m[0].endswith("/garage_pulse/config"))[0]
    assert "tok-42" in btn[1]                                           # token substituted for Home Assistant
    state = col.wait(lambda m: m[0] == "jarvis/state")[0]
    assert json.loads(state[1])["vision_fps"] == 6.5
    col.wait(lambda m: m[0] == "jarvis/state/door" and m[1] == "closed")  # plain text for simple controllers
    bridge.publish_event("face_recognized", person_id=3, name="Alice", score=0.8)
    col.wait(lambda m: m[0] == "jarvis/event/face" and json.loads(m[1])["name"] == "Alice")
    col.c.publish("jarvis/cmd/garage_pulse", json.dumps({"token": "wrong"}), qos=1)
    col.wait(lambda m: m[0] == "jarvis/event/mqtt_command_rejected")
    assert pulses == []
    col.c.publish("jarvis/cmd/garage_pulse", json.dumps({"token": "tok-42"}), qos=1)
    end = time.time() + 5
    while not pulses and time.time() < end:
        time.sleep(0.05)
    assert len(pulses) == 1
    bridge.stop()
    col.wait(lambda m: m[0] == "jarvis/status" and m[1] == "offline")
    col.c.loop_stop()
