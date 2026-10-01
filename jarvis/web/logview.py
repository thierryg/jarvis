# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/web/logview.py
# Purpose : Log viewer backend: parse, search and follow /var/log/jarvis/jarvis.log safely
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Log file access for ``GET /api/logs`` (search) and ``GET /api/logs/stream`` (live tail).

Entries follow :data:`jarvis.core.logs.FORMAT`::

    2026-09-30 12:00:00,123 INFO    [core] jarvis.vision.pipeline: message

Lines that do not start with a timestamp (tracebacks, multi-line messages) are attached to the
previous entry. Searches read the current file and, on request, the rotated ones
(``jarvis.log.1``, ``jarvis.log.2.gz``…), newest first, within a byte budget so that a request
can never exhaust memory. Every returned text goes through :func:`mask_secrets`: the logs should
not contain secrets, but a URL with credentials or a token printed by a third-party library must
never reach the browser.
"""

from __future__ import annotations

import gzip
import re
from datetime import datetime
from pathlib import Path

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}
LINE = re.compile(r"^(?P<time>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(?P<ms>\d{3}) (?P<level>[A-Z]+)\s+"
                  r"\[(?P<process>[\w-]+)\] (?P<logger>[\w.\-]+): (?P<message>.*)$")
_SECRETS = (
    (re.compile(r"(?i)\b(rtsps?|https?|mqtts?|ftp)://[^@/\s]+@"), r"\1://***@"),
    (re.compile(r"(?i)\b(authorization:\s*)(bearer|basic)\s+\S+"), r"\1\2 ***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer ***"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
     r"\1\2***"),
)
MAX_QUERY = 200


def mask_secrets(text: str) -> str:
    """Mask credentials in URLs, authorization headers, bearer tokens and key=value secrets."""
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


def parse(lines: list[str]) -> list[dict]:
    """Group raw lines into entries ``{ts, time, level, process, logger, message}``.

    Continuation lines (tracebacks) are appended to the previous entry's message. Leading lines
    without any header (e.g. the end of a traceback read by a later :meth:`Follower.poll`) are
    kept as entries flagged ``"continuation": True``, which the UI appends to its last entry.
    """
    entries: list[dict] = []
    for raw in lines:
        line = raw.rstrip("\n")
        m = LINE.match(line)
        if m:
            t = datetime.strptime(m["time"], "%Y-%m-%d %H:%M:%S")
            entries.append({"ts": t.timestamp() + int(m["ms"]) / 1000, "time": f"{m['time']}.{m['ms']}",
                            "level": m["level"], "process": m["process"], "logger": m["logger"],
                            "message": m["message"]})
        elif entries:
            entries[-1]["message"] += "\n" + line
        elif line.strip():
            entries.append({"ts": 0.0, "time": "", "level": "INFO", "process": "?", "logger": "", "message": line,
                            "continuation": True})
    return entries


def _read_tail(path: Path, budget: int) -> tuple[list[str], int]:
    """Last ``budget`` bytes of a (possibly gzip-compressed) file as lines; returns (lines, bytes read)."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
        data = data[-budget:]
        lines = data.splitlines(keepends=True)
        return (lines[1:] if len(data) == budget else lines), len(data.encode())
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, size - budget))
        data = fh.read().decode("utf-8", errors="replace")
    lines = data.splitlines(keepends=True)
    return (lines[1:] if size > budget else lines), min(size, budget)   # drop the cut first line


def log_files(path: Path, history: bool) -> list[Path]:
    """The log file, then (with ``history``) its rotations, newest first."""
    files = [path] if path.exists() else []
    if history:
        def order(p: Path) -> int:
            m = re.search(r"\.(\d+)(?:\.gz)?$", p.name)
            return int(m[1]) if m else 0
        files += sorted((p for p in path.parent.glob(path.name + ".*") if order(p)), key=order)
    return files


def compile_query(q: str, regex: bool) -> re.Pattern | None:
    """Compile the search text (plain, case-insensitive, or a regular expression).

    Raises:
        ValueError: On a query that is too long or an invalid regular expression.
    """
    if not q:
        return None
    if len(q) > MAX_QUERY:
        raise ValueError(f"search text longer than {MAX_QUERY} characters")
    try:
        return re.compile(q if regex else re.escape(q), re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"invalid regular expression: {exc}") from None


def matches(e: dict, pattern: re.Pattern | None, level: str, process: str, start: float | None,
            end: float | None) -> bool:
    """Filter predicate shared by the search and the live stream."""
    if LEVEL_RANK.get(e["level"], 1) < LEVEL_RANK.get(level, 0):
        return False
    if process and e["process"] != process:
        return False
    if start is not None and e["ts"] and e["ts"] < start:
        return False
    if end is not None and e["ts"] and e["ts"] > end:
        return False
    return pattern is None or bool(pattern.search(f"{e['logger']}: {e['message']}"))


def search(path: Path, q: str = "", regex: bool = False, level: str = "DEBUG", process: str = "",
           start: float | None = None, end: float | None = None, limit: int = 500, history: bool = False,
           budget: int = 8 * 1024 * 1024) -> dict:
    """Search the log (newest entries last, like a terminal), masked.

    Args:
        path: Current log file (``logging.file_path``).
        q: Text to find (in the logger name and the message); empty for everything.
        regex: Treat ``q`` as a regular expression.
        level: Minimum level.
        process: ``core``, ``api``, ``console``... or empty for all.
        start: Lower bound (Unix seconds), inclusive.
        end: Upper bound (Unix seconds), inclusive.
        limit: Maximum number of entries returned (the most recent ones).
        history: Also read the rotated files.
        budget: Maximum bytes read over all files.

    Returns:
        ``{"entries": [...], "total_matched": int, "truncated": bool, "bytes_read": int, "files": [...]}``.
    """
    pattern = compile_query(q, regex)
    matched: list[dict] = []
    used, names = 0, []
    for f in log_files(path, history):
        if used >= budget:
            break
        lines, n = _read_tail(f, budget - used)
        used += n
        names.append(f.name)
        chunk = [e for e in parse(lines) if matches(e, pattern, level, process, start, end)]
        matched = chunk + matched          # older files go before newer ones
    total = len(matched)
    out = matched[-limit:]
    for e in out:
        e["message"] = mask_secrets(e["message"])
    return {"entries": out, "total_matched": total, "truncated": total > len(out) or used >= budget,
            "bytes_read": used, "files": names}


class Follower:
    """Incremental reader for the live tail: returns the entries appended since the last call.

    Handles log rotation (the file is replaced or truncated: reading restarts at the beginning of
    the new file) and partial last lines (kept until their newline arrives).
    """

    def __init__(self, path: Path, from_end: bool = True):
        self.path = path
        self.inode, self.pos, self.partial = None, 0, ""
        if from_end and path.exists():
            st = path.stat()
            self.inode, self.pos = st.st_ino, st.st_size

    def poll(self) -> list[dict]:
        """Read what was appended; masked entries."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return []
        if st.st_ino != self.inode or st.st_size < self.pos:      # rotated or truncated
            self.inode, self.pos, self.partial = st.st_ino, 0, ""
        if st.st_size == self.pos:
            return []
        with self.path.open("rb") as fh:
            fh.seek(self.pos)
            data = fh.read(st.st_size - self.pos)
        self.pos += len(data)
        text = self.partial + data.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self.partial = lines.pop()                                # incomplete last line (or "")
        entries = parse([ln + "\n" for ln in lines])
        for e in entries:
            e["message"] = mask_secrets(e["message"])
        return entries
