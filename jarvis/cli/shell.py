# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/cli/shell.py
# Purpose : cmd2-based administration shell (interactive or single-command mode)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Jarvis administration shell, built on cmd2.

- ``jarvis`` alone: interactive shell (Tab completion, persistent history, help by category,
  aliases/macros, ``run_script`` scripts, ``>`` and ``|`` redirection).
- ``jarvis <command> [arguments]``: runs a single command and propagates its exit code
  (used by systemd: ``jarvis core``, ``jarvis check-config core``, ``jarvis backup``...).

Command names accept either hyphens or underscores (``check-config`` = ``check_config``).
Every change (persons, settings, sessions...) is recorded in the audit log with the author
``cli:<Unix user>`` and a before -> after diff.

Exit codes: 0 on success, 1 on a command failure, 2 when argument parsing fails (or, for
``audit-verify``, when the hash chain is broken).

The shell accesses the database directly and talks to the core over its control socket; it is
meant to be run by an operator with local access to the appliance.
"""

from __future__ import annotations

import csv
import functools
import getpass
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cmd2
from cmd2 import Cmd2ArgumentParser, with_argparser, with_category
from rich.table import Table

from jarvis import __version__
from jarvis.cli import tasks
from jarvis.config.settings import VALUE_SOURCES, Settings

# Help categories (shown by ``help``).
SERVICES, ADMIN, TRACE, SETTINGS, MAINT = ("Services", "Administration", "Traceability", "Settings",
                                          "Maintenance")
HISTORY = Path.home() / ".jarvis_history"


def ok(func):
    """Mark successful argument parsing by resetting the exit code to 0 before the command runs.

    ``run`` presets the exit code to 2; cmd2 only calls the command body once its argparse
    parser has succeeded, so reaching the wrapper means parsing went fine.
    """
    @functools.wraps(func)
    def wrapper(self, *a, **k):
        self.exit_code = 0
        return func(self, *a, **k)
    return wrapper


def fmt_ts(ts: float | None) -> str:
    """Format a Unix timestamp as local ``YYYY-MM-DD HH:MM:SS`` (an em dash when missing)."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "—"


def parse_day(value: str | None, end: bool = False) -> float | None:
    """Convert ``YYYY-MM-DD`` (local time) to the timestamp of the day's start.

    Args:
        value: Date string, or None/empty for "no bound".
        end: Return the start of the next day instead (exclusive bound that includes ``value``).

    Raises:
        ValueError: If the date is malformed.
    """
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").timestamp() + (86400 if end else 0)


def parse_bool(value: str) -> bool:
    """Parse a boolean typed by the operator (English words, digits; French words kept for compatibility).

    Raises:
        ValueError: If the value is not a recognized boolean.
    """
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on", "oui", "vrai"):
        return True
    if v in ("0", "false", "no", "off", "non", "faux"):
        return False
    raise ValueError(f"boolean expected: {value}")


def add_period_args(p: Cmd2ArgumentParser) -> None:
    """Add the common ``--start/--end/--from/--to`` time filter options to a parser."""
    p.add_argument("--start", metavar="YYYY-MM-DD", help="first day included (local time)")
    p.add_argument("--end", metavar="YYYY-MM-DD", help="last day included")
    p.add_argument("--from", dest="time_from", metavar="HH:MM", help="start of the daily time slot")
    p.add_argument("--to", dest="time_to", metavar="HH:MM", help="end of the time slot (may wrap past midnight)")


class JarvisShell(cmd2.Cmd):
    """cmd2 shell: each ``do_*`` method is a command, documented by its docstring and its parser.

    Attributes:
        settings: Loaded settings (``config.yaml``; database overrides are applied per command).
        exit_code: Exit code of the last command, returned by :func:`run`.
    """

    def __init__(self, settings: Settings, interactive: bool):
        """Create the shell.

        Args:
            settings: Loaded settings.
            interactive: Enable persistent history and auto-suggestion (interactive session).
        """
        super().__init__(allow_cli_args=False, auto_suggest=interactive,
                         persistent_history_file=str(HISTORY) if interactive else "",
                         suggest_similar_command=True)
        self.settings = settings
        self.prompt = "jarvis> "
        self.intro = (f"Jarvis {__version__} - administration shell. \"help\" lists the commands, "
                      "\"help <command>\" describes one, Tab completes, Ctrl+D exits.")
        self._db = None
        # Generic cmd2 commands are useful but secondary: they get their own category.
        self.default_category = "Shell (cmd2)"

    @property
    def db(self):
        """Database handle, opened lazily so commands that do not need it (e.g. ``core``) stay light."""
        if self._db is None:
            self._db = tasks.open_db(self.settings)
        return self._db

    def table(self, title: str, columns: list[str], rows: list[list]) -> None:
        """Print a Rich table (``None`` cells are shown empty)."""
        t = Table(*columns, title=title, title_justify="left", header_style="bold")
        for r in rows:
            t.add_row(*("" if v is None else str(v) for v in r))
        self.poutput(t)

    def fail(self, message: str, code: int = 1) -> None:
        """Print an error on stderr and set the exit code."""
        self.perror(message)
        self.exit_code = code

    def audit(self, type_: str, person_id: int | None = None, **details) -> None:
        """Record an administrative action with author ``cli:<Unix user>``, IP ``local`` and ``via=cli``."""
        self.db.log_event(type_, actor=tasks.actor(), person_id=person_id, ip="local", via="cli", **details)

    # ========================================================================================= Services
    @with_category(SERVICES)
    @ok
    def do_core(self, _):
        """Start the core service (vision, voice, decision, hardware). Launched by systemd."""
        tasks.run_core(self.settings)

    @with_category(SERVICES)
    @ok
    def do_api(self, _):
        """Start the web API (FastAPI on 127.0.0.1:8000). Launched by systemd."""
        tasks.run_api(self.settings)

    @with_category(SERVICES)
    @ok
    def do_console(self, _):
        """Local appliance console (pfSense-style menu), intended for tty1."""
        from jarvis.console import main as console_main

        console_main(self.settings)

    @with_category(SERVICES)
    @ok
    def do_status(self, _):
        """Core status: camera, vision, tracks, gallery, voice, door, authorized persons."""
        st = tasks.notify_core(self.settings, "status")
        if not st or not st.get("ok"):
            return self.fail("core unreachable (service stopped?)")
        rows = [[k, json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v] for k, v in st.items() if k != "ok"]
        self.table("Core status", ["Indicator", "Value"], rows)

    # =================================================================================== Administration
    user_parser = Cmd2ArgumentParser(description="Create a web account or reset its password.")
    user_parser.add_argument("username", help="account name")

    @with_category(ADMIN)
    @with_argparser(user_parser)
    @ok
    def do_create_user(self, args):
        """Create a web administration account (password asked twice, 12 characters minimum)."""
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Confirm: "):
            return self.fail("passwords do not match")
        try:
            tasks.set_user_password(self.settings, args.username, pw)
        except ValueError as exc:
            return self.fail(str(exc))
        self.psuccess(f"Account \"{args.username}\" saved.")

    reset_admin_parser = Cmd2ArgumentParser(
        description="Factory reset of the web account admin: password 'admin', change forced at the next sign-in.")
    reset_admin_parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    @with_category(ADMIN)
    @with_argparser(reset_admin_parser)
    @ok
    def do_reset_admin(self, args):
        """Restore admin/admin (mandatory change at the next web sign-in) and close its web sessions."""
        from argon2 import PasswordHasher

        if not args.yes and self.read_input("Reset the web account 'admin' to 'admin'? [y/N] ").lower() not in ("y", "yes"):
            return
        self.db.add_user("admin", PasswordHasher().hash("admin"), must_change=True)
        closed = self.db.end_user_sessions(self.db.get_user_by_name("admin")["id"], "revoked")
        self.audit("admin_factory_reset", sessions_closed=closed)
        self.psuccess("Account 'admin' reset to admin/admin (change forced at the next sign-in).")

    @with_category(ADMIN)
    @ok
    def do_users(self, _):
        """List web accounts and their last login."""
        with self.db.connect() as c:
            rows = c.execute("SELECT u.username, u.created_at, MAX(s.created_at) AS last FROM users u "
                             "LEFT JOIN sessions s ON s.user_id = u.id GROUP BY u.id ORDER BY u.username").fetchall()
        self.table("Web accounts", ["Account", "Created", "Last login"],
                   [[r["username"], fmt_ts(r["created_at"]), fmt_ts(r["last"])] for r in rows])

    @with_category(ADMIN)
    @ok
    def do_persons(self, _):
        """List enrolled persons with their rights, access rules and last sighting."""
        rows = []
        for p in self.db.list_persons():
            rule = " ".join(x for x in (p["access_days"] and f"days {p['access_days']}",
                                        p["access_start"] and f"{p['access_start']}-{p['access_end']}",
                                        p["valid_until"] and f"until {fmt_ts(p['valid_until'])[:10]}") if x)
            rows.append([p["id"], f"{p['first_name']} {p['last_name']}".strip(), "yes" if p["can_open_garage"] else "no",
                         "YES" if p["watchlist"] else "", rule or "permanent", p["face_count"], p["voice_count"],
                         fmt_ts(p["last_seen"])])
        self.table("Persons", ["#", "Name", "Garage", "Watchlist", "Rules", "Faces", "Voice", "Last seen"], rows)

    person_parser = Cmd2ArgumentParser(description="Create or update a person (omitted fields are left unchanged).")
    person_parser.add_argument("id", nargs="?", type=int, help="person id (omit to create)")
    person_parser.add_argument("--first-name")
    person_parser.add_argument("--last-name")
    person_parser.add_argument("--garage", choices=("yes", "no"), help="allowed to open the garage")
    person_parser.add_argument("--watchlist", choices=("yes", "no"), help="watchlist")
    person_parser.add_argument("--days", metavar="1234567", help="allowed ISO weekdays (empty = all)")
    person_parser.add_argument("--hours", metavar="HH:MM-HH:MM", help="time slot (\"-\" = none)")
    person_parser.add_argument("--until", metavar="YYYY-MM-DD", help="access expiration (\"never\" = permanent)")

    @with_category(ADMIN)
    @with_argparser(person_parser)
    @ok
    def do_person(self, args):
        """Create (without an id) or update a person; updates are audited with their diff."""
        from jarvis.config.catalog import diff
        from jarvis.storage.database import person_snapshot

        fields: dict = {}
        if args.first_name:
            fields["first_name"] = args.first_name
        if args.last_name is not None:
            fields["last_name"] = args.last_name
        if args.garage:
            fields["can_open_garage"] = args.garage == "yes"
        if args.watchlist:
            fields["watchlist"] = args.watchlist == "yes"
        if args.days is not None:
            if not set(args.days) <= set("1234567"):
                return self.fail("--days: digits 1 (Monday) to 7 (Sunday)")
            fields["access_days"] = args.days
        if args.hours is not None:
            start, _, end = args.hours.partition("-") if args.hours != "-" else ("", "", "")
            fields["access_start"], fields["access_end"] = start, end
        clear = False
        if args.until:
            if args.until == "never":
                clear = True
            else:
                # Inclusive: access is valid until the last second of the given day.
                fields["valid_until"] = parse_day(args.until, end=True) - 1
        if args.id is None:
            if "first_name" not in fields:
                return self.fail("--first-name is required to create a person")
            pid = self.db.add_person(**fields)
            self.audit("person_created", person_id=pid, values=person_snapshot(self.db.get_person(pid)))
            self.psuccess(f"Person #{pid} created.")
        else:
            before = self.db.get_person(args.id)
            if before is None:
                return self.fail(f"unknown person #{args.id}")
            self.db.update_person(args.id, clear_valid_until=clear, **fields)
            changes = diff(person_snapshot(before), person_snapshot(self.db.get_person(args.id)))
            if changes:
                self.audit("person_updated", person_id=args.id, changes=changes)
            self.table(f"Person #{args.id}: diff", ["Field", "Before", "After"],
                       [[c["field"], c["before"], c["after"]] for c in changes])
        tasks.notify_core(self.settings, "reload_faces")

    delete_parser = Cmd2ArgumentParser(description="Delete a person and ALL their data (right to erasure).")
    delete_parser.add_argument("id", type=int)
    delete_parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    @with_category(ADMIN)
    @with_argparser(delete_parser)
    @ok
    def do_person_delete(self, args):
        """Erase the person, their faces, voice profiles, sightings and photos."""
        from jarvis.core.service import remove_files
        from jarvis.storage.database import person_snapshot

        p = self.db.get_person(args.id)
        if p is None:
            return self.fail(f"unknown person #{args.id}")
        # "o"/"oui" are still accepted for operators used to the former French prompt.
        if not args.yes and self.read_input(f"Delete {p.first_name} {p.last_name}? [y/N] ").lower() not in ("o", "oui", "y"):
            return
        remove_files(self.db.delete_person(args.id))
        self.audit("person_deleted", deleted_person_id=args.id, values=person_snapshot(p))
        tasks.notify_core(self.settings, "reload_faces")
        tasks.notify_core(self.settings, "reload_voices")
        self.psuccess("Person deleted.")

    enroll_parser = Cmd2ArgumentParser(description="Offline enrollment from photos (installation).")
    enroll_parser.add_argument("--first-name", required=True)
    enroll_parser.add_argument("--last-name", default="")
    enroll_parser.add_argument("--can-open-garage", action="store_true")
    enroll_parser.add_argument("images", nargs="+", completer=cmd2.Cmd.path_complete)

    @with_category(ADMIN)
    @with_argparser(enroll_parser)
    @ok
    def do_enroll_face(self, args):
        """Create a person and encode their photos without going through the core."""
        pid = tasks.enroll_face(self.settings, args.first_name, args.last_name, args.can_open_garage, args.images,
                                report=self.poutput)
        self.psuccess(f"Person #{pid} created.")

    sessions_parser = Cmd2ArgumentParser(description="Web login history (IP, start, end, reason).")
    sessions_parser.add_argument("--active", action="store_true", help="open sessions only")
    sessions_parser.add_argument("--limit", type=int, default=50)

    @with_category(ADMIN)
    @with_argparser(sessions_parser)
    @ok
    def do_sessions(self, args):
        """Web sessions: user, source IP, browser, login, last activity, logout and reason."""
        rows = []
        for s in self.db.list_sessions(limit=args.limit):
            if args.active and s["ended_at"]:
                continue
            dur = round(((s["ended_at"] or time.time()) - (s["created_at"] or time.time())) / 60)
            rows.append([s["id"], s["username"], s["ip"], (s["user_agent"] or "")[:30], fmt_ts(s["created_at"]),
                         fmt_ts(s["last_seen"]), fmt_ts(s["ended_at"]), s["end_reason"] or "active", f"{dur} min"])
        self.table("Web sessions", ["#", "Account", "IP", "Browser", "Login", "Activity", "Logout",
                                    "Reason", "Duration"], rows)

    revoke_parser = Cmd2ArgumentParser(description="Revoke an open web session.")
    revoke_parser.add_argument("id", type=int)

    @with_category(ADMIN)
    @with_argparser(revoke_parser)
    @ok
    def do_session_revoke(self, args):
        """Close a web session immediately (the user is logged out)."""
        token = self.db.session_token_by_id(args.id)
        if not token or not self.db.end_session(token, "revoked"):
            return self.fail("unknown or already closed session")
        self.audit("session_revoked", revoked_session_id=args.id)
        self.psuccess(f"Session #{args.id} revoked.")

    # ==================================================================================== Traceability
    sightings_parser = Cmd2ArgumentParser(description="Timestamped sightings, between two dates and within a time slot.")
    add_period_args(sightings_parser)
    sightings_parser.add_argument("--person", type=int, help="person id")
    sightings_parser.add_argument("--status", choices=("known", "unknown", "labeled", "watchlist"))
    sightings_parser.add_argument("--limit", type=int, default=100)
    sightings_parser.add_argument("--csv", metavar="FILE", completer=cmd2.Cmd.path_complete, help="CSV export")

    @with_category(TRACE)
    @with_argparser(sightings_parser)
    @ok
    def do_sightings(self, args):
        """List (or export as CSV) sightings: date, time, identity, status, score, track."""
        rows = self.db.list_sightings(parse_day(args.start), parse_day(args.end, True), args.person, args.status,
                                      limit=args.limit if not args.csv else 1_000_000, time_from=args.time_from,
                                      time_to=args.time_to)
        out = [[r["id"], fmt_ts(r["ts"])[:10], fmt_ts(r["ts"])[11:], r["status"],
                f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or (f"unknown #{r['cluster_id']}" if r["cluster_id"] else "unknown"),
                r["score"], r["track_id"]] for r in rows]
        if args.csv:
            with open(args.csv, "w", newline="", encoding="utf-8") as f:
                csv.writer(f, delimiter=";").writerows([["id", "date", "time", "status", "person", "score", "track"], *out])
            self.audit("sightings_exported", rows=len(out), file=args.csv)
            return self.psuccess(f"{len(out)} sighting(s) exported to {args.csv}")
        self.table(f"Sightings ({len(out)})", ["#", "Date", "Time", "Status", "Identity", "Score", "Track"], out)

    events_parser = Cmd2ArgumentParser(description="Event log (or the audit trail of administrative actions).")
    add_period_args(events_parser)
    events_parser.add_argument("--type")
    events_parser.add_argument("--actor", help="author (account, cli:root, vision, voice...)")
    events_parser.add_argument("--admin", action="store_true", help="administrative actions only (audit)")
    events_parser.add_argument("--limit", type=int, default=50)

    @with_category(TRACE)
    @with_argparser(events_parser)
    @ok
    def do_events(self, args):
        """Filterable event log; "--admin": audit trail (author, IP, diffs)."""
        rows = self.db.list_events(args.limit, args.type, None, start_ts=parse_day(args.start),
                                   end_ts=parse_day(args.end, True), time_from=args.time_from, time_to=args.time_to,
                                   actor=args.actor, admin_only=args.admin)
        out = []
        for e in rows:
            d = dict(e["details"])
            ip = d.pop("ip", "")
            changes = d.pop("changes", None)
            # Show diffs as "field: before → after"; other details as truncated JSON.
            detail = "; ".join(f"{c['field']}: {c['before']!r} → {c['after']!r}" for c in changes) if changes else \
                json.dumps(d, ensure_ascii=False)[:90]
            out.append([e["id"], fmt_ts(e["ts"]), e["actor"], ip, e["type"], e["first_name"] or "", detail])
        self.table("Audit" if args.admin else "Event log", ["#", "Date", "Author", "IP", "Type", "Person", "Details"], out)

    @with_category(TRACE)
    @ok
    def do_audit_verify(self, _):
        """Verify the SHA-256 hash chain of the log (detects any altered or deleted link). Exit code 2 if broken."""
        r = self.db.verify_events()
        self.audit("audit_verified", ok=r["ok"], checked=r["checked"], broken_at=r.get("broken_at"))
        if r["ok"]:
            self.psuccess(f"Chain intact: {r['checked']} sealed events ({r['unsealed']} predating the "
                          f"chain). Last hash: {r.get('last_hash')}")
        else:
            self.fail(f"CHAIN BROKEN at event #{r['broken_at']}: {r['reason']}", code=2)

    # ======================================================================================== Settings
    settings_parser = Cmd2ArgumentParser(description="List editable settings and their effective value.")
    settings_parser.add_argument("group", nargs="?", help="group filter (substring)")

    @with_category(SETTINGS)
    @with_argparser(settings_parser)
    @ok
    def do_settings(self, args):
        """Settings: key, effective value, config.yaml value, origin, hot-applied or not."""
        from jarvis.config.catalog import CATALOG, apply_overrides, get_path

        base = self.settings.model_copy(deep=True)
        eff = self.settings.model_copy(deep=True)
        overrides = self.db.list_settings_overrides()
        apply_overrides(eff, {k: v["value"] for k, v in overrides.items()})
        rows = []
        for p in CATALOG:
            if args.group and args.group.lower() not in p.group.lower():
                continue
            ov = overrides.get(p.key)
            value, file_value = get_path(eff, p.key), get_path(base, p.key)
            if p.type == "secret":  # write-only: show whether it is set, never the value
                value, file_value = ("(set)" if value else "(empty)"), ("(set)" if file_value else "(empty)")
            origin = f"parameter ({ov['updated_by']})" if ov else VALUE_SOURCES.get(p.key, "config.yaml")
            rows.append([p.group, p.key, value, file_value, origin, "" if p.hot else "restart"])
        self.table("Settings", ["Group", "Key", "Value", "config.yaml", "Origin", "Effect"], rows)

    set_parser = Cmd2ArgumentParser(description="Change a setting (database override, audited, applied hot).")
    set_parser.add_argument("key", help="key, e.g. faces.match_threshold or secrets.camera_password")
    set_parser.add_argument("value", help="value; lists separated by \";\"; booleans yes/no; \"-\" prompts for a secret")

    @with_category(SETTINGS)
    @with_argparser(set_parser)
    @ok
    def do_set(self, args):
        """Change a catalog setting; print the diff and flag whether a restart is needed."""
        from jarvis.config.catalog import BY_KEY, apply_overrides, diff, get_path, validate_changes

        p = BY_KEY.get(args.key)
        if p is None:
            return self.fail(f"setting not editable: {args.key} (see \"settings\")")
        raw: object = args.value
        if p.type == "secret" and args.value == "-":  # read the secret without echoing it
            raw = getpass.getpass(f"{args.key}: ")
        try:
            if p.type == "bool":
                raw = parse_bool(args.value)
            elif p.type in ("list", "events", "multi"):
                raw = [x.strip() for x in args.value.split(";") if x.strip()]
            eff = self.settings.model_copy(deep=True)
            apply_overrides(eff, self.db.get_settings_overrides())
            values = validate_changes(eff, {args.key: raw})
        except ValueError as exc:
            return self.fail(str(exc))
        changes = diff({args.key: get_path(eff, args.key)}, values)
        if not changes:
            return self.poutput("Value unchanged.")
        self.db.set_settings_overrides(values, actor=tasks.actor())
        self.audit("settings_changed", changes=changes, restart_required=[] if p.hot else [args.key])
        tasks.notify_core(self.settings, "reload_settings")
        c = changes[0]
        self.psuccess(f"{c['field']}: {c['before']!r} → {c['after']!r}"
                      + ("" if p.hot else "  (service restart required)"))

    reset_parser = Cmd2ArgumentParser(description="Remove overrides: revert to config.yaml values.")
    reset_parser.add_argument("keys", nargs="+", help="keys, or \"all\"")

    @with_category(SETTINGS)
    @with_argparser(reset_parser)
    @ok
    def do_reset(self, args):
        """Restore the config.yaml value for the given keys (or all of them: "reset all").

        Internal ``_secret.*`` entries (e.g. the metrics token) are never touched by "all".
        """
        overrides = self.db.get_settings_overrides()
        keys = [k for k in overrides if not k.startswith("_secret.")] if args.keys == ["all"] else args.keys
        self.db.delete_settings_overrides(keys)
        self.audit("settings_reset", keys=keys)
        tasks.notify_core(self.settings, "reload_settings")
        self.psuccess(f"{len(keys)} setting(s) restored.")

    # ===================================================================================== Maintenance
    check_parser = Cmd2ArgumentParser(description="Check the configuration and models (ExecStartPre).")
    check_parser.add_argument("target", nargs="?", choices=("core", "api"), default="core")

    @with_category(MAINT)
    @with_argparser(check_parser)
    @ok
    def do_check_config(self, args):
        """Pre-start check: exit code 1 if a blocking item is missing."""
        r = tasks.check_config(self.settings, args.target)
        for w in r.warnings:
            self.pwarning(f"WARNING: {w}")
        for pb in r.problems:
            self.perror(f"ERROR: {pb}")
        if r.ok:
            self.psuccess("Configuration is valid.")
        else:
            self.exit_code = 1

    backup_parser = Cmd2ArgumentParser(description="Hot backup of the database and media, with rotation.")
    backup_parser.add_argument("--dest", default="/var/backups/jarvis", completer=cmd2.Cmd.path_complete)
    backup_parser.add_argument("--keep", type=int, default=14, help="archives to keep (default 14)")
    backup_parser.add_argument("--list", action="store_true", help="list archives without backing up")

    @with_category(MAINT)
    @with_argparser(backup_parser)
    @ok
    def do_backup(self, args):
        """Create a jarvis-YYYYMMDD-HHMMSS.tar.gz archive (mode 600) or list existing archives."""
        if not args.list:
            r = tasks.backup(self.settings, args.dest, args.keep)
            self.psuccess(f"Backup: {r.archive} ({r.size / 1e6:.1f} MB), {len(r.removed)} old archive(s) removed")
        self.table("Archives", ["File", "Size", "Date"],
                   [[f.name, f"{f.stat().st_size / 1e6:.1f} MB", fmt_ts(f.stat().st_mtime)]
                    for f in tasks.list_backups(args.dest)])

    @with_category(MAINT)
    @ok
    def do_setup_models(self, _):
        """Download and export the models (the only step that requires Internet access)."""
        tasks.setup_models(self.settings)

    say_parser = Cmd2ArgumentParser(description="Test speech synthesis and the speaker.")
    say_parser.add_argument("text", nargs="+")

    @with_category(MAINT)
    @with_argparser(say_parser)
    @ok
    def do_say(self, args):
        """Speak the given text (Piper)."""
        tasks.say(self.settings, " ".join(args.text))

    hw_parser = Cmd2ArgumentParser(description="Test the door sensor and LEDs; --pulse triggers the door.")
    hw_parser.add_argument("--pulse", action="store_true", help="also send a pulse to the door")

    @with_category(MAINT)
    @with_argparser(hw_parser)
    @ok
    def do_test_hardware(self, args):
        """Hardware test (stop jarvis-core first: it owns the devices)."""
        tasks.test_hardware(self.settings, args.pulse, report=self.poutput)

    @with_category(MAINT)
    @ok
    def do_monitoring_apply(self, _):
        """(root) Apply the desired monitoring state (run by jarvis-monitoring-apply.service)."""
        r = tasks.monitoring_apply(self.settings)
        self.poutput(r["message"])
        self.exit_code = 1 if r["errors"] else 0

    logs_parser = Cmd2ArgumentParser(description="Show the tail of the log file.")
    logs_parser.add_argument("-n", type=int, default=40, help="number of lines")

    @with_category(MAINT)
    @with_argparser(logs_parser)
    @ok
    def do_logs(self, args):
        """Last lines of /var/log/jarvis/jarvis.log (see also "journalctl -u jarvis-core")."""
        path = Path(self.settings.logging.file_path)
        if not path.exists():
            return self.fail(f"{path} not found (file logging disabled?)")
        self.ppaged("".join(path.read_text(errors="replace").splitlines(keepends=True)[-args.n:]))

    resources_parser = Cmd2ArgumentParser(description="Live CPU, RAM, network, disk I/O and GPU usage.")
    resources_parser.add_argument("--watch", type=float, metavar="SECONDS",
                                  help="refresh every SECONDS until Ctrl+C (default: one sample)")
    resources_parser.add_argument("--hardware", action="store_true", help="also list the CPU/GPU support")

    @with_category(MAINT)
    @with_argparser(resources_parser)
    @ok
    def do_resources(self, args):
        """CPU current/max/free, RAM total/used/free, per-interface network in/out, disk I/O, GPU, temperatures."""
        from jarvis.core.sysinfo import ResourceSampler, detect_capabilities, render_resources

        caps = detect_capabilities() if args.hardware else None
        sampler = ResourceSampler()
        try:
            while True:
                time.sleep(args.watch or 1.0)
                rows = render_resources(sampler.sample(), caps)
                if args.watch:
                    self.poutput("\033[2J\033[H", end="")
                self.table(f"System resources · {datetime.now():%H:%M:%S}", ["Section", "Metric", "Value"],
                           [list(r) for r in rows])
                if not args.watch:
                    break
        except KeyboardInterrupt:
            pass

    @with_category(MAINT)
    @ok
    def do_hardware(self, _):
        """Hardware support detected (CPU instruction sets, GPUs, OpenVINO devices, ONNX providers, VAAPI)
        and the choices of the automatic performance tuning."""
        from jarvis.core.sysinfo import choose, detect_capabilities, render_capabilities

        caps = detect_capabilities()
        self.table("Hardware support", ["Section", "Item", "Value"], [list(r) for r in render_capabilities(caps)])
        live = tasks.notify_core(self.settings, "system")
        if live and live.get("ok"):
            perf = live["performance"]
            rows = [["applied", k, v] for k, v in perf["applied"].items()] + [["kept (set by you)", k, ""] for k in perf["kept"]]
            self.table("Performance tuning in effect (running core)", ["Status", "Setting", "Value"], rows)
            for why in perf["why"]:
                self.poutput(f"  • {why}")
        else:
            for why in choose(caps)["_why"]:
                self.poutput(f"  • {why}")
            self.pwarning("core not running: these are the choices it would make at start-up")

    @with_category(MAINT)
    @ok
    def do_version(self, _):
        """Version of Jarvis and its main components."""
        import platform

        self.poutput(f"jarvis-home {__version__} · Python {platform.python_version()} · {platform.platform()}")


def run(settings: Settings, argv: list[str]) -> int:
    """Run one command (non-empty ``argv``) or open the interactive shell.

    Args:
        settings: Loaded settings.
        argv: Command and its arguments; empty for the interactive shell. The command name may
            use hyphens (``check-config``), which are mapped to cmd2's underscores.

    Returns:
        The process exit code.
    """
    interactive = not argv
    shell = JarvisShell(settings, interactive)
    if interactive:
        return shell.cmdloop() or 0
    import shlex

    argv = [argv[0].replace("-", "_"), *argv[1:]]
    shell.exit_code = 2  # stays at 2 if argument parsing fails (see ``ok``)
    shell.onecmd_plus_hooks(" ".join(shlex.quote(a) for a in argv), add_to_history=False)
    return shell.exit_code


def main_argv() -> list[str]:
    """Return the command-line arguments without the program name."""
    return sys.argv[1:]
