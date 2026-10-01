# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/vision/ptz.py
# Purpose : ONVIF PTZ driver and proportional target-tracking controller
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""PTZ control.

- ``OnvifPTZ`` sends ONVIF commands from its own asyncio event loop. Commands
  are coalesced: only the most recent pending command is sent, so a slow
  camera never blocks the vision loop and never accumulates a command backlog.
- ``PTZTracker`` is a proportional controller that keeps the target centered
  and zooms toward a desired apparent height, returning to a home preset after
  a period of inactivity.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass


from jarvis.config.settings import PTZConfig

log = logging.getLogger(__name__)

class PTZ:
    """PTZ interface. Speeds are normalized to [-1, 1]; all methods are non-blocking."""

    def move(self, pan: float, tilt: float, zoom: float) -> None:
        """Start a continuous move at the given normalized velocities."""
        ...

    def stop(self) -> None:
        """Stop pan, tilt and zoom."""
        ...

    def goto_preset(self, preset: str) -> None:
        """Move to a stored preset (ONVIF preset token)."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


class NullPTZ(PTZ):
    """No-op PTZ for fixed cameras or when PTZ control is disabled."""

    pass


class OnvifPTZ(PTZ):
    """ONVIF PTZ driver (onvif-zeep-async) running on a private asyncio loop thread.

    Connection failures are retried with exponential backoff (5 s up to 120 s).

    Attributes:
        cfg: PTZ configuration (host, credentials, profile index).
    """

    def __init__(self, cfg: PTZConfig):
        """Start the event loop thread and the command worker.

        Args:
            cfg: PTZ configuration.
        """
        self.cfg = cfg
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ptz-onvif", daemon=True)
        self._thread.start()
        self._wakeup: asyncio.Event | None = None
        # Single-slot mailbox: a new command overwrites an unsent one (coalescing).
        self._pending: tuple | None = None
        self._cam = self._ptz = self._token = None
        asyncio.run_coroutine_threadsafe(self._worker(), self._loop)

    async def _connect(self) -> None:
        """Connect to the camera and resolve the media profile token and PTZ service."""
        from onvif import ONVIFCamera  # onvif-zeep-async

        cam = ONVIFCamera(self.cfg.host, self.cfg.port, self.cfg.username, self.cfg.password)
        await cam.update_xaddrs()
        media = await cam.create_media_service()
        profiles = await media.GetProfiles()
        self._token = profiles[self.cfg.profile_index].token
        self._ptz = await cam.create_ptz_service()
        self._cam = cam
        log.info("ONVIF PTZ connected (profile %s)", self._token)

    async def _worker(self) -> None:
        """Connect (with backoff), then send the latest pending command on each wakeup."""
        self._wakeup = asyncio.Event()
        backoff = 5.0
        while True:
            if self._ptz is None:
                try:
                    await self._connect()
                    backoff = 5.0
                except Exception as exc:
                    log.warning("ONVIF connection failed (%s), retrying in %.0fs", exc, backoff)
                    # Drop stale commands: they are meaningless once we reconnect.
                    self._pending = None
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 120)
                    continue
            await self._wakeup.wait()
            self._wakeup.clear()
            cmd, self._pending = self._pending, None
            if cmd is None:
                continue
            try:
                await self._execute(*cmd)
            except Exception as exc:
                log.warning("PTZ command %s failed: %s", cmd[0], exc)
                if "connect" in str(exc).lower():
                    self._ptz = None  # force a reconnect on the next iteration

    async def _execute(self, kind: str, *args) -> None:
        """Send one command to the camera.

        Args:
            kind: ``"move"``, ``"stop"`` or ``"preset"``.
            *args: ``(pan, tilt, zoom)`` for move, ``(preset_token,)`` for preset.
        """
        token = self._token
        if kind == "move":
            pan, tilt, zoom = args
            await self._ptz.ContinuousMove({
                "ProfileToken": token,
                "Velocity": {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}},
            })
        elif kind == "stop":
            await self._ptz.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": True})
        elif kind == "preset":
            await self._ptz.GotoPreset({"ProfileToken": token, "PresetToken": args[0]})

    def _submit(self, *cmd) -> None:
        """Post a command to the loop thread, replacing any unsent one."""
        def _set():
            self._pending = cmd
            if self._wakeup:
                self._wakeup.set()
        self._loop.call_soon_threadsafe(_set)

    def move(self, pan: float, tilt: float, zoom: float) -> None:
        """Queue a continuous move (see ``PTZ.move``)."""
        self._submit("move", pan, tilt, zoom)

    def stop(self) -> None:
        """Queue a stop command."""
        self._submit("stop")

    def goto_preset(self, preset: str) -> None:
        """Queue a go-to-preset command."""
        self._submit("preset", preset)

    def close(self) -> None:
        """Close the ONVIF session (best effort, 3 s timeout) and stop the loop."""
        async def _close():
            if self._cam is not None:
                await self._cam.close()
        try:
            asyncio.run_coroutine_threadsafe(_close(), self._loop).result(timeout=3)
        except Exception as exc:  # best effort on shutdown: the camera may already be unreachable
            log.debug("ONVIF close failed: %s", exc)
        self._loop.call_soon_threadsafe(self._loop.stop)


def build_ptz(cfg: PTZConfig) -> PTZ:
    """Create the PTZ driver for the configuration.

    Args:
        cfg: PTZ configuration.

    Returns:
        ``OnvifPTZ`` if enabled, otherwise ``NullPTZ``.
    """
    return OnvifPTZ(cfg) if cfg.enabled else NullPTZ()


@dataclass(frozen=True)
class PTZCommand:
    """Normalized PTZ velocity command (values rounded to 0.01 by ``compute_command``)."""

    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0

    @property
    def is_zero(self) -> bool:
        """True if the command means "do not move"."""
        return self.pan == 0 and self.tilt == 0 and self.zoom == 0


def _clamp(v: float, limit: float) -> float:
    """Clamp ``v`` to ``[-limit, limit]``."""
    return max(-limit, min(limit, v))


def compute_command(box: tuple[float, float, float, float], frame_w: int, frame_h: int,
                    cfg: PTZConfig) -> PTZCommand:
    """Compute PTZ velocities to re-center a person (aiming at the upper body).

    Proportional control on normalized errors in [-1, 1]:

    - ``ex``: horizontal offset of the box center; ``ey``: vertical offset of a
      point 30% down from the box top (roughly the face).
    - Pan/tilt = ``gain * error`` clamped to ``max_speed``, zero inside ``deadzone``.
    - Zoom drives the box height ratio toward ``target_height_ratio``: zoom out
      at half gain when a large box touches the frame edge (to avoid losing
      the target); zoom in only when the target is roughly centered; zoom out
      is always allowed. Errors below 0.1 are ignored.

    Args:
        box: Person box ``(x1, y1, x2, y2)``.
        frame_w: Frame width.
        frame_h: Frame height.
        cfg: PTZ configuration (gains, deadzone, speed limit, zoom settings).

    Returns:
        The command, each axis rounded to two decimals.
    """
    x1, y1, x2, y2 = box
    ex = ((x1 + x2) / 2) / frame_w * 2 - 1              # -1 (left) .. 1 (right)
    ey = (y1 + (y2 - y1) * 0.3) / frame_h * 2 - 1        # aim ~ at the face
    pan = 0.0 if abs(ex) < cfg.deadzone else _clamp(cfg.pan_gain * ex, cfg.max_speed)
    # ONVIF: positive y points up, whereas the image y axis points down.
    tilt = 0.0 if abs(ey) < cfg.deadzone else _clamp(-cfg.tilt_gain * ey, cfg.max_speed)
    if cfg.invert_tilt:
        tilt = -tilt
    zoom = 0.0
    if cfg.zoom_enabled:
        ratio = (y2 - y1) / frame_h
        err = cfg.target_height_ratio - ratio
        touches_edge = x1 <= 2 or y1 <= 2 or x2 >= frame_w - 2 or y2 >= frame_h - 2
        if touches_edge and ratio > 0.3:
            zoom = -cfg.zoom_gain * 0.5                   # zoom out so we do not lose the target
        elif abs(err) > 0.1 and (err < 0 or (abs(ex) < 0.3 and abs(ey) < 0.4)):
            zoom = _clamp(cfg.zoom_gain * err, cfg.max_speed)  # zoom in only when centered
    return PTZCommand(round(pan, 2), round(tilt, 2), round(zoom, 2))


class PTZTracker:
    """Follow the target and return to the "home" preset after inactivity.

    Commands are only sent when they change, and at most once per
    ``command_interval_s``; a zero command triggers a single stop.

    Attributes:
        ptz: PTZ driver.
        cfg: PTZ configuration.
        enabled: False for ``NullPTZ`` (no commands are ever sent).
    """

    def __init__(self, ptz: PTZ, cfg: PTZConfig, clock=time.monotonic):
        """Initialize the tracker.

        Args:
            ptz: PTZ driver.
            cfg: PTZ configuration.
            clock: Monotonic time source (injectable for tests).
        """
        self.ptz, self.cfg, self._clock = ptz, cfg, clock
        # Fixed camera / PTZ disabled: no commands, hence never "moving"
        # (otherwise face recognition, paused while moving, would never run).
        self.enabled = not isinstance(ptz, NullPTZ)
        self._last_cmd = PTZCommand()
        self._last_sent = float("-inf")
        self._last_seen = clock()
        self._at_home = False

    def update(self, box: tuple[float, float, float, float] | None, frame_w: int, frame_h: int) -> None:
        """Feed the current target (or its absence) to the controller.

        Args:
            box: Target box, or ``None`` if no one is tracked.
            frame_w: Frame width.
            frame_h: Frame height.
        """
        if not self.enabled:
            return
        now = self._clock()
        if box is None:
            if not self._last_cmd.is_zero:
                self.ptz.stop()
                self._last_cmd = PTZCommand()
            if (self.cfg.home_preset and not self._at_home
                    and now - self._last_seen > self.cfg.return_home_after_s):
                log.info("No target: returning to preset %s", self.cfg.home_preset)
                self.ptz.goto_preset(self.cfg.home_preset)
                self._at_home = True
            return

        self._last_seen, self._at_home = now, False
        cmd = compute_command(box, frame_w, frame_h, self.cfg)
        if cmd.is_zero:
            if not self._last_cmd.is_zero:
                self.ptz.stop()
                self._last_cmd = cmd
            return
        if cmd != self._last_cmd and now - self._last_sent >= self.cfg.command_interval_s:
            self.ptz.move(cmd.pan, cmd.tilt, cmd.zoom)
            self._last_cmd, self._last_sent = cmd, now

    def on_sound(self) -> None:
        """Sound trigger: point to ``sound_preset`` if no target was seen for 5 s."""
        if self.cfg.sound_preset and self._clock() - self._last_seen > 5:
            self.ptz.goto_preset(self.cfg.sound_preset)
            self._at_home = False

    @property
    def moving(self) -> bool:
        """True while a non-zero move command is active (frames are likely blurry)."""
        return not self._last_cmd.is_zero
