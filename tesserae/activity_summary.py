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

import glob
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from tesserae.harness_sessions import (
    _claude_project_dir,
    _claude_turns,
    _codex_turns,
    _parse_jsonl,
    _root_supports_claude,
    _root_supports_codex,
    _rows_match_project,
    discover_harness_roots,
)
from tesserae.research_graph import SESSION_FINDING_TYPES, ResearchNodeType

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


# ponytail: pinned to KST (UTC+9, no DST) rather than the machine locale — the
# user is in Korea, which never shifts, so day/week midnights are unambiguous and
# there is no DST case to handle. Swap this constant if the deployment zone changes.
KST = timezone(timedelta(hours=9))


def _local_tz() -> tzinfo:
    return KST


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
        if since:
            s = parse_ts(since)
            if s is None:
                raise ValueError(
                    f"--since: could not parse {since!r}; use ISO-8601 "
                    "(e.g. 2026-07-04 or 2026-07-04T12:00:00Z)"
                )
        else:
            s = datetime(1970, 1, 1, tzinfo=tz)
        if until:
            e = parse_ts(until)
            if e is None:
                raise ValueError(
                    f"--until: could not parse {until!r}; use ISO-8601 "
                    "(e.g. 2026-07-04 or 2026-07-04T12:00:00Z)"
                )
        else:
            e = datetime.now(tz)
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
# Message scan — window-scoped, direct from ALL harness roots (all AI accounts)
# --------------------------------------------------------------------------- #
# The summary reads live transcripts across every discovered AI-account config
# root (``~/.claude*``, ``~/.codex*``), NOT the per-project ``harness_sessions.db``
# index. The index proved unreliable for completeness — a suspended/behind tailer
# silently drops every session started since it stalled — and the user's spec is
# explicit: "go through all session transcripts of registered projects with the
# ai backend config directories". A window only touches a handful of files, so a
# cheap ``mtime >= window.start`` prune keeps a full-account scan fast (~30s for
# all projects across all accounts). Turns are windowed on their OWN timestamp;
# a session's long-running ``started_at`` is never consulted (Global Constraint).
def scan_messages(
    projects: Sequence[Tuple[str, object]],
    windows: Sequence[Window],
    *,
    turn_limit: int = 100_000,
) -> Dict[str, Dict[str, List[MessageItem]]]:
    """One pass over all harness roots → ``{project_name: {window_label: [msgs]}}``.

    Claude transcripts are matched precisely by the project's encoded ``projects/``
    slug directory (including its ``--worktrees-*`` siblings); Codex transcripts
    are matched by Tesserae's own project matcher (:func:`_rows_match_project`)
    over the parsed rows. Each transcript is parsed at most once (deduped by real
    path so a symlinked ``~/.claude`` root isn't double-counted), and only files
    with ``mtime >= min(window.start)`` are opened.
    """
    out: Dict[str, Dict[str, List[MessageItem]]] = {
        name: {w.label: [] for w in windows} for name, _root in projects
    }
    for name, harness, path, key in iter_project_transcripts(projects, windows):
        rows = _parse_jsonl(path)
        turns = (
            _codex_turns(rows, limit=turn_limit)
            if harness == "codex"
            else _claude_turns(rows, limit=turn_limit)
        )
        for turn in turns:
            ts = parse_ts(str(turn.get("timestamp") or ""))
            if not ts:
                continue
            for w in windows:
                if in_window(ts, w):
                    nm = turn.get("name")
                    out[name][w.label].append(
                        MessageItem(
                            ts=ts,
                            role=str(turn.get("role") or ""),
                            name=str(nm) if nm else None,
                            text=str(turn.get("text") or ""),
                            project=name,
                            session_id=key,
                            harness=harness,
                        )
                    )
                    break
    return out


def iter_project_transcripts(
    projects: Sequence[Tuple[str, object]],
    windows: Sequence[Window],
) -> "Iterator[Tuple[str, str, Path, str]]":
    """Yield ``(project_name, harness, transcript_path, session_key)`` for every
    transcript across all harness roots that matches a project and was touched in
    the window (``mtime >= min(window.start)``), deduped by real path.

    Claude transcripts match by the project's encoded ``projects/`` slug dir
    (incl. ``--worktrees-*`` siblings); codex by :func:`_rows_match_project`.
    ``harness`` is ``"claude-code"`` or ``"codex"``; ``session_key`` is
    ``"<account-dir>:<file-stem>"``. Shared by :func:`scan_messages` and the
    decisions module so both discover transcripts identically.
    """
    roots = discover_harness_roots()
    floor = min(w.start for w in windows).timestamp()
    seen_files: set[str] = set()
    seen_roots: set[str] = set()

    def _fresh(path: str) -> bool:
        try:
            if os.stat(path).st_mtime < floor:
                return False
        except OSError:
            return False
        real = os.path.realpath(path)
        if real in seen_files:
            return False
        seen_files.add(real)
        return True

    for r in roots:
        rk = os.path.realpath(r)
        if rk in seen_roots:
            continue
        seen_roots.add(rk)
        acct = Path(r).name
        if _root_supports_claude(r):
            for name, root in projects:
                slug = _claude_project_dir(Path(root))
                for d in glob.glob(str(Path(r) / "projects" / (slug + "*"))):
                    dn = Path(d).name
                    if dn != slug and not dn.startswith(slug + "-"):
                        continue  # avoid a different project whose slug shares this prefix
                    for f in glob.glob(str(Path(d) / "*.jsonl")):
                        if _fresh(f):
                            yield name, "claude-code", Path(f), f"{acct}:{Path(f).stem}"
        if _root_supports_codex(r):
            for f in glob.glob(str(Path(r) / "sessions" / "**" / "*.jsonl"), recursive=True):
                if not _fresh(f):
                    continue
                rows = _parse_jsonl(Path(f))
                for name, root in projects:
                    if _rows_match_project(rows, Path(root)):
                        yield name, "codex", Path(f), f"{acct}:{Path(f).stem}"
                        break


# --------------------------------------------------------------------------- #
# Finding gatherer — dated by the source turn, compile-aligned, never started_at
# --------------------------------------------------------------------------- #
def gather_findings(
    project: str,
    graph: object,
    window: Window,
) -> List[FindingItem]:
    """Gather compiled session findings whose source turn falls in ``window``.

    A session-finding node (``SessionInsight``/``SessionDecision``/…) carries
    ``metadata["first_seen_at"]``, which the compiler sets from the *source
    turn's own timestamp* (``session_event.py``), falling back to the session's
    ``started_at`` only when that turn has no timestamp. We date the finding by
    ``first_seen_at`` but **drop the started_at-fallback case** — a finding whose
    ``first_seen_at`` equals its Session node's ``started_at`` is skipped, so a
    finding is never windowed by the session's long-running start (Global
    Constraint). Findings only exist for windows that have been compiled; a
    recent, un-compiled window yields none (that is correct, not a miss).
    The returned list is in ``graph.nodes`` order; the renderer sorts.
    """
    started_at_by_session: Dict[str, str] = {}
    for node in graph.nodes:
        if getattr(node.type, "value", node.type) == "Session":
            meta = getattr(node, "metadata", None) or {}
            sid = meta.get("session_id")
            if sid is not None:
                started_at_by_session[str(sid)] = str(meta.get("started_at") or "")

    out: List[FindingItem] = []
    for node in graph.nodes:
        tname = getattr(node.type, "value", node.type)
        if tname not in _FINDING_TYPE_VALUES:
            continue
        meta = getattr(node, "metadata", None) or {}
        raw = str(meta.get("first_seen_at") or "")
        if not raw:
            continue  # undated -> skip; never fall back to started_at
        sid = meta.get("session_id")
        if sid is not None and raw == started_at_by_session.get(str(sid)):
            continue  # first_seen_at fell back to the session's started_at -> drop
        ts = parse_ts(raw)
        if ts and in_window(ts, window):
            out.append(
                FindingItem(
                    ts=ts,
                    kind=str(tname),
                    body=str(meta.get("body") or getattr(node, "name", "") or ""),
                    project=project,
                    session_id=sid,
                    node_id=node.id,
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Git + PR gatherers — read live at summary time, drop the section on any error
# --------------------------------------------------------------------------- #
def _run(cmd: List[str], cwd: str, timeout: int = 20) -> Optional[str]:
    """Run ``cmd`` in ``cwd`` and return its stdout, or ``None`` on any failure.

    Mirrors ``raganything_refresh._git_head``'s capture+timeout pattern. A
    missing binary, a non-zero exit (not a git repo, no ``origin``, ``gh``
    unauthenticated), or a timeout all collapse to ``None`` so the caller can
    drop just this section rather than fail the whole summary (Global
    Constraint: graceful degradation).
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _is_repo_root(root: str) -> bool:
    """True iff ``root`` is itself the top level of a git work tree.

    ``git -C <dir>`` searches *upward* for a repo, so a non-repo directory
    nested inside a parent repo would otherwise report the parent's commits and
    origin. Requiring ``root`` to be the repo top level keeps a project's digest
    to that project's own history and makes "not a git repo → drop the section"
    hold for nested directories too.
    """
    top = _run(["git", "rev-parse", "--show-toplevel"], cwd=root)
    if not top:
        return False
    try:
        return os.path.realpath(top.strip()) == os.path.realpath(root)
    except OSError:
        return False


def gather_git(project: str, root: str, window: Window) -> List[CommitItem]:
    """Gather commits whose *author date* falls inside ``window``.

    Runs ``git log`` in ``root`` with the window as a ``--since``/``--until``
    pre-filter, then re-checks each commit's strict-ISO author date (``%aI``)
    against the half-open window so the boundary is exact regardless of git's
    fuzzier date matching. Fields are ``\\x1f``-separated to survive subjects
    that contain any other punctuation. A non-git directory / missing git
    yields ``[]`` (via :func:`_run`), never a raise. Returned in git-log order
    (newest first); the renderer sorts.
    """
    if not _is_repo_root(root):
        return []
    out = _run(
        [
            "git",
            "log",
            f"--since={window.start.isoformat()}",
            f"--until={window.end.isoformat()}",
            "--date=iso-strict",
            "--pretty=%H%x1f%an%x1f%aI%x1f%s",
        ],
        cwd=root,
    )
    if not out:
        return []
    items: List[CommitItem] = []
    for line in out.splitlines():
        sha, author, authored_iso, subject = (line.split("\x1f") + ["", "", "", ""])[:4]
        ts = parse_ts(authored_iso)
        if ts and in_window(ts, window):
            items.append(
                CommitItem(
                    ts=ts,
                    sha=sha[:12],
                    author=author,
                    subject=subject,
                    project=project,
                )
            )
    return items


def gather_prs(project: str, root: str, window: Window) -> List[PRItem]:
    """Gather GitHub PR events (opened/merged/closed) that fall in ``window``.

    Requires an ``origin`` remote and an authenticated ``gh``; each PR emits an
    event per lifecycle timestamp that lands in the window: ``createdAt``→
    ``opened``, ``mergedAt``→``merged``, ``closedAt``→``closed`` (a merged PR is
    *not* also double-counted as closed). Any failure — no repo, no ``origin``,
    ``gh`` missing/unauthenticated, non-zero exit, or malformed JSON — yields
    ``[]`` (Global Constraint: graceful degradation), never a raise. Returned in
    ``gh`` order; the renderer sorts.
    """
    if not _is_repo_root(root):
        return []
    origin = _run(["git", "remote", "get-url", "origin"], cwd=root)
    if not origin:
        return []
    raw = _run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            "200",
            "--json",
            "number,title,state,createdAt,mergedAt,closedAt",
        ],
        cwd=root,
        timeout=30,
    )
    if not raw:
        return []
    try:
        prs = json.loads(raw)
    except ValueError:
        return []
    items: List[PRItem] = []
    for pr in prs:
        # A valid-JSON-but-unexpected shape (future gh schema change) must still
        # degrade to a skip, not raise — the contract promises never a raise.
        if not isinstance(pr, dict) or pr.get("number") is None:
            continue
        number = pr["number"]
        title = pr.get("title") or ""
        state = pr.get("state") or ""
        for field, event in (
            ("createdAt", "opened"),
            ("mergedAt", "merged"),
            ("closedAt", "closed"),
        ):
            if event == "closed" and pr.get("mergedAt"):
                continue  # a merge already counted; don't double-count as closed
            ts = parse_ts(str(pr.get(field) or ""))
            if ts and in_window(ts, window):
                items.append(
                    PRItem(
                        ts=ts,
                        number=number,
                        title=title,
                        state=state,
                        event=event,
                        project=project,
                    )
                )
    return items


# --------------------------------------------------------------------------- #
# Ingested-docs gatherer — best-effort, dated by the doc's OWN timestamp
# --------------------------------------------------------------------------- #
# ``SourceDocument`` is the single ingested-document node type in the ontology:
# the RAG-Anything adapter (``raganything_adapter``) and ``extract_file``'s
# default ``source_kind`` both mint it for every ingested markdown/PDF/note.
# Matched on the string ``node.type.value`` to mirror the other gatherers.
_DOC_TYPE_VALUES = {ResearchNodeType.SOURCE_DOCUMENT.value}

# Metadata keys that, when present, carry the document's own last-touched time.
# Checked in order; the first parseable value wins. Graph nodes rarely carry
# these in practice (the RAG manifest records ``updated_at`` at the corpus level
# in ``meta.json``, not per node), so the ``source_path`` mtime is the common
# path — but an explicit per-node timestamp always takes precedence when set.
_DOC_TS_KEYS = ("updated_at", "created", "analysis_date")


def _resolve_doc_path(source_path: Optional[str], root: str) -> Optional[Path]:
    """Resolve a doc's stored ``source_path`` to an absolute path for stat().

    Ingested ``SourceDocument`` nodes store a *project-relative* ``source_path``
    (the RAG manifest records ``path.relative_to(project)``), so a relative value
    is resolved against ``root``. An already-absolute path is used as-is. ``None``
    / empty yields ``None`` (nothing to stat).
    """
    if not source_path:
        return None
    p = Path(source_path)
    return p if p.is_absolute() else Path(root) / p


def _doc_ts(
    resolved_path: Optional[Path],
    meta: Dict[str, object],
    tz: Optional[tzinfo],
) -> Optional[datetime]:
    """Best-effort timestamp for an ingested doc: metadata key, else file mtime.

    Precedence: the first parseable ``updated_at``/``created``/``analysis_date``
    metadata value, else the resolved ``source_path`` file's mtime. Returns
    ``None`` when neither is available/readable (the doc is then skipped — never
    dated from a session's ``started_at``).
    """
    for key in _DOC_TS_KEYS:
        ts = parse_ts(str(meta.get(key) or ""))
        if ts:
            return ts
    if resolved_path is not None:
        try:
            mtime = resolved_path.stat().st_mtime
        except OSError:
            return None
        return datetime.fromtimestamp(mtime, tz=tz or timezone.utc)
    return None


def gather_docs(
    project: str,
    root: str,
    graph: object,
    window: Window,
) -> List[DocItem]:
    """Gather ingested documents whose *own* timestamp falls inside ``window``.

    For every ``SourceDocument`` node, the timestamp is the doc's own
    ``updated_at``/``created``/``analysis_date`` metadata when present, else its
    ``source_path`` file's mtime (relative paths resolved against ``root``). A
    node whose timestamp can't be determined — no metadata time and a
    missing/unreadable/absent ``source_path`` — is **skipped** (best-effort);
    the session's long-running ``started_at`` is never consulted (Global
    Constraint). The as-stored ``source_path`` (which may be project-relative) is
    preserved on the emitted :class:`DocItem`. Returned in ``graph.nodes`` order;
    the renderer (Task 9) sorts.
    """
    out: List[DocItem] = []
    for node in graph.nodes:
        tname = getattr(node.type, "value", node.type)
        if tname not in _DOC_TYPE_VALUES:
            continue
        meta = getattr(node, "metadata", None) or {}
        raw_source_path = getattr(node, "source_path", None) or meta.get("source_path")
        raw_source_path = str(raw_source_path) if raw_source_path else None
        resolved = _resolve_doc_path(raw_source_path, root)
        ts = _doc_ts(resolved, meta, window.start.tzinfo)
        if ts and in_window(ts, window):
            out.append(
                DocItem(
                    ts=ts,
                    title=str(getattr(node, "name", "") or node.id),
                    source_path=raw_source_path or "",
                    project=project,
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Deterministic renderer — one day's gathered items -> stable markdown
# --------------------------------------------------------------------------- #
# Every subsection is always emitted (empty -> ``_none_``) and every list is
# sorted by ``(ts, id)`` inside the renderer so the same gathered inputs always
# produce byte-identical markdown regardless of the order the gatherers yielded
# them (git-log order, graph.nodes order, dict iteration order).
_NONE = "_none_"


def render_day(window: Window, items_by_kind: Dict[str, object], aggregates: Dict[str, object]) -> str:
    """Render one window's gathered items into deterministic markdown.

    Layout: ``### <label>`` then the raw-fact subsections (``#### <name>``) —
    Sessions (a count), Files touched, Commits, Pull Requests, Ingested docs. An
    empty subsection renders ``_none_``. Decisions/insights are NOT here: they are
    LLM-derived from the conversations and live in the narrative (the compiled
    findings section was always empty for a live, un-compiled window). Ordering is
    fixed and every list sorted so the output is reproducible for identical inputs.
    """
    commits = sorted(
        list(items_by_kind.get("commits") or []), key=lambda c: (c.ts, c.sha)
    )
    prs = sorted(
        list(items_by_kind.get("prs") or []), key=lambda p: (p.ts, p.number, p.event)
    )
    docs = sorted(
        list(items_by_kind.get("docs") or []), key=lambda d: (d.ts, d.title, d.source_path)
    )
    sessions = sorted(
        list(aggregates.get("sessions") or []), key=lambda s: s["session_id"]
    )
    files = sorted(str(f) for f in (aggregates.get("files_touched") or []))

    lines: List[str] = [f"### {window.label}", ""]

    def _section(heading: str, rows: List[str]) -> None:
        lines.append(heading)
        lines.extend(rows if rows else [_NONE])
        lines.append("")

    # A count line, not a hash dump — the per-session detail lives in the
    # narrative's **Sessions** subsection. Turns are in-window only.
    n_sessions = len(sessions)
    n_turns = sum(int(s.get("turns", 0)) for s in sessions)
    harness_tally: Dict[str, int] = {}
    for s in sessions:
        h = str(s.get("harness", "") or "?")
        harness_tally[h] = harness_tally.get(h, 0) + 1
    breakdown = ", ".join(f"{h} ×{n}" for h, n in sorted(harness_tally.items()))
    session_rows = (
        [
            f"- {n_sessions} sessions · {n_turns} in-window turns"
            + (f" ({breakdown})" if breakdown else "")
        ]
        if n_sessions
        else []
    )
    _section("#### Sessions", session_rows)
    _section("#### Files touched", [f"- `{f}`" for f in files])
    _section(
        "#### Commits",
        [f"- `{c.sha}` {c.subject} ({c.author})" for c in commits],
    )
    _section(
        "#### Pull Requests",
        [f"- #{p.number} {p.title} — {p.event}" for p in prs],
    )
    _section(
        "#### Ingested docs",
        [f"- {d.title}" for d in docs],
    )

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Orchestration — resolve projects, run the gatherers, render, optionally write
# --------------------------------------------------------------------------- #
@dataclass
class SummaryResult:
    """The rendered activity summary: combined markdown + any files written."""

    markdown: str
    paths: List[Path] = field(default_factory=list)


def _sessions_aggregate(messages: List[MessageItem]) -> List[Dict[str, object]]:
    """Fold in-window messages into one row per session (id, harness, turn count).

    Order-independent: keyed on ``session_id`` and re-sorted by the renderer, so
    the Sessions subsection is deterministic regardless of gather order.
    """
    by_session: Dict[str, Dict[str, object]] = {}
    for m in messages:
        entry = by_session.get(m.session_id)
        if entry is None:
            entry = {"session_id": m.session_id, "harness": m.harness, "turns": 0}
            by_session[m.session_id] = entry
        entry["turns"] = int(entry["turns"]) + 1
    return list(by_session.values())


def _files_touched(root: str, commits: List[CommitItem]) -> List[str]:
    """Distinct files changed by the in-window ``commits`` (best-effort, sorted).

    Derived from the already-windowed commit SHAs via a single
    ``git show --name-only`` so the window boundary is exactly the commits'
    (never re-filtered by git's fuzzier date matching). Any git failure — not a
    repo, missing git — yields ``[]`` (graceful degradation), never a raise.
    """
    if not commits:
        return []
    out = _run(
        ["git", "show", "--name-only", "--format=", *[c.sha for c in commits]],
        cwd=root,
    )
    if not out:
        return []
    files = {line.strip() for line in out.splitlines() if line.strip()}
    return sorted(files)


def _load_project_graph(root: Path) -> Optional[object]:
    """Load a project's compiled ``graph.json`` (findings/docs source), or None.

    Uses the same loader the MCP server uses (imported lazily to avoid a
    circular import once ``mcp_server`` imports :func:`build_summary`). A project
    with no compiled graph — or an unreadable one — yields ``None`` so the caller
    simply drops the graph-derived sections (findings, docs) for that project.
    """
    graph_path = Path(root) / ".tesserae" / "graph.json"
    if not graph_path.is_file():
        return None
    try:
        from tesserae.mcp_server import load_graph

        return load_graph(graph_path)
    except Exception as exc:  # pragma: no cover - defensive: corrupt graph.json
        logger.warning("activity summary: failed to load graph for %s: %s", root, exc)
        return None


def _resolve_projects(project_names: Optional[List[str]]) -> List[Tuple[str, Path]]:
    """Resolve the projects to summarize: all registered, or the named subset.

    Default scope is every registered project
    (``ProjectRegistry.iter_registered_projects()``). ``project_names`` opts into
    a subset (order preserved as registered). ``mcp_server`` is imported lazily so
    this module stays importable from ``mcp_server`` without a cycle.
    """
    from tesserae.mcp_server import ProjectRegistry

    registered = list(ProjectRegistry().iter_registered_projects())
    if not project_names:
        return registered
    wanted = set(project_names)
    return [(name, root) for name, root in registered if name in wanted]


def _summary_filename(windows: List[Window]) -> str:
    """``daily-<label>.md`` for a single window, ``weekly-<first>_<last>.md`` else."""
    if len(windows) > 1:
        return f"weekly-{windows[0].label}_{windows[-1].label}.md"
    return f"daily-{windows[0].label}.md"


def _write_project_summary(root: Path, name: str, windows: List[Window], body: str) -> Path:
    """Write one project's deterministic digest under ``.tesserae/summaries``.

    Path: ``<root>/.tesserae/summaries/<project>/daily-<label>.md`` (or
    ``weekly-*.md`` for a multi-day run). Only the deterministic per-project body
    is written — no graph timestamps are involved, so byte-idempotence is
    unaffected (Global Constraint).
    """
    out_dir = Path(root) / ".tesserae" / "summaries" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _summary_filename(windows)
    path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# LLM narrative — one prose "what happened" section over the deterministic digest
# --------------------------------------------------------------------------- #
_SUMMARY_SYSTEM = (
    "You summarize a developer's activity for a time period from a deterministic "
    "digest of sessions, commits, PRs, findings, and ingested docs.\n"
    "\n"
    "Output STRUCTURED MARKDOWN ONLY — never flowing paragraphs, never an essay.\n"
    "Format:\n"
    "- One `## <project>` heading (exactly two hashes) per project that had "
    "substantive activity, ordered by volume (most active first). Use the project "
    "name verbatim. Skip projects with only trivial or no activity.\n"
    "- Under each project, terse one-line bullets grouped under bold category "
    "labels, and ONLY the categories that apply:\n"
    "  - **Shipped** — merged PRs / completed features (cite PR # and phase/name).\n"
    "  - **Fixed** — bugs and security issues closed (name the actual issue).\n"
    "  - **Decisions & Insights** — decisions made, trade-offs chosen, direction "
    "changes, and non-obvious things learned. Mine these from the SESSION "
    "CONVERSATION EXCERPTS as well as the digest; almost every active project has "
    "at least one. This is important — do not omit it when the sessions decided or "
    "discovered anything.\n"
    "  - **In progress** — opened-but-unmerged PRs / partial work.\n"
    "  - **Docs** — ingested/synthesized knowledge, one bullet each.\n"
    "  - **Watch** — risks or follow-ups the digest implies (e.g. untested path).\n"
    "  - **Sessions** — REQUIRED whenever excerpts are present. Under the SESSION "
    "CONVERSATION EXCERPTS, each session appears as `### session <id> (<harness>, "
    "<N> turns)`. Emit exactly ONE bullet per session summarizing what that "
    "session actually did — the work, key decisions, and outcome/blocker — in one "
    "concrete line, suffixed with `(<harness>, <N> turns)` copied from that "
    "session's header. Cover every session; never merge, drop, or reorder-away a "
    "session; NEVER print the session id/hash. The excerpts contain ONLY the turns "
    "inside the requested time window, so summarize just that slice.\n"
    "- Every bullet is a single concrete line grounded in the digest or the "
    "conversation excerpts (real PR numbers, phases, files, or what the session "
    "worked through). No sub-paragraphs. Never write 'session activity only'.\n"
    "- No opening preamble, no closing 'net for the day' summary, no prose.\n"
    "- NEVER invent activity absent from the digest or the conversation excerpts."
)


def _summary_llm_client(root: str) -> object:
    """Build the same rotating, no-API-key LLM client ``tesserae ask`` uses.

    Mirrors ``query.QueryEngine._answer_via_cli``: :func:`build_rotating_client`
    composes every available backend — the Claude CLI (rotating its config dirs),
    the Codex CLI (rotating its homes), and the Anthropic SDK if a key is set — so
    narration works over OAuth without an ``ANTHROPIC_API_KEY`` and survives a
    rate-limited account by rotating to the next one. The returned client exposes
    ``complete_text(system, user) -> str``. Raises ``RuntimeError`` when no backend
    is usable so the caller (:func:`_maybe_narrate`) falls back to the
    deterministic digest rather than dereferencing ``None``.

    ``root`` is accepted for call-site symmetry with the other per-project helpers
    (and to leave room for a future per-project provider override); the client is
    discovered from the machine's global CLI accounts exactly as the ask path does,
    so it is not consulted today.
    """
    from tesserae.llm_json import build_rotating_client

    client = build_rotating_client()
    if client is None:
        raise RuntimeError("no LLM backend available for narrative synthesis")
    return client


def render_session_excerpts(
    messages: Sequence[MessageItem],
    *,
    per_turn_chars: int = 600,
    per_session_chars: int = 3500,
    project_chars: int = 24000,
) -> str:
    """Compact per-session transcript so the narrator can summarize a session's
    actual work — the deterministic digest only has counts, so a session with no
    commits/PRs would otherwise be un-summarizable ("session activity only").

    Groups ``messages`` by session, orders turns by time, and renders one bullet
    per user/assistant turn (tool turns collapse to ``[tool:<name>]`` to cut
    noise). Bounded three ways — per turn, per session, and per project — so a
    busy project never starves the others' excerpt budget in a single LLM call.
    """
    by_session: Dict[str, List[MessageItem]] = {}
    for m in messages:
        by_session.setdefault(m.session_id, []).append(m)

    blocks: List[str] = []
    used = 0
    for sid, msgs in by_session.items():
        msgs = sorted(msgs, key=lambda m: m.ts)
        lines = [f"### session {sid} ({msgs[0].harness}, {len(msgs)} turns)"]
        slen = 0
        for m in msgs:
            text = " ".join((m.text or "").split())
            if m.role == "tool":
                frag = f"- [tool:{m.name}]" if m.name else "- [tool]"
            elif text:
                frag = f"- [{m.role}] {text[:per_turn_chars]}"
            else:
                continue
            if slen + len(frag) > per_session_chars:
                lines.append("- …(session truncated)")
                break
            lines.append(frag)
            slen += len(frag)
        block = "\n".join(lines)
        if used + len(block) > project_chars:
            blocks.append("### …(more sessions omitted for length)")
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


def synthesize_narrative(
    deterministic_md: str, client: object, *, conversation: str = ""
) -> str:
    """Return the LLM "what happened" narrative (``## <project>`` sections) — prose
    only, NOT joined with the digest (the caller assembles the document).

    ``client`` exposes ``complete_text(system, user) -> str`` (the rotating CLI/SDK
    client :func:`_summary_llm_client` builds). The model is given the deterministic
    digest AND — when supplied — ``conversation`` (bounded per-session excerpts) so
    it can summarize what each session actually did, including sessions with no
    commits/PRs. Returns ``""`` on an empty/whitespace reply. Excerpts are model
    context only; they never appear in the returned text.
    """
    user = deterministic_md
    if conversation.strip():
        user = (
            deterministic_md
            + "\n\n=== SESSION CONVERSATION EXCERPTS ===\n"
            + "(Use these to summarize what each session worked on and how it went, "
            "especially sessions with no commits/PRs. Do not quote verbatim.)\n\n"
            + conversation
        )
    return (client.complete_text(system=_SUMMARY_SYSTEM, user=user) or "").strip()


def _maybe_narrate(
    deterministic_md: str,
    projects: List[Tuple[str, Path]],
    conversation: str = "",
) -> str:
    """Return the LLM narrative prose, or ``""`` when no narrator is wired / it fails.

    Narration is best-effort — a missing LLM client or any LLM error yields ``""``
    (logged), never raising, so the summary always renders (facts-only). The client
    is built from the first project's root; ``conversation`` carries the bounded
    per-session excerpts the narrator summarizes.
    """
    narrate = globals().get("synthesize_narrative")
    make_client = globals().get("_summary_llm_client")
    if not callable(narrate) or not callable(make_client) or not projects:
        return ""
    try:
        _name, root = projects[0]
        client = make_client(str(root))
        return narrate(deterministic_md, client, conversation=conversation)
    except Exception as exc:  # narration is best-effort; facts-only still renders
        logger.warning("activity summary narrative synthesis failed: %s", exc)
        return ""


def build_summary(
    windows: List[Window],
    project_names: Optional[List[str]] = None,
    *,
    synthesize: bool = True,
    write: bool = True,
    turn_limit: int = 100_000,
) -> SummaryResult:
    """Gather → render → (optionally narrate + write) the activity summary.

    For every resolved project (all registered, or ``project_names``) and every
    window, runs the five gatherers, folds the Sessions/Files-touched aggregates,
    and renders one deterministic day block. Findings and ingested docs are
    dropped for a project with no compiled ``graph.json``. The per-project days
    are joined under a ``# <project>`` heading. When ``write``, each project's
    deterministic digest is written to ``.tesserae/summaries/<project>/``. When
    ``synthesize``, an LLM narrative is prepended to the returned markdown
    (best-effort; falls back to the deterministic digest on any failure).
    """
    projects = _resolve_projects(project_names)
    project_sections: List[str] = []
    convo_blocks: List[str] = []
    paths: List[Path] = []

    # One window-scoped scan over ALL harness roots for every project at once.
    messages_by = scan_messages(projects, windows, turn_limit=turn_limit)

    for name, root in projects:
        root_str = str(root)
        graph = _load_project_graph(root)
        day_blocks: List[str] = []
        for window in windows:
            messages = messages_by.get(name, {}).get(window.label, [])
            # Bounded per-session excerpts so the narrator can summarize sessions
            # with no commits/PRs (only built when we'll actually narrate).
            if synthesize and messages:
                excerpts = render_session_excerpts(messages)
                if excerpts:
                    convo_blocks.append(f"## {name} — {window.label}\n{excerpts}")
            commits = gather_git(name, root_str, window)
            prs = gather_prs(name, root_str, window)
            docs = gather_docs(name, root_str, graph, window) if graph is not None else []
            items_by_kind = {"commits": commits, "prs": prs, "docs": docs}
            aggregates = {
                "sessions": _sessions_aggregate(messages),
                "files_touched": _files_touched(root_str, commits),
            }
            day_blocks.append(render_day(window, items_by_kind, aggregates))
        body = f"## {name}\n\n" + "\n\n".join(day_blocks)
        project_sections.append(body)
        if write:
            paths.append(_write_project_summary(root, name, windows, body))

    facts_md = "\n\n".join(project_sections)
    label = (
        windows[0].label
        if len(windows) == 1
        else f"{windows[0].label} … {windows[-1].label}"
    )
    conversation = "\n\n".join(convo_blocks)
    if synthesize:
        # Surface explicit human decisions (AskUserQuestion) so the narrator's
        # Decisions & Insights reliably includes them — the excerpts truncate the
        # tool input and often miss the chosen answer. Deterministic; best-effort.
        from tesserae.decisions import gather_decisions  # lazy: decisions imports this module

        try:
            human = gather_decisions(windows, [n for n, _ in projects], include_agent=False)
        except Exception as exc:  # noqa: BLE001 - narration is best-effort
            logger.warning("summary: human-decision gather failed: %s", exc)
            human = []
        if human:
            hd_block = (
                "=== HUMAN DECISIONS (explicit AskUserQuestion choices — include "
                "these in each project's Decisions & Insights) ===\n"
                + "\n".join(f"- [{d.project}] {d.question} -> {d.answer}" for d in human)
            )
            conversation = f"{hd_block}\n\n{conversation}" if conversation.strip() else hd_block
    narrative = _maybe_narrate(facts_md, projects, conversation) if synthesize else ""

    parts = [f"# Activity summary — {label}"]
    if narrative.strip():
        parts.append(narrative)
    parts.append(f"# Windowed facts\n\n{facts_md}")
    markdown = "\n\n".join(parts)
    return SummaryResult(markdown=markdown, paths=paths)
