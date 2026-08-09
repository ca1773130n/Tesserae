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
    _TURN_LIMIT_BACKSTOP,
    _claude_project_dir,
    _claude_turns,
    _codex_turns,
    _is_claude_subagent_transcript,
    _parse_claude_session,
    _parse_codex_session,
    _rows_match_project,
    discover_harness_roots,
    is_tesserae_internal_session,
)
from ..harness_sessions_db import HarnessSessionsDB

logger = logging.getLogger("tesserae.session_tail")

OnNewTurns = Callable[[Path, List[dict]], None]

# Chunk-store hook: ``(harness, transcript_path, session_key, turns)`` where
# ``harness`` is the activity-summary name ("claude-code"|"codex") and
# ``session_key`` matches ``iter_project_transcripts``'s "<account>:<stem>".
OnChunkTurns = Callable[[str, Path, str, List[dict]], None]


def _dir_changed_since(directory: Path, floor: float) -> bool:
    """Return True if ``directory``'s mtime is at/after ``floor``.

    ``floor == 0`` admits everything (first full scan). A missing/unstattable
    dir is treated as changed so we don't silently drop it.
    """
    if floor <= 0:
        return True
    try:
        return directory.stat().st_mtime >= floor
    except OSError:
        return True


def _file_signature(path: Path) -> "tuple[int, float]":
    """Return ``(size, mtime)`` for change-detection, ``(0, 0.0)`` if missing."""
    try:
        st = path.stat()
        return int(st.st_size), float(st.st_mtime)
    except OSError:
        return (0, 0.0)


class SessionTailer:
    """Seek-based, partial-line-safe tailer; writes the store, then enqueues."""

    #: Host-agnostic scan-floor key, written by every version before the floor
    #: was host-qualified. Still READ once as a seed — see :meth:`_host_floor_key`.
    _FLOOR_META_KEY = "codex_dir_floor"
    _FLOOR_LOOKBACK_S = 7 * 86400.0

    @classmethod
    def _host_floor_key(cls) -> str:
        """The Codex scan-floor meta key scoped to THIS machine.

        ``harness_sessions.db`` lives in the project's ``.tesserae/``, which
        several servers can share over one disk — but each of them tails its
        OWN local transcript tree. Under a single ``codex_dir_floor`` the host
        that enumerated last pushes the floor past date directories another
        host has never read, and since the floor only ever moves forward those
        transcripts are then never imported by anyone. Qualifying the key by
        host gives each machine the floor it actually earned.

        Degrades to the unqualified key when no host id can be determined,
        which is exactly the single-host behaviour it replaces.
        """
        try:
            from ..harness_sessions import local_host_id

            host = local_host_id().strip()
        except Exception as exc:  # noqa: BLE001 — a meta key must not break the tailer
            logger.debug("no host id available (%s); using the shared scan floor", exc)
            host = ""
        return f"{cls._FLOOR_META_KEY}:{host}" if host else cls._FLOOR_META_KEY

    def __init__(
        self,
        project_root: Path,
        sessions_db: HarnessSessionsDB,
        on_new_turns: OnNewTurns,
        watch_roots: Optional[List[Path]] = None,
        poll_interval: float = 1.0,
        on_chunk_turns: Optional[OnChunkTurns] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.sessions_db = sessions_db
        self.on_new_turns = on_new_turns
        self.on_chunk_turns = on_chunk_turns
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
        # Negative-match re-peek state (Codex #4): for a Codex rollout that did
        # NOT yet match the project, remember the (size, mtime) we peeked at.
        # When the file grows or its mtime advances, RE-PEEK — its project
        # signal (session_meta/cwd row) may have landed after the first peek.
        # Positive matches are promoted to ``_known`` and never re-peeked.
        self._codex_unmatched: Dict[Path, "tuple[int, float]"] = {}
        self._reenum_interval = 60.0
        self._last_enum = 0.0
        # Bounded Codex discovery (Codex #3): only scan date dirs whose mtime is
        # at/after this floor (recent dirs). The floor is persisted in the
        # sessions DB so a RESTART does not redo the full cold sweep — only the
        # first-ever run scans all history. A 7-day lookback below the persisted
        # value re-peeks recently-touched date dirs (a Codex session resumed
        # after the previous run may have grown an older rollout).
        self._codex_dir_floor = 0.0
        self._floor_key = self._host_floor_key()
        persisted_floor = self.sessions_db.get_meta(self._floor_key)
        if not persisted_floor and self._floor_key != self._FLOOR_META_KEY:
            # Upgrade path: adopt the pre-fix, host-agnostic floor as this
            # host's seed so an existing single-host store does not redo the
            # full cold sweep on first start after the upgrade. The legacy row
            # is only READ — never rewritten, never deleted — because a second
            # host sharing this store needs the same seed when it upgrades. From
            # the first enumerate onward each host writes only its own key, so
            # the collision stops there. On a store two hosts were already
            # sharing, the seed is whatever the last of them wrote: no worse
            # than the behaviour being fixed, and the lookback below still
            # re-peeks recently touched date dirs.
            persisted_floor = self.sessions_db.get_meta(self._FLOOR_META_KEY)
        if persisted_floor:
            try:
                self._codex_dir_floor = max(0.0, float(persisted_floor) - self._FLOOR_LOOKBACK_S)
            except ValueError:
                pass
        sweep_started = time.monotonic()
        cold = self._codex_dir_floor == 0.0
        if cold:
            logger.info(
                "session tail (%s): first run — sweeping full session history once "
                "(this can take a while; restarts resume from a persisted floor)",
                self.project_root.name,
            )
        self._enumerate()
        logger.info(
            "session tail (%s): %s sweep done in %.1fs — %d in-scope transcripts, "
            "%d unmatched rollouts tracked",
            self.project_root.name,
            "cold" if cold else "warm",
            time.monotonic() - sweep_started,
            len(self._known),
            len(self._codex_unmatched),
        )

    # ------------------------------------------------------------------ #
    # Discovery — project-scoped only (NEVER rglob ~85k files)            #
    # ------------------------------------------------------------------ #

    def _enumerate(self) -> None:
        """Refresh the in-scope file set, scoped to the project slug dir.

        Bounded by design (Codex #3): the Claude side is slug-scoped and the
        Codex side scans ONLY recent/changed date dirs plus re-peeks already
        tracked still-growing rollouts — never an rglob of the whole history.
        """
        # First enumeration scans the full history once; afterwards only date
        # dirs touched recently are visited. We capture the *previous* floor
        # for this pass, then advance the floor for the next tick.
        prev_floor = self._codex_dir_floor
        scan_started = time.time()
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
                self._enumerate_codex(sessions_dir, prev_floor)
        # Re-peek already-tracked unmatched rollouts that have grown/changed —
        # their project signal may have landed after the first peek (Codex #4).
        self._repeek_unmatched()
        # Advance the floor: next pass only revisits date dirs touched since the
        # start of THIS scan (steady-state work is bounded by recent activity).
        self._codex_dir_floor = scan_started
        # Persist so a restarted tailer resumes here instead of re-sweeping the
        # full history. Best-effort: a DB hiccup must not kill the enumerate.
        try:
            self.sessions_db.set_meta(self._floor_key, str(scan_started))
        except Exception as exc:  # noqa: BLE001 — durable floor is an optimization
            logger.warning("could not persist codex dir floor: %s", exc)

    def _enumerate_codex(self, sessions_dir: Path, floor: float) -> None:
        """Scan ONLY date dirs whose mtime is at/after ``floor``.

        The Codex layout is ``<sessions>/YYYY/MM/DD/rollout-*.jsonl``. Instead of
        ``rglob`` over the whole tree every tick, we walk the shallow date
        hierarchy and skip any DAY directory that has not changed since the last
        scan, so steady-state cost is bounded by today's activity regardless of
        total history size (Codex #3).
        """
        for day_dir in self._recent_day_dirs(sessions_dir, floor):
            for path in day_dir.glob("rollout-*.jsonl"):
                if path in self._known:
                    continue
                self._consider_codex_file(path)

    @staticmethod
    def _recent_day_dirs(sessions_dir: Path, floor: float) -> List[Path]:
        """Yield ``YYYY/MM/DD`` leaf dirs whose mtime is >= ``floor``.

        Each level is pruned by mtime so untouched years/months are never
        descended into. ``floor == 0`` (first scan) admits everything.
        """
        days: List[Path] = []
        try:
            year_dirs = [d for d in sessions_dir.iterdir() if d.is_dir()]
        except OSError:
            return days
        for year in year_dirs:
            if not _dir_changed_since(year, floor):
                continue
            try:
                month_dirs = [d for d in year.iterdir() if d.is_dir()]
            except OSError:
                continue
            for month in month_dirs:
                if not _dir_changed_since(month, floor):
                    continue
                try:
                    day_dirs = [d for d in month.iterdir() if d.is_dir()]
                except OSError:
                    continue
                for day in day_dirs:
                    if _dir_changed_since(day, floor):
                        days.append(day)
        return days

    def _consider_codex_file(self, path: Path) -> None:
        """Peek a candidate rollout; promote to known on match, else track it."""
        if self._codex_file_matches(path):
            self._known[path] = "codex"
            self._codex_unmatched.pop(path, None)
        else:
            self._codex_unmatched[path] = _file_signature(path)

    def _repeek_unmatched(self) -> None:
        """Re-peek tracked unmatched rollouts that have grown/changed (Codex #4).

        Never permanently blacklists a still-growing file: a rollout seen before
        its ``session_meta``/cwd row landed is re-peeked once its bytes change.
        """
        for path in list(self._codex_unmatched):
            if path in self._known:
                self._codex_unmatched.pop(path, None)
                continue
            sig = _file_signature(path)
            if sig == (0, 0.0):
                # File vanished — drop it so we don't re-stat forever.
                self._codex_unmatched.pop(path, None)
                continue
            if sig == self._codex_unmatched[path]:
                continue  # unchanged since last peek — skip the re-read
            self._consider_codex_file(path)

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
        self, path: Path, offset: int, end: Optional[int] = None
    ) -> tuple[List[str], int]:
        """Read from ``offset`` to EOF; return complete lines + new byte offset.

        A trailing fragment with no ``\\n`` is a half-written line — it is dropped
        and the offset is NOT advanced past it, so it is re-read next tick once
        its newline lands (03-RESEARCH Pitfall 1).

        ``end`` bounds the read at a byte offset already known to sit on a line
        boundary. :meth:`_rows_through` uses it to re-read the prefix of a file
        WITHOUT racing the writer: bytes that landed since the batch was read
        must not leak into it, or turns from a later batch would be emitted as
        part of this one and then emitted again next tick.
        """
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = (
                    handle.read() if end is None else handle.read(max(0, end - offset))
                )
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

    def _rows_through(self, path: Path, end: int) -> List[dict]:
        """Every complete row from the START of the file through ``end`` bytes.

        Parsed QUIETLY — no unknown-type or unparseable-line warnings — because
        this re-reads bytes the tick already warned about; :meth:`_parse_lines`
        owns that reporting for the delta.
        """
        lines, _ = self._read_new_complete_lines(path, 0, end=end)
        rows: List[dict] = []
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    # ------------------------------------------------------------------ #
    # Tick                                                                #
    # ------------------------------------------------------------------ #

    def tick(self) -> None:
        if time.monotonic() - self._last_enum >= self._reenum_interval:
            self._enumerate()
        # Snapshot to avoid mutation-during-iteration if enumerate runs.
        # One bad file must not kill the tick — but a systemic failure (e.g.
        # fd exhaustion breaking every sqlite open) must not print one
        # traceback PER FILE either: log the first with full traceback and
        # summarize the rest of this tick.
        suppressed = 0
        first_logged = False
        for path, harness in list(self._known.items()):
            try:
                self._tick_file(path, harness)
            except Exception:  # noqa: BLE001
                if first_logged:
                    suppressed += 1
                else:
                    first_logged = True
                    logger.exception("session tail failed for %s", path)
        if suppressed:
            logger.error(
                "session tail: %d more files failed this tick (first traceback above; "
                "likely one root cause)",
                suppressed,
            )

    def _tick_file(self, path: Path, harness: str) -> None:
        offset = self._offsets.get(path)
        if offset is None:
            offset = self.sessions_db.get_offset(path)
        # Cheap stat gate (fd-exhaustion fix): ``_known`` is dominated by finished,
        # idle transcripts, and a large project history can hold tens of thousands
        # of them. Opening the .jsonl (and, uncached, a sqlite connection) for
        # EVERY known file EVERY tick exhausts the process fd table faster than it
        # is reclaimed — surfacing as "Too many open files" / "unable to open
        # database file" storms across the daemon. A single ``stat`` (no fd) lets
        # us skip the open when there are no new bytes.
        try:
            size = path.stat().st_size
        except OSError:
            # The file vanished (deleted / rotated). Stop tracking it so ``_known``
            # and the per-tick work stay bounded over a long-running daemon.
            self._known.pop(path, None)
            self._offsets.pop(path, None)
            return
        if size < offset:
            # The file SHRANK (truncated / rotated / replaced at the same path).
            # The cached offset is now past EOF and ``size <= offset`` would skip
            # it forever — reset to re-read from the start. The successful read
            # below rewrites the offset, so this self-corrects (codex review).
            offset = 0
        # Cache the offset so subsequent idle ticks never touch sqlite for this
        # file again (the first tick may have read it from the DB above).
        self._offsets.setdefault(path, offset)
        if size <= offset:
            return  # fully tailed, no new bytes — skip the open entirely
        lines, new_offset = self._read_new_complete_lines(path, offset)
        if not lines or new_offset == offset:
            return
        new_rows = self._parse_lines(lines)
        # Re-parse the whole (small, project-owned) transcript into a full
        # HarnessSession with metadata["turns"] populated by the verified parsers.
        # No ``dropped`` accumulator: this runs on every poll cycle, from the
        # tailer's own thread, and nothing here reads a drop tally. Passing one
        # would make the count grow for the lifetime of `tesserae engine` and
        # describe an unbounded span of polls rather than one discovery.
        #
        # The turns are parsed from every complete row THROUGH this batch, not
        # from the batch alone. A tool result names only the id of the call it
        # answers, so a batch that contains the result but not the invocation
        # cannot resolve the name and emits a generic "tool" / "function_call".
        # That is the common case, not an edge one: a call and its result are
        # written seconds apart and the poll interval falls between them.
        # ``on_chunk_turns`` then persists the wrong attribution under a
        # uniqueness key a later backfill will not replace, so the activity
        # summary stays mislabelled for good.
        #
        # The delta is taken off the TAIL of that parse rather than off
        # ``session.metadata["turns"]``: the session is parsed from the raw
        # file, which may carry a half-written trailing line this tick has
        # deliberately not consumed, and slicing that list would emit a turn
        # whose bytes are not yet accounted for by the offset.
        rows_through = new_rows if offset <= 0 else self._rows_through(path, new_offset)
        if harness == "claude":
            session = _parse_claude_session(self.project_root, self._root_for(path), path)
            turns_through = _claude_turns(rows_through)
            delta_len = len(_claude_turns(new_rows))
        else:
            session = _parse_codex_session(self.project_root, self._root_for(path), path)
            turns_through = _codex_turns(rows_through)
            delta_len = len(_codex_turns(new_rows))
        # Turn production is row-local in COUNT — a row contributes the same
        # number of turns parsed alone or in context — so the last ``delta_len``
        # entries are exactly this batch's, now with names resolved.
        #
        # EXCEPT when the prefix parse saturates. Both parsers ``break`` at
        # ``_TURN_LIMIT_BACKSTOP``, and a saturated ``turns_through`` stops
        # growing — so the tail slice would return the SAME trailing turns on
        # every subsequent tick, duplicating them forever and never emitting the
        # rows that actually arrived. Reproduced at a simulated backstop of 5:
        # three ticks emitted 'done A' three times and 'done B' never.
        # Fall back to the delta parse there: it loses the cross-batch tool-name
        # resolution this block exists for, which is strictly better than a
        # stuck tail.
        #
        # Not reachable on any observed corpus — across the 49 live transcripts
        # over 3 MB the maximum is 3,532 turns (28x headroom), and at the highest
        # observed density a file would need ~436 MB to reach the backstop
        # against a 43 MB observed maximum. Guarded anyway because the failure is
        # silent, unbounded, and the check is one comparison.
        if delta_len and len(turns_through) < _TURN_LIMIT_BACKSTOP:
            new_turns = turns_through[len(turns_through) - delta_len:]
        elif delta_len:
            new_turns = _claude_turns(new_rows) if harness == "claude" else _codex_turns(new_rows)
        else:
            new_turns = []
        if session is None:
            # File doesn't (yet) match the project — advance offset so we don't
            # re-scan the same complete bytes forever, but emit nothing.
            self._offsets[path] = new_offset
            self.sessions_db.set_offset(path, new_offset)
            return

        if is_tesserae_internal_session(session):
            # Tesserae's OWN compile-time LLM call (codex/claude exec for
            # extraction/synthesis/etc.) captured by the monitor — never ingest
            # it (self-capture feedback loop). Advance the offset so we don't
            # re-scan, but emit nothing.
            self._offsets[path] = new_offset
            self.sessions_db.set_offset(path, new_offset)
            return

        # CRITICAL ORDERING: persist session + offset BEFORE the callback so the
        # debounced compile reads correct state regardless of changed_paths drop.
        # ATOMICITY (Codex #5): the session row and its resume offset are written
        # in ONE transaction so a crash between them can't leave a stale offset
        # that re-emits already-ingested turns on restart.
        self.sessions_db.upsert_with_offset(
            session, jsonl_path=path, last_offset=new_offset
        )
        self._offsets[path] = new_offset
        if new_turns and self.on_chunk_turns is not None:
            # Daily chunk capture (session_chunks.db) — an optimization only.
            # It must NEVER raise into the tick: a chunk failure degrades the
            # activity summary to its raw scan, it does not break tailing.
            try:
                self.on_chunk_turns(
                    "codex" if harness == "codex" else "claude-code",
                    path,
                    f"{self._root_for(path).name}:{path.stem}",
                    new_turns,
                )
            except Exception:  # noqa: BLE001 — chunk capture is best-effort
                logger.warning(
                    "session chunk write failed for %s", path, exc_info=True
                )
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

    # Benign metadata row types are skipped silently; anything truly new warns
    # ONCE per process — a per-row warning floods the console on live tailing.
    _KNOWN_ROW_TYPES = frozenset({
        "user", "assistant", "system", "summary", "permission-mode",
        "attachment", "session_meta", "response_item", "event_msg",
        "turn_context", "compact_boundary",
        # Claude Code metadata rows (mid-2026 transcript format):
        "last-prompt", "queue-operation", "mode", "ai-title",
        "file-history-snapshot", "pr-link",
    })
    _warned_row_types: "set[str]" = set()

    @classmethod
    def _warn_unknown_type(cls, row: dict) -> None:
        rtype = row.get("type")
        if (
            isinstance(rtype, str)
            and rtype
            and rtype not in cls._KNOWN_ROW_TYPES
            and rtype not in cls._warned_row_types
        ):
            cls._warned_row_types.add(rtype)
            logger.warning("unrecognized transcript row type: %s (warning once)", rtype)

    def _root_for(self, path: Path) -> Path:
        """Return the harness config root that owns ``path`` (best-effort)."""
        for root in self._watch_roots:
            try:
                path.relative_to(root)
                return root
            except ValueError:
                continue
        return self._watch_roots[0] if self._watch_roots else self.project_root
