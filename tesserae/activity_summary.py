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

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from tesserae.harness_sessions import _claude_turns, _codex_turns, _parse_jsonl
from tesserae.harness_sessions_db import HarnessSessionsDB
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

    Layout: ``## <label>`` then the six fixed subsections — Sessions and Files
    touched from ``aggregates``; Decisions & Insights, Commits, Pull Requests and
    Ingested docs from ``items_by_kind``. An empty subsection renders ``_none_``.
    Ordering is fixed and every list is sorted (``(ts, id)``) so the output is
    reproducible for identical inputs.
    """
    findings = sorted(
        list(items_by_kind.get("findings") or []), key=lambda f: (f.ts, f.node_id)
    )
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

    lines: List[str] = [f"## {window.label}", ""]

    def _section(heading: str, rows: List[str]) -> None:
        lines.append(heading)
        lines.extend(rows if rows else [_NONE])
        lines.append("")

    _section(
        "### Sessions",
        [
            f"- `{s['session_id']}` ({s.get('harness', '')}, {s.get('turns', 0)} turns)"
            for s in sessions
        ],
    )
    _section("### Files touched", [f"- `{f}`" for f in files])
    _section(
        "### Decisions & Insights",
        [f"- **{f.kind}** {f.body}" for f in findings],
    )
    _section(
        "### Commits",
        [f"- `{c.sha}` {c.subject} ({c.author})" for c in commits],
    )
    _section(
        "### Pull Requests",
        [f"- #{p.number} {p.title} — {p.event}" for p in prs],
    )
    _section(
        "### Ingested docs",
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
    "You are summarizing a developer's activity for a time period. "
    "Given a deterministic digest of sessions, findings, commits, PRs and "
    "ingested docs, write a concise narrative of what happened and why it "
    "mattered. Do not invent activity not present in the digest."
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


def synthesize_narrative(deterministic_md: str, client: object) -> str:
    """Prepend an LLM "what happened" narrative to the deterministic digest.

    ``client`` is any object exposing ``complete_text(system, user) -> str`` — the
    rotating CLI/SDK client :func:`_summary_llm_client` builds. The deterministic
    digest is passed verbatim as the *only* user context (the system prompt forbids
    inventing activity absent from it), and the model's prose is placed above the
    unchanged digest, separated by a horizontal rule, so the exact windowed facts
    stay auditable below the narrative. An empty/whitespace-only reply yields the
    digest unchanged (no dangling narrative or rule).
    """
    prose = (client.complete_text(system=_SUMMARY_SYSTEM, user=deterministic_md) or "").strip()
    if not prose:
        return deterministic_md
    return f"{prose}\n\n---\n\n{deterministic_md}"


def _maybe_narrate(deterministic_md: str, projects: List[Tuple[str, Path]]) -> str:
    """Prepend an LLM narrative to the digest when a narrator is wired.

    The narrative layer (``synthesize_narrative`` + ``_summary_llm_client``) is
    added by a later step; until it exists this returns the deterministic digest
    unchanged. When present, narration is best-effort — a missing LLM client or
    any LLM error falls back to the digest (logged), never raising (so a summary
    always renders). The client is built from the first project's root.
    """
    narrate = globals().get("synthesize_narrative")
    make_client = globals().get("_summary_llm_client")
    if not callable(narrate) or not callable(make_client) or not projects:
        return deterministic_md
    try:
        _name, root = projects[0]
        client = make_client(str(root))
        return narrate(deterministic_md, client)
    except Exception as exc:  # narration is best-effort; the digest always stands
        logger.warning("activity summary narrative synthesis failed: %s", exc)
        return deterministic_md


def build_summary(
    windows: List[Window],
    project_names: Optional[List[str]] = None,
    *,
    synthesize: bool = True,
    write: bool = True,
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
    paths: List[Path] = []

    for name, root in projects:
        root_str = str(root)
        graph = _load_project_graph(root)
        day_blocks: List[str] = []
        for window in windows:
            messages, turns_by_session = gather_messages(name, root_str, window)
            findings = (
                gather_findings(name, graph, turns_by_session, window)
                if graph is not None
                else []
            )
            commits = gather_git(name, root_str, window)
            prs = gather_prs(name, root_str, window)
            docs = gather_docs(name, root_str, graph, window) if graph is not None else []
            items_by_kind = {
                "findings": findings,
                "commits": commits,
                "prs": prs,
                "docs": docs,
            }
            aggregates = {
                "sessions": _sessions_aggregate(messages),
                "files_touched": _files_touched(root_str, commits),
            }
            day_blocks.append(render_day(window, items_by_kind, aggregates))
        body = f"# {name}\n\n" + "\n\n".join(day_blocks)
        project_sections.append(body)
        if write:
            paths.append(_write_project_summary(root, name, windows, body))

    deterministic_md = "\n\n".join(project_sections)
    markdown = _maybe_narrate(deterministic_md, projects) if synthesize else deterministic_md
    return SummaryResult(markdown=markdown, paths=paths)
