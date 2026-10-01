# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/decision.py
# Purpose : GO / NO_GO decision engine combining face, voice and door state
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""GO / NO_GO decision engine.

Garage opening rule (defense in depth):
  1. an authorized person (``can_open_garage``) was recognized by the camera within
     the last ``auth_window_s`` seconds;
  2. a valid voice command was spoken;
  3. if speaker verification is enabled, the voice matches that same person;
  4. if a door sensor is present, the action is consistent with the door state
     (the Novomatic opener only has a pulse input: open and close are the same pulse).

A recognition only opens the authorization window if the person's access rules allow
it (weekdays, time window, expiration date: see ``access.py``). A person on the
watchlist triggers an alert (red LED, event log, notification) and never gets access.

The engine runs on its own thread and processes events sequentially from a queue, so
the authorization state needs no locking.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import date, datetime
from typing import Callable

from jarvis.core.access import access_allowed
from jarvis.config.settings import Settings
from jarvis.storage.database import Database
from jarvis.core.events import (Event, FaceRecognized, Intent, ManualGarage, UnknownFaceSeen, VehicleEvent,
                                VoiceCommand)
from jarvis.core.plates import plate_status
from jarvis.hardware.devices import Hardware

log = logging.getLogger(__name__)


class DecisionEngine(threading.Thread):
    """Consumes pipeline events and decides whether to pulse the garage relay.

    Attributes:
        s: Application settings.
        db: Database used for person lookups and the event log.
        hw: Hardware facade (LEDs, relay, door sensor).
        say: Text-to-speech callback.
        open_listening: Callback opening a voice listening window for N seconds
            (``None`` if the voice pipeline is disabled).
        activate_voice: Callback enabling the voice commands for N seconds (the recognition
            window), set by the core after construction; ``None`` without a voice pipeline.
        events: Input event queue.
        notify: Notification callback ``notify(event_type, **details)``.
    """

    def __init__(self, settings: Settings, db: Database, hardware: Hardware, say: Callable[[str], None],
                 open_listening: Callable[[float], None] | None, events: queue.Queue[Event],
                 clock=time.time, today=date.today, notify: Callable[..., None] | None = None):
        """Initialize the engine.

        Args:
            settings: Application settings.
            db: Database instance.
            hardware: Hardware facade.
            say: Text-to-speech callback.
            open_listening: Callback opening a listening window, or ``None``.
            events: Queue the engine consumes events from.
            clock: Epoch-seconds clock (injectable for tests).
            today: Current-date provider (injectable for tests).
            notify: Optional notification callback; defaults to a no-op.
        """
        super().__init__(name="decision", daemon=True)
        self.s, self.db, self.hw = settings, db, hardware
        self.say, self.open_listening = say, open_listening
        self.events = events
        self._clock, self._today = clock, today
        self.notify = notify or (lambda *_a, **_k: None)
        self._authorized: dict[int, float] = {}  # person_id -> timestamp of last recognition
        self.activate_voice: Callable[[float], None] | None = None
        # Plate automation: per-track state of the vehicles with a confirmed plate, the pending
        # close after a known vehicle left, and a probe telling whether the scene is empty
        # (no person, no vehicle), set by the core from the vision pipeline.
        self._vehicles: dict[int, dict] = {}
        self._pending_close: dict | None = None
        self.scene_clear: Callable[[], bool] | None = None
        # Simulation (Settings > Simulation): events injected by the user, or frames coming from
        # a simulated camera, never drive the relay unless simulation.drive_relay is set.
        self.camera_simulating: Callable[[], object] | None = None
        self._sim = False
        # Serializes event handling: the decision thread and the synchronous simulation calls.
        self.lock = threading.RLock()
        self._halt = threading.Event()

    def stop(self) -> None:
        """Ask the processing loop to exit (within about 0.5 s)."""
        self._halt.set()

    def run(self) -> None:
        """Process queued events until :meth:`stop` is called.

        Exceptions raised while handling an event are logged and do not stop the loop.
        """
        while not self._halt.is_set():
            try:
                # Short timeout so the halt flag and the pending close are checked regularly.
                event = self.events.get(timeout=0.5)
            except queue.Empty:
                event = None
            try:
                with self.lock:
                    if event is not None:
                        self.handle(event)
                    self.tick()
            except Exception:
                log.exception("Error while handling event %r", event)

    def handle(self, event: Event) -> None:
        """Dispatch a single event to its handler.

        Args:
            event: The event to process.
        """
        self._sim = bool(getattr(event, "simulated", False)) or bool(self.camera_simulating and self.camera_simulating())
        if isinstance(event, FaceRecognized):
            self._on_face(event)
        elif isinstance(event, UnknownFaceSeen):
            self._on_unknown(event)
        elif isinstance(event, VoiceCommand):
            self._on_voice(event)
        elif isinstance(event, ManualGarage):
            self._pulse(actor=event.actor, person_id=None, intent=None, source="web")
        elif isinstance(event, VehicleEvent):
            self._on_vehicle(event)

    # --- Vision ------------------------------------------------------------------------
    def _on_face(self, ev: FaceRecognized) -> None:
        """Handle a recognized face: alert on watchlist, otherwise greet and authorize.

        The authorization window is only opened when the person may open the garage and
        their access rules allow it right now; a schedule denial is logged and notified.
        """
        person = self.db.get_person(ev.person_id)
        if person is None:
            return
        if person.watchlist:
            self.hw.indicate("red", self.s.decision.led_on_s)
            self.db.log_event("watchlist_seen", actor="vision", person_id=person.id, score=round(ev.score, 3),
                              track=ev.track_id, sighting_id=ev.sighting_id)
            self.notify("watchlist_seen", person_id=person.id, name=person.first_name, score=round(ev.score, 3),
                        sighting_id=ev.sighting_id)
            if self.s.decision.watchlist_message:
                self.say(self.s.decision.watchlist_message)
            return
        # Recognition window: the green LED stays on for its whole duration and turns off at its
        # end (a new recognition restarts it); voice commands are only accepted meanwhile.
        window = self.s.decision.auth_window_s
        self.hw.indicate("green", window)
        self.db.log_event("face_recognized", actor="vision", person_id=person.id,
                          score=round(ev.score, 3), track=ev.track_id, sighting_id=ev.sighting_id)
        self.notify("face_recognized", person_id=person.id, name=person.first_name, score=round(ev.score, 3),
                    sighting_id=ev.sighting_id)
        if person.can_open_garage:
            allowed, reason = access_allowed(person, datetime.fromtimestamp(self._clock()))
            if allowed:
                self._authorized[person.id] = ev.ts
                if self.activate_voice:
                    self.activate_voice(window)
                self.db.log_event("voice_window_opened", actor="vision", person_id=person.id, seconds=window)
                log.info("Recognition window opened for %s: green LED and voice commands for %.0f s",
                         person.first_name, window)
            else:
                self.db.log_event("access_denied_schedule", actor="vision", person_id=person.id, reason=reason)
                self.notify("access_denied_schedule", person_id=person.id, name=person.first_name, reason=reason)
        # Greet at most once per person per day.
        if self.db.mark_greeted(person.id, self._today()):
            self.say(self.s.decision.greeting.format(first_name=person.first_name))
        if self.open_listening and self.s.decision.listen_after_recognition_s > 0:
            self.open_listening(self.s.decision.listen_after_recognition_s)

    def _on_unknown(self, ev: UnknownFaceSeen) -> None:
        """Handle an unknown face: red LED, event log, notification and optional message."""
        self.hw.indicate("red", self.s.decision.led_on_s)
        self.db.log_event("face_unknown", actor="vision", unknown_id=ev.unknown_id, track=ev.track_id,
                          cluster_id=ev.cluster_id, sighting_id=ev.sighting_id)
        self.notify("face_unknown", unknown_id=ev.unknown_id, cluster_id=ev.cluster_id, sighting_id=ev.sighting_id)
        if self.s.decision.unknown_message:
            self.say(self.s.decision.unknown_message)

    # --- Voice -------------------------------------------------------------------------
    def authorized_persons(self) -> set[int]:
        """Return the ids of persons whose authorization window is still open.

        Expired entries are pruned as a side effect.

        Returns:
            Set of person ids recognized within the last ``auth_window_s`` seconds.
        """
        now = self._clock()
        window = self.s.decision.auth_window_s
        self._authorized = {pid: ts for pid, ts in self._authorized.items() if now - ts <= window}
        return set(self._authorized)

    def _deny(self, reason: str, ev: VoiceCommand, message: str) -> None:
        """Reject a voice command: log, red LED, notify, and speak ``message``.

        Args:
            reason: Machine-readable denial reason stored in the event log.
            ev: The rejected voice command.
            message: Spoken feedback.
        """
        log.warning("Command denied (%s): %r", reason, ev.text)
        self.db.log_event("voice_denied", actor="voice", person_id=ev.speaker_person_id, reason=reason,
                          text=ev.text, speaker_score=ev.speaker_score)
        self.hw.indicate("red", self.s.decision.led_on_s)
        self.notify("voice_denied", reason=reason, text=ev.text, person_id=ev.speaker_person_id)
        self.say(message)

    def _on_voice(self, ev: VoiceCommand) -> None:
        """Apply the GO / NO_GO rule to a voice command and pulse the garage if allowed."""
        if ev.intent is None:
            self.db.log_event("voice_not_understood", actor="voice", text=ev.text)
            self.say("Je n'ai pas compris.")
            return
        if ev.intent == Intent.CANCEL:
            self.db.log_event("voice_cancel", actor="voice", text=ev.text)
            self.say("D'accord.")
            return

        candidates = self.authorized_persons()
        if not candidates:
            return self._deny("no_authorized_face", ev, "Accès refusé. Je ne vous ai pas reconnu.")
        if self.s.speaker.enabled:
            if ev.speaker_person_id is None or ev.speaker_person_id not in candidates:
                return self._deny("speaker_mismatch", ev, "Accès refusé. Voix non reconnue.")
            person_id = ev.speaker_person_id
        else:
            # Without speaker verification, attribute the command to the most recently recognized person.
            person_id = max(candidates, key=lambda p: self._authorized[p])

        # The single pulse input toggles the door: if it is already in the requested state,
        # a pulse would do the opposite of what was asked.
        door = self.hw.door_state()
        if ev.intent == Intent.OPEN_GARAGE and door == "open":
            self.say("Le garage est déjà ouvert.")
            return
        if ev.intent == Intent.CLOSE_GARAGE and door == "closed":
            self.say("Le garage est déjà fermé.")
            return
        if self._pulse(actor="voice", person_id=person_id, intent=ev.intent, source="voice", text=ev.text):
            self.say("J'ouvre le garage." if ev.intent == Intent.OPEN_GARAGE else "Je ferme le garage.")

    # --- Vehicles (license plates) --------------------------------------------------------
    def _on_vehicle(self, ev: VehicleEvent) -> None:
        """Handle a plate reader event: record the read, open for an approaching known vehicle,
        schedule the close once a known vehicle has left (see :class:`PlatesConfig`)."""
        if ev.kind == "plate":
            self._on_plate(ev)
            v = self._vehicles.get(ev.track_id)
            if v and ev.direction == "approaching":
                self._vehicle_arrives(v, ev.ts)
            elif v and ev.direction == "leaving":
                self._vehicle_leaves(v)
            return
        v = self._vehicles.get(ev.track_id)
        if v is None:
            return                                   # vehicle without a confirmed plate: nothing to decide
        if ev.kind == "approaching":
            self._vehicle_arrives(v, ev.ts)
        elif ev.kind == "leaving":
            self._vehicle_leaves(v)
        elif ev.kind == "gone":
            self._vehicles.pop(ev.track_id, None)
            self._vehicle_gone(v, ev)

    def _on_plate(self, ev: VehicleEvent) -> None:
        """Record a confirmed plate (plate_reads row + event) and notify."""
        row = self.db.find_plate(ev.plate)
        status = plate_status(row, self._clock())
        person = self.db.get_person(row["person_id"]) if row and row.get("person_id") else None
        read_id = self.db.add_plate_read(ev.plate, ev.confidence, ev.region, status, row["id"] if row else None,
                                         ev.track_id, ev.direction, ev.image_path, ts=ev.ts)
        details = dict(plate=ev.plate, status=status, confidence=ev.confidence, region=ev.region,
                       direction=ev.direction, track=ev.track_id, read_id=read_id,
                       plate_id=row["id"] if row else None, label=row["label"] if row else "")
        self.db.log_event("plate_read", actor="vision", person_id=person.id if person else None, **details)
        log.info("Plate %s read (%s, confidence %.2f, %s)", ev.plate, status, ev.confidence, ev.direction)
        if status == "known":
            self.notify("plate_recognized", person_id=person.id if person else None, **details)
        elif self.s.plates.unknown_notify:
            self.notify("plate_unknown" if status == "unknown" else "plate_refused", **details)
        self._vehicles[ev.track_id] = {"plate": ev.plate, "status": status, "row": row, "person": person,
                                       "read_id": read_id, "opened": False, "leaving": False, "refused": set(),
                                       "simulated": self._sim}

    def _refuse(self, v: dict, reason: str, action: str = "open") -> None:
        """Log (once per vehicle and reason) why the garage was not operated."""
        if reason in v["refused"]:
            return
        v["refused"].add(reason)
        self.db.log_event(f"plate_{action}_refused", actor="vision", plate=v["plate"], reason=reason,
                          read_id=v["read_id"], person_id=v["person"].id if v["person"] else None)
        log.info("Plate %s: garage not %s (%s)", v["plate"], "opened" if action == "open" else "closed", reason)

    def _vehicle_arrives(self, v: dict, ts: float) -> None:
        """Open for an approaching vehicle with a known, allowed plate while the door is closed."""
        if v["opened"]:
            return
        if not self.s.plates.auto_open:
            return self._refuse(v, "auto_open_disabled")
        if v["status"] != "known":
            return self._refuse(v, f"plate_{v['status']}")
        person = v["person"]
        if person is not None:
            if not person.can_open_garage:
                return self._refuse(v, "person_not_allowed")
            allowed, reason = access_allowed(person, datetime.fromtimestamp(ts))
            if not allowed:
                return self._refuse(v, f"schedule_{reason}")
        # A known, allowed vehicle coming back cancels a pending close, whatever the door state.
        if self._pending_close is not None:
            self._pending_close = None
            self.db.log_event("garage_close_cancelled", actor="vision", plate=v["plate"], read_id=v["read_id"])
        # The motor has a single toggle input: without the door state a pulse could close it.
        door = self.hw.door_state()
        if door is None:
            return self._refuse(v, "door_state_unknown")
        if door == "open":
            v["opened"] = True
            self.db.set_plate_read_action(v["read_id"], "none", "approaching")
            return self._refuse(v, "already_open")
        if self._pulse(actor="plate", person_id=person.id if person else None, intent=Intent.OPEN_GARAGE,
                       source="plate", plate=v["plate"], read_id=v["read_id"]):
            v["opened"] = True
            self.db.set_plate_read_action(v["read_id"], "opened", "approaching")
            log.info("Garage opened for plate %s", v["plate"])

    def _vehicle_leaves(self, v: dict) -> None:
        """Mark a known vehicle as leaving (the close is scheduled when it is gone)."""
        if not v["leaving"]:
            v["leaving"] = True
            self.db.log_event("vehicle_leaving", actor="vision", plate=v["plate"], read_id=v["read_id"])

    def _vehicle_gone(self, v: dict, ev: VehicleEvent) -> None:
        """A known vehicle that was leaving is out of sight: schedule the close (opt-in)."""
        if v["status"] != "known" or not (v["leaving"] or ev.direction == "leaving"):
            return
        if not self.s.plates.auto_close:
            return self._refuse(v, "auto_close_disabled", "close")
        now = self._clock()
        self._pending_close = {"due": now + self.s.plates.close_delay_s, "give_up": now + self.s.plates.close_give_up_s,
                               "plate": v["plate"], "read_id": v["read_id"], "vehicle": v}
        self.db.log_event("garage_close_scheduled", actor="vision", plate=v["plate"], read_id=v["read_id"],
                          delay_s=self.s.plates.close_delay_s)
        log.info("Plate %s left: garage close scheduled in %.0f s", v["plate"], self.s.plates.close_delay_s)

    def tick(self) -> None:
        """Periodic work (every loop turn): run the pending close when its conditions are met.

        The scene must stay clear (no person, no vehicle) for the whole ``close_delay_s``: any
        presence restarts the countdown. The door must read "open"; otherwise, or after
        ``close_give_up_s``, the close is abandoned and logged.
        """
        pc = self._pending_close
        if pc is None:
            return
        now = self._clock()
        v = pc["vehicle"]
        if now >= pc["give_up"]:
            self._pending_close = None
            return self._refuse(v, "scene_never_clear", "close")
        if self.scene_clear is not None and not self.scene_clear():
            pc["due"] = now + self.s.plates.close_delay_s
            return
        if now < pc["due"]:
            return
        self._pending_close = None
        self._sim = bool(v.get("simulated"))
        door = self.hw.door_state()
        if door != "open":
            return self._refuse(v, "door_state_unknown" if door is None else "already_closed", "close")
        if self._pulse(actor="plate", person_id=v["person"].id if v["person"] else None, intent=Intent.CLOSE_GARAGE,
                       source="plate", plate=pc["plate"], read_id=pc["read_id"]):
            self.db.set_plate_read_action(pc["read_id"], "closed", "leaving")
            self.db.log_event("garage_closed_vehicle_left", actor="vision", plate=pc["plate"], read_id=pc["read_id"])
            log.info("Garage closed after plate %s left", pc["plate"])

    # --- Action ------------------------------------------------------------------------
    def _pulse(self, actor: str, person_id: int | None, intent: Intent | None, source: str, **details) -> bool:
        """Pulse the garage relay and record the outcome.

        Args:
            actor: Who triggered the action (``"voice"`` or a web user name).
            person_id: Authorized person, if known.
            intent: Voice intent, or ``None`` for a manual toggle.
            source: Origin of the request (``"voice"`` or ``"web"``).
            **details: Extra fields stored with the event.

        Returns:
            ``True`` if the hardware accepted the pulse, ``False`` if it was rejected
            (cooldown between pulses).
        """
        door_before = self.hw.door_state()
        if self._sim and not self.s.simulation.drive_relay:
            # Simulation: the decision is traced exactly like a real one, the relay stays still.
            self.db.log_event("garage_pulse_simulated", actor=actor, person_id=person_id,
                              intent=intent.value if intent else "toggle", source=source, door_before=door_before,
                              **details)
            log.info("Simulated garage pulse (%s, %s): relay NOT driven (simulation.drive_relay is off)",
                     source, intent.value if intent else "toggle")
            self.hw.indicate("green", self.s.decision.led_on_s)
            return True
        ok = self.hw.pulse_garage()
        self.db.log_event("garage_pulse" if ok else "garage_pulse_rejected", actor=actor, person_id=person_id,
                          intent=intent.value if intent else "toggle", source=source, door_before=door_before,
                          **details)
        if ok:
            self.hw.indicate("green", self.s.decision.led_on_s)
            self.notify("garage_pulse", actor=actor, person_id=person_id, source=source,
                        intent=intent.value if intent else "toggle", door_before=door_before)
        return ok
