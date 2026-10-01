# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/plates.py
# Purpose : License plate rules shared by vision, decision, storage and API (pure functions)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""License plate normalization and status (no I/O, unit-tested).

Plates are compared in a **normalized** form so that the way they are typed or printed does not
matter: Unicode NFKC, upper case, and only letters and digits kept (spaces, hyphens, dots and
middle dots are separators on many international plates: "AB-123-CD", "B MW 1234",
"12·AB·34"). Letters of any script are kept (Cyrillic, Arabic, CJK plates stay distinct); the
comparison is exact — no "O/0" or "I/1" fuzziness, which would widen the set of plates that
open the garage.
"""

from __future__ import annotations

import time
import unicodedata

MIN_LEN, MAX_LEN = 2, 12


def normalize_plate(text: str) -> str:
    """Normalized form of a plate: NFKC, upper case, letters and digits only.

    >>> normalize_plate(" ab-123 cd ")
    'AB123CD'
    """
    text = unicodedata.normalize("NFKC", text or "").upper()
    return "".join(ch for ch in text if ch.isalnum())


def validate_plate(text: str) -> str:
    """Normalize and check a plate entered by an administrator.

    Raises:
        ValueError: When the normalized plate has fewer than 2 or more than 12 characters.
    """
    plate = normalize_plate(text)
    if not MIN_LEN <= len(plate) <= MAX_LEN:
        raise ValueError(f"License plate: {MIN_LEN} to {MAX_LEN} letters or digits expected")
    return plate


def plate_status(row: dict | None, now: float | None = None) -> str:
    """Status of a read against the registry row of its plate.

    Returns:
        ``unknown`` (not registered), ``disabled``, ``expired`` or ``known``.
    """
    if row is None:
        return "unknown"
    if not row.get("enabled"):
        return "disabled"
    until = row.get("valid_until")
    if until and (now or time.time()) > until:
        return "expired"
    return "known"


def mean_confidence(conf: float | list[float] | None, length: int) -> float:
    """Mean OCR confidence over the characters actually read.

    fast-plate-ocr returns one value per character slot (padded slots included): only the
    first ``length`` values describe the plate text.
    """
    if conf is None:
        return 0.0
    if isinstance(conf, (int, float)):
        return float(conf)
    values = list(conf)[:max(length, 1)]
    return float(sum(values) / len(values)) if values else 0.0
