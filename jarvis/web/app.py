# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/web/app.py
# Purpose : FastAPI REST API and static web UI, served behind Nginx (TLS)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Web API (FastAPI) running behind Nginx, which terminates TLS.

Role:
    Serves the administration REST API under ``/api/*``, the Prometheus endpoint ``/metrics``
    and the static single-page UI mounted at ``/``. It manages persons, faces, voice profiles,
    unknown visitors, the sightings log, the audit trail, runtime settings and monitoring.

Design:
    - Privilege separation: this process never touches the camera, GPIO or the ML models. Any
      operation that needs them (face/voice enrollment, embedding, garage pulse, PTZ, TTS,
      restart) is delegated to the ``core`` service through its local control socket
      (:class:`jarvis.core.control.ControlClient`). If the core is down, the API keeps working
      for read-only/administrative data and returns HTTP 503 for delegated operations.
    - Everything is built inside :func:`create_app` so tests can inject a database, a settings
      object and a fake core client.

Security:
    - Passwords are hashed with Argon2id (transparently rehashed when parameters change).
    - Session tokens are random (``secrets.token_urlsafe(32)``); only their SHA-256 digest is
      stored in the database, so a database leak does not expose usable tokens.
    - The session cookie is ``HttpOnly`` + ``Secure`` + ``SameSite=Strict``, and every
      state-changing request (anything but GET/HEAD) must carry the ``X-Jarvis: 1`` header.
      This blocks CSRF: a cross-site HTML form cannot add custom headers, and a cross-origin
      ``fetch`` with custom headers triggers a CORS preflight that this API never approves.
    - Sessions have an absolute lifetime (``api.session_hours``) and an idle timeout
      (``api.idle_timeout_minutes``); background polling does not extend a session.
    - Login attempts are rate-limited per client IP.
    - Every administrative action is recorded in the hash-chained audit log (SHA-256) with its
      author, source IP, session id and a before/after diff where relevant.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
import secrets
import shutil
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import numpy as np

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from contextlib import asynccontextmanager

from jarvis import __version__
from jarvis.config.settings import (VALUE_SOURCES, Settings, load_settings, resolve_credentials,
                                    rtsp_url_with_credentials)
from jarvis.core.control import ControlClient, CoreUnavailable
from jarvis.core.service import remove_files
from jarvis.storage.database import Database, person_snapshot
from jarvis.config.catalog import (BY_KEY, CATALOG, apply_overrides, check_rtsp_url, diff, get_path, set_path,
                                   validate_changes)
from jarvis.core.plates import normalize_plate, plate_status, validate_plate
from jarvis.web import logview, probe
from jarvis.core.monitoring import read_monitoring_status, render_metrics, write_monitoring_state



log = logging.getLogger(__name__)

COOKIE = "jarvis_session"
WEB_DIR = Path(__file__).parent / "static"
ph = PasswordHasher()


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a session token.

    Only this digest is persisted, so the raw token exists solely in the client's cookie.

    Args:
        token: Raw session token taken from the cookie.

    Returns:
        The 64-character lowercase hex digest.
    """
    return hashlib.sha256(token.encode()).hexdigest()


class LoginRateLimiter:
    """In-memory sliding-window limiter for failed login attempts, keyed by client IP.

    State is per process and is lost on restart, which is acceptable for a single-instance
    appliance: its goal is to slow down online brute force, not to be a durable lockout.
    """

    def __init__(self, max_failures: int = 5, window_s: float = 300):
        """Initialize the limiter.

        Args:
            max_failures: Number of failures within the window after which the key is blocked.
            window_s: Sliding window length, in seconds.
        """
        self.max, self.window = max_failures, window_s
        self._fails: dict[str, deque] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        """Tell whether ``key`` has reached the failure limit, after pruning expired entries.

        Args:
            key: Rate-limit key (the client IP).

        Returns:
            True if further attempts must be rejected.
        """
        q = self._fails[key]
        while q and time.monotonic() - q[0] > self.window:
            q.popleft()
        return len(q) >= self.max

    def fail(self, key: str) -> None:
        """Record one failed attempt for ``key``."""
        self._fails[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        """Forget all failures for ``key`` (called after a successful login)."""
        self._fails.pop(key, None)


# --- Request schemas --------------------------------------------------------------------------

class LoginIn(BaseModel):
    """Body of ``POST /api/login``."""

    username: str
    password: str


HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$|^$"   # "HH:MM" or empty (no time restriction)
DAYS = r"^[1-7]{0,7}$"                          # ISO weekdays, 1 = Monday ... 7 = Sunday


class PersonIn(BaseModel):
    """Body used to create a person (``POST /api/persons`` or ``new_person`` in a label request)."""

    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(default="", max_length=60)
    can_open_garage: bool = False
    watchlist: bool = False
    access_days: str = Field(default="", pattern=DAYS)      # "12345" = Monday..Friday
    access_start: str = Field(default="", pattern=HHMM)
    access_end: str = Field(default="", pattern=HHMM)
    valid_until: float | None = None                         # expiration as a Unix timestamp


class PersonPatch(BaseModel):
    """Body of ``PATCH /api/persons/{id}``; omitted (``None``) fields are left unchanged."""

    first_name: str | None = Field(default=None, min_length=1, max_length=60)
    last_name: str | None = Field(default=None, max_length=60)
    can_open_garage: bool | None = None
    watchlist: bool | None = None
    access_days: str | None = Field(default=None, pattern=DAYS)
    access_start: str | None = Field(default=None, pattern=HHMM)
    access_end: str | None = Field(default=None, pattern=HHMM)
    valid_until: float | None = None
    clear_valid_until: bool = False                          # True = access with no expiration date


class PlateIn(BaseModel):
    """Body of ``POST /api/plates``: a license plate allowed to open the garage."""

    plate: str = Field(min_length=2, max_length=24)          # normalized by the server
    country: str = Field(default="", max_length=40)
    label: str = Field(default="", max_length=80)
    person_id: int | None = None
    enabled: bool = True
    valid_until: float | None = None                          # expiration as a Unix timestamp


class PlatePatch(BaseModel):
    """Body of ``PATCH /api/plates/{id}``; omitted (``None``) fields are left unchanged."""

    country: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=80)
    person_id: int | None = None
    clear_person: bool = False                                 # True = unlink the person
    enabled: bool | None = None
    valid_until: float | None = None
    clear_valid_until: bool = False                            # True = no expiration date


def plate_snapshot(row: dict) -> dict:
    """Audited fields of a registry row (the diff of an update compares two snapshots)."""
    return {k: row.get(k) for k in ("plate", "display", "country", "label", "person_id", "enabled", "valid_until")}


class LabelIn(BaseModel):
    """Target of a labeling request: an existing ``person_id`` or a ``new_person`` to create."""

    person_id: int | None = None
    new_person: PersonIn | None = None


class SayIn(BaseModel):
    """Body of ``POST /api/say``: text to be spoken by the core's TTS engine."""

    text: str = Field(min_length=1, max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


# While a password change is pending, only these endpoints are reachable.
PASSWORD_CHANGE_ALLOWED = {"/api/me", "/api/logout", "/api/account/password"}


class SettingsIn(BaseModel):
    """Body of ``PUT /api/settings``: mapping of catalog key to new value."""

    changes: dict[str, object]


class ProbeIn(BaseModel):
    """Body of ``POST /api/camera/probe``: URL to test (the saved one when omitted)."""

    url: str | None = Field(default=None, max_length=1024)


class SettingsResetIn(BaseModel):
    """Body of ``POST /api/settings/reset``: catalog keys whose override must be removed."""

    keys: list[str]


HHMM_Q = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"   # strict "HH:MM" for query parameters


def create_app(settings: Settings | None = None, db: Database | None = None,
               core: ControlClient | None = None, manage_logging: bool = False) -> FastAPI:
    """Build the FastAPI application.

    Settings overrides stored in the database (edited from the UI) are applied on top of
    ``config.yaml`` at startup; a pristine copy of the file values is kept to report defaults
    and to support resets.

    Args:
        settings: Loaded settings; read from the default configuration when omitted.
        db: Database handle; opened from ``settings.storage.db_path`` when omitted.
        core: Control-socket client for the core service; created when omitted.
        manage_logging: When True, the API process (re)configures its own logging, including
            after a logging-related settings change.

    Returns:
        The configured application, with the static UI mounted at ``/``.
    """
    settings = settings or load_settings()
    db = db or Database(settings.storage.db_path)
    db.init()
    db.ensure_default_admin()  # factory account admin/admin, password change forced at first sign-in
    base_settings = settings.model_copy(deep=True)       # config.yaml values, without overrides
    overrides = db.get_settings_overrides()
    if "_secret.metrics_token" in overrides:  # migration: legacy storage of the metrics token
        db.set_settings_overrides({"secrets.metrics_token": overrides["_secret.metrics_token"]}, actor="system")
        db.delete_settings_overrides(["_secret.metrics_token"])
    apply_overrides(settings, db.get_settings_overrides())
    resolve_credentials(settings)
    if manage_logging:
        from jarvis.core import logs

        logs.configure(settings.log_level, settings.logging, "api")
    core = core or ControlClient(settings.control.socket_path)
    data_dir = Path(settings.storage.data_dir).resolve()
    limiter = LoginRateLimiter()

    def max_upload() -> int:
        """Maximum accepted upload size, in bytes (read live so setting changes apply hot)."""
        return settings.api.max_upload_mb * 1024 * 1024

    def idle_timeout_s() -> float:
        """Session idle timeout, in seconds."""
        return settings.api.idle_timeout_minutes * 60

    def log_session_end(sess: dict, reason: str, when: float) -> None:
        """Record the end of a session in the event log.

        Args:
            sess: Session row (as returned by the database).
            reason: ``logout``, ``revoked``, ``expired`` or ``idle_timeout``.
            when: Effective end time (Unix timestamp); for an idle timeout this is the moment the
                session became idle, not the moment it was detected.
        """
        db.log_event("logout" if reason == "logout" else ("session_revoked" if reason == "revoked"
                                                         else "session_timeout"),
                     actor=sess["username"], ip=sess.get("ip"), session_id=sess.get("session_id") or sess.get("id"),
                     reason=reason, login_at=sess.get("created_at"), logout_at=when,
                     duration_s=round(when - (sess.get("created_at") or when)))

    async def sweeper():
        """Background task: close expired/idle sessions every minute and log each automatic logout."""
        while True:
            await asyncio.sleep(60)
            try:
                for sess in await asyncio.to_thread(db.sweep_sessions, idle_timeout_s()):
                    log_session_end(sess, sess["end_reason"], sess["ended_at"])
            except Exception:
                log.exception("Session sweep failed")

    @asynccontextmanager
    async def lifespan(_app):
        """Start the session sweeper and report readiness/shutdown to systemd."""
        from jarvis.core.sdnotify import notify as sd_notify

        task = asyncio.create_task(sweeper())
        sd_notify("READY=1")  # Type=notify: tells systemd the API is listening
        yield
        sd_notify("STOPPING=1")
        task.cancel()

    # OpenAPI/Swagger endpoints are disabled: no need to publish the API surface.
    app = FastAPI(title="Jarvis", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def client_ip(request: Request) -> str:
        """Client IP address: ``X-Real-IP`` set by Nginx, else the socket peer address."""
        return request.headers.get("X-Real-IP") or (request.client.host if request.client else "?")

    # --- Authentication ------------------------------------------------------------------
    def check_session(token: str | None, touch: bool) -> dict:
        """Validate a session token, or raise HTTP 401.

        A session that has passed its absolute expiration or its idle timeout is closed and the
        end is logged before rejecting the request.

        Args:
            token: Raw token from the session cookie (may be None).
            touch: When True, refresh ``last_seen`` (throttled to once every 10 s to limit writes).

        Returns:
            The authenticated user: ``id``, ``username``, ``session_id``, ``token_hash``, ``ip``.

        Raises:
            HTTPException: 401 if the token is missing, unknown, closed, expired or idle.
        """
        sess = db.get_session(hash_token(token)) if token else None
        if sess is None or sess["ended_at"] is not None:
            raise HTTPException(401, "Not authenticated")
        now = time.time()
        last = sess["last_seen"] or sess["created_at"] or now
        reason = when = None
        if now > sess["expires_at"]:
            reason, when = "expired", sess["expires_at"]
        elif now - last > idle_timeout_s():
            reason, when = "idle_timeout", last + idle_timeout_s()
        if reason:
            if db.end_session(sess["token_hash"], reason, when):
                log_session_end(sess, reason, when)
            raise HTTPException(401, "Session expired (idle timeout)" if reason == "idle_timeout" else "Session expired")
        if touch and now - last >= 10:
            db.touch_session(sess["token_hash"])
        return {"id": sess["user_id"], "username": sess["username"], "session_id": sess["session_id"],
                "token_hash": sess["token_hash"], "ip": sess["ip"],
                "must_change_password": bool(sess.get("must_change_password"))}

    def current_user(request: Request) -> dict:
        """FastAPI dependency: authenticated user for the request, with anti-CSRF enforcement.

        Requests flagged ``X-Jarvis-Background: 1`` (UI auto-refresh) and the MJPEG stream do
        not extend the session, so an unattended open tab still hits the idle timeout.

        Returns:
            The user dict from :func:`check_session`, plus ``request_ip``.

        Raises:
            HTTPException: 401 if not authenticated; 403 if a state-changing request lacks
                the ``X-Jarvis: 1`` header.
        """
        background = (request.headers.get("X-Jarvis-Background") == "1"
                      or request.url.path in ("/api/stream.mjpg", "/api/logs/stream"))
        user = check_session(request.cookies.get(COOKIE), touch=not background)
        if request.method not in ("GET", "HEAD") and request.headers.get("X-Jarvis") != "1":
            raise HTTPException(403, "Missing X-Jarvis header")
        # Default or reset password: nothing but reading the profile, signing out and changing it.
        if user["must_change_password"] and request.url.path not in PASSWORD_CHANGE_ALLOWED:
            raise HTTPException(403, "Password change required")
        user["request_ip"] = client_ip(request)
        return user

    def audit(type_: str, user: dict, person_id: int | None = None, **details) -> None:
        """Record an administrative action: author, IP, session and details (including diffs).

        Args:
            type_: Event type name (e.g. ``person_updated``).
            user: Authenticated user from :func:`current_user`.
            person_id: Related person, if any.
            **details: Extra JSON-serializable details stored with the event.
        """
        db.log_event(type_, actor=user["username"], person_id=person_id, ip=user.get("request_ip"),
                     session_id=user.get("session_id"), **details)

    def call_core(cmd: str, **params) -> dict:
        """Send a command to the core service and require success.

        Args:
            cmd: Control command name.
            **params: Command parameters.

        Returns:
            The core's response dict (``ok`` is true).

        Raises:
            HTTPException: 503 if the core is unreachable; 422 if it reported an error.
        """
        try:
            resp = core.call(cmd, **params)
        except CoreUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        if not resp.get("ok"):
            raise HTTPException(422, resp.get("error", "core service error"))
        return resp

    def notify_core(cmd: str) -> None:
        """Best-effort notification: the core reloads everything at startup anyway."""
        try:
            core.call(cmd)
        except CoreUnavailable:
            log.warning("Core unreachable for %s", cmd)

    @app.post("/api/login")
    def login(body: LoginIn, request: Request, response: Response):
        """Authenticate and open a session.

        Body:
            ``{"username": str, "password": str}``.

        Returns:
            ``{"username", "idle_timeout_minutes", "language"}`` and sets the session cookie
            (HttpOnly, Secure per ``api.cookie_secure``, SameSite=Strict).

        Raises:
            HTTPException: 429 when the client IP is rate-limited; 401 on invalid credentials.
        """
        ip = client_ip(request)
        if limiter.blocked(ip):
            raise HTTPException(429, "Too many attempts, please try again later")
        user = db.get_user_by_name(body.username)
        try:
            if user is None:
                # Same error path for unknown user and bad password: no account enumeration.
                raise VerificationError()
            ph.verify(user["password_hash"], body.password)
        except VerificationError:
            limiter.fail(ip)
            db.log_event("login_failed", actor=body.username[:60], ip=ip)
            raise HTTPException(401, "Invalid credentials") from None
        if ph.check_needs_rehash(user["password_hash"]):
            db.update_password_hash(user["id"], ph.hash(body.password))
        limiter.reset(ip)
        token = secrets.token_urlsafe(32)
        max_age = int(settings.api.session_hours * 3600)
        ua = request.headers.get("User-Agent", "")
        db.create_session(hash_token(token), user["id"], time.time() + max_age, ip=ip, user_agent=ua)
        response.set_cookie(COOKIE, token, max_age=max_age, httponly=True, secure=settings.api.cookie_secure,
                            samesite="strict", path="/")
        sess = db.get_session(hash_token(token))
        db.log_event("login", actor=user["username"], ip=ip, user_agent=ua[:200], session_id=sess["session_id"])
        return {"username": user["username"], "idle_timeout_minutes": settings.api.idle_timeout_minutes,
                "language": settings.ui.language, "must_change_password": bool(user.get("must_change_password"))}

    @app.post("/api/logout")
    def logout(request: Request, response: Response, user: dict = Depends(current_user)):
        """Close the current session and clear the cookie.

        Returns:
            ``{}``.
        """
        sess = db.get_session(user["token_hash"])
        now = time.time()
        if db.end_session(user["token_hash"], "logout", now):
            log_session_end(sess, "logout", now)
        response.delete_cookie(COOKIE, path="/")
        return {}

    @app.post("/api/account/password")
    def change_password(body: PasswordChangeIn, user: dict = Depends(current_user)):
        """Change the signed-in user's password (mandatory after the default admin/admin or a reset).

        Body: ``{"current_password", "new_password"}``. Policy: at least 12 characters, different from
        the account name, from "admin" and from the current password. The other sessions of the account
        are revoked; the change is audited.

        Raises:
            HTTPException: 401 on a wrong current password; 422 when the policy is not met.
        """
        row = db.get_user_by_name(user["username"])
        try:
            ph.verify(row["password_hash"], body.current_password)
        except VerificationError:
            audit("password_change_failed", user)
            raise HTTPException(401, "Current password is incorrect") from None
        new = body.new_password
        problems = [msg for bad, msg in (
            (len(new) < 12, "at least 12 characters"),
            (new.lower() in (user["username"].lower(), "admin", "password", "jarvis"), "not the account name or a default word"),
            (new == body.current_password, "different from the current password"),
            (len(set(new)) < 5, "at least 5 different characters"),
        ) if bad]
        if problems:
            raise HTTPException(422, "Password policy: " + ", ".join(problems))
        db.set_password(row["id"], ph.hash(new), must_change=False)
        revoked = db.end_user_sessions(row["id"], "revoked", except_token=user["token_hash"])
        audit("password_changed", user, other_sessions_revoked=revoked, forced=user["must_change_password"])
        return {"other_sessions_revoked": revoked}

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)):
        """Return the current user; used by the UI to check whether a session is still valid.

        Returns:
            ``{"username", "idle_timeout_minutes", "language", "must_change_password", "version"}``.
            The version is only disclosed to authenticated users (not on the public login screen).
        """
        return {"username": user["username"], "idle_timeout_minutes": settings.api.idle_timeout_minutes,
                "language": settings.ui.language, "must_change_password": bool(user.get("must_change_password")),
                "version": __version__}

    @app.get("/api/ui")
    def ui_prefs():
        """Public UI preferences (login screen language); no authentication required.

        Returns:
            ``{"language": str}``.
        """
        return {"language": settings.ui.language}

    # --- Sessions (login traceability) -------------------------------------------------
    @app.get("/api/sessions")
    def sessions(start: str | None = None, end: str | None = None, username: str | None = None,
                 limit: int = 200, user: dict = Depends(current_user)):
        """List web sessions, newest first. Stale sessions are swept first so states are accurate.

        Query params:
            start, end: Inclusive day range ``YYYY-MM-DD`` (local time) on the login time.
            username: Filter on account name.
            limit: Maximum number of rows (capped at 1000).

        Returns:
            A list of session rows, each extended with ``active`` (still open), ``current``
            (the caller's own session) and ``duration_s``.
        """
        for sess in db.sweep_sessions(idle_timeout_s()):
            log_session_end(sess, sess["end_reason"], sess["ended_at"])
        rows = db.list_sessions(parse_day(start), parse_day(end, True), username, min(limit, 1000))
        now = time.time()
        for r in rows:
            r["active"] = r["ended_at"] is None
            r["current"] = r["id"] == user["session_id"]
            r["duration_s"] = round((r["ended_at"] or now) - (r["created_at"] or now))
        return rows

    @app.delete("/api/sessions/{session_id}")
    def revoke_session(session_id: int, user: dict = Depends(current_user)):
        """Revoke a session (its user is logged out at the next request). Idempotent.

        Returns:
            ``{}``.

        Raises:
            HTTPException: 404 if the session id is unknown.
        """
        token_hash = db.session_token_by_id(session_id)
        if token_hash is None:
            raise HTTPException(404, "Unknown session")
        sess = db.get_session(token_hash)
        now = time.time()
        if db.end_session(token_hash, "revoked", now):
            db.log_event("session_revoked", actor=user["username"], ip=user["request_ip"],
                         session_id=user["session_id"], revoked_session_id=session_id, revoked_user=sess["username"],
                         revoked_ip=sess["ip"], login_at=sess["created_at"], logout_at=now,
                         duration_s=round(now - (sess["created_at"] or now)))
        return {}

    # --- Persons -------------------------------------------------------------------------
    @app.get("/api/persons")
    def list_persons(user: dict = Depends(current_user)):
        """List enrolled persons with their rights, access rules, face/voice counts and last sighting."""
        return db.list_persons()

    @app.post("/api/persons", status_code=201)
    def create_person(body: PersonIn, user: dict = Depends(current_user)):
        """Create a person.

        Body:
            :class:`PersonIn`.

        Returns:
            ``{"id": int}`` with HTTP 201.
        """
        pid = db.add_person(**body.model_dump())
        audit("person_created", user, person_id=pid, values=person_snapshot(db.get_person(pid)))
        return {"id": pid}

    @app.patch("/api/persons/{person_id}")
    def update_person(person_id: int, body: PersonPatch, user: dict = Depends(current_user)):
        """Update a person; the before/after diff is audited and returned.

        Body:
            :class:`PersonPatch` (omitted fields unchanged).

        Returns:
            ``{"changes": [{"field", "before", "after"}, ...]}``.

        Raises:
            HTTPException: 404 if the person does not exist.
        """
        before = db.get_person(person_id)
        if before is None:
            raise HTTPException(404, "Unknown person")
        db.update_person(person_id, **body.model_dump())
        notify_core("reload_faces")  # apply immediately (watchlist, names)
        changes = diff(person_snapshot(before), person_snapshot(db.get_person(person_id)))
        if changes:
            audit("person_updated", user, person_id=person_id,
                  name=f"{before.first_name} {before.last_name}".strip(), changes=changes)
        return {"changes": changes}

    # --- License plates ---------------------------------------------------------------------
    @app.get("/api/plates")
    def plates(user: dict = Depends(current_user)):
        """The plate registry, with each plate's status (known / disabled / expired) and last read."""
        now = time.time()
        return [{**r, "status": plate_status(r, now)} for r in db.list_plates()]

    @app.post("/api/plates")
    def add_plate(body: PlateIn, user: dict = Depends(current_user)):
        """Register a plate (normalized: upper case, letters and digits); audited.

        Raises:
            HTTPException: 422 on an invalid plate or unknown person, 409 if already registered.
        """
        try:
            plate = validate_plate(body.plate)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if body.person_id is not None and db.get_person(body.person_id) is None:
            raise HTTPException(422, "Unknown person")
        if db.find_plate(plate):
            raise HTTPException(409, f"Plate {plate} is already registered")
        pid = db.add_plate(plate, body.plate.strip().upper(), body.country.strip(), body.label.strip(), body.person_id,
                           body.enabled, body.valid_until)
        audit("plate_added", user, person_id=body.person_id, values=plate_snapshot(db.get_plate(pid)))
        return {"id": pid, "plate": plate}

    @app.patch("/api/plates/{plate_id}")
    def update_plate(plate_id: int, body: PlatePatch, user: dict = Depends(current_user)):
        """Update a registered plate; the before/after diff is audited and returned."""
        before = db.get_plate(plate_id)
        if before is None:
            raise HTTPException(404, "Unknown plate")
        if body.person_id is not None and db.get_person(body.person_id) is None:
            raise HTTPException(422, "Unknown person")
        fields = {k: v for k, v in body.model_dump().items()
                  if v is not None and k in ("country", "label", "person_id", "enabled", "valid_until")}
        if body.clear_person:
            fields["person_id"] = None
        if body.clear_valid_until:
            fields["valid_until"] = None
        db.update_plate(plate_id, **fields)
        changes = diff(plate_snapshot(before), plate_snapshot(db.get_plate(plate_id)))
        if changes:
            audit("plate_updated", user, person_id=before.get("person_id"), plate=before["plate"], changes=changes)
        return {"changes": changes}

    @app.delete("/api/plates/{plate_id}")
    def delete_plate(plate_id: int, user: dict = Depends(current_user)):
        """Remove a plate from the registry (its past reads are kept, unlinked); audited."""
        row = db.get_plate(plate_id)
        if row is None:
            raise HTTPException(404, "Unknown plate")
        db.delete_plate(plate_id)
        audit("plate_deleted", user, person_id=row.get("person_id"), values=plate_snapshot(row))
        return {"ok": True}

    @app.get("/api/plate-reads")
    def plate_reads(start: str | None = None, end: str | None = None, time_from: str | None = Query(None, pattern=HHMM_Q),
                    time_to: str | None = Query(None, pattern=HHMM_Q), plate: str = "", status: str = "",
                    limit: int = Query(200, ge=1, le=2000), user: dict = Depends(current_user)):
        """History of the confirmed plate reads (newest first): date range, daily time window,
        plate fragment, status (known / unknown / disabled / expired), with the action taken."""
        if status and status not in ("known", "unknown", "disabled", "expired"):
            raise HTTPException(422, "status must be known, unknown, disabled or expired")
        return db.list_plate_reads(parse_day(start), parse_day(end, end=True), normalize_plate(plate) or None,
                                   status or None, time_from, time_to, limit)

    @app.delete("/api/persons/{person_id}")
    def delete_person(person_id: int, user: dict = Depends(current_user)):
        """Delete a person and ALL related data (right to erasure).

        Removes faces, voice profiles, sightings and their image files; the audit entry keeps a
        snapshot of the deleted record and counts of what was erased.

        Returns:
            ``{}``.

        Raises:
            HTTPException: 404 if the person does not exist.
        """
        person = db.get_person(person_id)
        if person is None:
            raise HTTPException(404, "Unknown person")
        summary = next((p for p in db.list_persons() if p["id"] == person_id), {})
        erased_sightings = len(db.list_sightings(person_id=person_id, limit=1_000_000))
        remove_files(db.delete_person(person_id))
        for sub in ("faces", "voice"):
            shutil.rmtree(data_dir / sub / str(person_id), ignore_errors=True)
        audit("person_deleted", user, deleted_person_id=person_id, values=person_snapshot(person),
              erased={"faces": summary.get("face_count", 0), "voice_profiles": summary.get("voice_count", 0),
                      "sightings": erased_sightings})
        notify_core("reload_faces")
        notify_core("reload_voices")
        return {}

    # --- Faces ---------------------------------------------------------------------------
    async def read_upload(upload: UploadFile) -> bytes:
        """Read an uploaded file, enforcing ``api.max_upload_mb``.

        Reads at most one byte past the limit so oversized uploads are detected without being
        fully buffered.

        Raises:
            HTTPException: 413 if the file exceeds the limit.
        """
        data = await upload.read(max_upload() + 1)
        if len(data) > max_upload():
            raise HTTPException(413, "File too large")
        return data

    @app.get("/api/persons/{person_id}/faces")
    def list_faces(person_id: int, user: dict = Depends(current_user)):
        """List the enrolled face images of a person."""
        return db.list_faces(person_id)

    @app.post("/api/persons/{person_id}/faces", status_code=201)
    async def add_face(person_id: int, file: UploadFile = File(...), user: dict = Depends(current_user)):
        """Enroll a face photo for a person (multipart field ``file``).

        The image is decoded, EXIF-rotated, downscaled to 1600 px and re-encoded as JPEG (which
        strips EXIF/GPS metadata), then the core computes and stores its embedding.

        Returns:
            ``{"face_id": int}`` with HTTP 201.

        Raises:
            HTTPException: 404 unknown person; 413 file too large; 415 not a valid image;
                422 no usable face (core error); 503 core unreachable.
        """
        from PIL import Image, ImageOps, UnidentifiedImageError

        if db.get_person(person_id) is None:
            raise HTTPException(404, "Unknown person")
        data = await read_upload(file)
        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        except (UnidentifiedImageError, OSError):
            raise HTTPException(415, "Invalid image") from None
        img.thumbnail((1600, 1600))
        dest = data_dir / "faces" / str(person_id) / f"{uuid.uuid4().hex}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=92)  # re-encoding strips EXIF / GPS
        try:
            resp = await asyncio.to_thread(call_core, "enroll_face", person_id=person_id, image_path=str(dest))
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        audit("face_added", user, person_id=person_id, face_id=resp["face_id"], det_score=resp.get("det_score"),
              file=file.filename)
        return {"face_id": resp["face_id"]}

    @app.delete("/api/faces/{face_id}")
    def delete_face(face_id: int, user: dict = Depends(current_user)):
        """Delete one enrolled face and its image file.

        Returns:
            ``{}``.
        """
        path = db.delete_face(face_id)
        if path:
            remove_files([path])
        audit("face_deleted", user, face_id=face_id, image=Path(path).name if path else None)
        notify_core("reload_faces")
        return {}

    # --- Voice profiles ------------------------------------------------------------------
    @app.post("/api/persons/{person_id}/voice", status_code=201)
    async def add_voice(person_id: int, file: UploadFile = File(...), user: dict = Depends(current_user)):
        """Enroll a voice sample for a person (multipart field ``file``).

        Unknown extensions are stored as ``.webm`` (the browser MediaRecorder default); the core
        decodes the audio and computes the speaker embedding.

        Returns:
            ``{"profile_id": int}`` with HTTP 201.

        Raises:
            HTTPException: 404 unknown person; 413 file too large; 422 core error;
                503 core unreachable.
        """
        if db.get_person(person_id) is None:
            raise HTTPException(404, "Unknown person")
        data = await read_upload(file)
        suffix = Path(file.filename or "").suffix.lower()
        suffix = suffix if suffix in {".wav", ".webm", ".ogg", ".mp3", ".m4a", ".flac"} else ".webm"
        dest = data_dir / "voice" / str(person_id) / f"{uuid.uuid4().hex}{suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        try:
            resp = await asyncio.to_thread(call_core, "enroll_voice", person_id=person_id, audio_path=str(dest))
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        audit("voice_added", user, person_id=person_id, profile_id=resp["profile_id"])
        return {"profile_id": resp["profile_id"]}

    @app.delete("/api/persons/{person_id}/voice")
    def delete_voice(person_id: int, user: dict = Depends(current_user)):
        """Delete all voice profiles of a person and their audio files.

        Returns:
            ``{}``.
        """
        files = db.delete_voice_profiles(person_id)
        remove_files(files)
        audit("voice_deleted", user, person_id=person_id, profiles=len(files))
        notify_core("reload_voices")
        return {}

    # --- Unknown faces -------------------------------------------------------------------
    @app.get("/api/unknowns")
    def list_unknowns(user: dict = Depends(current_user)):
        """List captured unknown faces awaiting labeling."""
        return db.list_unknowns()

    @app.post("/api/unknowns/{unknown_id}/label")
    def label_unknown(unknown_id: int, body: LabelIn, user: dict = Depends(current_user)):
        """Assign an unknown face to a person (existing or new) and relabel its sightings.

        The capture is moved into the person's face gallery and becomes an enrolled face.

        Body:
            :class:`LabelIn` (``person_id`` or ``new_person``).

        Returns:
            ``{"person_id", "face_id", "sightings_relabeled"}``.

        Raises:
            HTTPException: 404 unknown face not found; 422 no valid target.
        """
        unknown = db.get_unknown(unknown_id)
        if unknown is None:
            raise HTTPException(404, "Unknown face not found")
        person_id = resolve_label_target(body, user)
        src = Path(unknown["image_path"])
        dest = data_dir / "faces" / str(person_id) / f"{uuid.uuid4().hex}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.move(src, dest)
        face_id = db.label_unknown(unknown_id, person_id, str(dest))
        relabeled = db.relabel_sightings(person_id, unknown_ids=[unknown_id])
        audit("unknown_labeled", user, person_id=person_id, unknown_id=unknown_id, face_id=face_id,
              sightings_relabeled=relabeled)
        notify_core("reload_faces")
        return {"person_id": person_id, "face_id": face_id, "sightings_relabeled": relabeled}

    def resolve_label_target(body: LabelIn, user: dict) -> int:
        """Return the person id targeted by a label request, creating the person if requested.

        Raises:
            HTTPException: 422 if neither a valid ``person_id`` nor ``new_person`` is given.
        """
        if body.new_person is not None:
            person_id = db.add_person(**body.new_person.model_dump())
            audit("person_created", user, person_id=person_id, values=person_snapshot(db.get_person(person_id)))
            return person_id
        if body.person_id is not None and db.get_person(body.person_id):
            return body.person_id
        raise HTTPException(422, "person_id or new_person required")

    @app.get("/api/unknowns/clusters")
    def unknown_clusters(user: dict = Depends(current_user)):
        """List clusters of unknown faces (same unknown visitor seen several times)."""
        return db.list_unknown_clusters()

    @app.delete("/api/unknowns/clusters/{cluster_id}")
    def delete_cluster(cluster_id: int, user: dict = Depends(current_user)):
        """Delete every unknown face of a cluster and their images.

        Returns:
            ``{"deleted": int}``.
        """
        ids = db.unknown_ids_in_cluster(cluster_id)
        remove_files([p for p in (db.delete_unknown(uid) for uid in ids) if p])
        audit("cluster_deleted", user, cluster_id=cluster_id, faces=len(ids))
        notify_core("reload_faces")  # also drops the cluster from the core's cache
        return {"deleted": len(ids)}

    @app.post("/api/unknowns/clusters/{cluster_id}/label")
    def label_cluster(cluster_id: int, body: LabelIn, user: dict = Depends(current_user)):
        """Label every visit of the same unknown visitor, and their sightings (retroactive).

        Body:
            :class:`LabelIn` (``person_id`` or ``new_person``).

        Returns:
            ``{"person_id", "faces", "sightings_relabeled"}``.

        Raises:
            HTTPException: 404 empty/unknown cluster; 422 no valid target.
        """
        ids = db.unknown_ids_in_cluster(cluster_id)
        if not ids:
            raise HTTPException(404, "Unknown cluster")
        person_id = resolve_label_target(body, user)
        faces = []
        for uid in ids:
            unknown = db.get_unknown(uid)
            src = Path(unknown["image_path"])
            dest = data_dir / "faces" / str(person_id) / f"{uuid.uuid4().hex}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.move(src, dest)
            faces.append(db.label_unknown(uid, person_id, str(dest)))
        relabeled = db.relabel_sightings(person_id, unknown_ids=ids, cluster_id=cluster_id)
        audit("cluster_labeled", user, person_id=person_id, cluster_id=cluster_id, faces=len(faces),
              sightings_relabeled=relabeled)
        notify_core("reload_faces")
        return {"person_id": person_id, "faces": len(faces), "sightings_relabeled": relabeled}

    @app.delete("/api/unknowns/{unknown_id}")
    def delete_unknown(unknown_id: int, user: dict = Depends(current_user)):
        """Delete one unknown face and its image.

        Returns:
            ``{}``.
        """
        path = db.delete_unknown(unknown_id)
        if path:
            remove_files([path])
        audit("unknown_deleted", user, unknown_id=unknown_id)
        return {}

    # --- Media (images protected by authentication) --------------------------------------
    @app.get("/api/media")
    def media(path: str, user: dict = Depends(current_user)):
        """Serve a stored image to an authenticated user.

        Query params:
            path: Absolute path of the image, as returned by other endpoints.

        The resolved path must lie inside ``faces/``, ``unknown/`` or ``sightings/`` under the
        data directory (``resolve()`` defeats ``..`` and symlink traversal); anything else is
        answered with 404 so the filesystem layout is not disclosed.

        Raises:
            HTTPException: 404 if the path is outside the allowed directories or not a file.
        """
        p = Path(path).resolve()
        allowed = [(data_dir / d).resolve() for d in ("faces", "unknown", "sightings", "recordings", "timelapse", "plates")]
        if not any(p.is_relative_to(a) for a in allowed) or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})

    # --- Sightings: timestamped traceability ---------------------------------------------
    def parse_day(value: str | None, end: bool = False) -> float | None:
        """Convert ``YYYY-MM-DD`` (local time) to the timestamp of that day's start.

        Args:
            value: Date string, or None/empty for "no bound".
            end: When True, return the start of the next day (exclusive upper bound), so the
                given day is included.

        Raises:
            HTTPException: 422 if the date is malformed.
        """
        if not value:
            return None
        try:
            d = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "Expected date format: YYYY-MM-DD") from None
        return d.timestamp() + (86400 if end else 0)

    def check_hhmm(value: str | None) -> str | None:
        """Validate an optional ``HH:MM`` query parameter; empty becomes None.

        Raises:
            HTTPException: 422 if the time is malformed.
        """
        import re

        if value and not re.match(HHMM_Q, value):
            raise HTTPException(422, "Expected time format: HH:MM")
        return value or None

    def time_window(start, end, time_from, time_to) -> dict:
        """Build the common time filter: day range (inclusive, local time) + daily time slot.

        The daily slot ``time_from``..``time_to`` may wrap past midnight (e.g. 22:00-06:00).

        Returns:
            Keyword arguments ``start_ts``, ``end_ts``, ``time_from``, ``time_to`` for the DB layer.
        """
        return {"start_ts": parse_day(start), "end_ts": parse_day(end, True),
                "time_from": check_hhmm(time_from), "time_to": check_hhmm(time_to)}

    @app.get("/api/sightings")
    def sightings(start: str | None = None, end: str | None = None, time_from: str | None = None,
                  time_to: str | None = None, person_id: int | None = None, status: str | None = None,
                  limit: int = 200, before_id: int | None = None, user: dict = Depends(current_user)):
        """List sightings, newest first, with keyset pagination.

        Query params:
            start, end: Inclusive day range ``YYYY-MM-DD``.
            time_from, time_to: Daily time slot ``HH:MM`` (may wrap past midnight).
            person_id: Only sightings of this person.
            status: ``known``, ``unknown``, ``labeled`` or ``watchlist``.
            limit: Page size (capped at 1000).
            before_id: Return rows with an id lower than this (next page).
        """
        return db.list_sightings(person_id=person_id, status=status, limit=min(limit, 1000), before_id=before_id,
                                 **time_window(start, end, time_from, time_to))

    def csv_response(header: list[str], rows: list[list], prefix: str) -> Response:
        """Build a downloadable CSV response.

        Uses ``;`` as delimiter and a UTF-8 BOM so spreadsheet software (Excel in European
        locales) opens it correctly. The file name is ``<prefix>-YYYYMMDD-HHMM.csv``.
        """
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(header)
        w.writerows(rows)
        name = f"{prefix}-{datetime.now():%Y%m%d-%H%M}.csv"
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/sightings.csv")
    def sightings_csv(start: str | None = None, end: str | None = None, time_from: str | None = None,
                      time_to: str | None = None, person_id: int | None = None, status: str | None = None,
                      user: dict = Depends(current_user)):
        """Export sightings matching the filters as CSV (same filters as ``/api/sightings``, no limit).

        The export itself is audited with its filters and row count.
        """
        window = time_window(start, end, time_from, time_to)
        rows = db.list_sightings(person_id=person_id, status=status, limit=1_000_000, **window)
        out = []
        for r in rows:
            dt = datetime.fromtimestamp(r["ts"])
            who = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip()
            out.append([r["id"], dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), r["status"], who,
                        r["score"], r["quality"], r["track_id"], r["cluster_id"] or ""])
        audit("sightings_exported", user, rows=len(rows), filters={"start": start, "end": end, "time_from": time_from,
                                                                   "time_to": time_to, "person_id": person_id,
                                                                   "status": status})
        return csv_response(["id", "date", "time", "status", "person", "score", "quality", "track", "unknown_group"],
                            out, "jarvis-sightings")

    @app.get("/api/sightings/stats")
    def sightings_stats(days: int = 14, time_from: str | None = None, time_to: str | None = None,
                        user: dict = Depends(current_user)):
        """Aggregated sighting statistics for the dashboard.

        Query params:
            days: Look-back period in days (capped at 366).
            time_from, time_to: Optional daily time slot ``HH:MM``.
        """
        return db.sightings_stats(time.time() - min(days, 366) * 86400, check_hhmm(time_from), check_hhmm(time_to))

    @app.post("/api/search/face")
    async def search_face(file: UploadFile = File(...), limit: int = 50, start: str | None = None,
                          end: str | None = None, time_from: str | None = None, time_to: str | None = None,
                          user: dict = Depends(current_user)):
        """Face search: find every sighting whose capture looks like the uploaded photo.

        The core computes the embedding of the photo; cosine similarity against stored
        sighting embeddings is computed here and filtered by ``faces.search_threshold``.

        Query params:
            limit: Maximum number of hits (capped at 500).
            start, end, time_from, time_to: Same time filters as ``/api/sightings``.

        Returns:
            Sighting rows sorted by decreasing ``similarity`` (rounded to 3 decimals).

        Raises:
            HTTPException: 413 file too large; 415 invalid image; 422 no face found;
                503 core unreachable.
        """
        from PIL import Image, ImageOps, UnidentifiedImageError

        data = await read_upload(file)
        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        except (UnidentifiedImageError, OSError):
            raise HTTPException(415, "Invalid image") from None
        tmp = data_dir / "tmp" / f"search_{uuid.uuid4().hex}.jpg"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        img.save(tmp, "JPEG", quality=92)
        try:
            resp = await asyncio.to_thread(call_core, "embed_face", image_path=str(tmp))
        finally:
            tmp.unlink(missing_ok=True)  # the search photo is never kept
        query = np.asarray(resp["embedding"], dtype=np.float32)
        query /= np.linalg.norm(query) + 1e-9
        window = time_window(start, end, time_from, time_to)
        scored = [(float(np.dot(emb, query)), sid) for sid, emb in db.sighting_embeddings(**window)]
        hits = sorted((h for h in scored if h[0] >= settings.faces.search_threshold), reverse=True)[:min(limit, 500)]
        rows = db.get_sightings([sid for _, sid in hits])
        audit("face_search", user, results=len(hits), filters={"start": start, "end": end, "time_from": time_from,
                                                               "time_to": time_to})
        return [rows[sid] | {"similarity": round(sim, 3)} for sim, sid in hits if sid in rows]

    # --- Event log / status / actions ----------------------------------------------------
    @app.get("/api/events")
    def events(limit: int = 200, type: str | None = None, before_id: int | None = None, start: str | None = None,
               end: str | None = None, time_from: str | None = None, time_to: str | None = None,
               actor: str | None = None, person_id: int | None = None, user: dict = Depends(current_user)):
        """List events from the full log (system, vision, voice and administration), newest first.

        Query params:
            limit: Page size (capped at 1000).
            type: Event type filter.
            before_id: Keyset pagination cursor.
            start, end, time_from, time_to: Time filters (see ``/api/sightings``).
            actor: Author filter (account name, ``cli:<user>``, ``vision``, ``voice``...).
            person_id: Related person filter.
        """
        return db.list_events(min(limit, 1000), type, before_id, actor=actor, person_id=person_id,
                              **time_window(start, end, time_from, time_to))

    # --- Audit of administrative actions -------------------------------------------------
    @app.get("/api/audit")
    def audit_log(limit: int = 200, type: str | None = None, before_id: int | None = None, start: str | None = None,
                  end: str | None = None, time_from: str | None = None, time_to: str | None = None,
                  actor: str | None = None, user: dict = Depends(current_user)):
        """List administrative actions only (audit trail). Same query params as ``/api/events``."""
        return db.list_events(min(limit, 1000), type, before_id, actor=actor, admin_only=True,
                              **time_window(start, end, time_from, time_to))

    @app.get("/api/audit.csv")
    def audit_csv(type: str | None = None, start: str | None = None, end: str | None = None,
                  time_from: str | None = None, time_to: str | None = None, actor: str | None = None,
                  user: dict = Depends(current_user)):
        """Export the audit trail as CSV, including each event's chain hash (``sha256`` column).

        The export itself is audited.
        """
        import json as _json

        rows = db.list_events(1_000_000, type, None, actor=actor, admin_only=True,
                              **time_window(start, end, time_from, time_to))
        out = []
        for r in rows:
            dt = datetime.fromtimestamp(r["ts"])
            d = r["details"]
            out.append([r["id"], dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"), r["actor"], d.get("ip", ""),
                        r["type"], r["person_id"] or "", _json.dumps(d, ensure_ascii=False), r["hash"] or ""])
        audit("events_exported", user, rows=len(rows))
        return csv_response(["id", "date", "time", "user", "ip", "action", "person_id", "details", "sha256"], out,
                            "jarvis-audit")

    @app.get("/api/audit/verify")
    def audit_verify(user: dict = Depends(current_user)):
        """Verify the SHA-256 hash chain of the event log (detects any altered or deleted link).

        Returns:
            ``{"ok": bool, "checked": int, ...}``; on failure also ``broken_at`` and ``reason``.
        """
        result = db.verify_events()
        audit("audit_verified", user, ok=result["ok"], checked=result["checked"], broken_at=result.get("broken_at"))
        return result

    # --- Settings ------------------------------------------------------------------------
    def settings_view() -> dict:
        """Describe every catalog setting, grouped, with effective value, default and override origin."""
        overrides = db.list_settings_overrides()
        groups: dict[str, list] = {}
        for prm in CATALOG:
            ov = overrides.get(prm.key)
            value, default = get_path(settings, prm.key), get_path(base_settings, prm.key)
            source = "parameter" if ov else VALUE_SOURCES.get(prm.key, "file" if value not in ("", None) else "default")
            entry = {
                "key": prm.key, "label": prm.label, "help": prm.help, "type": prm.type, "min": prm.min,
                "max": prm.max, "step": prm.step, "unit": prm.unit, "choices": list(prm.choices), "hot": prm.hot,
                "extra": prm.extra,
                "value": value, "default": default, "source": source,
                "overridden": ov is not None, "updated_at": ov and ov["updated_at"], "updated_by": ov and ov["updated_by"],
            }
            if prm.type == "secret":  # write-only: never send the value back
                entry.update(value=None, default=None, is_set=bool(value))
            groups.setdefault(prm.group, []).append(entry)
        return {"groups": [{"name": g, "params": ps} for g, ps in groups.items()]}

    def after_settings_change(keys: list[str]) -> None:
        """Apply API-side effects of a settings change: reconfigure logging, publish monitoring state."""
        if manage_logging and any(k == "log_level" or k.startswith("logging.") for k in keys):
            from jarvis.core import logs

            logs.configure(settings.log_level, settings.logging, "api")
        if any(k.startswith("monitoring.") for k in keys):
            write_monitoring_state(settings)

    def push_settings_to_core() -> dict:
        """Ask the core to reload settings from the database.

        Returns:
            ``{"core_reloaded": bool, "restart_required": list[str]}``; ``core_reloaded`` is
            False when the core is down (it will read the overrides at its next start).
        """
        try:
            resp = core.call("reload_settings")
            return {"core_reloaded": True, "restart_required": resp.get("restart_required", [])}
        except CoreUnavailable:
            return {"core_reloaded": False, "restart_required": []}

    @app.get("/api/settings")
    def get_settings(user: dict = Depends(current_user)):
        """Return editable settings.

        Returns:
            ``{"groups": [{"name", "params": [{key, label, help, type, min, max, step, unit,
            choices, hot, value, default, overridden, updated_at, updated_by}]}]}``.
        """
        return settings_view()

    @app.put("/api/settings")
    def put_settings(body: SettingsIn, user: dict = Depends(current_user)):
        """Change settings: stored as database overrides, audited, and applied hot when possible.

        Body:
            ``{"changes": {"<key>": value, ...}}`` (catalog keys only).

        Returns:
            ``{"changes": [...], "restart_required": [keys], "core_reloaded": bool}``; when
            nothing actually changes: ``{"changes": [], "restart_required": []}``.

        Raises:
            HTTPException: 422 if a key is unknown or a value fails validation.
        """
        try:
            values = validate_changes(settings, body.changes)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        before = {k: get_path(settings, k) for k in values}
        changes = diff(before, values)
        if not changes:
            return {"changes": [], "restart_required": []}
        changed = {c["field"]: values[c["field"]] for c in changes}   # real values (the diff is masked)
        db.set_settings_overrides(changed, actor=user["username"])
        for c in changes:
            set_path(settings, c["field"], changed[c["field"]])
        result = push_settings_to_core()
        after_settings_change([c["field"] for c in changes])
        restart = sorted({c["field"] for c in changes if not BY_KEY[c["field"]].hot})
        audit("settings_changed", user, changes=changes, restart_required=restart)
        return {"changes": changes, "restart_required": restart, "core_reloaded": result["core_reloaded"]}

    @app.post("/api/settings/reset")
    def reset_settings(body: SettingsResetIn, user: dict = Depends(current_user)):
        """Remove overrides: the given settings go back to their ``config.yaml`` value.

        Body:
            ``{"keys": [catalog keys]}``.

        Returns:
            ``{"changes": [...], "restart_required": [keys]}``.

        Raises:
            HTTPException: 422 if a key is not in the catalog.
        """
        unknown = [k for k in body.keys if k not in BY_KEY]
        if unknown:
            raise HTTPException(422, f"Unknown setting: {unknown}")
        before = {k: get_path(settings, k) for k in body.keys}
        after = {k: get_path(base_settings, k) for k in body.keys}
        db.delete_settings_overrides(body.keys)
        for k, v in after.items():
            set_path(settings, k, v)
        changes = diff(before, after)
        push_settings_to_core()
        after_settings_change(list(body.keys))
        restart = sorted({c["field"] for c in changes if not BY_KEY[c["field"]].hot})
        audit("settings_reset", user, changes=changes, keys=body.keys, restart_required=restart)
        return {"changes": changes, "restart_required": restart}

    # --- Recordings and time-lapse -----------------------------------------------------------------
    @app.get("/api/recordings")
    def recordings(start: str | None = None, end: str | None = None, time_from: str | None = None,
                   time_to: str | None = None, person_id: int | None = None, trigger: str | None = None,
                   limit: int = 100, before_id: int | None = None, user: dict = Depends(current_user)):
        """List event-triggered clips of a period and a daily time window, newest first.

        Query parameters mirror ``/api/sightings``; ``trigger`` filters on person/known/unknown/watchlist.
        """
        return db.list_recordings(person_id=person_id, trigger=trigger, limit=min(limit, 500), before_id=before_id,
                                  **time_window(start, end, time_from, time_to))

    @app.get("/api/recordings/{recording_id}/video")
    def recording_video(recording_id: int, user: dict = Depends(current_user)):
        """Stream a clip (MP4 H.264). HTTP Range requests are honored, so the player can seek."""
        row = db.get_recording(recording_id)
        if row is None or not Path(row["path"]).is_file():
            raise HTTPException(404, "Unknown recording")
        return FileResponse(row["path"], media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})

    @app.delete("/api/recordings/{recording_id}")
    def delete_recording(recording_id: int, user: dict = Depends(current_user)):
        """Delete a clip and its thumbnail (audited)."""
        row = db.delete_recording(recording_id)
        if row is None:
            raise HTTPException(404, "Unknown recording")
        remove_files([f for f in (row["path"], row["thumb_path"]) if f])
        audit("recording_deleted", user, recording_id=recording_id, started=row["start_ts"])
        return {}

    def timelapse_list(start, end, time_from, time_to, max_frames):
        """Resolve the period / daily window and list the matching time-lapse snapshots."""
        from jarvis.vision.recorder import timelapse_frames

        w = time_window(start, end, time_from, time_to)
        return timelapse_frames(data_dir / "timelapse", w["start_ts"], w["end_ts"], w["time_from"], w["time_to"],
                                limit=max(1, min(max_frames, 20000)))

    @app.get("/api/timelapse")
    def timelapse(start: str | None = None, end: str | None = None, time_from: str | None = None,
                  time_to: str | None = None, max_frames: int = 3000, user: dict = Depends(current_user)):
        """Time-lapse snapshots of a period and a daily window (evenly sub-sampled to ``max_frames``).

        Returns:
            ``{"frames": [{"ts": float, "path": str}, ...], "interval_s": float}``; images are fetched
            through ``/api/media``.
        """
        frames = timelapse_list(start, end, time_from, time_to, max_frames)
        return {"frames": [{"ts": ts, "path": str(p)} for ts, p in frames], "interval_s": settings.timelapse.interval_s}

    @app.get("/api/timelapse.mp4")
    async def timelapse_mp4(start: str | None = None, end: str | None = None, time_from: str | None = None,
                            time_to: str | None = None, fps: int = 24, max_frames: int = 5000,
                            user: dict = Depends(current_user)):
        """Render the selected snapshots as an MP4 (FFmpeg concat demuxer) and download it."""
        from starlette.background import BackgroundTask

        frames = timelapse_list(start, end, time_from, time_to, max_frames)
        if not frames:
            raise HTTPException(404, "No snapshot in this period")
        fps = max(1, min(fps, 60))
        work = data_dir / "tmp" / f"timelapse_{uuid.uuid4().hex}"
        work.mkdir(parents=True, exist_ok=True)
        listing, out = work / "frames.txt", work / "timelapse.mp4"
        # Paths come from our own snapshot folder (fixed digits-only names): safe for the concat list.
        listing.write_text("".join(f"file '{p}'\nduration {1 / fps:.4f}\n" for _, p in frames))
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
               "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "-r", str(fps), "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "26", "-movflags", "+faststart", str(out)]
        proc = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            shutil.rmtree(work, ignore_errors=True)
            raise HTTPException(500, f"FFmpeg failed: {err.decode(errors='replace')[-200:]}")
        audit("timelapse_exported", user, frames=len(frames), filters={"start": start, "end": end,
                                                                       "time_from": time_from, "time_to": time_to})
        name = f"jarvis-timelapse-{datetime.now():%Y%m%d-%H%M}.mp4"
        return FileResponse(out, media_type="video/mp4", filename=name,
                            background=BackgroundTask(shutil.rmtree, work, ignore_errors=True))

    # --- Monitoring: Prometheus metrics and agents ---------------------------------------
    def metrics_token() -> str:
        """Return the Bearer token for ``/metrics``, generating and storing it on first use."""
        if not settings.secrets.metrics_token:
            settings.secrets.metrics_token = secrets.token_urlsafe(32)
            db.set_settings_overrides({"secrets.metrics_token": settings.secrets.metrics_token}, actor="system")
        return settings.secrets.metrics_token

    @app.get("/metrics")
    def metrics(request: Request):
        """Prometheus exposition format.

        Direct local access (127.0.0.1:8000, no ``X-Real-IP`` header) is open; requests coming
        through Nginx must present ``Authorization: Bearer <metrics token>`` (compared in
        constant time).

        Raises:
            HTTPException: 404 if metrics are disabled; 401 on a missing/invalid token.
        """
        if not settings.monitoring.metrics_enabled:
            raise HTTPException(404)
        local = request.headers.get("X-Real-IP") is None and request.client and request.client.host in (
            "127.0.0.1", "::1", "testclient")
        if not local and not secrets.compare_digest(request.headers.get("Authorization", ""),
                                                    f"Bearer {metrics_token()}"):
            raise HTTPException(401, "Invalid metrics token")
        try:
            status = core.call("status")
        except CoreUnavailable:
            status = {}
        return Response(render_metrics(settings, db, status), media_type="text/plain; version=0.0.4")

    @app.get("/api/monitoring")
    def monitoring_status(user: dict = Depends(current_user)):
        """Return monitoring configuration for the UI.

        Returns:
            ``{"metrics_enabled", "metrics_token", "metrics_url", "status"}`` where ``status``
            is the last state reported by the root monitoring applier.
        """
        return {"metrics_enabled": settings.monitoring.metrics_enabled, "metrics_token": metrics_token(),
                "metrics_url": "/metrics", "status": read_monitoring_status(settings)}

    @app.post("/api/monitoring/token")
    def monitoring_new_token(user: dict = Depends(current_user)):
        """Rotate the metrics Bearer token (the previous one stops working immediately).

        Returns:
            ``{"metrics_token": str}``.
        """
        token = secrets.token_urlsafe(32)
        settings.secrets.metrics_token = token
        db.set_settings_overrides({"secrets.metrics_token": token}, actor=user["username"])
        audit("metrics_token_rotated", user)
        return {"metrics_token": token}

    @app.post("/api/monitoring/apply")
    def monitoring_apply(user: dict = Depends(current_user)):
        """Republish the desired monitoring state (triggers the root applier service again).

        The API runs unprivileged: it only writes the desired state; a separate root service
        (``jarvis-monitoring-apply``) applies it to the system.

        Returns:
            ``{}``.
        """
        write_monitoring_state(settings)
        audit("monitoring_apply_requested", user)
        return {}

    @app.post("/api/core/restart")
    def restart_core(user: dict = Depends(current_user)):
        """Restart the core service to apply cold (non-hot) settings; systemd respawns it (Restart=always).

        Returns:
            ``{}``.

        Raises:
            HTTPException: 503 if the core is unreachable.
        """
        audit("core_restart_requested", user)
        call_core("restart")
        return {}

    @app.get("/api/status")
    def status(user: dict = Depends(current_user)):
        """Return the core status (camera, vision, tracks, gallery, voice, door...).

        Returns:
            The core's status dict, or ``{"ok": false, "error": str}`` if it is unreachable.
        """
        try:
            return core.call("status")
        except CoreUnavailable as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/garage/pulse")
    def garage_pulse(user: dict = Depends(current_user)):
        """Send a pulse to the garage door relay (logged by the core with the author).

        Returns:
            ``{}``.
        """
        call_core("garage_pulse", actor=user["username"])
        return {}

    @app.post("/api/ptz/home")
    def ptz_home(user: dict = Depends(current_user)):
        """Move the PTZ camera back to its home position.

        Returns:
            ``{}``.
        """
        call_core("ptz_home")
        audit("ptz_home", user)
        return {}

    @app.post("/api/say")
    def say(body: SayIn, user: dict = Depends(current_user)):
        """Speak a text through the core's TTS and speaker.

        Body:
            ``{"text": str}`` (1-200 characters).

        Returns:
            ``{}``.
        """
        call_core("say", text=body.text)
        audit("say", user, text=body.text)
        return {}

    # --- MJPEG video stream --------------------------------------------------------------
    # --- Camera stream test ------------------------------------------------------------
    probe_lock = asyncio.Lock()

    @app.post("/api/camera/probe")
    async def camera_probe(body: ProbeIn, user: dict = Depends(current_user)):
        """Test a camera stream with ffprobe before (or after) saving it in the settings.

        The URL must not carry credentials (same rule as the ``camera.rtsp_url`` setting): the
        camera account of the secrets is injected, as the core does. One probe at a time; the
        result (never the credentials) is audited.

        Returns:
            See :func:`jarvis.web.probe.probe`, plus the tested ``url`` (without credentials).

        Raises:
            HTTPException: 422 on a malformed URL, 429 while another probe runs.
        """
        url = body.url if body.url else settings.camera.rtsp_url
        try:
            check_rtsp_url(url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if probe_lock.locked():
            raise HTTPException(429, "A stream test is already running")
        trial = settings.model_copy(deep=True)
        trial.camera.rtsp_url = url
        async with probe_lock:
            result = await probe.probe(rtsp_url_with_credentials(trial))
        audit("camera_probed", user, url=url, ok=result["ok"], video=result["video"], error=result["error"])
        return {"url": url, **result}

    # --- Logs ----------------------------------------------------------------------------
    def log_filters(q: str, regex: bool, level: str, process: str, start: str | None, end: str | None):
        """Validate the common log filters; ``start``/``end`` are local ISO date-times (``YYYY-MM-DDTHH:MM``)."""
        if level.upper() not in logview.LEVELS:
            raise HTTPException(422, f"level must be one of {', '.join(logview.LEVELS)}")
        if process and not re.fullmatch(r"[\w-]{1,32}", process):
            raise HTTPException(422, "invalid process name")
        try:
            pattern = logview.compile_query(q, regex)
            bounds = [datetime.fromisoformat(v).timestamp() if v else None for v in (start, end)]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return pattern, level.upper(), bounds[0], bounds[1]

    @app.get("/api/logs")
    def logs(q: str = "", regex: bool = False, level: str = "DEBUG", process: str = "",
             start: str | None = None, end: str | None = None, limit: int = Query(500, ge=1, le=5000),
             history: bool = False, user: dict = Depends(current_user)):
        """Search the Jarvis log file (``logging.file_path``): text or regular expression, minimum
        level, process, date-time range, optionally the rotated files. Secrets are masked.

        Returns:
            See :func:`jarvis.web.logview.search`, plus ``file`` (the log path).
        """
        log_filters(q, regex, level, process, start, end)          # validation, 422 on error
        path = Path(settings.logging.file_path)
        if not settings.logging.file_enabled:
            raise HTTPException(409, "The log file is disabled (Settings > Logging)")
        start_ts = datetime.fromisoformat(start).timestamp() if start else None
        end_ts = datetime.fromisoformat(end).timestamp() if end else None
        result = logview.search(path, q, regex, level.upper(), process, start_ts, end_ts, limit, history)
        return {"file": str(path), **result}

    @app.get("/api/logs/stream")
    async def logs_stream(request: Request, q: str = "", regex: bool = False, level: str = "DEBUG",
                          process: str = "", user: dict = Depends(current_user)):
        """Live tail of the log as Server-Sent Events (``text/event-stream``).

        Each ``data:`` message is a JSON list of new entries matching the filters; a comment
        line is sent every 15 s to keep proxies from closing the connection. The session is
        re-checked every 5 s (the stream stops on logout, revocation or idle timeout, and does
        not itself count as activity); log rotation is followed.
        """
        pattern, lvl, _, _ = log_filters(q, regex, level, process, None, None)
        follower = logview.Follower(Path(settings.logging.file_path))
        token = request.cookies.get(COOKIE)

        async def gen():
            last_check = last_beat = time.monotonic()
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                now = time.monotonic()
                if now - last_check > 5:
                    last_check = now
                    try:
                        await asyncio.to_thread(check_session, token, False)
                    except HTTPException:
                        yield "event: end\ndata: session\n\n"
                        return
                batch = [e for e in await asyncio.to_thread(follower.poll)
                         if e.get("continuation") or logview.matches(e, pattern, lvl, process, None, None)]
                if batch:
                    yield f"data: {json.dumps(batch)}\n\n"
                elif now - last_beat > 15:
                    last_beat = now
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/stream.mjpg")
    async def stream(request: Request, user: dict = Depends(current_user)):
        """Live preview as ``multipart/x-mixed-replace`` MJPEG.

        The core writes the latest annotated frame to ``vision.frame_path``; this endpoint
        pushes it whenever its mtime changes, at most ``vision.preview_fps`` times per second.
        The session is re-checked every 5 s so the stream stops on logout, revocation or idle
        timeout (the stream itself does not count as activity).
        """
        frame_path = Path(settings.vision.frame_path)
        interval = 1 / settings.vision.preview_fps
        token = request.cookies.get(COOKIE)

        async def gen():
            # Browsers display a multipart part only when the NEXT boundary arrives: the boundary
            # is therefore written right after each image (not before the next one), and the
            # current image is re-sent every 2 s even when unchanged, so a frozen or reconnecting
            # camera still shows its last frame instead of a black box.
            last_mtime, last_check, last_sent = 0.0, time.monotonic(), 0.0
            yield b"--frame\r\n"
            while not await request.is_disconnected():
                if time.monotonic() - last_check > 5:  # the stream ends with the session (idle, revoked)
                    last_check = time.monotonic()
                    try:
                        await asyncio.to_thread(check_session, token, False)
                    except HTTPException:
                        return
                try:
                    mtime = frame_path.stat().st_mtime
                    if mtime != last_mtime or time.monotonic() - last_sent > 2:
                        last_mtime, last_sent = mtime, time.monotonic()
                        jpg = frame_path.read_bytes()
                        yield (b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode()
                               + b"\r\n\r\n" + jpg + b"\r\n--frame\r\n")
                except FileNotFoundError:
                    pass
                await asyncio.sleep(interval)

        # X-Accel-Buffering: no -> Nginx must not buffer the stream.
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def run(settings: Settings) -> None:
    """Run the API with uvicorn (entry point of ``jarvis api`` / ``jarvis-api.service``).

    Proxy headers are trusted only from ``api.forwarded_allow_ips`` (127.0.0.1, the local nginx, by default).
    """
    import uvicorn

    uvicorn.run(create_app(settings, manage_logging=True), host=settings.api.host, port=settings.api.port,
                proxy_headers=True, forwarded_allow_ips=settings.api.forwarded_allow_ips,
                log_level=settings.log_level.lower())
