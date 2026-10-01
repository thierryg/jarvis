# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/notify.py
# Purpose : Non-blocking outgoing webhook notifications (JSON POST)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Outgoing notifications: JSON POST to a local webhook, never blocking the core.

Works with Home Assistant (``webhook`` trigger), Node-RED, n8n and ntfy (the JSON body
becomes the message). Notifications are queued and sent from a dedicated thread; when
the queue is full, new notifications are dropped rather than slowing down the caller.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.request
from datetime import datetime

from jarvis.config.settings import NotificationsConfig, SecretsConfig

log = logging.getLogger(__name__)


class WebhookNotifier(threading.Thread):
    """Background webhook sender, used as a callable ``notifier(event_type, **payload)``.

    Attributes:
        cfg: Notification settings (webhook URL, enabled event types, timeout).
    """

    def __init__(self, cfg: NotificationsConfig, secrets: SecretsConfig | None = None):
        """Initialize the notifier.

        Args:
            cfg: Notification settings.
            secrets: Credential store; ``webhook_token`` is sent as a bearer token when set.
        """
        super().__init__(name="notify", daemon=True)
        self.cfg = cfg
        self.secrets = secrets
        self._q: queue.Queue[dict | None] = queue.Queue(maxsize=100)

    @property
    def token(self) -> str:
        """Current webhook bearer token (read at each send: rotating it takes effect immediately)."""
        return self.secrets.webhook_token if self.secrets else ""

    @property
    def enabled(self) -> bool:
        """Whether a webhook URL is configured."""
        return bool(self.cfg.webhook_url)

    def __call__(self, event_type: str, **payload) -> None:
        """Queue a notification if enabled and ``event_type`` is subscribed.

        Never blocks: the notification is dropped (with a warning) if the queue is full.

        Args:
            event_type: Event type name (e.g. ``"face_unknown"``).
            **payload: Extra fields merged into the JSON body.
        """
        if not self.enabled or event_type not in self.cfg.events:
            return
        now = time.time()
        msg = {"source": "jarvis", "type": event_type, "ts": now,
               "datetime": datetime.fromtimestamp(now).isoformat(timespec="seconds")} | payload
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            log.warning("Notification queue full: %s dropped", event_type)

    def stop(self) -> None:
        """Stop the sender thread once already queued notifications are processed."""
        self._q.put(None)

    def run(self) -> None:
        """Send queued notifications until the ``None`` sentinel is received."""
        while (msg := self._q.get()) is not None:
            if not self.cfg.webhook_url.lower().startswith(("http://", "https://")):
                # config.yaml or the environment could hold file:// or other schemes: never open them.
                log.error("Webhook URL rejected (only http:// and https:// are allowed)")
                continue
            headers = {"Content-Type": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            req = urllib.request.Request(self.cfg.webhook_url, data=json.dumps(msg, default=str).encode(),  # noqa: S310  # nosec B310
                                         headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as r:  # noqa: S310  # nosec B310
                    r.read()
            except Exception as exc:  # network error, HTTP 4xx/5xx: log and move on
                log.warning("Webhook %s failed: %s", msg["type"], exc)
