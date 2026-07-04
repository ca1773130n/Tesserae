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
from typing import List, Optional


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
