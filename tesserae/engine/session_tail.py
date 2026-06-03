"""Live transcript tailer — the daemon's third trigger source (SESS-01/SESS-03).

``SessionTailer`` watches the *project's own* agent transcripts (Claude Code and
Codex) and, on each :meth:`tick`, seeks to a persisted byte offset per file,
reads only the bytes that have landed since, and yields ONLY complete
(newline-terminated) JSONL lines — a half-written final line is left for the
next tick (03-RESEARCH Pitfall 1). New lines are parsed (reusing the verified
``harness_sessions`` parsers — NOT a re-implementation) into a full
:class:`HarnessSession` plus a delta of normalised turns.

CRITICAL ORDERING (03-RESEARCH Integration Point + Phase-2 W1 note): the updated
session and new offset are written to :class:`HarnessSessionsDB` BEFORE the
``on_new_turns`` callback (which the daemon turns into a debounced ``TriggerEvent``)
fires. The compile reads the live store, so store-before-enqueue guarantees the
debounced compile sees correct state even if the debounce drops ``changed_paths``.

Scope discipline (03-RESEARCH Pitfall 4): Claude scanning is restricted to
``<root>/projects/<slug>/*.jsonl`` via :func:`_claude_project_dir`; Codex files
are filtered to the project's own sessions via a cached cwd peek. The tailer
NEVER rglobs all of ``~/.claude`` (~85k files) per tick.

On construction the tailer seeds offsets from
:meth:`HarnessSessionsDB.all_offsets` so a daemon restart resumes without
replaying already-ingested turns (Pitfall 3 / SESS-01).

Std-lib only (``json``/``logging``/``pathlib``/``time``) plus the
``harness_sessions`` / ``harness_sessions_db`` modules.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..harness_sessions import (
    HarnessSession,
    _claude_project_dir,
    _claude_turns,
    _codex_turns,
    _is_claude_subagent_transcript,
    _parse_claude_session,
    _parse_codex_session,
    _rows_match_project,
    discover_harness_roots,
)
from ..harness_sessions_db import HarnessSessionsDB

logger = logging.getLogger("tesserae.session_tail")

OnNewTurns = Callable[[Path, List[dict]], None]


class SessionTailer:
    """Seek-based, partial-line-safe tailer; writes the store, then enqueues."""

    def __init__(
        self,
        project_root: Path,
        sessions_db: HarnessSessionsDB,
        on_new_turns: OnNewTurns,
        watch_roots: Optional[List[Path]] = None,
        poll_interval: float = 1.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.sessions_db = sessions_db
        self.on_new_turns = on_new_turns
        self.poll_interval = poll_interval
        self._watch_roots = (
            [Path(r).expanduser() for r in watch_roots]
            if watch_roots is not None
            else discover_harness_roots()
        )
        # Restart-resume: seed byte offsets from the durable store (Pitfall 3).
        self._offsets: Dict[Path, int] = {
            Path(p): int(o) for p, o in self.sessions_db.all_offsets().items()
        }
        # Enumerated in-scope transcript files (path -> harness "claude"|"codex").
        self._known: Dict[Path, str] = {}
        # Cached per-file project-match decision for Codex peeks (avoid re-peek).
        self._codex_match: Dict[Path, bool] = {}
        self._reenum_interval = 60.0
        self._last_enum = 0.0
        self._enumerate()

    # ------------------------------------------------------------------ #
    # Discovery — project-scoped only (NEVER rglob ~85k files)            #
    # ------------------------------------------------------------------ #

    def _enumerate(self) -> None:
        """Refresh the in-scope file set, scoped to the project slug dir."""
        self._last_enum = time.monotonic()
        slug = _claude_project_dir(self.project_root)
        for root in self._watch_roots:
            if not root.exists():
                continue
            # Claude: ONLY <root>/projects/<slug>/*.jsonl (exclude subagents).
            project_dir = root / "projects" / slug
            if project_dir.exists():
                for path in project_dir.glob("*.jsonl"):
                    if _is_claude_subagent_transcript(path):
                        continue
                    self._known.setdefault(path, "claude")
            # Codex: dated rollout files filtered to this project's cwd.
            sessions_dir = root / "sessions"
            if sessions_dir.exists():
                for path in sessions_dir.rglob("rollout-*.jsonl"):
                    if path in self._known or path in self._codex_match:
                        if self._codex_match.get(path):
                            self._known.setdefault(path, "codex")
                        continue
                    matched = self._codex_file_matches(path)
                    self._codex_match[path] = matched
                    if matched:
                        self._known[path] = "codex"

    def _codex_file_matches(self, path: Path) -> bool:
        """Cheap first-lines peek: does this Codex rollout target our project?"""
        rows = self._peek_rows(path, limit=8)
        return _rows_match_project(rows, self.project_root)

    @staticmethod
    def _peek_rows(path: Path, limit: int) -> List[dict]:
        rows: List[dict] = []
        try:
            with path.open(encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        rows.append(obj)
                    if len(rows) >= limit:
                        break
        except OSError:
            return []
        return rows

    # ------------------------------------------------------------------ #
    # Partial-line-safe read                                              #
    # ------------------------------------------------------------------ #

    def _read_new_complete_lines(
        self, path: Path, offset: int
    ) -> tuple[List[str], int]:
        """Read from ``offset`` to EOF; return complete lines + new byte offset.

        A trailing fragment with no ``\\n`` is a half-written line — it is dropped
        and the offset is NOT advanced past it, so it is re-read next tick once
        its newline lands (03-RESEARCH Pitfall 1).
        """
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            return [], offset
        if not chunk:
            return [], offset
        # Bytes up to and including the last newline are "complete".
        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            # No complete line yet — leave offset untouched.
            return [], offset
        complete = chunk[: last_nl + 1]
        new_offset = offset + len(complete)
        text = complete.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.split("\n") if ln.strip()]
        return lines, new_offset

    # ------------------------------------------------------------------ #
    # Tick                                                                #
    # ------------------------------------------------------------------ #

    def tick(self) -> None:
        if time.monotonic() - self._last_enum >= self._reenum_interval:
            self._enumerate()
        # Snapshot to avoid mutation-during-iteration if enumerate runs.
        for path, harness in list(self._known.items()):
            try:
                self._tick_file(path, harness)
            except Exception:  # noqa: BLE001 - one bad file must not kill the tick
                logger.exception("session tail failed for %s", path)

    def _tick_file(self, path: Path, harness: str) -> None:
        offset = self._offsets.get(path)
        if offset is None:
            offset = self.sessions_db.get_offset(path)
        lines, new_offset = self._read_new_complete_lines(path, offset)
        if not lines or new_offset == offset:
            return
        new_rows = self._parse_lines(lines)
        # Re-parse the whole (small, project-owned) transcript into a full
        # HarnessSession with metadata["turns"] populated by the verified parsers.
        if harness == "claude":
            session = _parse_claude_session(self.project_root, self._root_for(path), path)
            new_turns = _claude_turns(new_rows)
        else:
            session = _parse_codex_session(self.project_root, self._root_for(path), path)
            new_turns = _codex_turns(new_rows)
        if session is None:
            # File doesn't (yet) match the project — advance offset so we don't
            # re-scan the same complete bytes forever, but emit nothing.
            self._offsets[path] = new_offset
            self.sessions_db.set_offset(path, new_offset)
            return

        # CRITICAL ORDERING: persist session + offset BEFORE the callback so the
        # debounced compile reads correct state regardless of changed_paths drop.
        self.sessions_db.upsert(session, jsonl_path=path, last_offset=new_offset)
        self.sessions_db.set_offset(path, new_offset)
        self._offsets[path] = new_offset
        if new_turns:
            self.on_new_turns(path, new_turns)

    def _parse_lines(self, lines: Sequence[str]) -> List[dict]:
        rows: List[dict] = []
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("skipping unparseable transcript line")
                continue
            if isinstance(obj, dict):
                rows.append(obj)
                self._warn_unknown_type(obj)
        return rows

    @staticmethod
    def _warn_unknown_type(row: dict) -> None:
        rtype = row.get("type")
        known = {
            "user", "assistant", "system", "summary", "permission-mode",
            "attachment", "session_meta", "response_item", "event_msg",
            "turn_context", "compact_boundary",
        }
        if isinstance(rtype, str) and rtype and rtype not in known:
            logger.warning("unrecognized transcript row type: %s", rtype)

    def _root_for(self, path: Path) -> Path:
        """Return the harness config root that owns ``path`` (best-effort)."""
        for root in self._watch_roots:
            try:
                path.relative_to(root)
                return root
            except ValueError:
                continue
        return self._watch_roots[0] if self._watch_roots else self.project_root
