# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/events.py
# Purpose : Event types exchanged between the pipelines and the decision engine
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Events exchanged between the perception pipelines (vision, voice) and the decision engine.

Pipelines produce these plain dataclasses and push them onto a shared queue; the
:class:`~jarvis.core.decision.DecisionEngine` consumes them on its own thread. Every
event carries a ``ts`` timestamp (epoch seconds) set at creation time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    """Voice command intents understood by the decision engine."""

    OPEN_GARAGE = "open_garage"
    CLOSE_GARAGE = "close_garage"
    CANCEL = "cancel"


@dataclass
class FaceRecognized:
    """A tracked face was matched to an enrolled person.

    Attributes:
        track_id: Tracker identifier of the face within the video stream.
        person_id: Database id of the matched person.
        first_name: First name of the matched person.
        score: Cosine similarity of the match.
        ts: Event timestamp (epoch seconds).
        sighting_id: Id of the recorded sighting (snapshot), if any.
    """

    track_id: int
    person_id: int
    first_name: str
    score: float
    ts: float = field(default_factory=time.time)
    sighting_id: int | None = None


@dataclass
class UnknownFaceSeen:
    """A tracked face did not match any enrolled person.

    Attributes:
        track_id: Tracker identifier of the face within the video stream.
        unknown_id: Id of the stored unknown face, or ``None`` when deduplicated
            (the same face was already stored recently).
        ts: Event timestamp (epoch seconds).
        cluster_id: Cluster grouping repeated sightings of the same unknown visitor.
        sighting_id: Id of the recorded sighting (snapshot), if any.
    """

    track_id: int
    unknown_id: int | None  # None when deduplicated (already stored recently)
    ts: float = field(default_factory=time.time)
    cluster_id: int | None = None  # groups repeated sightings of the same unknown visitor
    sighting_id: int | None = None


@dataclass
class VoiceCommand:
    """A transcribed voice utterance, with its parsed intent and speaker verification.

    Attributes:
        text: Raw transcription.
        intent: Parsed intent, or ``None`` if the utterance was not understood.
        speaker_person_id: Person identified by speaker verification, if any.
        speaker_score: Speaker verification similarity score, if computed.
        via_wakeword: ``True`` if listening was triggered by the wake word rather than
            by a recent face recognition.
        ts: Event timestamp (epoch seconds).
    """

    text: str
    intent: Intent | None
    speaker_person_id: int | None = None
    speaker_score: float | None = None
    via_wakeword: bool = True
    ts: float = field(default_factory=time.time)


@dataclass
class ManualGarage:
    """A manual garage pulse requested from the web UI.

    Attributes:
        actor: Name of the user who triggered the action.
        ts: Event timestamp (epoch seconds).
    """

    actor: str
    ts: float = field(default_factory=time.time)


@dataclass
class VehicleEvent:
    """A vehicle observation from the plate reader (see :class:`jarvis.vision.plates.VehicleTracker`).

    Attributes:
        kind: ``plate`` (a plate was confirmed for this vehicle), ``approaching``, ``leaving``
            (first time the direction is established) or ``gone`` (the track ended).
        track_id: ByteTrack ID of the vehicle.
        plate: Normalized plate text, when known for this track.
        confidence: Mean OCR confidence of the confirming reads (``plate`` events).
        region: Country or region predicted by the OCR model, if any.
        direction: Last direction of the vehicle: ``approaching``, ``leaving`` or ``stationary``.
        image_path: Vehicle snapshot of the confirming read, if saved.
        ts: Event timestamp (epoch seconds).
    """

    kind: str
    track_id: int
    plate: str | None = None
    confidence: float = 0.0
    region: str | None = None
    direction: str = "stationary"
    image_path: str | None = None
    ts: float = field(default_factory=time.time)


Event = FaceRecognized | UnknownFaceSeen | VoiceCommand | ManualGarage | VehicleEvent
