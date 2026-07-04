"""Activity summary — window resolution, item types, gatherers, rendering.

This module powers the daily/weekly activity summary. Each artifact is
windowed by its *own* timestamp (message/finding turn timestamp, commit/PR
git/GitHub date, ingested-doc mtime), never by a session's long-running
``started_at``/``ended_at``. Windows are half-open ``[start, end)`` and
tz-aware so edge inclusion is unambiguous and output is reproducible.

Task 4 lands the primitives: :class:`Window`, :func:`resolve_windows`,
:func:`in_window`, :func:`parse_ts`, and the five item dataclasses the
gatherers (later tasks) emit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tesserae.harness_sessions import _claude_turns, _codex_turns, _parse_jsonl
from tesserae.harness_sessions_db import HarnessSessionsDB
from tesserae.research_graph import SESSION_FINDING_TYPES

# String values of the six structured session-finding node types. Findings are
# matched on ``node.type.value`` (a string), so mirror the canonical enum set
# rather than re-listing the names — one source of truth, no drift.
_FINDING_TYPE_VALUES = {t.value for t in SESSION_FINDING_TYPES}


# --------------------------------------------------------------------------- #
# Window resolution
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Window:
    """A half-open time window ``[start, end)``. Both bounds tz-aware.

    ``start`` is inclusive, ``end`` is exclusive. ``label`` is the stable,
    human-facing name used in filenames and markdown headings.
    """

    start: datetime
    end: datetime
    label: str


def _local_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc


def _midnight(day: str, tz: tzinfo) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, tzinfo=tz)


def resolve_windows(
    *,
    day: Optional[str] = None,
    week: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    tz: Optional[tzinfo] = None,
) -> List[Window]:
    """Resolve CLI/MCP time selectors into a list of daily windows.

    - ``day="YYYY-MM-DD"`` → one 24h window ``[00:00, +24h)``.
    - ``week="YYYY-MM-DD"`` → seven consecutive daily windows ending on that
      day (oldest-first). ``week=""`` (empty-but-present, i.e. the bare
      ``--week`` flag) → the last 7 days ending today.
    - ``since``/``until`` → one window spanning the parsed bounds.
    - nothing → today's single 24h window.

    Default tz is the local zone.
    """
    tz = tz or _local_tz()
    if day:
        s = _midnight(day, tz)
        return [Window(s, s + timedelta(days=1), day)]
    if week is not None:
        end_day = (
            _midnight(week, tz)
            if week
            else _midnight(datetime.now(tz).strftime("%Y-%m-%d"), tz)
        )
        first = end_day - timedelta(days=6)
        out: List[Window] = []
        for i in range(7):
            s = first + timedelta(days=i)
            out.append(Window(s, s + timedelta(days=1), s.strftime("%Y-%m-%d")))
        return out
    if since or until:
        s = parse_ts(since) if since else datetime(1970, 1, 1, tzinfo=tz)
        e = parse_ts(until) if until else datetime.now(tz)
        return [Window(s, e, f"{s.date()}..{e.date()}")]
    today = datetime.now(tz).strftime("%Y-%m-%d")
    s = _midnight(today, tz)
    return [Window(s, s + timedelta(days=1), today)]


def in_window(ts: datetime, w: Window) -> bool:
    """True iff ``w.start <= ts < w.end`` (half-open)."""
    return w.start <= ts < w.end


def parse_ts(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``.

    Returns a tz-aware datetime (naive input is assumed UTC), or ``None`` when
    the value is empty or not parseable.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Item dataclasses — one per artifact kind the gatherers emit
# --------------------------------------------------------------------------- #
@dataclass
class MessageItem:
    """A single conversation turn dated by the turn's own timestamp."""

    ts: datetime
    role: str
    name: Optional[str]
    text: str
    project: str
    session_id: str
    harness: str


@dataclass
class FindingItem:
    """A compiled session finding dated by its source turn's timestamp."""

    ts: datetime
    kind: str
    body: str
    project: str
    session_id: Optional[str]
    node_id: str


@dataclass
class CommitItem:
    """A git commit dated by its author date."""

    ts: datetime
    sha: str
    author: str
    subject: str
    project: str


@dataclass
class PRItem:
    """A pull-request event (opened/merged/closed) dated by that event."""

    ts: datetime
    number: int
    title: str
    state: str
    event: str
    project: str


@dataclass
class DocItem:
    """An ingested document dated by its own updated_at/created/mtime."""

    ts: datetime
    title: str
    source_path: str
    project: str


# --------------------------------------------------------------------------- #
# Message gatherer — turn-level, uncapped, never windowed on started_at
# --------------------------------------------------------------------------- #
def _turns_for(session: object, turn_limit: int) -> List[Dict[str, object]]:
    """Parse a session's transcript into its full, harness-aware turn list.

    ``turn_limit`` is effectively unbounded (default 100k) so no in-window turn
    is dropped by the head cap the compiler uses for its own 300-turn slice.
    """
    rows = _parse_jsonl(Path(getattr(session, "raw_transcript_path", "") or ""))
    if getattr(session, "harness", "") == "codex":
        return _codex_turns(rows, limit=turn_limit)
    return _claude_turns(rows, limit=turn_limit)


def gather_messages(
    project: str,
    root: str,
    window: Window,
    *,
    turn_limit: int = 100_000,
) -> Tuple[List[MessageItem], Dict[str, List[Dict[str, object]]]]:
    """Gather conversation turns whose *own* timestamp falls inside ``window``.

    Reads the project's ``.tesserae/harness_sessions.db`` and, for every stored
    session, parses its transcript into turns. A turn is kept when
    ``parse_ts(turn["timestamp"]) ∈ window`` — the session's long-running
    ``started_at``/``ended_at`` is never consulted (Global Constraint). Returns
    the in-window :class:`MessageItem` list plus a ``session_id -> full turns
    list`` map, which Task 6 reuses to date findings by their source turn.
    """
    db = HarnessSessionsDB(Path(root) / ".tesserae" / "harness_sessions.db")
    messages: List[MessageItem] = []
    turns_by_session: Dict[str, List[Dict[str, object]]] = {}
    for session in db.list_for_project(root):
        tpath = Path(session.raw_transcript_path or "")
        # Cheap prune: a transcript last written before the window opened cannot
        # hold any in-window turn (a stored turn is never newer than the file's
        # own mtime), so skip it without parsing.
        try:
            mtime = tpath.stat().st_mtime
        except OSError:
            continue
        if mtime < window.start.timestamp():
            continue
        turns = _turns_for(session, turn_limit)
        turns_by_session[session.id] = turns
        for turn in turns:
            ts = parse_ts(str(turn.get("timestamp") or ""))
            if ts and in_window(ts, window):
                name = turn.get("name")
                messages.append(
                    MessageItem(
                        ts=ts,
                        role=str(turn.get("role") or ""),
                        name=str(name) if name else None,
                        text=str(turn.get("text") or ""),
                        project=project,
                        session_id=session.id,
                        harness=session.harness,
                    )
                )
    return messages, turns_by_session


# --------------------------------------------------------------------------- #
# Finding gatherer — dated by the source turn, compile-aligned, never started_at
# --------------------------------------------------------------------------- #
def gather_findings(
    project: str,
    graph: object,
    turns_by_session: Dict[str, List[Dict[str, object]]],
    window: Window,
) -> List[FindingItem]:
    """Gather compiled session findings whose *source turn* falls in ``window``.

    Each session-finding node (``SessionInsight``/``SessionDecision``/…) carries
    ``metadata["turn_ids"]`` — indices into the session's turn list assigned at
    compile time (``session_graph.py`` mints them against the default,
    HEAD-capped slice; the finding's body is stored as ``node.name``). A finding
    is dated by its *latest* source turn::

        ts = parse_ts(turns[max(turn_ids)]["timestamp"])

    resolved through the ``turns_by_session`` map produced by
    :func:`gather_messages`. Because ``_claude_turns``/``_codex_turns`` cap by
    keeping the HEAD (they ``break`` once ``len(turns) >= limit``), the indices
    produced with the 100k gather limit are identical to the compile-time
    default-limit (300) indices for every valid ``turn_id`` — so the map is
    reused directly, with no rebuild.

    A finding whose turns can't be resolved — its session is absent from the
    map, or every ``turn_id`` is out of range — is **skipped**. It is never
    dated from the session's long-running ``started_at`` (Global Constraint).
    The returned list is in ``graph.nodes`` order; the renderer (Task 9) sorts.
    """
    out: List[FindingItem] = []
    for node in graph.nodes:
        tname = getattr(node.type, "value", node.type)
        if tname not in _FINDING_TYPE_VALUES:
            continue
        meta = getattr(node, "metadata", None) or {}
        turns = turns_by_session.get(meta.get("session_id")) or []
        ids = [
            i
            for i in (meta.get("turn_ids") or [])
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(turns)
        ]
        if not ids:
            continue  # no resolvable source turn -> undated -> skip
        ts = parse_ts(str(turns[max(ids)].get("timestamp") or ""))
        if ts and in_window(ts, window):
            out.append(
                FindingItem(
                    ts=ts,
                    kind=str(tname),
                    body=str(meta.get("body") or getattr(node, "name", "") or ""),
                    project=project,
                    session_id=meta.get("session_id"),
                    node_id=node.id,
                )
            )
    return out
