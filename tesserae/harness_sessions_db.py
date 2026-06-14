"""SQLite-backed sessions index (append-friendly store).

Replaces the delete-then-rewrite glob behavior of
:class:`tesserae.harness_sessions.HarnessSessionStore`. With a live tailer
firing per turn, the legacy store's ``unlink(*/*.json)`` + full rewrite (O(N)
on every event) and ``glob(*/*.json)`` read path are untenable. This module
keys sessions on ``id`` with ``INSERT OR REPLACE`` (O(1) per session) and adds
a durable ``last_turn_offset`` so a daemon restart resumes tailing without
replaying from offset 0 (SESS-01 restart-resume; SESS-03 append store).

The connection/upsert pattern is copied from
:mod:`tesserae.graph_stores.sqlite` (short-lived ``sqlite3.connect`` per call,
a static ``_ensure_schema``) — NOT imported, to keep this module standalone and
dependency-light (stdlib ``sqlite3``/``json``/``datetime``/``pathlib`` plus the
``HarnessSession`` import).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .harness_sessions import (
    HarnessSession,
    is_tesserae_internal_session,
    session_matches_project,
)


class HarnessSessionsDB:
    """SQLite sessions index with O(1) upsert and durable tail offsets.

    Every public method opens a short-lived connection, commits, and closes
    (matching the :class:`SqliteGraphStore` pattern) so multiple daemon threads
    and the compile path can safely touch the same database file.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            self._ensure_schema(con)
            con.commit()

    # ------------------------------------------------------------------ #
    # Sessions                                                            #
    # ------------------------------------------------------------------ #

    def upsert(
        self,
        session: HarnessSession,
        jsonl_path: str | Path | None = None,
        last_offset: int = 0,
    ) -> None:
        """Insert or replace one session row keyed on ``session.id`` (O(1))."""
        with self._connect() as con:
            self._upsert_session(con, session, jsonl_path, last_offset)
            con.commit()

    def upsert_with_offset(
        self,
        session: HarnessSession,
        jsonl_path: str | Path,
        last_offset: int,
    ) -> None:
        """Atomically write the session row AND advance the tail offset.

        ``upsert`` + ``set_offset`` as two separate transactions can be torn
        apart by a crash, leaving the session stored but the resume offset
        stale (re-emitting already-ingested turns on restart). This method
        commits both writes in ONE transaction so the persisted session and
        the offset it was derived from can never disagree (Codex #5).
        """
        with self._connect() as con:
            self._upsert_session(con, session, jsonl_path, last_offset)
            self._set_offset(con, jsonl_path, last_offset)
            con.commit()

    @staticmethod
    def _upsert_session(
        con: sqlite3.Connection,
        session: HarnessSession,
        jsonl_path: str | Path | None,
        last_offset: int,
    ) -> None:
        session_json = json.dumps(
            session.to_dict(), ensure_ascii=False, sort_keys=True
        )
        row = (
            session.id,
            session.harness,
            session.project_root,
            session_json,
            str(jsonl_path) if jsonl_path is not None else None,
            int(last_offset),
            datetime.now(timezone.utc).isoformat(),
        )
        con.execute(
            """
            insert or replace into sessions
            (id, harness, project_root, session_json, source_jsonl_path,
             last_turn_offset, updated_at)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )

    def list_for_project(self, project_root: str | Path) -> List[HarnessSession]:
        """Return sessions for ``project_root``, read via SELECT (no FS glob).

        The ``project_root = ?`` WHERE clause is the fast path; each surfaced
        row is re-confirmed with :func:`session_matches_project` so a row stored
        with an unresolved path is still matched correctly.
        """
        resolved = str(Path(project_root).resolve())
        with self._connect() as con:
            rows = con.execute(
                "select session_json from sessions"
                " where project_root = ? or project_root = ?",
                (str(project_root), resolved),
            ).fetchall()
            # Fall back to a full scan only if the indexed lookup found nothing
            # (covers rows persisted under a differently-normalized path).
            if not rows:
                rows = con.execute("select session_json from sessions").fetchall()
        sessions: List[HarnessSession] = []
        seen: set[str] = set()
        for (session_json,) in rows:
            try:
                payload = json.loads(session_json)
            except (json.JSONDecodeError, TypeError):
                continue
            sess = HarnessSession.from_dict(payload)
            if sess.id in seen:
                continue
            if session_matches_project(sess, project_root):
                seen.add(sess.id)
                sessions.append(sess)
        sessions.sort(key=lambda s: (s.started_at or "", s.harness, s.slug), reverse=True)
        return sessions

    def count_sessions(self) -> int:
        """Return the total number of stored session rows.

        Lets callers distinguish a *legitimately empty* DB (quiet legacy-glob
        fallback) from a *read error* (which must be logged loudly) — Codex #7.
        """
        with self._connect() as con:
            return int(con.execute("select count(*) from sessions").fetchone()[0])

    def prune_internal_sessions(self) -> int:
        """Delete rows that are Tesserae's OWN captured LLM calls (self-capture).

        Retroactive cleanup for DBs polluted before the discovery/tailer filter
        existed. Returns the number of session rows removed.
        """
        to_delete: List[str] = []
        with self._connect() as con:
            rows = con.execute("select session_json from sessions").fetchall()
            for (session_json,) in rows:
                try:
                    sess = HarnessSession.from_dict(json.loads(session_json))
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
                if is_tesserae_internal_session(sess):
                    to_delete.append(sess.id)
            for sid in to_delete:
                con.execute("delete from sessions where id = ?", (sid,))
        return len(to_delete)

    # ------------------------------------------------------------------ #
    # Tail offsets (restart-resume)                                       #
    # ------------------------------------------------------------------ #

    def get_offset(self, jsonl_path: str | Path) -> int:
        """Return the persisted byte offset for ``jsonl_path`` (0 if absent)."""
        with self._connect() as con:
            row = con.execute(
                "select last_turn_offset from tail_offsets where jsonl_path = ?",
                (str(jsonl_path),),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def set_offset(self, jsonl_path: str | Path, offset: int) -> None:
        """Persist the byte offset for ``jsonl_path`` (INSERT OR REPLACE)."""
        with self._connect() as con:
            self._set_offset(con, jsonl_path, offset)
            con.commit()

    @staticmethod
    def _set_offset(
        con: sqlite3.Connection, jsonl_path: str | Path, offset: int
    ) -> None:
        con.execute(
            """
            insert or replace into tail_offsets
            (jsonl_path, last_turn_offset, updated_at)
            values (?, ?, ?)
            """,
            (str(jsonl_path), int(offset), datetime.now(timezone.utc).isoformat()),
        )

    def all_offsets(self) -> Dict[str, int]:
        """Return ``{jsonl_path: offset}`` for seeding the tailer on startup."""
        with self._connect() as con:
            rows = con.execute(
                "select jsonl_path, last_turn_offset from tail_offsets"
            ).fetchall()
        return {str(path): int(offset) for path, offset in rows}

    # ------------------------------------------------------------------ #
    # Meta (small durable key/value: e.g. the tailer's codex dir floor)   #
    # ------------------------------------------------------------------ #

    def get_meta(self, key: str) -> "str | None":
        """Return the persisted meta value for ``key`` (None if absent)."""
        with self._connect() as con:
            row = con.execute("select value from meta where key = ?", (key,)).fetchone()
        return str(row[0]) if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        """Persist ``key`` -> ``value`` (INSERT OR REPLACE)."""
        with self._connect() as con:
            con.execute(
                "insert or replace into meta (key, value, updated_at) values (?, ?, ?)",
                (key, str(value), datetime.now(timezone.utc).isoformat()),
            )
            con.commit()

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    # Block up to this long when another writer holds the lock instead of
    # raising ``OperationalError: database is locked`` immediately. Lets the
    # compile path and concurrent tailer writes coexist (Codex #7 lock case).
    _BUSY_TIMEOUT_S = 5.0

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=self._BUSY_TIMEOUT_S)
        con.execute("pragma busy_timeout = %d" % int(self._BUSY_TIMEOUT_S * 1000))
        return con

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        """Create the sessions + tail_offsets schema if absent (idempotent)."""
        con.execute(
            """
            create table if not exists sessions (
                id                text primary key,
                harness           text not null,
                project_root      text not null,
                session_json      text not null,
                source_jsonl_path text,
                last_turn_offset  integer not null default 0,
                updated_at        text not null
            )
            """
        )
        con.execute(
            "create index if not exists idx_sessions_project on sessions(project_root)"
        )
        con.execute(
            """
            create table if not exists tail_offsets (
                jsonl_path       text primary key,
                last_turn_offset integer not null default 0,
                updated_at       text not null
            )
            """
        )
        con.execute(
            """
            create table if not exists meta (
                key        text primary key,
                value      text not null,
                updated_at text not null
            )
            """
        )
