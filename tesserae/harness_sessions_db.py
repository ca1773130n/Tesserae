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

from .harness_sessions import HarnessSession, session_matches_project


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
        with self._connect() as con:
            con.execute(
                """
                insert or replace into sessions
                (id, harness, project_root, session_json, source_jsonl_path,
                 last_turn_offset, updated_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            con.commit()

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
            con.execute(
                """
                insert or replace into tail_offsets
                (jsonl_path, last_turn_offset, updated_at)
                values (?, ?, ?)
                """,
                (str(jsonl_path), int(offset), datetime.now(timezone.utc).isoformat()),
            )
            con.commit()

    def all_offsets(self) -> Dict[str, int]:
        """Return ``{jsonl_path: offset}`` for seeding the tailer on startup."""
        with self._connect() as con:
            rows = con.execute(
                "select jsonl_path, last_turn_offset from tail_offsets"
            ).fetchall()
        return {str(path): int(offset) for path, offset in rows}

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

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
