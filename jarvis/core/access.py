# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/access.py
# Purpose : Per-person access rules (weekdays, time window, expiration date)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Per-person access rules: allowed weekdays, daily time window and expiration date.

This module is intentionally pure (no I/O, no clock access): the caller passes the
point in time to evaluate, which keeps the rules trivially unit-testable.
"""

from __future__ import annotations

from datetime import datetime, time

from jarvis.storage.database import Person


def _parse_hhmm(value: str) -> time | None:
    """Parse an ``HH:MM`` string into a :class:`datetime.time`.

    Args:
        value: Time string such as ``"07:30"``; an empty string means "unset".

    Returns:
        The parsed time, or ``None`` when ``value`` is empty.

    Raises:
        ValueError: If ``value`` is not a valid ``HH:MM`` string.
    """
    if not value:
        return None
    h, m = value.split(":")
    return time(int(h), int(m))


def access_allowed(person: Person, when: datetime) -> tuple[bool, str]:
    """Check whether ``person`` may open the garage at ``when``.

    Rules are evaluated in order and the first failing rule determines the reason.
    The daily time window may wrap around midnight (e.g. 22:00 -> 06:00).

    Args:
        person: The person to evaluate.
        when: Local date and time at which access is requested.

    Returns:
        A ``(allowed, reason)`` tuple. ``reason`` is ``"ok"`` when allowed, otherwise
        one of ``"not_authorized"``, ``"watchlist"``, ``"expired"``,
        ``"day_not_allowed"`` or ``"outside_hours"``.
    """
    if not person.can_open_garage:
        return False, "not_authorized"
    if person.watchlist:
        return False, "watchlist"
    if person.valid_until is not None and when.timestamp() > person.valid_until:
        return False, "expired"
    # access_days holds ISO weekday digits ("1" = Monday ... "7" = Sunday); empty means every day.
    if person.access_days and str(when.isoweekday()) not in person.access_days:
        return False, "day_not_allowed"
    start, end = _parse_hhmm(person.access_start), _parse_hhmm(person.access_end)
    if start and end:
        now = when.time()
        # start > end means the window spans midnight.
        inside = start <= now < end if start <= end else (now >= start or now < end)
        if not inside:
            return False, "outside_hours"
    return True, "ok"
