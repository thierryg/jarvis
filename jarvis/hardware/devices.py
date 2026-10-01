# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/hardware/devices.py
# Purpose : Relay outputs (garage, LEDs), door sensor input and hardware facade
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Outputs (garage relay, LEDs) and door sensor input.

The ThinkCentre M73 has no GPIO, so relays are driven over USB. Available
output backends:

- ``serial_lcus``: CH340 USB relay boards, "LCUS-1/2/4" type (frame ``A0 <n> <state> <sum>``).
- ``hid_dcttech``: USB HID relay boards (VID 16c0 / PID 05df, "USBRelay2/4/8").
- ``gpiod``: Linux GPIO via libgpiod v2 (Raspberry Pi, FT232H/MCP2221 boards...).
- ``mock``: log only (development).

Door position sensor (strongly recommended, see docs/ARCHITECTURE.md section 6):

- ``serial_cts``: reed switch wired between the CTS and GND pins of a USB-serial adapter.
- ``gpiod``: GPIO input line.

Any backend that fails to initialize falls back to the mock output / no
sensor, so the rest of the system keeps running.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Literal, Protocol

from jarvis.config.settings import HardwareConfig, InputConfig, OutputConfig

log = logging.getLogger(__name__)

DoorState = Literal["open", "closed"]


class DigitalOutput(Protocol):
    """A binary output (relay or LED)."""

    def set(self, on: bool) -> None:
        """Drive the output on or off (logical level; polarity is handled by the backend)."""
        ...

    def close(self) -> None:
        """Release the underlying device."""
        ...


class MockOutput:
    """Output that only logs and records its state changes (development and tests).

    Attributes:
        name: Output name used in logs.
        state: Last value set.
        history: Every value set, in order.
    """

    def __init__(self, name: str):
        """Create a mock output.

        Args:
            name: Output name used in logs.
        """
        self.name = name
        self.state = False
        self.history: list[bool] = []

    def set(self, on: bool) -> None:
        """Record and log the new state."""
        self.state = on
        self.history.append(on)
        log.info("[mock] %s -> %s", self.name, "ON" if on else "OFF")

    def close(self) -> None:
        """No-op."""
        pass


def lcus_frame(channel: int, on: bool) -> bytes:
    """Build a CH340 "LCUS" relay command frame: ``A0 <channel> <state> <checksum>``.

    Args:
        channel: Relay number (1-based).
        on: Relay state.

    Returns:
        The 4-byte frame; checksum is the low byte of the sum of the first three.
    """
    state = 0x01 if on else 0x00
    return bytes([0xA0, channel, state, (0xA0 + channel + state) & 0xFF])


class _SharedSerial:
    """Per-port serial handle shared by all relays of a multi-channel board.

    Attributes:
        lock: Serializes writes to the port.
        ser: pyserial ``Serial`` (9600 baud).
    """

    _instances: dict[str, _SharedSerial] = {}
    _guard = threading.Lock()

    def __init__(self, port: str):
        """Open the serial port.

        Args:
            port: Device path, e.g. ``/dev/ttyUSB0``.
        """
        import serial  # pyserial

        self.lock = threading.Lock()
        self.ser = serial.Serial(port, 9600, timeout=1)

    @classmethod
    def get(cls, port: str) -> _SharedSerial:
        """Return the shared handle for ``port``, opening it on first use."""
        with cls._guard:
            if port not in cls._instances:
                cls._instances[port] = cls(port)
            return cls._instances[port]


class SerialLcusOutput:
    """One channel of a CH340 "LCUS" serial relay board.

    Attributes:
        channel: Relay number on the board.
        port: Shared serial handle.
        active_low: Invert the logical state (for normally-closed wiring).
    """

    def __init__(self, cfg: OutputConfig):
        """Bind to the configured port (default ``/dev/ttyUSB0``) and channel.

        Args:
            cfg: Output configuration.
        """
        self.channel = cfg.channel
        self.port = _SharedSerial.get(cfg.device or "/dev/ttyUSB0")
        self.active_low = cfg.active_low

    def set(self, on: bool) -> None:
        """Send the relay command frame."""
        with self.port.lock:
            self.port.ser.write(lcus_frame(self.channel, on != self.active_low))
            self.port.ser.flush()

    def close(self) -> None:
        """No-op: the port is shared and stays open for the process lifetime."""
        pass


def dcttech_report(channel: int, on: bool) -> list[int]:
    """Build the HID feature report for "USBRelay" boards.

    Args:
        channel: Relay number (1-based).
        on: Relay state.

    Returns:
        9 bytes: report ID 0, then ``0xFF`` (on) / ``0xFD`` (off), channel, padding.
    """
    return [0x00, 0xFF if on else 0xFD, channel, 0, 0, 0, 0, 0, 0]


class HidDcttechOutput:
    """One channel of a USB HID "USBRelay" board (dcttech).

    The HID device handle is opened once and shared by all channels.

    Attributes:
        VID: USB vendor ID.
        PID: USB product ID.
        channel: Relay number on the board.
        active_low: Invert the logical state.
    """

    VID, PID = 0x16C0, 0x05DF
    _dev = None
    _guard = threading.Lock()

    def __init__(self, cfg: OutputConfig):
        """Open the HID device on first use (by explicit hidraw path, else by VID/PID).

        Args:
            cfg: Output configuration.
        """
        import hid  # pip package "hidapi"

        self.channel = cfg.channel
        self.active_low = cfg.active_low
        with HidDcttechOutput._guard:
            if HidDcttechOutput._dev is None:
                dev = hid.device()
                if cfg.device:  # explicit hidraw path (several boards)
                    dev.open_path(cfg.device.encode())
                else:
                    dev.open(self.VID, self.PID)
                HidDcttechOutput._dev = dev

    def set(self, on: bool) -> None:
        """Send the feature report for this channel."""
        with HidDcttechOutput._guard:
            HidDcttechOutput._dev.send_feature_report(dcttech_report(self.channel, on != self.active_low))

    def close(self) -> None:
        """No-op: the device handle is shared."""
        pass


class GpiodOutput:
    """GPIO output line via libgpiod v2 (polarity handled by the kernel via ``active_low``).

    Attributes:
        line: GPIO line offset.
        request: libgpiod line request.
    """

    def __init__(self, cfg: OutputConfig):
        """Request the line as an output, initially inactive.

        Args:
            cfg: Output configuration (``device`` defaults to ``/dev/gpiochip0``).
        """
        import gpiod
        from gpiod.line import Direction, Value

        self._Value = Value
        self.line = cfg.channel
        self.request = gpiod.request_lines(
            cfg.device or "/dev/gpiochip0",
            consumer="jarvis",
            config={self.line: gpiod.LineSettings(direction=Direction.OUTPUT, active_low=cfg.active_low,
                                                  output_value=Value.INACTIVE)},
        )

    def set(self, on: bool) -> None:
        """Set the line active or inactive."""
        self.request.set_value(self.line, self._Value.ACTIVE if on else self._Value.INACTIVE)

    def close(self) -> None:
        """Release the line request."""
        self.request.release()


def build_output(name: str, cfg: OutputConfig) -> DigitalOutput:
    """Create the configured output backend, falling back to ``MockOutput`` on error.

    Args:
        name: Output name (for logs and the mock).
        cfg: Output configuration.

    Returns:
        A ready-to-use output.
    """
    try:
        if cfg.backend == "serial_lcus":
            return SerialLcusOutput(cfg)
        if cfg.backend == "hid_dcttech":
            return HidDcttechOutput(cfg)
        if cfg.backend == "gpiod":
            return GpiodOutput(cfg)
    except Exception:
        log.exception("Output %s (%s) unavailable, falling back to mock", name, cfg.backend)
    return MockOutput(name)


# --- Door sensor input -----------------------------------------------------------------

class DoorSensor(Protocol):
    """Door position sensor."""

    def state(self) -> DoorState | None:
        """Return ``"open"``, ``"closed"``, or ``None`` if unknown."""
        ...


class NoSensor:
    """Placeholder when no door sensor is installed: state is always unknown."""

    def state(self) -> DoorState | None:
        """Always ``None``."""
        return None


class SerialCtsSensor:
    """Reed switch read through the CTS modem line of a USB-serial adapter.

    Attributes:
        ser: pyserial ``Serial``.
        active_means_closed: True if an asserted CTS means the door is closed.
    """

    def __init__(self, cfg: InputConfig):
        """Open the serial port (default ``/dev/ttyUSB1``).

        Args:
            cfg: Input configuration.
        """
        import serial

        self.ser = serial.Serial(cfg.device or "/dev/ttyUSB1")
        self.active_means_closed = cfg.active_means_closed

    def state(self) -> DoorState | None:
        """Read CTS and map it to a door state."""
        active = bool(self.ser.cts)
        return "closed" if active == self.active_means_closed else "open"


class GpiodSensor:
    """Door contact on a GPIO input with pull-up (contact to ground reads as active).

    Attributes:
        line: GPIO line offset.
        active_means_closed: True if an active line means the door is closed.
        request: libgpiod line request.
    """

    def __init__(self, cfg: InputConfig):
        """Request the line as an input with pull-up bias, active-low.

        Args:
            cfg: Input configuration (``device`` defaults to ``/dev/gpiochip0``).
        """
        import gpiod
        from gpiod.line import Bias, Direction, Value

        self._active = Value.ACTIVE
        self.line = cfg.line
        self.active_means_closed = cfg.active_means_closed
        self.request = gpiod.request_lines(
            cfg.device or "/dev/gpiochip0", consumer="jarvis-door",
            config={self.line: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP,
                                                  active_low=True)},
        )

    def state(self) -> DoorState | None:
        """Read the line and map it to a door state."""
        active = self.request.get_value(self.line) == self._active
        return "closed" if active == self.active_means_closed else "open"


def build_sensor(cfg: InputConfig) -> DoorSensor:
    """Create the configured door sensor, falling back to ``NoSensor`` on error.

    Args:
        cfg: Input configuration.

    Returns:
        A door sensor.
    """
    try:
        if cfg.backend == "serial_cts":
            return SerialCtsSensor(cfg)
        if cfg.backend == "gpiod":
            return GpiodSensor(cfg)
    except Exception:
        log.exception("Door sensor (%s) unavailable", cfg.backend)
    return NoSensor()


# --- Facade -----------------------------------------------------------------------------

class Hardware:
    """Single, thread-safe access point to the hardware (only the core process instantiates it).

    Attributes:
        cfg: Hardware configuration (pulse length, cooldown).
        garage: Garage opener relay.
        green: Green LED.
        red: Red LED.
        door: Door position sensor.
    """

    def __init__(self, cfg: HardwareConfig, garage: DigitalOutput, green: DigitalOutput,
                 red: DigitalOutput, door: DoorSensor, clock=time.monotonic, sleep=time.sleep):
        """Assemble the facade from already-built devices.

        Args:
            cfg: Hardware configuration.
            garage: Garage relay output.
            green: Green LED output.
            red: Red LED output.
            door: Door sensor.
            clock: Monotonic time source (injectable for tests).
            sleep: Sleep function (injectable for tests).
        """
        self.cfg = cfg
        self.garage, self.green, self.red, self.door = garage, green, red, door
        self._clock, self._sleep = clock, sleep
        self._pulse_lock = threading.Lock()
        self._last_pulse = float("-inf")
        self._led_timer: threading.Timer | None = None
        self._led_lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg: HardwareConfig) -> Hardware:
        """Build all devices from the configuration.

        Args:
            cfg: Hardware configuration.

        Returns:
            A ``Hardware`` instance.
        """
        return cls(cfg, build_output("garage", cfg.garage), build_output("led_green", cfg.led_green),
                   build_output("led_red", cfg.led_red), build_sensor(cfg.door_sensor))

    def pulse_garage(self) -> bool:
        """Simulate a press on the Novomatic opener's push button.

        The relay is closed for ``pulse_ms`` then always released, even on error.

        Returns:
            True if the pulse was sent, False if rejected by the ``cooldown_s``
            guard (debounce against repeated triggers).
        """
        with self._pulse_lock:
            now = self._clock()
            if now - self._last_pulse < self.cfg.cooldown_s:
                log.warning("Garage pulse ignored (cooldown %.1fs)", self.cfg.cooldown_s)
                return False
            self._last_pulse = now
            try:
                self.garage.set(True)
                self._sleep(self.cfg.pulse_ms / 1000)
            finally:
                self.garage.set(False)
            log.info("Garage pulse sent (%d ms)", self.cfg.pulse_ms)
            return True

    def door_state(self) -> DoorState | None:
        """Read the door sensor.

        Returns:
            ``"open"``, ``"closed"``, or ``None`` if unknown or on read error.
        """
        try:
            return self.door.state()
        except Exception:
            log.exception("Unable to read door sensor")
            return None

    def indicate(self, color: Literal["green", "red"], seconds: float) -> None:
        """Light one LED for ``seconds`` (the other one is turned off).

        A new call cancels the pending turn-off timer of the previous one.

        Args:
            color: ``"green"`` or ``"red"``.
            seconds: Duration before both LEDs are turned off.
        """
        with self._led_lock:
            if self._led_timer:
                self._led_timer.cancel()
            on, off = (self.green, self.red) if color == "green" else (self.red, self.green)
            off.set(False)
            on.set(True)
            self._led_timer = threading.Timer(seconds, self._leds_off)
            self._led_timer.daemon = True
            self._led_timer.start()

    def _leds_off(self) -> None:
        """Turn both LEDs off (timer callback)."""
        with self._led_lock:
            self.green.set(False)
            self.red.set(False)

    def close(self) -> None:
        """Turn every output off and release the devices."""
        if self._led_timer:
            self._led_timer.cancel()
        for out in (self.garage, self.green, self.red):
            try:
                out.set(False)
                out.close()
            except Exception:
                log.exception("Failed to close output")
