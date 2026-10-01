# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/storage/database.py
# Purpose : SQLite (WAL) persistence shared by the core and API processes
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""SQLite persistence (WAL mode) shared by the core and API processes.

A new connection is opened for every operation: SQLite stays fast at this data volume,
and it avoids sharing any connection across threads or processes. WAL mode lets the API
read while the core writes.

Stored data: enrolled persons with their face embeddings and voice profiles, unknown
faces (clustered per visitor), timestamped sightings, a tamper-evident event log (each
event is SHA-256 chained to the previous one), web users and sessions, and settings
overrides. Embeddings are stored as raw float32 BLOBs.

The schema is created idempotently by :meth:`Database.init`; columns added after the
first release are applied through :data:`MIGRATIONS`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL DEFAULT '',
    can_open_garage INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    watchlist INTEGER NOT NULL DEFAULT 0,
    access_days TEXT NOT NULL DEFAULT '',
    access_start TEXT NOT NULL DEFAULT '',
    access_end TEXT NOT NULL DEFAULT '',
    valid_until REAL
);
CREATE TABLE IF NOT EXISTS face_embeddings (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,
    image_path TEXT,
    source TEXT NOT NULL DEFAULT 'upload',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_profiles (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,
    audio_path TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS unknown_faces (
    id INTEGER PRIMARY KEY,
    embedding BLOB NOT NULL,
    image_path TEXT NOT NULL,
    det_score REAL,
    created_at REAL NOT NULL,
    cluster_id INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    actor TEXT,
    person_id INTEGER,
    details TEXT,
    prev_hash TEXT,
    hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS greetings (
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    PRIMARY KEY (person_id, day)
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL,
    created_at REAL,
    last_seen REAL,
    ip TEXT,
    user_agent TEXT,
    ended_at REAL,
    end_reason TEXT                    -- logout | idle_timeout | expired | revoked
);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    path TEXT NOT NULL UNIQUE,
    thumb_path TEXT,
    size_bytes INTEGER NOT NULL,
    triggers TEXT NOT NULL,            -- JSON list: person | known | unknown | watchlist
    person_ids TEXT NOT NULL,          -- JSON list of recognized person ids
    sighting_ids TEXT NOT NULL         -- JSON list of related sightings
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,               -- JSON
    updated_at REAL NOT NULL,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    track_id INTEGER,
    status TEXT NOT NULL,              -- known | unknown | watchlist | labeled
    person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    score REAL,
    quality REAL,
    image_path TEXT,
    embedding BLOB,
    unknown_id INTEGER,
    cluster_id INTEGER
);
-- License plates allowed to open the garage (normalized: upper case, letters and digits only).
CREATE TABLE IF NOT EXISTS plates (
    id INTEGER PRIMARY KEY,
    plate TEXT NOT NULL UNIQUE,        -- normalized, e.g. "AB123CD"
    display TEXT NOT NULL,             -- as entered, e.g. "AB-123-CD"
    country TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    valid_until REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
-- Every confirmed plate read (traceability), with its vehicle snapshot.
CREATE TABLE IF NOT EXISTS plate_reads (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    plate TEXT NOT NULL,               -- normalized
    confidence REAL,
    region TEXT,
    status TEXT NOT NULL,              -- known | unknown | disabled | expired
    plate_id INTEGER REFERENCES plates(id) ON DELETE SET NULL,
    track_id INTEGER,
    direction TEXT,                    -- approaching | leaving | stationary
    action TEXT,                       -- opened | closed | none, with the reason in the event log
    image_path TEXT
);
CREATE INDEX IF NOT EXISTS plate_reads_ts ON plate_reads(ts);
"""

# Columns added after the first release: (table, column, definition).
MIGRATIONS = [
    ("persons", "watchlist", "INTEGER NOT NULL DEFAULT 0"),
    ("persons", "access_days", "TEXT NOT NULL DEFAULT ''"),
    ("persons", "access_start", "TEXT NOT NULL DEFAULT ''"),
    ("persons", "access_end", "TEXT NOT NULL DEFAULT ''"),
    ("persons", "valid_until", "REAL"),
    ("unknown_faces", "cluster_id", "INTEGER"),
    ("events", "prev_hash", "TEXT"),
    ("events", "hash", "TEXT"),
    ("sessions", "created_at", "REAL"),
    ("sessions", "last_seen", "REAL"),
    ("sessions", "ip", "TEXT"),
    ("sessions", "user_agent", "TEXT"),
    ("sessions", "ended_at", "REAL"),
    ("sessions", "end_reason", "TEXT"),
    ("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0"),
]

# prev_hash of the very first sealed event.
GENESIS = "0" * 64


def event_hash(prev_hash: str, ts: float, type_: str, actor: str | None, person_id: int | None, details: str) -> str:
    """Compute the audit chain hash of an event.

    Each event seals the previous one, so any modification, deletion or reordering
    breaks the chain.

    Args:
        prev_hash: Hash of the previous event (:data:`GENESIS` for the first one).
        ts: Event timestamp; hashed through ``repr`` to keep full float precision.
        type_: Event type.
        actor: Event actor, if any.
        person_id: Related person, if any.
        details: JSON-encoded details, exactly as stored.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    payload = json.dumps([prev_hash, repr(ts), type_, actor, person_id, details], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def time_of_day_clause(column: str, time_from: str | None, time_to: str | None) -> tuple[str, list]:
    """Build a SQL "time of day" filter (local time, ``HH:MM``) on an epoch column.

    The range may wrap around midnight (e.g. 22:00 -> 06:00). A missing bound defaults
    to 00:00 or 23:59 respectively.

    Args:
        column: SQL expression holding an epoch timestamp (trusted, not user input).
        time_from: Range start, ``HH:MM``, or ``None``.
        time_to: Range end (inclusive), ``HH:MM``, or ``None``.

    Returns:
        ``(sql, params)``: an ``AND ...`` fragment to append to a WHERE clause, and its
        parameters. Both are empty when no bound is given.
    """
    if not time_from and not time_to:
        return "", []
    hm = f"strftime('%H:%M', {column}, 'unixepoch', 'localtime')"
    t0, t1 = time_from or "00:00", time_to or "23:59"
    if t0 <= t1:
        return f" AND {hm} >= ? AND {hm} <= ?", [t0, t1]
    return f" AND ({hm} >= ? OR {hm} <= ?)", [t0, t1]

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_recordings_start ON recordings(start_ts);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_sightings_ts ON sightings(ts);
CREATE INDEX IF NOT EXISTS idx_sightings_person ON sightings(person_id);
CREATE INDEX IF NOT EXISTS idx_sightings_cluster ON sightings(cluster_id);
CREATE INDEX IF NOT EXISTS idx_unknown_cluster ON unknown_faces(cluster_id);
"""


def to_blob(vec: np.ndarray) -> bytes:
    """Serialize a vector as a raw float32 BLOB."""
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    """Deserialize a raw float32 BLOB into a (writable) NumPy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()


@dataclass
class Person:
    """An enrolled person and their access rules.

    Attributes:
        id: Database id.
        first_name: First name (used for greetings).
        last_name: Last name.
        can_open_garage: Whether the person may operate the garage.
        created_at: Creation timestamp.
        watchlist: Whether the person is on the watchlist (alert, never access).
        access_days: Allowed ISO weekdays as digits; empty means every day.
        access_start: Daily access window start (``HH:MM``); empty means no restriction.
        access_end: Daily access window end (``HH:MM``, exclusive).
        valid_until: Access expiration timestamp; ``None`` means permanent.
    """

    id: int
    first_name: str
    last_name: str
    can_open_garage: bool
    created_at: float
    watchlist: bool = False
    access_days: str = ""        # allowed ISO weekdays, e.g. "12345" = Monday..Friday; empty = every day
    access_start: str = ""       # "HH:MM"; empty = no time restriction
    access_end: str = ""
    valid_until: float | None = None  # access expiration timestamp; None = permanent

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Person:
        """Build a :class:`Person` from a ``persons`` table row."""
        return cls(row["id"], row["first_name"], row["last_name"], bool(row["can_open_garage"]),
                   row["created_at"], bool(row["watchlist"]), row["access_days"], row["access_start"],
                   row["access_end"], row["valid_until"])


PERSON_AUDIT_FIELDS = ("first_name", "last_name", "can_open_garage", "watchlist", "access_days", "access_start",
                       "access_end", "valid_until")


def person_snapshot(p: Person | None) -> dict:
    """Return the audited fields of a person, used to build before/after audit diffs.

    Args:
        p: The person, or ``None``.

    Returns:
        Field -> value mapping, or an empty dict if ``p`` is ``None``.
    """
    return {f: getattr(p, f) for f in PERSON_AUDIT_FIELDS} if p else {}


class Database:
    """Data access layer over the Jarvis SQLite database.

    Instances are cheap and stateless apart from the database path; every method opens
    its own short-lived connection, so a single instance can be shared across threads.

    Attributes:
        path: Path of the SQLite database file.
    """

    def __init__(self, path: str | Path):
        """Initialize the accessor (does not touch the file; call :meth:`init`).

        Args:
            path: Path of the SQLite database file.
        """
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection for the duration of a ``with`` block.

        Foreign keys are enforced and a busy timeout is set so that concurrent writers
        (core and API) wait instead of failing. The transaction is committed on normal
        exit and rolled back on exception.

        Yields:
            A :class:`sqlite3.Connection` with :class:`sqlite3.Row` rows.
        """
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        """Create the database, schema and indexes, and apply column migrations.

        Idempotent: safe to call at every startup of either process.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("PRAGMA journal_mode = WAL")
            c.executescript(SCHEMA)
            for table, column, ddl in MIGRATIONS:
                cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            c.executescript(INDEXES)
            # Sessions opened before session tracking existed (no date, no IP): close them to force a new login.
            c.execute("UPDATE sessions SET ended_at = ?, end_reason = 'expired' "
                      "WHERE created_at IS NULL AND ended_at IS NULL", (time.time(),))

    # --- Persons -----------------------------------------------------------------------
    def add_person(self, first_name: str, last_name: str = "", can_open_garage: bool = False,
                   **extra) -> int:
        """Create a person.

        Args:
            first_name: First name (whitespace-stripped).
            last_name: Last name (whitespace-stripped).
            can_open_garage: Whether the person may operate the garage.
            **extra: Additional fields forwarded to :meth:`update_person`.

        Returns:
            Id of the new person.
        """
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO persons(first_name, last_name, can_open_garage, created_at) VALUES (?,?,?,?)",
                (first_name.strip(), last_name.strip(), int(can_open_garage), time.time()),
            )
            pid = int(cur.lastrowid)
        if extra:
            self.update_person(pid, **extra)
        return pid

    # Columns that update_person() is allowed to write.
    PERSON_FIELDS = {"first_name", "last_name", "can_open_garage", "watchlist", "access_days", "access_start",
                     "access_end", "valid_until"}

    def update_person(self, person_id: int, clear_valid_until: bool = False, **fields) -> None:
        """Update the given person fields.

        Unknown field names are ignored. Because ``None`` means "unchanged", clearing
        the expiration date requires ``clear_valid_until``.

        Args:
            person_id: Person to update.
            clear_valid_until: If ``True``, make the access permanent (``valid_until = NULL``).
            **fields: New values for any of :attr:`PERSON_FIELDS`; ``None`` leaves a field unchanged.
        """
        sets = {k: (int(v) if k in ("can_open_garage", "watchlist") else v) for k, v in fields.items()
                if k in self.PERSON_FIELDS and v is not None}
        if clear_valid_until:
            sets["valid_until"] = None
        if not sets:
            return
        cols = ", ".join(f"{k} = ?" for k in sets)
        with self.connect() as c:
            # cols only contains names from PERSON_FIELDS (allow-list); values are bound parameters.
            c.execute(f"UPDATE persons SET {cols} WHERE id = ?", (*sets.values(), person_id))  # noqa: S608  # nosec B608

    def get_person(self, person_id: int) -> Person | None:
        """Return the person with the given id, or ``None``."""
        with self.connect() as c:
            row = c.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
        return Person.from_row(row) if row else None

    def list_persons(self) -> list[dict]:
        """List all persons sorted by first name.

        Returns:
            Person rows with ``face_count``, ``auto_face_count``, ``voice_count`` and
            ``last_seen`` (latest sighting timestamp) added.
        """
        with self.connect() as c:
            rows = c.execute(
                """SELECT p.*,
                          (SELECT COUNT(*) FROM face_embeddings f WHERE f.person_id = p.id) AS face_count,
                          (SELECT COUNT(*) FROM face_embeddings f WHERE f.person_id = p.id
                           AND f.source = 'auto') AS auto_face_count,
                          (SELECT COUNT(*) FROM voice_profiles v WHERE v.person_id = p.id) AS voice_count,
                          (SELECT MAX(ts) FROM sightings s WHERE s.person_id = p.id) AS last_seen
                   FROM persons p ORDER BY p.first_name"""
            ).fetchall()
        return [dict(r) | {"can_open_garage": bool(r["can_open_garage"]), "watchlist": bool(r["watchlist"])}
                for r in rows]

    def delete_person(self, person_id: int) -> list[str]:
        """Delete a person and all of their biometric data (GDPR right to erasure).

        Face embeddings, voice profiles and greetings are removed by ``ON DELETE
        CASCADE``; sightings are deleted explicitly.

        Args:
            person_id: Person to delete.

        Returns:
            Paths of the associated files (photos, recordings) the caller must delete.
        """
        with self.connect() as c:
            files = [r[0] for r in c.execute(
                "SELECT image_path FROM face_embeddings WHERE person_id = ? AND image_path IS NOT NULL "
                "UNION ALL SELECT audio_path FROM voice_profiles WHERE person_id = ? AND audio_path IS NOT NULL "
                "UNION ALL SELECT image_path FROM sightings WHERE person_id = ? AND image_path IS NOT NULL",
                (person_id, person_id, person_id))]
            # Full erasure: timestamped sightings (photo + embedding) are biometric data too.
            c.execute("DELETE FROM sightings WHERE person_id = ?", (person_id,))
            c.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        return files

    # --- Faces -------------------------------------------------------------------------
    def add_face_embedding(self, person_id: int, embedding: np.ndarray, image_path: str | None,
                           source: str = "upload") -> int:
        """Store a face embedding for a person.

        Args:
            person_id: Owner of the face.
            embedding: Face embedding.
            image_path: Path of the source photo, if kept.
            source: Origin: ``upload``, ``auto`` (adaptive learning) or ``labeled``.

        Returns:
            Id of the new face embedding.
        """
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO face_embeddings(person_id, embedding, image_path, source, created_at) "
                "VALUES (?,?,?,?,?)",
                (person_id, to_blob(embedding), image_path, source, time.time()),
            )
            return int(cur.lastrowid)

    def list_faces(self, person_id: int) -> list[dict]:
        """List the faces of a person, newest first (without embeddings)."""
        with self.connect() as c:
            rows = c.execute(
                "SELECT id, image_path, source, created_at FROM face_embeddings WHERE person_id = ? "
                "ORDER BY created_at DESC", (person_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_face(self, face_id: int) -> str | None:
        """Delete a face embedding.

        Returns:
            Path of the associated photo to delete, or ``None``.
        """
        with self.connect() as c:
            row = c.execute("SELECT image_path FROM face_embeddings WHERE id = ?", (face_id,)).fetchone()
            c.execute("DELETE FROM face_embeddings WHERE id = ?", (face_id,))
        return row["image_path"] if row else None

    def count_faces(self, person_id: int, source: str | None = None) -> int:
        """Count the faces of a person, optionally restricted to one ``source``."""
        sql, args = "SELECT COUNT(*) FROM face_embeddings WHERE person_id = ?", [person_id]
        if source:
            sql += " AND source = ?"
            args.append(source)
        with self.connect() as c:
            return int(c.execute(sql, args).fetchone()[0])

    def person_face_embeddings(self, person_id: int) -> list[np.ndarray]:
        """Return all face embeddings of a person."""
        with self.connect() as c:
            rows = c.execute("SELECT embedding FROM face_embeddings WHERE person_id = ?", (person_id,)).fetchall()
        return [from_blob(r["embedding"]) for r in rows]

    def all_face_embeddings(self) -> list[tuple[int, np.ndarray]]:
        """Return ``(person_id, embedding)`` for every enrolled face (gallery loading)."""
        with self.connect() as c:
            rows = c.execute("SELECT person_id, embedding FROM face_embeddings").fetchall()
        return [(r["person_id"], from_blob(r["embedding"])) for r in rows]

    # --- Voice -------------------------------------------------------------------------
    def add_voice_profile(self, person_id: int, embedding: np.ndarray, audio_path: str | None) -> int:
        """Store a voice profile (speaker embedding) for a person.

        Returns:
            Id of the new voice profile.
        """
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO voice_profiles(person_id, embedding, audio_path, created_at) VALUES (?,?,?,?)",
                (person_id, to_blob(embedding), audio_path, time.time()),
            )
            return int(cur.lastrowid)

    def delete_voice_profiles(self, person_id: int) -> list[str]:
        """Delete every voice profile of a person.

        Returns:
            Paths of the associated recordings to delete.
        """
        with self.connect() as c:
            files = [r[0] for r in c.execute(
                "SELECT audio_path FROM voice_profiles WHERE person_id = ? AND audio_path IS NOT NULL",
                (person_id,))]
            c.execute("DELETE FROM voice_profiles WHERE person_id = ?", (person_id,))
        return files

    def all_voice_profiles(self) -> list[tuple[int, np.ndarray]]:
        """Return ``(person_id, embedding)`` for every voice profile."""
        with self.connect() as c:
            rows = c.execute("SELECT person_id, embedding FROM voice_profiles").fetchall()
        return [(r["person_id"], from_blob(r["embedding"])) for r in rows]

    # --- Unknown faces -----------------------------------------------------------------
    def add_unknown(self, embedding: np.ndarray, image_path: str, det_score: float,
                    cluster_id: int | None = None) -> int:
        """Store an unknown face.

        Args:
            embedding: Face embedding.
            image_path: Path of the face snapshot.
            det_score: Detector confidence.
            cluster_id: Cluster of the matching unknown visitor; if ``None``, the face
                starts its own cluster (``cluster_id = id``).

        Returns:
            Id of the new unknown face.
        """
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO unknown_faces(embedding, image_path, det_score, created_at, cluster_id) "
                "VALUES (?,?,?,?,?)",
                (to_blob(embedding), image_path, det_score, time.time(), cluster_id),
            )
            uid = int(cur.lastrowid)
            if cluster_id is None:
                c.execute("UPDATE unknown_faces SET cluster_id = ? WHERE id = ?", (uid, uid))
            return uid

    def list_unknowns(self, limit: int = 200) -> list[dict]:
        """List the most recent unknown faces (without embeddings)."""
        with self.connect() as c:
            rows = c.execute(
                "SELECT id, image_path, det_score, created_at, cluster_id FROM unknown_faces "
                "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def list_unknown_clusters(self) -> list[dict]:
        """List unknown visitor clusters, most recently seen first.

        A cluster is the same unknown visitor seen several times.

        Returns:
            One dict per cluster with ``cluster_id``, ``faces`` count, ``first_seen``,
            ``last_seen``, the best-scoring ``image_path`` and the ``sightings`` count.
        """
        with self.connect() as c:
            rows = c.execute(
                """SELECT u.cluster_id, COUNT(*) AS faces, MIN(u.created_at) AS first_seen,
                          MAX(u.created_at) AS last_seen,
                          (SELECT image_path FROM unknown_faces b WHERE b.cluster_id = u.cluster_id
                           ORDER BY b.det_score DESC LIMIT 1) AS image_path,
                          (SELECT COUNT(*) FROM sightings s WHERE s.cluster_id = u.cluster_id) AS sightings
                   FROM unknown_faces u GROUP BY u.cluster_id ORDER BY last_seen DESC""").fetchall()
        return [dict(r) for r in rows]

    def unknown_ids_in_cluster(self, cluster_id: int) -> list[int]:
        """Return the ids of the unknown faces belonging to a cluster."""
        with self.connect() as c:
            return [r[0] for r in c.execute("SELECT id FROM unknown_faces WHERE cluster_id = ?", (cluster_id,))]

    def all_unknown_embeddings(self) -> list[tuple[int, int, float, np.ndarray]]:
        """Return every stored unknown face.

        Returns:
            ``(id, cluster_id, created_at, embedding)`` tuples; rows without a cluster
            fall back to their own id.
        """
        with self.connect() as c:
            rows = c.execute("SELECT id, cluster_id, created_at, embedding FROM unknown_faces").fetchall()
        return [(r["id"], r["cluster_id"] or r["id"], r["created_at"], from_blob(r["embedding"])) for r in rows]

    def get_unknown(self, unknown_id: int) -> dict | None:
        """Return an unknown face with its decoded embedding, or ``None``."""
        with self.connect() as c:
            row = c.execute("SELECT * FROM unknown_faces WHERE id = ?", (unknown_id,)).fetchone()
        if not row:
            return None
        return dict(row) | {"embedding": from_blob(row["embedding"])}

    def delete_unknown(self, unknown_id: int) -> str | None:
        """Delete an unknown face.

        Returns:
            Path of the associated snapshot to delete, or ``None``.
        """
        with self.connect() as c:
            row = c.execute("SELECT image_path FROM unknown_faces WHERE id = ?", (unknown_id,)).fetchone()
            c.execute("DELETE FROM unknown_faces WHERE id = ?", (unknown_id,))
        return row["image_path"] if row else None

    def label_unknown(self, unknown_id: int, person_id: int, new_image_path: str) -> int:
        """Turn an unknown face into a known face of ``person_id``.

        The embedding is reused as is, and the unknown face is deleted.

        Args:
            unknown_id: Unknown face to label.
            person_id: Person to attach the face to.
            new_image_path: Path of the photo after it was moved into the faces directory.

        Returns:
            Id of the new face embedding.

        Raises:
            KeyError: If the unknown face does not exist.
        """
        with self.connect() as c:
            row = c.execute("SELECT embedding FROM unknown_faces WHERE id = ?", (unknown_id,)).fetchone()
            if row is None:
                raise KeyError(unknown_id)
            cur = c.execute(
                "INSERT INTO face_embeddings(person_id, embedding, image_path, source, created_at) "
                "VALUES (?,?,?,?,?)",
                (person_id, row["embedding"], new_image_path, "labeled", time.time()),
            )
            c.execute("DELETE FROM unknown_faces WHERE id = ?", (unknown_id,))
            return int(cur.lastrowid)

    # --- Sightings (traceability) -------------------------------------------------------
    def add_sighting(self, status: str, track_id: int | None, person_id: int | None, score: float | None,
                     quality: float | None, image_path: str | None, embedding: np.ndarray | None,
                     unknown_id: int | None = None, cluster_id: int | None = None,
                     ts: float | None = None) -> int:
        """Record a timestamped sighting.

        Args:
            status: ``known``, ``unknown``, ``watchlist`` or ``labeled``.
            track_id: Tracker id of the person in the video stream.
            person_id: Recognized person, if any.
            score: Recognition similarity, if any.
            quality: Capture quality score (0..1).
            image_path: Path of the snapshot, if kept.
            embedding: Face embedding (enables search-by-face), if any.
            unknown_id: Related unknown face, if any.
            cluster_id: Related unknown visitor cluster, if any.
            ts: Timestamp (defaults to now).

        Returns:
            Id of the new sighting.
        """
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO sightings(ts, track_id, status, person_id, score, quality, image_path, embedding, "
                "unknown_id, cluster_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts or time.time(), track_id, status, person_id, score, quality, image_path,
                 to_blob(embedding) if embedding is not None else None, unknown_id, cluster_id))
            return int(cur.lastrowid)

    @staticmethod
    def _sighting_filter(start_ts: float | None, end_ts: float | None, person_id: int | None,
                         status: str | None, time_from: str | None = None,
                         time_to: str | None = None) -> tuple[str, list]:
        """Build the WHERE clause shared by the sighting queries (table alias ``s``).

        ``None`` or empty filters are skipped.

        Returns:
            ``(sql, params)`` starting with ``" WHERE 1=1"``.
        """
        sql, args = " WHERE 1=1", []
        for cond, val in (("s.ts >= ?", start_ts), ("s.ts < ?", end_ts), ("s.person_id = ?", person_id),
                          ("s.status = ?", status)):
            if val is not None and val != "":
                sql += f" AND {cond}"
                args.append(val)
        tod, targs = time_of_day_clause("s.ts", time_from, time_to)
        return sql + tod, args + targs

    def list_sightings(self, start_ts: float | None = None, end_ts: float | None = None,
                       person_id: int | None = None, status: str | None = None, limit: int = 200,
                       before_id: int | None = None, time_from: str | None = None,
                       time_to: str | None = None) -> list[dict]:
        """List sightings, newest first, joined with the person name.

        Args:
            start_ts: Inclusive lower timestamp bound.
            end_ts: Exclusive upper timestamp bound.
            person_id: Restrict to one person.
            status: Restrict to one status.
            limit: Maximum number of rows.
            before_id: Keyset pagination: only rows with a smaller id.
            time_from: Time-of-day range start (``HH:MM``).
            time_to: Time-of-day range end (``HH:MM``).

        Returns:
            Sighting dicts (without embeddings).
        """
        where, args = self._sighting_filter(start_ts, end_ts, person_id, status, time_from, time_to)
        if before_id:
            where += " AND s.id < ?"
            args.append(before_id)
        with self.connect() as c:
            rows = c.execute(
                "SELECT s.id, s.ts, s.track_id, s.status, s.person_id, s.score, s.quality, s.image_path, "  # noqa: S608  # nosec B608
                "s.unknown_id, s.cluster_id, p.first_name, p.last_name FROM sightings s "
                "LEFT JOIN persons p ON p.id = s.person_id" + where + " ORDER BY s.id DESC LIMIT ?",
                (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def sighting_embeddings(self, start_ts: float | None = None, end_ts: float | None = None,
                            time_from: str | None = None, time_to: str | None = None) -> list[tuple[int, np.ndarray]]:
        """Return ``(sighting_id, embedding)`` for sightings that have an embedding.

        Used by search-by-face; filters have the same meaning as in :meth:`list_sightings`.
        """
        where, args = self._sighting_filter(start_ts, end_ts, None, None, time_from, time_to)
        with self.connect() as c:
            # where is assembled from constant fragments; every value is a bound parameter.
            rows = c.execute("SELECT s.id, s.embedding FROM sightings s" + where + " AND s.embedding IS NOT NULL",  # noqa: S608  # nosec B608
                             args).fetchall()
        return [(r["id"], from_blob(r["embedding"])) for r in rows]

    def get_sightings(self, ids: list[int]) -> dict[int, dict]:
        """Fetch sightings by id, joined with the person name, as an ``id -> row`` mapping."""
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self.connect() as c:
            rows = c.execute(
                "SELECT s.id, s.ts, s.track_id, s.status, s.person_id, s.score, s.image_path, s.cluster_id, "  # noqa: S608  # nosec B608
                f"p.first_name, p.last_name FROM sightings s LEFT JOIN persons p ON p.id = s.person_id "
                f"WHERE s.id IN ({marks})", ids).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def sightings_stats(self, since_ts: float, time_from: str | None = None, time_to: str | None = None) -> list[dict]:
        """Count sightings per day (local time) and status.

        Args:
            since_ts: Only count sightings at or after this timestamp.
            time_from: Optional time-of-day range start (``HH:MM``).
            time_to: Optional time-of-day range end (``HH:MM``).

        Returns:
            ``[{"day": "YYYY-MM-DD", "status": ..., "n": ...}, ...]`` ordered by day.
        """
        tod, targs = time_of_day_clause("ts", time_from, time_to)
        with self.connect() as c:
            rows = c.execute(
                "SELECT date(ts, 'unixepoch', 'localtime') AS day, status, COUNT(*) AS n FROM sightings "  # noqa: S608  # nosec B608
                f"WHERE ts >= ?{tod} GROUP BY day, status ORDER BY day", (since_ts, *targs)).fetchall()
        return [dict(r) for r in rows]

    def relabel_sightings(self, person_id: int, unknown_ids: list[int] = (), cluster_id: int | None = None) -> int:
        """Retroactively attribute the sightings of a labeled unknown visitor to a person.

        Only sightings still in ``unknown`` status are updated; they become ``labeled``.

        Args:
            person_id: Person the sightings now belong to.
            unknown_ids: Unknown face ids whose sightings are relabeled.
            cluster_id: Cluster whose sightings are relabeled.

        Returns:
            Number of updated sightings (0 if neither selector is given).
        """
        conds, args = [], []
        if unknown_ids:
            conds.append(f"unknown_id IN ({','.join('?' * len(unknown_ids))})")
            args += list(unknown_ids)
        if cluster_id is not None:
            conds.append("cluster_id = ?")
            args.append(cluster_id)
        if not conds:
            return 0
        with self.connect() as c:
            cur = c.execute(f"UPDATE sightings SET person_id = ?, status = 'labeled' WHERE status = 'unknown' "  # noqa: S608  # nosec B608
                            f"AND ({' OR '.join(conds)})", (person_id, *args))
            return cur.rowcount

    def recent_unknown_embeddings(self, since_ts: float) -> list[np.ndarray]:
        """Return the embeddings of unknown faces stored since ``since_ts`` (deduplication)."""
        with self.connect() as c:
            rows = c.execute("SELECT embedding FROM unknown_faces WHERE created_at >= ?", (since_ts,)).fetchall()
        return [from_blob(r["embedding"]) for r in rows]

    # --- Greetings ---------------------------------------------------------------------
    def mark_greeted(self, person_id: int, day: date | None = None) -> bool:
        """Record that a person was greeted today.

        Args:
            person_id: Greeted person.
            day: Day to record (defaults to today).

        Returns:
            ``True`` if this is the person's first greeting of the day.
        """
        day_s = (day or date.today()).isoformat()
        with self.connect() as c:
            cur = c.execute("INSERT OR IGNORE INTO greetings(person_id, day) VALUES (?,?)", (person_id, day_s))
            return cur.rowcount == 1

    # --- Event log (tamper-evident audit) ----------------------------------------------
    def log_event(self, type_: str, actor: str | None = None, person_id: int | None = None,
                  **details) -> None:
        """Append an event to the tamper-evident log.

        The new event is chained to the latest one (see :func:`event_hash`) inside an
        immediate transaction, so concurrent writers cannot fork the chain.

        Args:
            type_: Event type (e.g. ``"garage_pulse"``).
            actor: Who caused the event (``vision``, ``voice``, ``system`` or a user name).
            person_id: Related person, if any.
            **details: Extra fields stored as sorted-key JSON.
        """
        payload = json.dumps(details, ensure_ascii=False, default=str, sort_keys=True)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")  # serializes chaining across the core and API processes
            row = c.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
            prev = (row["hash"] if row else None) or GENESIS
            ts = time.time()
            c.execute(
                "INSERT INTO events(ts, type, actor, person_id, details, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                (ts, type_, actor, person_id, payload, prev, event_hash(prev, ts, type_, actor, person_id, payload)),
            )

    def verify_events(self) -> dict:
        """Verify the hash chain of the whole retained event log.

        Events recorded before chaining was introduced (``hash`` is NULL) are counted
        separately. Retention purges do not break the chain: the first retained event
        serves as the anchor.

        Returns:
            ``{"ok": True, "checked", "unsealed", "last_hash"}`` if the chain is intact,
            otherwise ``{"ok": False, "checked", "unsealed", "broken_at", "reason"}``
            where ``broken_at`` is the id of the first offending event.
        """
        with self.connect() as c:
            rows = c.execute("SELECT id, ts, type, actor, person_id, details, prev_hash, hash FROM events "
                             "ORDER BY id").fetchall()
        checked, unsealed, prev = 0, 0, None
        for r in rows:
            if r["hash"] is None:
                unsealed += 1
                continue
            if prev is not None and r["prev_hash"] != prev:
                return {"ok": False, "checked": checked, "unsealed": unsealed, "broken_at": r["id"],
                        "reason": "missing or reordered link"}
            if event_hash(r["prev_hash"], r["ts"], r["type"], r["actor"], r["person_id"], r["details"]) != r["hash"]:
                return {"ok": False, "checked": checked, "unsealed": unsealed, "broken_at": r["id"],
                        "reason": "content modified"}
            prev, checked = r["hash"], checked + 1
        return {"ok": True, "checked": checked, "unsealed": unsealed, "last_hash": prev}

    # Event types shown in the administration audit view.
    ADMIN_EVENT_TYPES = (
        "login", "login_failed", "logout", "session_timeout", "session_revoked", "user_created",
        "password_changed", "password_change_failed", "default_admin_created", "admin_factory_reset",
        "person_created", "person_updated", "person_deleted", "face_added", "face_deleted", "voice_added",
        "voice_deleted", "unknown_labeled", "unknown_deleted", "cluster_labeled", "cluster_deleted",
        "settings_changed", "settings_reset", "core_restart_requested", "sightings_exported", "face_search",
        "garage_pulse", "ptz_home", "say", "audit_verified", "events_exported", "metrics_token_rotated",
        "monitoring_apply_requested", "console_login", "console_login_failed", "console_logout",
        "console_shell", "user_password_reset", "services_restarted", "backup_restored", "sessions_revoked_all",
        "system_reboot", "system_shutdown", "recording_deleted", "timelapse_exported", "plate_added", "plate_updated",
        "plate_deleted", "camera_probed")

    def list_events(self, limit: int = 200, type_: str | None = None, before_id: int | None = None,
                    start_ts: float | None = None, end_ts: float | None = None, time_from: str | None = None,
                    time_to: str | None = None, actor: str | None = None, admin_only: bool = False,
                    person_id: int | None = None) -> list[dict]:
        """List events, newest first, joined with the person's first name.

        Args:
            limit: Maximum number of rows.
            type_: Restrict to one event type.
            before_id: Keyset pagination: only rows with a smaller id.
            start_ts: Inclusive lower timestamp bound.
            end_ts: Exclusive upper timestamp bound.
            time_from: Time-of-day range start (``HH:MM``).
            time_to: Time-of-day range end (``HH:MM``).
            actor: Restrict to one actor.
            admin_only: Only administrative actions (:attr:`ADMIN_EVENT_TYPES`, excluding
                the automatic ``vision``/``voice`` actors).
            person_id: Restrict to one person.

        Returns:
            Event dicts with ``details`` decoded from JSON.
        """
        sql = ("SELECT e.*, p.first_name FROM events e LEFT JOIN persons p ON p.id = e.person_id "
               "WHERE 1=1")
        args: list = []
        for cond, val in (("e.type = ?", type_), ("e.id < ?", before_id), ("e.ts >= ?", start_ts),
                          ("e.ts < ?", end_ts), ("e.actor = ?", actor), ("e.person_id = ?", person_id)):
            if val not in (None, ""):
                sql += f" AND {cond}"
                args.append(val)
        if admin_only:
            sql += f" AND e.type IN ({','.join('?' * len(self.ADMIN_EVENT_TYPES))}) AND e.actor NOT IN ('vision', 'voice')"
            args += list(self.ADMIN_EVENT_TYPES)
        tod, targs = time_of_day_clause("e.ts", time_from, time_to)
        sql += tod
        args += targs
        sql += " ORDER BY e.id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) | {"details": json.loads(r["details"] or "{}")} for r in rows]

    # --- Users / sessions --------------------------------------------------------------
    def add_user(self, username: str, password_hash: str, must_change: bool = False) -> int:
        """Create a web user, or replace the password hash if the user already exists.

        Args:
            username: Account name.
            password_hash: Argon2id hash.
            must_change: Force a password change at the next sign-in (default account, reset).

        Returns:
            The row id reported by SQLite for the insert/upsert.
        """
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO users(username, password_hash, created_at, must_change_password) VALUES (?,?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash, "
                "must_change_password = excluded.must_change_password",
                (username, password_hash, time.time(), int(must_change)),
            )
            return int(cur.lastrowid)

    DEFAULT_ADMIN = ("admin", "admin")

    def ensure_default_admin(self) -> bool:
        """Create the factory account admin/admin (password change forced) when no account exists.

        Returns:
            True if the default account was created.
        """
        from argon2 import PasswordHasher

        with self.connect() as c:
            if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                return False
        user, password = self.DEFAULT_ADMIN
        self.add_user(user, PasswordHasher().hash(password), must_change=True)
        self.log_event("default_admin_created", actor="system", username=user)
        return True

    def set_password(self, user_id: int, password_hash: str, must_change: bool = False) -> None:
        with self.connect() as c:
            c.execute("UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?",
                      (password_hash, int(must_change), user_id))

    def end_user_sessions(self, user_id: int, reason: str, except_token: str | None = None) -> int:
        """Close every open session of a user (except ``except_token``); return how many were closed."""
        with self.connect() as c:
            cur = c.execute("UPDATE sessions SET ended_at = ?, end_reason = ? WHERE user_id = ? AND ended_at IS NULL "
                            "AND token_hash != ?", (time.time(), reason, user_id, except_token or ""))
            return cur.rowcount

    def get_user_by_name(self, username: str) -> dict | None:
        """Return a user row by user name, or ``None``."""
        with self.connect() as c:
            row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def update_password_hash(self, user_id: int, password_hash: str) -> None:
        """Replace a user's password hash."""
        with self.connect() as c:
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

    def create_session(self, token_hash: str, user_id: int, expires_at: float, ip: str | None = None,
                       user_agent: str | None = None) -> None:
        """Create a web session.

        Args:
            token_hash: Hash of the session token (the raw token is never stored).
            user_id: Session owner.
            expires_at: Absolute expiration timestamp.
            ip: Client IP address.
            user_agent: Client user agent (truncated to 200 characters).
        """
        now = time.time()
        with self.connect() as c:
            c.execute("INSERT INTO sessions(token_hash, user_id, expires_at, created_at, last_seen, ip, user_agent) "
                      "VALUES (?,?,?,?,?,?,?)", (token_hash, user_id, expires_at, now, now, ip, (user_agent or "")[:200]))

    def get_session(self, token_hash: str) -> dict | None:
        """Return a session row (open or closed) with its ``session_id`` and ``username``, or ``None``."""
        with self.connect() as c:
            row = c.execute(
                "SELECT s.rowid AS session_id, s.*, u.username, u.must_change_password FROM sessions s "
                "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (token_hash,)).fetchone()
        return dict(row) if row else None

    def get_session_user(self, token_hash: str, idle_timeout_s: float | None = None) -> dict | None:
        """Return the user of an active session.

        A session that has expired or been idle too long is closed on the spot (with
        the time it actually lapsed) and rejected.

        Args:
            token_hash: Hash of the session token.
            idle_timeout_s: Maximum inactivity in seconds, or ``None`` to disable.

        Returns:
            ``{"id", "username", "session_id"}``, or ``None`` if the session is not active.
        """
        s = self.get_session(token_hash)
        if s is None or s["ended_at"] is not None:
            return None
        now = time.time()
        if now > s["expires_at"]:
            self.end_session(token_hash, "expired", s["expires_at"])
            return None
        last = s["last_seen"] or s["created_at"] or now
        if idle_timeout_s and now - last > idle_timeout_s:
            self.end_session(token_hash, "idle_timeout", last + idle_timeout_s)
            return None
        return {"id": s["user_id"], "username": s["username"], "session_id": s["session_id"]}

    def touch_session(self, token_hash: str) -> None:
        """Refresh the last-activity timestamp of an open session."""
        with self.connect() as c:
            c.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ? AND ended_at IS NULL",
                      (time.time(), token_hash))

    def end_session(self, token_hash: str, reason: str, ended_at: float | None = None) -> bool:
        """Close a session (only once).

        Args:
            token_hash: Hash of the session token.
            reason: End reason: ``logout``, ``idle_timeout``, ``expired`` or ``revoked``.
            ended_at: End timestamp (defaults to now).

        Returns:
            ``True`` if the session was still open.
        """
        with self.connect() as c:
            cur = c.execute("UPDATE sessions SET ended_at = ?, end_reason = ? WHERE token_hash = ? AND ended_at IS NULL",
                            (ended_at or time.time(), reason, token_hash))
            return cur.rowcount == 1

    def delete_session(self, token_hash: str) -> None:
        """Close a session as a user logout (the row is kept for the session history)."""
        self.end_session(token_hash, "logout")

    def sweep_sessions(self, idle_timeout_s: float) -> list[dict]:
        """Close idle or expired sessions that made no further request.

        Args:
            idle_timeout_s: Maximum inactivity in seconds.

        Returns:
            The sessions that were closed, with ``end_reason`` and ``ended_at`` set.
        """
        now = time.time()
        closed = []
        with self.connect() as c:
            rows = c.execute("SELECT s.rowid AS session_id, s.*, u.username FROM sessions s JOIN users u "
                             "ON u.id = s.user_id WHERE s.ended_at IS NULL").fetchall()
        for r in rows:
            last = r["last_seen"] or r["created_at"] or now
            if now > r["expires_at"]:
                reason, when = "expired", r["expires_at"]
            elif now - last > idle_timeout_s:
                reason, when = "idle_timeout", last + idle_timeout_s
            else:
                continue
            if self.end_session(r["token_hash"], reason, when):
                closed.append(dict(r) | {"end_reason": reason, "ended_at": when})
        return closed

    def list_sessions(self, start_ts: float | None = None, end_ts: float | None = None,
                      username: str | None = None, limit: int = 200) -> list[dict]:
        """List sessions (history), newest first.

        Args:
            start_ts: Inclusive lower bound on the creation timestamp.
            end_ts: Exclusive upper bound on the creation timestamp.
            username: Restrict to one user.
            limit: Maximum number of rows.

        Returns:
            Session dicts (without the token hash).
        """
        sql = ("SELECT s.rowid AS id, u.username, s.ip, s.user_agent, s.created_at, s.last_seen, s.ended_at, "
               "s.end_reason, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE 1=1")
        args: list = []
        for cond, val in (("s.created_at >= ?", start_ts), ("s.created_at < ?", end_ts), ("u.username = ?", username)):
            if val not in (None, ""):
                sql += f" AND {cond}"
                args.append(val)
        sql += " ORDER BY s.created_at DESC LIMIT ?"
        with self.connect() as c:
            rows = c.execute(sql, (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def session_token_by_id(self, session_id: int) -> str | None:
        """Return the token hash of a session by its row id, or ``None``."""
        with self.connect() as c:
            row = c.execute("SELECT token_hash FROM sessions WHERE rowid = ?", (session_id,)).fetchone()
        return row["token_hash"] if row else None

    # --- Settings (config.yaml overrides) ------------------------------------------------
    # --- Recordings (event-triggered clips) -----------------------------------------------
    def add_recording(self, start_ts: float, end_ts: float, path: str, thumb_path: str | None, size: int,
                      triggers: list[str], person_ids: list[int], sighting_ids: list[int]) -> int:
        """Index a finished clip and return its id."""
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO recordings(start_ts, end_ts, path, thumb_path, size_bytes, triggers, person_ids, "
                "sighting_ids) VALUES (?,?,?,?,?,?,?,?)",
                (start_ts, end_ts, path, thumb_path, size, json.dumps(triggers), json.dumps(person_ids),
                 json.dumps(sighting_ids)))
            return int(cur.lastrowid)

    def list_recordings(self, start_ts: float | None = None, end_ts: float | None = None,
                        time_from: str | None = None, time_to: str | None = None, person_id: int | None = None,
                        trigger: str | None = None, limit: int = 200, before_id: int | None = None) -> list[dict]:
        """Clips of a period / daily time window, newest first, with the names of the people seen."""
        sql, args = "SELECT * FROM recordings WHERE 1=1", []
        for cond, val in (("start_ts >= ?", start_ts), ("start_ts < ?", end_ts), ("id < ?", before_id)):
            if val is not None:
                sql += f" AND {cond}"
                args.append(val)
        if person_id is not None:
            sql += " AND EXISTS (SELECT 1 FROM json_each(person_ids) WHERE value = ?)"
            args.append(person_id)
        if trigger:
            sql += " AND EXISTS (SELECT 1 FROM json_each(triggers) WHERE value = ?)"
            args.append(trigger)
        tod, targs = time_of_day_clause("start_ts", time_from, time_to)
        sql += tod + " ORDER BY id DESC LIMIT ?"
        with self.connect() as c:
            rows = [dict(r) for r in c.execute(sql, (*args, *targs, limit)).fetchall()]
            names = {r["id"]: f"{r['first_name']} {r['last_name']}".strip()
                     for r in c.execute("SELECT id, first_name, last_name FROM persons")}
        for r in rows:
            for k in ("triggers", "person_ids", "sighting_ids"):
                r[k] = json.loads(r[k])
            r["persons"] = [names[p] for p in r["person_ids"] if p in names]
            r["duration_s"] = round(r["end_ts"] - r["start_ts"], 1)
        return rows

    def get_recording(self, recording_id: int) -> dict | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
        return dict(row) if row else None

    def delete_recording(self, recording_id: int) -> dict | None:
        """Delete a clip's index row; return it so that the caller removes the files."""
        row = self.get_recording(recording_id)
        if row:
            with self.connect() as c:
                c.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
        return row

    def delete_recording_by_path(self, path: str) -> str | None:
        """Drop the index row of a clip removed by the FIFO rotation; return its thumbnail path."""
        with self.connect() as c:
            row = c.execute("SELECT thumb_path FROM recordings WHERE path = ?", (path,)).fetchone()
            c.execute("DELETE FROM recordings WHERE path = ?", (path,))
        return row["thumb_path"] if row else None

    def get_settings_overrides(self) -> dict:
        """Return all settings overrides as a ``key -> value`` mapping."""
        with self.connect() as c:
            rows = c.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def list_settings_overrides(self) -> dict[str, dict]:
        """Return all settings overrides with their ``updated_at`` and ``updated_by`` metadata."""
        with self.connect() as c:
            rows = c.execute("SELECT key, value, updated_at, updated_by FROM settings").fetchall()
        return {r["key"]: {"value": json.loads(r["value"]), "updated_at": r["updated_at"],
                           "updated_by": r["updated_by"]} for r in rows}

    def set_settings_overrides(self, values: dict, actor: str) -> None:
        """Insert or update settings overrides.

        Args:
            values: ``key -> value`` mapping (values are stored as JSON).
            actor: User who made the change.
        """
        now = time.time()
        with self.connect() as c:
            for k, v in values.items():
                c.execute("INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                          "updated_by = excluded.updated_by", (k, json.dumps(v, ensure_ascii=False), now, actor))

    def delete_settings_overrides(self, keys: list[str]) -> None:
        """Delete settings overrides (the ``config.yaml`` values apply again)."""
        with self.connect() as c:
            c.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in keys])

    # --- Retention (GDPR) --------------------------------------------------------------
    # --- License plates -------------------------------------------------------------------
    PLATE_FIELDS = ("display", "country", "label", "person_id", "enabled", "valid_until")

    def add_plate(self, plate: str, display: str, country: str = "", label: str = "",
                  person_id: int | None = None, enabled: bool = True, valid_until: float | None = None) -> int:
        """Register a plate (``plate`` already normalized, see :func:`jarvis.core.plates.validate_plate`).

        Raises:
            sqlite3.IntegrityError: If the plate is already registered.
        """
        now = time.time()
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO plates(plate, display, country, label, person_id, enabled, valid_until, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (plate, display, country, label, person_id, int(enabled), valid_until, now, now))
            return int(cur.lastrowid)

    def update_plate(self, plate_id: int, **fields) -> None:
        """Update registry fields (``display``, ``country``, ``label``, ``person_id``, ``enabled``, ``valid_until``)."""
        cols = [k for k in fields if k in self.PLATE_FIELDS]
        if not cols:
            return
        with self.connect() as c:
            # Column names come from the PLATE_FIELDS whitelist; values are bound parameters.
            c.execute(f"UPDATE plates SET {', '.join(f'{k} = ?' for k in cols)}, updated_at = ? WHERE id = ?",  # noqa: S608  # nosec B608
                      (*[int(fields[k]) if k == "enabled" else fields[k] for k in cols], time.time(), plate_id))

    def delete_plate(self, plate_id: int) -> None:
        """Remove a plate from the registry (its past reads are kept, unlinked)."""
        with self.connect() as c:
            c.execute("DELETE FROM plates WHERE id = ?", (plate_id,))

    def get_plate(self, plate_id: int) -> dict | None:
        """Registry row by id, with the linked person's name."""
        with self.connect() as c:
            r = c.execute("SELECT pl.*, p.first_name, p.last_name FROM plates pl LEFT JOIN persons p "
                          "ON p.id = pl.person_id WHERE pl.id = ?", (plate_id,)).fetchone()
        return dict(r) if r else None

    def find_plate(self, plate: str) -> dict | None:
        """Registry row of a normalized plate, or ``None``."""
        with self.connect() as c:
            r = c.execute("SELECT * FROM plates WHERE plate = ?", (plate,)).fetchone()
        return dict(r) if r else None

    def list_plates(self) -> list[dict]:
        """Every registered plate with the linked person's name and the time of its last read."""
        with self.connect() as c:
            rows = c.execute(
                "SELECT pl.*, p.first_name, p.last_name, (SELECT MAX(ts) FROM plate_reads r WHERE r.plate_id = pl.id) "
                "AS last_read FROM plates pl LEFT JOIN persons p ON p.id = pl.person_id ORDER BY pl.plate").fetchall()
        return [dict(r) for r in rows]

    def add_plate_read(self, plate: str, confidence: float, region: str | None, status: str, plate_id: int | None,
                       track_id: int | None, direction: str, image_path: str | None, ts: float | None = None) -> int:
        """Record a confirmed plate read (traceability); returns its id."""
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO plate_reads(ts, plate, confidence, region, status, plate_id, track_id, direction, action, "
                "image_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts or time.time(), plate, confidence, region, status, plate_id, track_id, direction, "none", image_path))
            return int(cur.lastrowid)

    def set_plate_read_action(self, read_id: int, action: str, direction: str | None = None) -> None:
        """Record what was done after a read (``opened``, ``closed``, ``none``) and the latest direction."""
        with self.connect() as c:
            if direction:
                c.execute("UPDATE plate_reads SET action = ?, direction = ? WHERE id = ?", (action, direction, read_id))
            else:
                c.execute("UPDATE plate_reads SET action = ? WHERE id = ?", (action, read_id))

    def list_plate_reads(self, start_ts: float | None = None, end_ts: float | None = None, plate: str | None = None,
                         status: str | None = None, time_from: str | None = None, time_to: str | None = None,
                         limit: int = 200) -> list[dict]:
        """Plate reads, newest first, with the registry label and the linked person's name."""
        sql, args = " WHERE 1=1", []
        for cond, val in (("r.ts >= ?", start_ts), ("r.ts < ?", end_ts), ("r.status = ?", status)):
            if val is not None and val != "":
                sql += f" AND {cond}"
                args.append(val)
        if plate:
            sql += " AND r.plate LIKE ?"
            args.append(f"%{plate}%")
        tod, targs = time_of_day_clause("r.ts", time_from, time_to)
        with self.connect() as c:
            rows = c.execute(
                "SELECT r.*, pl.display, pl.label, pl.person_id, p.first_name, p.last_name FROM plate_reads r "  # noqa: S608  # nosec B608
                "LEFT JOIN plates pl ON pl.id = r.plate_id LEFT JOIN persons p ON p.id = pl.person_id"
                + sql + tod + " ORDER BY r.id DESC LIMIT ?", (*args, *targs, limit)).fetchall()
        return [dict(r) for r in rows]

    def purge(self, unknown_before_ts: float, events_before_ts: float,
              sightings_before_ts: float | None = None, plate_reads_before_ts: float | None = None) -> list[str]:
        """Apply the GDPR retention periods.

        Deletes unknown faces, sightings, events, closed sessions and greetings older
        than their respective cutoffs.

        Args:
            unknown_before_ts: Cutoff for unknown faces.
            events_before_ts: Cutoff for events, closed sessions and greetings.
            sightings_before_ts: Cutoff for sightings, or ``None`` to keep them.
            plate_reads_before_ts: Cutoff for plate reads, or ``None`` to keep them.

        Returns:
            Paths of the snapshot files the caller must delete.
        """
        with self.connect() as c:
            files = [r[0] for r in c.execute(
                "SELECT image_path FROM unknown_faces WHERE created_at < ?", (unknown_before_ts,))]
            c.execute("DELETE FROM unknown_faces WHERE created_at < ?", (unknown_before_ts,))
            if sightings_before_ts is not None:
                files += [r[0] for r in c.execute(
                    "SELECT image_path FROM sightings WHERE ts < ? AND image_path IS NOT NULL",
                    (sightings_before_ts,))]
                c.execute("DELETE FROM sightings WHERE ts < ?", (sightings_before_ts,))
            if plate_reads_before_ts is not None:
                files += [r[0] for r in c.execute(
                    "SELECT image_path FROM plate_reads WHERE ts < ? AND image_path IS NOT NULL",
                    (plate_reads_before_ts,))]
                c.execute("DELETE FROM plate_reads WHERE ts < ?", (plate_reads_before_ts,))
            c.execute("DELETE FROM events WHERE ts < ?", (events_before_ts,))
            # Session history (IP, login, logout) follows the event log retention.
            c.execute("DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (events_before_ts,))
            c.execute("DELETE FROM greetings WHERE day < ?", (date.fromtimestamp(events_before_ts).isoformat(),))
        return files
