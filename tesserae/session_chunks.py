"""Daily session chunk store — ``.tesserae/session_chunks.db``.

The activity summary (and everything downstream of :func:`scan_messages`)
re-parses every in-window transcript across every AI-account root on every
call. This module persists normalised turns *once*, bucketed by KST day label
(the same label semantics as :class:`tesserae.activity_summary.Window`), so a
fully covered past day is served from SQLite instead of a raw rescan.

Writers
-------
1. **Live** — the engine daemon's :class:`SessionTailer` chunk hook calls
   :func:`record_live_turns` per poll with the turns it just tailed. Cheap,
   append-only, and it must never raise into the daemon loop.
2. **Backfill** — :func:`backfill` walks existing transcripts (reusing
   ``activity_summary.iter_project_transcripts``) under a NON-BLOCKING flock on
   ``.tesserae/session_chunks.lock`` with skip-if-held semantics (the
   ``locking.compile_lock`` pattern). There is deliberately NO SessionEnd hook
   writer — backgrounded SessionEnd writers pile up (recorded failure mode).

Reader
------
:func:`served_messages_for_windows` returns ``{window_label: [turn rows]}`` for
windows that are (a) exact KST-aligned single days, (b) strictly before today
(today is still being written), and (c) covered for every requested harness in
``day_coverage``. Anything else — including any DB error — yields nothing, so
the caller's raw scan stays the source of truth (never-raise posture, imitating
``code_graph``). A ``schema_version`` mismatch drops and rebuilds the store
empty; coverage vanishes with it, so the raw fallback stays correct.

Parity invariant: for a fully covered day, chunk-served turns must equal what
the raw scan would have produced (same ts/role/name/text/session-key/harness).

Storage follows the short-lived-connection pattern of
:class:`tesserae.harness_sessions_db.HarnessSessionsDB` (one ``sqlite3.connect``
per operation, WAL, busy timeout) so daemon threads, backfill, and readers can
share the file safely.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows: lock degrades to no-op
    fcntl = None  # type: ignore[assignment]

from .activity_summary import KST, Window, parse_ts

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DB_FILENAME = "session_chunks.db"
LOCK_FILENAME = "session_chunks.lock"

# Harness names as the activity summary emits them (MessageItem.harness).
ALL_HARNESSES: Tuple[str, ...] = ("claude-code", "codex")


def chunks_db_path(project_root: str | Path) -> Path:
    return Path(project_root) / ".tesserae" / DB_FILENAME


def chunks_lock_path(project_root: str | Path) -> Path:
    return Path(project_root) / ".tesserae" / LOCK_FILENAME


def day_label(ts: datetime) -> str:
    """The KST day label for a tz-aware instant — matches ``Window.label``."""
    return ts.astimezone(KST).strftime("%Y-%m-%d")


def _today_label(now: Optional[datetime] = None) -> str:
    return day_label(now if now is not None else datetime.now(KST))


class SessionChunksDB:
    """Turns + day-coverage store; short-lived connection per operation."""

    _BUSY_TIMEOUT_S = 5.0

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            self._ensure_schema(con)
            row = con.execute(
                "select value from meta where key = 'schema_version'"
            ).fetchone()
            version = int(row[0]) if row is not None else None
            if version != SCHEMA_VERSION:
                if version is not None:
                    # Mismatch: drop and rebuild EMPTY. Coverage disappears with
                    # the tables, so readers fall back to the raw scan — correct
                    # by construction, never a partial migration.
                    con.execute("drop table if exists turns")
                    con.execute("drop table if exists day_coverage")
                    con.execute("drop table if exists meta")
                    self._ensure_schema(con)
                con.execute(
                    "insert or replace into meta (key, value) values ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            con.commit()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=self._BUSY_TIMEOUT_S)
        con.execute("pragma busy_timeout = %d" % int(self._BUSY_TIMEOUT_S * 1000))
        con.execute("pragma journal_mode = WAL")
        return con

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        con.execute(
            """
            create table if not exists turns (
                day          text not null,
                harness      text not null,
                session_path text not null,
                session_id   text not null,
                ts           text not null,
                role         text not null,
                text         text not null,
                meta         text not null default '{}',
                text_hash    text not null
            )
            """
        )
        con.execute("create index if not exists idx_turns_day on turns(day)")
        # Idempotence key: a re-delivered turn (tailer restart from offset 0,
        # backfill over tailer-written rows) is INSERT OR IGNOREd away.
        con.execute(
            """
            create unique index if not exists idx_turns_identity
            on turns(session_path, ts, role, text_hash)
            """
        )
        con.execute(
            """
            create table if not exists day_coverage (
                day        text not null,
                harness    text not null,
                source     text not null,
                updated_at text not null,
                primary key (day, harness)
            )
            """
        )
        con.execute(
            """
            create table if not exists meta (
                key   text primary key,
                value text not null
            )
            """
        )

    # ------------------------------------------------------------------ #
    # Writes                                                              #
    # ------------------------------------------------------------------ #

    def record_turns(
        self,
        harness: str,
        session_path: str | Path,
        session_id: str,
        turns: Sequence[dict],
    ) -> int:
        """Insert normalised turns (idempotent); return the number inserted.

        Turns without a parseable timestamp are skipped — the raw scan skips
        them too (``parse_ts`` gate in ``scan_messages``), so parity holds.
        Genuinely identical turns *within one delivery* are preserved via an
        occurrence suffix in the identity hash; re-delivered batches map onto
        the same keys and are ignored.
        """
        rows: List[Tuple[str, str, str, str, str, str, str, str, str]] = []
        occurrence: Dict[Tuple[str, str, str], int] = {}
        spath = str(session_path)
        for turn in turns:
            raw_ts = str(turn.get("timestamp") or "")
            ts = parse_ts(raw_ts)
            if ts is None:
                continue
            role = str(turn.get("role") or "")
            text = str(turn.get("text") or "")
            name = turn.get("name")
            meta = json.dumps({"name": str(name)} if name else {}, sort_keys=True)
            key = (raw_ts, role, text)
            nth = occurrence.get(key, 0)
            occurrence[key] = nth + 1
            digest = hashlib.sha1(
                ("%s\x00%d" % (text, nth)).encode("utf-8", "replace")
            ).hexdigest()
            rows.append(
                (day_label(ts), harness, spath, str(session_id), raw_ts, role, text, meta, digest)
            )
        if not rows:
            return 0
        with self._connect() as con:
            before = con.total_changes
            con.executemany(
                """
                insert or ignore into turns
                (day, harness, session_path, session_id, ts, role, text, meta, text_hash)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            inserted = con.total_changes - before
            con.commit()
        return int(inserted)

    def mark_coverage(
        self,
        days: Iterable[str],
        *,
        source: str,
        harnesses: Sequence[str] = ALL_HARNESSES,
    ) -> None:
        """Upsert ``day_coverage`` rows for every ``(day, harness)`` pair."""
        stamp = datetime.now(KST).isoformat()
        with self._connect() as con:
            for day in days:
                for harness in harnesses:
                    con.execute(
                        """
                        insert or replace into day_coverage
                        (day, harness, source, updated_at) values (?, ?, ?, ?)
                        """,
                        (str(day), str(harness), str(source), stamp),
                    )
            con.commit()

    # ------------------------------------------------------------------ #
    # Reads                                                               #
    # ------------------------------------------------------------------ #

    def covered_days(self, harnesses: Sequence[str] = ALL_HARNESSES) -> "set[str]":
        """Days with a coverage row for EVERY requested harness."""
        wanted = sorted(set(harnesses))
        if not wanted:
            return set()
        marks = ",".join("?" for _ in wanted)
        with self._connect() as con:
            rows = con.execute(
                f"select day from day_coverage where harness in ({marks})"
                " group by day having count(distinct harness) >= ?",
                (*wanted, len(wanted)),
            ).fetchall()
        return {str(day) for (day,) in rows}

    def coverage_rows(self) -> List[dict]:
        with self._connect() as con:
            rows = con.execute(
                "select day, harness, source, updated_at from day_coverage order by day, harness"
            ).fetchall()
        return [
            {"day": d, "harness": h, "source": s, "updated_at": u}
            for d, h, s, u in rows
        ]

    def turns_for_day(
        self,
        day: str,
        *,
        harnesses: Sequence[str] = ALL_HARNESSES,
        turn_limit: int = 100_000,
    ) -> List[dict]:
        """Stored turns for ``day``, capped at ``turn_limit`` per session file
        (mirroring the raw parsers' per-transcript ``limit``)."""
        wanted = sorted(set(harnesses))
        if not wanted:
            return []
        marks = ",".join("?" for _ in wanted)
        with self._connect() as con:
            rows = con.execute(
                "select harness, session_path, session_id, ts, role, text, meta"
                f" from turns where day = ? and harness in ({marks})"
                " order by session_path, ts, rowid",
                (str(day), *wanted),
            ).fetchall()
        out: List[dict] = []
        per_session: Dict[str, int] = {}
        for harness, session_path, session_id, ts, role, text, meta in rows:
            n = per_session.get(session_path, 0)
            if n >= max(1, int(turn_limit)):
                continue
            per_session[session_path] = n + 1
            try:
                meta_obj = json.loads(meta) if meta else {}
            except (json.JSONDecodeError, TypeError):
                meta_obj = {}
            out.append(
                {
                    "harness": str(harness),
                    "session_path": str(session_path),
                    "session_id": str(session_id),
                    "ts": str(ts),
                    "role": str(role),
                    "text": str(text),
                    "name": meta_obj.get("name"),
                }
            )
        return out


# --------------------------------------------------------------------------- #
# Writer 1 — live tailer hook (engine daemon)                                  #
# --------------------------------------------------------------------------- #
def record_live_turns(
    db: SessionChunksDB,
    harness: str,
    session_path: str | Path,
    session_key: str,
    turns: Sequence[dict],
) -> int:
    """Append tailed turns + upsert per-harness ``day_coverage(source='tailer')``.

    NEVER raises — it runs inside the daemon's tail tick, and chunk capture is
    an optimization, not a correctness dependency (raw scan remains the
    fallback). Returns the number of rows inserted (0 on any failure).
    """
    try:
        inserted = db.record_turns(harness, session_path, session_key, turns)
        days = set()
        for turn in turns:
            ts = parse_ts(str(turn.get("timestamp") or ""))
            if ts is not None:
                days.add(day_label(ts))
        if days:
            db.mark_coverage(sorted(days), source="tailer", harnesses=(harness,))
        return inserted
    except Exception as exc:  # noqa: BLE001 — never raise into the daemon loop
        logger.warning("session chunks: live write failed (%s); skipping", exc)
        return 0


# --------------------------------------------------------------------------- #
# Writer 2 — backfill from existing transcripts (non-blocking flock)           #
# --------------------------------------------------------------------------- #
@dataclass
class BackfillResult:
    skipped: bool = False
    reason: str = ""
    turns_inserted: int = 0
    days_covered: int = 0
    days: List[str] = field(default_factory=list)


def backfill(
    project_root: str | Path,
    since: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> BackfillResult:
    """Walk existing transcripts into the chunk store; skip-if-held lock.

    Reuses ``activity_summary.iter_project_transcripts`` so backfill discovers
    exactly the transcripts the raw scan would. Coverage is claimed for
    ``[since (or the earliest observed turn day), yesterday]`` for BOTH
    harnesses — a file whose mtime predates the floor cannot hold newer turns,
    so every day at/after the floor is fully observed. Today is never covered
    (still being written; the reader excludes it anyway).

    The flock on ``.tesserae/session_chunks.lock`` is NON-BLOCKING: a held lock
    returns ``BackfillResult(skipped=True)`` immediately (compile_lock pattern,
    skip-if-held — no queueing, no SessionEnd-style pileups).
    """
    root = Path(project_root).resolve()
    tesserae_dir = root / ".tesserae"
    tesserae_dir.mkdir(parents=True, exist_ok=True)
    handle = (tesserae_dir / LOCK_FILENAME).open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return BackfillResult(
                    skipped=True, reason="another chunk-backfill holds the lock"
                )
        return _backfill_locked(root, since, now)
    finally:
        handle.close()  # closing the fd releases the flock


def _backfill_locked(
    root: Path, since: Optional[str], now: Optional[datetime]
) -> BackfillResult:
    from .activity_summary import iter_project_transcripts
    from .harness_sessions import _claude_turns, _codex_turns, _parse_jsonl

    now_dt = now if now is not None else datetime.now(KST)
    today = day_label(now_dt)
    db = SessionChunksDB(root / ".tesserae" / DB_FILENAME)
    if since:
        since_ts = parse_ts(since)
        if since_ts is None:
            raise ValueError(
                f"--since: could not parse {since!r}; use YYYY-MM-DD"
            )
        start = _kst_midnight(day_label(since_ts))
        start_day: Optional[str] = day_label(since_ts)
    else:
        # Incremental resume: restart from the LAST covered day (not +1 — cheap
        # one-day overlap heals turns that landed after coverage was claimed;
        # record_turns dedupes). First run still walks full history.
        covered = db.covered_days()
        if covered:
            start_day = max(covered)
            start = _kst_midnight(start_day)
        else:
            start = datetime(1970, 1, 1, tzinfo=KST)
            start_day = None

    window = Window(start=start, end=now_dt, label="chunk-backfill")
    inserted = 0
    min_turn_day: Optional[str] = None
    for _name, harness, path, key in iter_project_transcripts(
        [(root.name, root)], [window]
    ):
        rows = _parse_jsonl(path)
        turns = (
            _codex_turns(rows, limit=100_000)
            if harness == "codex"
            else _claude_turns(rows, limit=100_000)
        )
        inserted += db.record_turns(harness, path, key, turns)
        for turn in turns:
            ts = parse_ts(str(turn.get("timestamp") or ""))
            if ts is None:
                continue
            d = day_label(ts)
            if min_turn_day is None or d < min_turn_day:
                min_turn_day = d

    first_day = start_day if start_day is not None else min_turn_day
    days = _day_range(first_day, today) if first_day else []
    if days:
        db.mark_coverage(days, source="backfill", harnesses=ALL_HARNESSES)
    return BackfillResult(
        turns_inserted=inserted, days_covered=len(days), days=days
    )


def _kst_midnight(day: str) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, tzinfo=KST)


def _day_range(first_day: str, today: str) -> List[str]:
    """Day labels from ``first_day`` through YESTERDAY (never ``today``)."""
    out: List[str] = []
    cursor = _kst_midnight(first_day)
    end = _kst_midnight(today)  # exclusive — today is still being written
    while cursor < end:
        out.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- #
# Reader — coverage-gated fast path for scan_messages                          #
# --------------------------------------------------------------------------- #
def served_messages_for_windows(
    project_root: str | Path,
    windows: Sequence[Window],
    *,
    harnesses: Sequence[str] = ALL_HARNESSES,
    turn_limit: int = 100_000,
    now: Optional[datetime] = None,
) -> Dict[str, List[dict]]:
    """``{window_label: [turn rows]}`` for chunk-servable windows only.

    A window is servable iff it is an exact KST-aligned single day, strictly
    before today, AND covered for every requested harness. NEVER raises: any
    DB problem (missing file, corruption, locked, schema surprise) returns
    ``{}`` so the caller's raw scan takes over unchanged.
    """
    try:
        db_path = chunks_db_path(project_root)
        if not db_path.is_file():
            return {}
        today = _today_label(now)
        wanted: Dict[str, Window] = {}
        for w in windows:
            label = w.label
            if label >= today:
                continue  # today (and anything not a past date) stays raw
            try:
                start = _kst_midnight(label)
            except (ValueError, AttributeError):
                continue  # not a plain day label (e.g. "a..b" span) — raw
            if w.start != start or w.end != start + timedelta(days=1):
                continue  # not KST-day-aligned (e.g. UTC windows) — raw
            wanted[label] = w
        if not wanted:
            return {}
        db = SessionChunksDB(db_path)
        covered = db.covered_days(harnesses)
        out: Dict[str, List[dict]] = {}
        for label in wanted:
            if label not in covered:
                continue
            out[label] = db.turns_for_day(
                label, harnesses=harnesses, turn_limit=turn_limit
            )
        return out
    except Exception as exc:  # noqa: BLE001 — degrade to the raw scan, never raise
        logger.debug("session chunks unavailable (%s); using raw scan", exc)
        return {}
