"""Normalized inbound agent-harness session history.

This module is intentionally separate from :mod:`tesserae.agent_harness`:
that module writes outbound harness config/instructions for agents, while this
one stores inbound historical sessions discovered from Claude Code, Codex, and
future local coding harnesses.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field, replace as replace_dc
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .redaction import redact_home_paths

logger = logging.getLogger(__name__)

# How far into nested ``content`` lists the tally walks. Harness images sit at
# depth 1 (inside a ``tool_result``) and the Anthropic content schema does not
# nest a tool_result inside a tool_result, so 1 is the real-world maximum; a
# scan of 150 recent transcripts found nothing deeper. The cap is set well
# above that purely so a malformed or hand-edited transcript cannot make the
# importer walk forever — termination must not depend on the input being
# well-formed. Hitting it is itself counted (``<truncated>``) rather than
# silently dropping the tail, which is the whole point of this tally.
_MAX_CONTENT_DEPTH = 8
_TRUNCATED_KEY = "<truncated>"


class HarnessDiscovery(List["HarnessSession"]):
    """The sessions one discovery found, plus what it could NOT represent.

    The tally travels with the result it describes. It used to be a module
    global, cleared at the start of a run and read at the end of it, which is
    only correct while exactly one discovery is ever in flight — and it is not:
    ``tesserae refresh --jobs N`` dispatches projects into a ThreadPoolExecutor
    inside ONE process (``multiproject.run_across_projects``). Two projects
    then shared one dict, and depending on which thread cleared it last, either
    a project with no images reported another project's three, or a project
    WITH three reported none. The second is precisely the silent multimodal gap
    this measurement exists to expose.

    A ``list`` subclass rather than a tuple or a new dataclass because ~20 call
    sites already treat the return of :func:`discover_harness_sessions` as the
    list of sessions and only two of them want the tally. Read it through
    :func:`dropped_content_blocks`, which also copes with the plain ``list``
    that test doubles return.
    """

    def __init__(
        self,
        sessions: Iterable["HarnessSession"] = (),
        dropped: Optional[Mapping[str, int]] = None,
    ) -> None:
        super().__init__(sessions)
        self.dropped_content_blocks: Dict[str, int] = dict(dropped or {})


def dropped_content_blocks(sessions: object) -> Dict[str, int]:
    """Content blocks a discovery could not represent, keyed by block ``type``.

    A copy, so a caller measuring the multimodal gap cannot edit the tally it
    is reading. ``{}`` for a plain list — a stubbed discovery measured nothing,
    which is not the same as "measured zero", but the only honest thing to
    report for a result that carries no measurement is nothing at all.
    """
    value = getattr(sessions, "dropped_content_blocks", None)
    return dict(value) if isinstance(value, dict) else {}


def format_dropped_content_blocks(sessions: object) -> Optional[str]:
    """One line naming what ``sessions``' discovery could not represent, or None.

    None when nothing was dropped — a summary printed unconditionally becomes
    background noise, and an operator stops reading it.
    """
    dropped = dropped_content_blocks(sessions)
    if not dropped:
        return None
    histogram = ", ".join(f"{key}={dropped[key]}" for key in sorted(dropped))
    return (
        f"Dropped {sum(dropped.values())} content block(s) with no text "
        f"projection: {histogram}"
    )


def _tally_dropped_blocks(
    content: object, dropped: Dict[str, int], depth: int = 0
) -> None:
    """Count every block in ``content`` that has no text projection, by type.

    Recurses into a block's own ``content`` list, because that is where the
    multimodal gap actually lives: harness images are attached INSIDE a
    ``tool_result``, never at the top level of a message. A tally that stopped
    at the ``tool_result`` counted it as one opaque drop and reported zero
    images on a machine that had them.

    A container is counted under its own type AND descended into. Both facts
    are true and neither implies the other: a ``tool_result`` full of text is a
    real drop (``_content_to_text`` returns nothing for it), and an image
    inside it is a second, different one.
    """
    if not isinstance(content, list):
        return
    if depth > _MAX_CONTENT_DEPTH:
        dropped[_TRUNCATED_KEY] = dropped.get(_TRUNCATED_KEY, 0) + 1
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("input_text") or item.get("output_text")
        if isinstance(text, str):
            continue
        kind = item.get("type")
        key = kind if isinstance(kind, str) and kind else "<untyped>"
        dropped[key] = dropped.get(key, 0) + 1
        _tally_dropped_blocks(item.get("content"), dropped, depth + 1)


def _tally_dropped_blocks_in_rows(
    rows: Sequence[Mapping[str, object]], dropped: Optional[Dict[str, int]]
) -> None:
    """Tally one transcript, ONCE, into the caller's own accumulator.

    ``dropped`` is None for callers that are not measuring — notably
    ``engine/session_tail.py``, which re-parses transcripts on every poll cycle
    and neither resets nor reads a tally. Threading the accumulator instead of
    writing to module state is what stops that loop growing a count forever,
    and stops its ``threading.Thread`` (daemon.py) mutating a dict a
    ``refresh --jobs`` worker is reading.

    Deliberately not done inside :func:`_content_to_text`: that helper is
    called an unpredictable number of times per transcript (activity, turns,
    title/preview all re-flatten the same rows), so counting there multiplied
    every block by the number of passes and the histogram measured passes
    rather than content.
    """
    if dropped is None:
        return
    for row in rows:
        message = row.get("message")
        if isinstance(message, dict):
            _tally_dropped_blocks(message.get("content"), dropped)
        payload = row.get("payload")
        if isinstance(payload, dict):
            _tally_dropped_blocks(payload.get("content"), dropped)


@dataclass(frozen=True)
class HarnessSession:
    """Harness-independent record for one local agent session."""

    id: str
    slug: str
    harness: str
    agent_label: str
    project_name: str
    project_root: str
    started_at: str
    ended_at: str = ""
    branch: str = ""
    commit_before: str = ""
    commit_after: str = ""
    model: str = ""
    title: str = ""
    summary: str = ""
    message_count: int = 0
    tool_call_count: int = 0
    token_input: int = 0
    token_output: int = 0
    token_total: int = 0
    cache_hit_ratio: Optional[float] = None
    tools_used: List[str] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    commands_run: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    raw_transcript_path: str = ""
    redacted_preview: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)
    #: Which importer wrote this record. Store bookkeeping, not session content:
    #: it is what lets a writer tell its own records from another producer's, on
    #: the only axis that actually separates them. Two importers routinely
    #: describe the SAME transcript — Tesserae's local scan mints a plain record
    #: from ~/.claude, while an orchestrator exports the same session with the
    #: agent identity it alone knows — so "where the transcript lives" cannot
    #: distinguish them and neither can the harness name. Empty means a record
    #: written before this field existed: unowned, and therefore nobody's to
    #: delete or overwrite. See PRODUCER_DISCOVERY / PRODUCER_IMPORT.
    producer: str = ""
    #: Which MACHINE harvested this record. The second axis of the same idea as
    #: ``producer``, and the one that matters when several servers share a disk
    #: and therefore share ``.tesserae``. ``producer`` cannot separate them:
    #: every host's local scan stamps the same constant PRODUCER_DISCOVERY, and
    #: their ``~/.claude`` paths resolve to the same string, so the prune scope
    #: check passes on a host that never saw the transcript. Empty means a
    #: record written before this field existed: unowned, and nobody's to
    #: delete without ``adopt_unowned``. See :func:`local_host_id`.
    host: str = ""

    @property
    def date(self) -> str:
        match = re.match(r"\d{4}-\d{2}-\d{2}", self.started_at or "")
        return match.group(0) if match else "undated"

    @property
    def safe_project(self) -> str:
        return safe_slug(self.project_name or Path(self.project_root).name or "project")

    @property
    def filename(self) -> str:
        stem = safe_slug(self.slug or self.title or self.id)
        digest = hashlib.sha1(self.id.encode("utf-8")).hexdigest()[:8]
        return f"{self.date}-{stem}-{digest}"

    @property
    def href(self) -> str:
        return f"sessions/{self.safe_project}/{self.filename}.html"

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["date"] = self.date
        payload["href"] = self.href
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "HarnessSession":
        allowed = set(cls.__dataclass_fields__.keys())
        clean: Dict[str, object] = {k: payload[k] for k in allowed if k in payload}
        for key in ("tools_used", "files_touched", "commands_run", "decisions", "errors"):
            value = clean.get(key)
            if value is None:
                clean[key] = []
            elif not isinstance(value, list):
                clean[key] = [str(value)]
        meta = clean.get("metadata")
        if meta is None or not isinstance(meta, dict):
            clean["metadata"] = {}
        return cls(**clean)  # type: ignore[arg-type]


def safe_slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value)).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text or "session"


def session_matches_project(session: HarnessSession, project_root: str | Path) -> bool:
    """Return true when a normalized session belongs to ``project_root``."""

    return _path_value_matches_project(session.project_root, Path(project_root).resolve())


class HarnessSessionStore:
    """Read/write normalized sessions under ``.tesserae/harness_sessions``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"

    def write_sessions(
        self,
        sessions: Iterable[HarnessSession],
        *,
        replace: bool = False,
        prune_roots: Optional[Sequence[str | Path]] = None,
        prune_harnesses: Optional[Sequence[str]] = None,
        producer: str = "",
        host: str = "",
        adopt_unowned: bool = False,
    ) -> Dict[str, object]:
        """Write normalized sessions into the store.

        Default (``replace=False``) MERGES: existing records are kept, new
        sessions are added, and the manifest is rebuilt from everything on
        disk. This makes an empty import / empty discover a no-op instead of a
        store wipe.

        ``replace=True`` also prunes stale records, so changed filename schemes
        or deduped imports cannot leave orphan pages/search entries behind.

        ``producer`` is what makes either safe. Two importers routinely
        describe the SAME session: the local scan mints a plain record from a
        transcript under ``~/.claude``, and an orchestrator exports that same
        session carrying the agent identity only it knows. They collide on
        filename, because both derive it from the session id. So a writer may
        only touch records it produced:

        * a record is pruned only if its ``producer`` equals this one;
        * a record is **not overwritten** by a different producer — the
          incoming write is skipped and counted in ``preserved``.

        ``prune_roots`` and ``prune_harnesses`` narrow further, to what the
        caller could see: a record is pruned only if its transcript lives under
        a scanned root and its harness was scanned. They are a second gate, not
        the mechanism — two producers reading the same transcripts are
        indistinguishable by path, which is what issue #104 turned out to be.

        A record with an empty ``producer`` predates this field. It is unowned,
        so nobody prunes or overwrites it. ``adopt_unowned=True`` claims those
        records for ``producer`` — a one-time migration for a store written
        before provenance existed, and wrong to pass if another tool writes
        into the same store.

        ``host`` is the same idea on the axis ``producer`` cannot express: WHICH
        MACHINE harvested the record. On a shared disk every host's scan stamps
        the same ``PRODUCER_DISCOVERY`` and their ``~/.claude`` roots resolve to
        the same string, so both gates above pass on a host that never saw the
        transcript and it deletes another machine's record. When ``host`` is
        given, a record is pruned only if it carries the same host (or is
        unowned and ``adopt_unowned`` is set).

        The WRITE path stays host-blind on purpose. Two hosts can only write the
        same session id when both can actually see the transcript, so the write
        is idempotent and simply re-stamps ownership onto whoever last proved
        visibility. Gating writes by host instead would freeze a decommissioned
        machine's records in place with no way to reclaim them.

        **Omitting ``prune_roots`` with ``replace=True`` deletes every record
        this producer owns**, and with no ``producer`` either, the whole store.
        That is the pre-#104 behaviour, kept only for a caller that genuinely
        owns everything in it. There is no such caller in Tesserae.
        """
        ordered = sorted(list(sessions), key=lambda s: (s.started_at or "", s.harness, s.slug))
        self.root.mkdir(parents=True, exist_ok=True)
        written: set[Path] = set()
        preserved = 0
        for session in ordered:
            harness_dir = self.root / safe_slug(session.harness)
            harness_dir.mkdir(parents=True, exist_ok=True)
            json_path = harness_dir / f"{session.filename}.json"
            owner = _record_producer(json_path)
            if owner is not None and not _may_write(owner, producer, adopt_unowned):
                # Somebody else's record for this same session. Theirs carries
                # attribution this writer cannot reconstruct, so it wins; the
                # store keeps one record per session and this is which one.
                preserved += 1
                continue
            if producer or host:
                session = replace_dc(
                    session,
                    producer=producer or session.producer,
                    host=host or session.host,
                )
            payload = session.to_dict()
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            md_path = harness_dir / f"{session.filename}.md"
            md_path.write_text(render_session_markdown(session), encoding="utf-8")
            written.update((json_path, md_path))
        removed = 0
        if replace:
            scope = _prune_scope(prune_roots)
            kinds = {h.lower() for h in prune_harnesses} if prune_harnesses is not None else None
            for stale in list(self.root.glob("*/*.json")) + list(self.root.glob("*/*.md")):
                if stale in written or not _within_prune_scope(stale, scope, kinds):
                    continue
                owner = _record_producer(stale.with_suffix(".json"))
                if owner is not None and not _may_write(owner, producer, adopt_unowned):
                    continue  # not this producer's record; not this producer's to delete
                record_host = _record_host(stale.with_suffix(".json"))
                if record_host is not None and not _may_prune_host(
                    record_host, host, adopt_unowned
                ):
                    continue  # harvested by another machine; not this one's to delete
                try:
                    stale.unlink()
                except OSError:
                    continue
                if stale.suffix == ".json":
                    removed += 1
        # Rebuild the manifest from disk so records this write did not touch —
        # and did not prune — survive.
        merged = sorted(
            self.list_sessions(),
            key=lambda s: (s.started_at or "", s.harness, s.slug),
        )
        manifest = {"version": "1", "sessions": [_manifest_entry(s) for s in merged]}
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"path": str(self.root), "sessions": len(written) // 2, "total": len(merged),
                "removed": removed, "preserved": preserved}

    def prune_internal(self, dry_run: bool = False) -> Dict[str, object]:
        """Delete records that are Tesserae's OWN compile-time LLM calls.

        The live discovery filter stops NEW ones being written, but it cannot
        retract what a previous version already stored, and
        ``HarnessSessionsDB.prune_internal_sessions`` only cleans the sqlite
        store. This directory is the one ``compile`` actually reads, so a store
        cleaned only in sqlite still feeds the graph its own extraction calls.

        Deliberately NOT gated on ``producer`` or ``host``, unlike every other
        prune here. Those gates exist to stop one writer destroying another
        writer's *work*; a captured extraction prompt is not anyone's work, it
        is this tool's exhaust, and it is identifiable from its content alone no
        matter which machine or importer filed it.

        ``dry_run`` counts without deleting — worth using first, because the
        ratio of noise to real sessions here is high enough that a mistake would
        be invisible in a summary count.
        """
        removed = 0
        kept = 0
        unreadable = 0
        for record in sorted(self.root.glob("*/*.json")):
            try:
                session = HarnessSession.from_dict(
                    json.loads(record.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                unreadable += 1  # unparseable is not provably internal — keep it
                continue
            if not is_tesserae_internal_session(session):
                kept += 1
                continue
            removed += 1
            if dry_run:
                continue
            for path in (record, record.with_suffix(".md")):
                try:
                    path.unlink()
                except OSError:
                    pass
        if not dry_run and removed:
            merged = sorted(
                self.list_sessions(),
                key=lambda s: (s.started_at or "", s.harness, s.slug),
            )
            manifest = {"version": "1", "sessions": [_manifest_entry(s) for s in merged]}
            self.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return {"removed": removed, "kept": kept, "unreadable": unreadable, "dry_run": dry_run}

    def list_sessions(self) -> List[HarnessSession]:
        if not self.root.exists():
            return []
        sessions: List[HarnessSession] = []
        for path in sorted(self.root.glob("*/*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(HarnessSession.from_dict(payload))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
        sessions.sort(key=lambda s: (s.started_at or "", s.harness, s.slug), reverse=True)
        return sessions


PRODUCER_DISCOVERY = "tesserae:discover"   #: written by the local harness scan
PRODUCER_IMPORT = "tesserae:import"        #: written by `sessions import <path>`

#: Where a machine's stable Tesserae host id is kept. Deliberately under the
#: per-host ``~/.tesserae`` rather than inside the (possibly shared) project
#: directory — the whole point is to distinguish machines that share one.
HOST_ID_PATH = Path.home() / ".tesserae" / "host_id"

_HOST_ID_CACHE: Optional[str] = None


def local_host_id() -> str:
    """The stable identity of the machine doing the harvesting.

    Resolution order:

    1. ``TESSERAE_HOST_ID`` — an operator override, and what tests pin.
    2. ``~/.tesserae/host_id`` — generated once, then reused forever.
    3. A freshly generated ``<hostname>-<8 hex>``, persisted to (2).

    A *persisted* id rather than a bare ``socket.gethostname()`` on purpose:
    hostnames get changed, duplicated across a fleet built from one image, and
    reassigned by DHCP. Any of those silently transfers ownership of another
    machine's session records — which is exactly the failure this exists to
    stop. The random suffix means two hosts that genuinely share a hostname
    still differ.

    Falls back to the bare hostname when ``~/.tesserae`` is unwritable: a
    read-only home should degrade to today's behaviour, not crash a harvest.
    """
    global _HOST_ID_CACHE

    override = (os.environ.get("TESSERAE_HOST_ID") or "").strip()
    if override:
        return safe_slug(override)
    if _HOST_ID_CACHE is not None:
        return _HOST_ID_CACHE

    import socket
    import uuid

    hostname = ""
    try:
        hostname = socket.gethostname().split(".")[0]
    except OSError:  # pragma: no cover — gethostname failing is exotic
        hostname = ""

    try:
        stored = HOST_ID_PATH.read_text(encoding="utf-8").strip()
        if stored:
            _HOST_ID_CACHE = safe_slug(stored)
            return _HOST_ID_CACHE
    except (OSError, ValueError):
        pass

    minted = safe_slug(f"{hostname or 'host'}-{uuid.uuid4().hex[:8]}")
    try:
        HOST_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
        HOST_ID_PATH.write_text(minted + "\n", encoding="utf-8")
    except OSError:
        # Unwritable home: use the hostname and do NOT cache a random id that
        # would change on every process, which would make every record look
        # like it came from a different machine.
        return safe_slug(hostname or "host")
    _HOST_ID_CACHE = minted
    return minted


def _record_producer(json_path: Path) -> Optional[str]:
    """The producer stamped on a stored record, or None when there is no record.

    An unreadable record answers "" — unowned — rather than raising or being
    treated as the caller's. Same rule as everywhere else here: what cannot be
    shown to be mine is not mine.
    """
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        return str(payload.get("producer") or "")
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return ""


def _record_host(json_path: Path) -> Optional[str]:
    """The host stamped on a stored record, or None when there is no record.

    Same conservatism as :func:`_record_producer`: unreadable answers "" —
    unowned — rather than raising or claiming the record for the caller.
    """
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        return str(payload.get("host") or "")
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return ""


def _may_prune_host(record_host: str, host: str, adopt_unowned: bool) -> bool:
    """May the machine identified by `host` delete a record stamped `record_host`?

    Mirrors :func:`_may_write` on the host axis. A caller that passes no
    ``host`` keeps the pre-host behaviour, so a single-machine deployment is
    byte-identical and needs no migration. A record with no host is unowned and
    survives until someone adopts it — the deliberate conservative choice,
    because every record written before this field existed carries the same
    ``PRODUCER_DISCOVERY`` and would otherwise stay deletable by any host.
    """
    if not host:
        return True          # host-unaware caller, legacy behaviour
    if record_host == host:
        return True          # this machine harvested it
    return not record_host and adopt_unowned


def _may_write(owner: str, producer: str, adopt_unowned: bool) -> bool:
    """May `producer` overwrite or delete a record owned by `owner`?"""
    if not producer:
        return True          # unscoped caller, legacy behaviour
    if owner == producer:
        return True          # its own record
    return not owner and adopt_unowned   # unowned, and the caller asked to adopt


def _prune_scope(roots: Optional[Sequence[str | Path]]) -> Optional[List[Path]]:
    if roots is None:
        return None
    scope: List[Path] = []
    for root in roots:
        try:
            scope.append(Path(root).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            continue  # unresolvable root scopes nothing; see _within_prune_scope
    return scope


def _within_prune_scope(
    path: Path,
    scope: Optional[List[Path]],
    harnesses: Optional[Set[str]] = None,
) -> bool:
    """Return true when a stored record is the writer's to prune.

    Every uncertain case answers *false*. Pruning is destructive and a scan's
    authority is narrow, so "I cannot show this record is mine" has to mean
    "leave it alone" — the whole point of issue #104.
    """

    if scope is None:
        return True  # unscoped replace: the caller owns the whole store
    record = path.with_suffix(".json")
    if not record.exists():
        return True  # a page with no record behind it is an orphan by definition
    try:
        payload = json.loads(record.read_text(encoding="utf-8"))
        source = str(payload.get("raw_transcript_path") or "")
        harness = str(payload.get("harness") or "")
    except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        # The record exists but will not parse: mid-write by another producer,
        # or corrupt. Either way its owner is unknown, so it is not ours to
        # delete. Deleting here would recreate #104 through a narrower door.
        return False
    if harnesses is not None and harness.lower() not in harnesses:
        # `--harness codex` scans codex roots only, so a codex run has nothing
        # to say about a claude-code record even when both live under a root it
        # happened to walk. Scope is (root AND harness), not root alone.
        return False
    if not source:
        return False  # no local-transcript provenance: another producer's record
    try:
        resolved = Path(source).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        # A symlink loop raises RuntimeError, an embedded NUL raises ValueError.
        # Unresolvable means unprovable, and this runs after the new files are
        # written but before the manifest is rebuilt — raising here would leave
        # the manifest disagreeing with the disk.
        return False
    return any(resolved == root or root in resolved.parents for root in scope)


DEFAULT_HARNESS_ROOT_NAMES: Tuple[str, ...] = (".claude", ".codex")


def discover_harness_roots(home: str | Path | None = None) -> List[Path]:
    """Find local Claude Code and Codex config roots under ``home``.

    The default accounts live at ``~/.claude`` and ``~/.codex``, but users can
    keep multiple accounts in arbitrarily named sibling directories. Detect
    hidden home-directory candidates by harness-specific marker files/directories
    rather than by a fixed list of account names or suffixes.
    """

    base = Path(home).expanduser() if home is not None else Path.home()
    candidates: List[Path] = []
    for name in DEFAULT_HARNESS_ROOT_NAMES:
        candidates.append(base / name)
    try:
        candidates.extend(
            p for p in base.iterdir()
            if p.is_dir() and p.name.startswith(".")
        )
    except OSError:
        pass

    roots: List[Path] = []
    seen: set[Path] = set()
    for candidate in sorted(set(candidates)):
        if candidate in seen or not candidate.exists():
            continue
        if _root_supports_claude(candidate) or _root_supports_codex(candidate):
            seen.add(candidate)
            roots.append(candidate)
    return roots


def _root_supports_claude(root: Path) -> bool:
    return any((root / marker).exists() for marker in ("projects", "history.jsonl", "settings.json", "settings.local.json"))


def _root_supports_codex(root: Path) -> bool:
    return any((root / marker).exists() for marker in ("sessions", "history.jsonl", "config.toml", "auth.json"))


# Verbatim opening phrases of Tesserae's OWN LLM system prompts. A discovered
# "session" that is really one of Tesserae's compile-time codex/claude calls
# (extraction, synthesis, community summaries, research, distillation, memory
# arbitration, doc extraction, …) is recorded by the harness like any CLI session
# and opens with one of these. We must NOT ingest our own LLM calls as user
# sessions — that is a self-capture feedback loop (the session DB fills with
# Tesserae's prompts, drowning real work, and the next compile "extracts findings"
# from Tesserae's own extraction calls).
#
# Keep in sync when a new Tesserae system prompt is added — the coverage test
# ``tests/test_harness_self_capture.py::test_every_system_prompt_is_covered``
# greps the package for system-prompt constants and fails if one is unlisted.
_TESSERAE_PROMPT_SIGNATURES: tuple[str, ...] = (
    # THE highest-volume prompt Tesserae issues: one call per document and per
    # session, on every compile. It was missing here for months, and the
    # anti-drift guard could not see it because that guard matched an allowlist
    # of opening verbs — are|distill|write|decide|arbitrate|split — and this one
    # opens with "You extract". Measured consequence on this repo: 14,377 of the
    # 14,606 records in .tesserae/harness_sessions (98.4%) were Tesserae's own
    # extraction calls, 10,666 of them carrying this exact title. The knowledge
    # base was mostly the tool talking to itself. The guard now enumerates
    # ``system=`` kwargs from the AST instead of guessing at verbs.
    "You extract a typed research-intelligence graph",
    "You are a Tesserae liveness probe",
    # Found only once the anti-drift scanner learned to resolve module-level
    # constants (`system=_SUMMARY_SYSTEM`) rather than inline literals alone.
    # Every one of these was landing in the session store as a user session;
    # the activity-summary prompt accounted for 14 surviving records in this
    # repo even AFTER the first prune.
    "You summarize a developer's activity for a time period",
    "You are summarizing a developer's activity for a time period",
    "You extract EXPLICIT decisions from a developer's agent-session excerpts",
    "You judge whether a SOURCE text supports a CLAIM",
    "You route a question to the right project",
    "You are an extractor that reads agent/user conversation transcripts",
    "You are summarizing a community of related typed research-graph nodes",
    "You are extracting a typed research intelligence graph for Tesserae",
    "You are an Tesserae synthesis writer",
    "You are the librarian voice of Tesserae",
    "You are the retrieval planner for a project knowledge graph",
    "You are an ontology engineer assisting the Tesserae knowledge-graph",
    "You are the lead planner of an agentic research loop",
    "You are a research subagent. Given a sub-question",
    "You are the writer of an agentic research report",
    "You are a structured-output adapter for Cognee",
    "You are a writing assistant for Cognee",
    "Summarize the following in 2 sentences as a TL;DR",
    # JSON-client compile/retrieval prompts (codex/claude exec, also captured):
    "You distill a cluster of related coding/agent session findings",
    "You write ONE terse extraction-guidance bullet",
    "You decide whether one research-session finding obsoletes another",
    "You arbitrate a contradiction between two research performance claims",
    "You split a single retrieval question into a short list",
    # agent_distill (per-agent L1 distillation, §5.3) map/reduce/fold prompts:
    "You distill a cluster of related agent-session findings",
    "You merge partial distilled notes over ONE cluster",
    "You maintain a distilled note in an agent's knowledge base",
    # agent_harness per-agent pointer block (§9): NOT an LLM call Tesserae
    # issues — it is instruction text embedded in harness files an agent reads,
    # so it is never captured as a session. Listed only so the source-scan
    # anti-drift guard (test_harness_self_capture) treats it as covered. The
    # literal ``{agent_key}`` placeholder is deliberate: it can never match a
    # real (resolved) session blob, so this entry adds zero false-positive risk
    # (dropping real work is worse than a missed capture — see the docstring).
    "You are agent `{agent_key}`; work from your own distilled expertise layer.",
)


def is_tesserae_internal_session(session: HarnessSession) -> bool:
    """True when a discovered "session" is actually one of Tesserae's OWN
    compile-time LLM subprocess calls, captured by the harness session monitor.

    Detection is by verbatim system-prompt signature (see
    :data:`_TESSERAE_PROMPT_SIGNATURES`). Matching is *anchored* — a blob must
    BEGIN with a signature, not merely contain one — so a real user session that
    quotes or reviews one of these prompts mid-conversation is NOT flagged. The
    title is already harness-boilerplate-stripped (see :func:`_title_and_preview`),
    so a Tesserae call's title/preview opens with its system prompt; the first few
    raw turns are also checked (anchored) in case a leading boilerplate turn pushed
    the prompt out of the title. False positives (dropping real work) are worse
    than false negatives here, so the anchor is deliberate.

    The turn window is the OPENING of the session: the turns spoken before the
    model first replies, capped at three. Three properties have to hold at once
    and only this window has all three.

    * a tool RESULT must not match. A ``Read`` of a prompt constant, a ``cat``
      of ``prompts/``, or a subagent result that echoes one puts a signature at
      position 0 of the result text. Tool turns are excluded outright.
    * a tool loop must not DISPLACE the signature out of the window. A raw
      ``turns[:3]`` slice is three *minted* turns, so a single tool call before
      the prompt would push it out. Today no Tesserae internal call uses tools
      (a ``claude -p`` one-shot has no tool loop; measured, 700 of 700
      self-capture transcripts carry the signature at turn 0 and 2 carry any
      tool result at all), but an agentic extraction or a retrieval planner
      with MCP access would break that silently. Counting spoken turns only
      makes it an invariant rather than a property of today's call shape.
    * the window must not REACH, and this is the one that bounds the other two.
      Counting spoken turns with no stop condition walks arbitrarily deep: the
      third spoken turn can be a thousand tool turns in, and a user who pastes
      one of these prompts to ask about it — routine in this repository — then
      has their whole session dropped. That is a false positive, and this
      docstring already says false positives are the worse failure. So the
      window ends at the model's first reply: a self-capture record IS a
      one-shot, the prompt is what precedes the answer, and after an answer
      exists, text that looks like a prompt is someone discussing one.
    """
    blobs = [session.title or "", session.summary or "", session.redacted_preview or ""]
    turns = (session.metadata or {}).get("turns") if isinstance(session.metadata, dict) else None
    if isinstance(turns, list):
        opening: List[str] = []
        for turn in turns:
            if not isinstance(turn, Mapping):
                continue
            role = str(turn.get("role") or "")
            if role in ("tool", "tool_result"):
                continue
            if role == "assistant":
                break
            opening.append(str(turn.get("text") or ""))
            if len(opening) >= 3:
                break
        blobs.extend(opening)
    return any(
        blob.lstrip().startswith(_TESSERAE_PROMPT_SIGNATURES) for blob in blobs
    )


def discover_harness_sessions(
    project_root: str | Path,
    roots: Optional[Sequence[str | Path]] = None,
    harnesses: Optional[Sequence[str]] = None,
) -> HarnessDiscovery:
    """Discover local Claude Code / Codex JSONL sessions for ``project_root``.

    Returns a :class:`HarnessDiscovery` — a list of sessions that ALSO carries
    what this scan could not represent, readable via
    :func:`dropped_content_blocks` / :func:`format_dropped_content_blocks`.
    Callers that only want the sessions can keep treating it as a list.

    Discovery is intentionally project-scoped: a transcript must carry a strong
    cwd/workdir signal equal to the project root, or live in Claude Code's
    project-encoded directory for that root. Raw transcript text is not copied
    into the generated pages; the path is stored as provenance only.
    """

    # Local to this call, so two discoveries running at once in the same
    # process (``refresh --jobs N``) cannot see each other's counts.
    dropped: Dict[str, int] = {}
    project = Path(project_root).resolve()
    selected = {h.lower() for h in (harnesses or ("claude-code", "codex"))}
    scan_roots = [Path(r).expanduser() for r in roots] if roots is not None else discover_harness_roots()
    sessions: List[HarnessSession] = []
    seen: set[str] = set()
    seen_roots: set[str] = set()
    scan_cache = _ScanCache.open(_default_scan_cache_path(), project)
    try:
        for root in scan_roots:
            if not root.exists():
                continue
            # ~/.claude is commonly a symlink to the active account directory;
            # scanning both would parse every transcript twice.
            root_key = str(root.resolve())
            if root_key in seen_roots:
                continue
            seen_roots.add(root_key)
            if _root_supports_claude(root) and "claude-code" in selected:
                for session in _discover_claude_sessions(
                    project, root, scan_cache, dropped
                ):
                    if session.id not in seen:
                        seen.add(session.id)
                        sessions.append(session)
            if _root_supports_codex(root) and "codex" in selected:
                for session in _discover_codex_sessions(
                    project, root, scan_cache, dropped
                ):
                    if session.id not in seen:
                        seen.add(session.id)
                        sessions.append(session)
    finally:
        if scan_cache is not None:
            scan_cache.close()
    # Drop Tesserae's own compile-time LLM calls captured by the harness — never
    # ingest our own extraction/synthesis calls as user sessions (self-capture).
    sessions = [s for s in sessions if not is_tesserae_internal_session(s)]
    sessions.sort(key=lambda s: (s.started_at or "", s.harness, s.slug), reverse=True)
    # Say out loud what this discovery could not represent. The histogram is
    # unfiltered on purpose: ``tool_use``/``tool_result``/``thinking`` blocks
    # are structural drops handled elsewhere (``_claude_activity``), while
    # ``image``/``document`` are the multimodal gap. Listing every type keeps
    # the measurement complete rather than shaping it with an allowlist that
    # would drift as harnesses add block types.
    #
    # This log line is for `tesserae engine`, the only caller that configures
    # logging. Every other entry point has to PRINT the same summary itself —
    # see format_dropped_content_blocks() and its callers in cli.py.
    result = HarnessDiscovery(sessions, dropped)
    summary = format_dropped_content_blocks(result)
    if summary:
        logger.info("session discovery: %s", summary)
    return result


def _is_claude_subagent_transcript(path: Path) -> bool:
    return "subagents" in path.parts


def _discover_claude_sessions(
    project: Path,
    root: Path,
    scan_cache: Optional["_ScanCache"] = None,
    dropped: Optional[Dict[str, int]] = None,
) -> List[HarnessSession]:
    project_dir = root / "projects" / _claude_project_dir(project)
    candidates: set[Path] = set()
    if project_dir.exists():
        candidates.update(p for p in project_dir.rglob("*.jsonl") if not _is_claude_subagent_transcript(p))
    projects_root = root / "projects"
    if projects_root.exists():
        # Some account directories may encode paths differently, and history can
        # move between accounts. Scan all project transcripts but keep the
        # parser's strong cwd/path match before importing anything. A strong
        # match requires the project path to appear literally in the file, so a
        # cheap byte scan filters foreign transcripts (often tens of GB across
        # all projects) before the ~50x more expensive JSON parse.
        markers = _project_markers(project)
        candidates.update(
            p
            for p in projects_root.rglob("*.jsonl")
            if p not in candidates
            and not _is_claude_subagent_transcript(p)
            and _mentions_project(p, markers, scan_cache)
        )
    history = root / "history.jsonl"
    if history.exists():
        candidates.add(history)
    return [
        s
        for p in sorted(candidates)
        if (s := _parse_claude_session(project, root, p, dropped=dropped))
    ]


def _discover_codex_sessions(
    project: Path,
    root: Path,
    scan_cache: Optional["_ScanCache"] = None,
    dropped: Optional[Dict[str, int]] = None,
) -> List[HarnessSession]:
    sessions_dir = root / "sessions"
    if not sessions_dir.exists():
        return []
    markers = _project_markers(project)
    return [
        s
        for p in sorted(sessions_dir.rglob("*.jsonl"))
        if _mentions_project(p, markers, scan_cache)
        and (s := _parse_codex_session(project, root, p, dropped=dropped))
    ]


def _mentions_project(path: Path, markers: Tuple[bytes, ...], scan_cache: Optional["_ScanCache"]) -> bool:
    if scan_cache is not None:
        return scan_cache.mentions(path, markers)
    return _file_mentions_project(path, markers)


def _project_markers(project: Path) -> Tuple[bytes, ...]:
    """Byte strings, one of which must appear in any transcript that can pass
    the strong cwd/workdir project match (literal path or ``~``-prefixed)."""
    markers = [str(project).encode("utf-8", "surrogateescape")]
    try:
        rel = project.relative_to(Path.home())
        markers.append(("~/" + str(rel)).encode("utf-8", "surrogateescape"))
    except (ValueError, RuntimeError):
        pass
    return tuple(markers)


def _default_scan_cache_path() -> Path:
    override = os.environ.get("TESSERAE_DISCOVERY_CACHE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "tesserae" / "discovery_scan.sqlite"


class _ScanCache:
    """mtime/size-keyed cache of marker-scan results.

    Screening tens of GB of foreign transcripts is I/O-bound; transcripts are
    append-only once a session ends, so a (mtime_ns, size) match lets repeat
    discover runs skip re-reading unchanged files entirely. Entries are scoped
    by project root because the markers depend on it.
    """

    def __init__(self, con: sqlite3.Connection, project_key: str) -> None:
        self._con = con
        self._project = project_key
        self._rows: Dict[str, Tuple[int, int, int]] = {}
        self._updates: Dict[str, Tuple[int, int, int]] = {}
        self._seen: set[str] = set()

    @classmethod
    def open(cls, path: Path, project: Path) -> Optional["_ScanCache"]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(path, timeout=5.0)
            con.execute(
                "CREATE TABLE IF NOT EXISTS marker_scan ("
                " project TEXT NOT NULL,"
                " path TEXT NOT NULL,"
                " mtime_ns INTEGER NOT NULL,"
                " size INTEGER NOT NULL,"
                " hit INTEGER NOT NULL,"
                " PRIMARY KEY (project, path))"
            )
            cache = cls(con, str(project))
            cache._rows = {
                row[0]: (row[1], row[2], row[3])
                for row in con.execute(
                    "SELECT path, mtime_ns, size, hit FROM marker_scan WHERE project = ?",
                    (cache._project,),
                )
            }
            return cache
        except (sqlite3.Error, OSError):
            return None

    def mentions(self, path: Path, markers: Tuple[bytes, ...]) -> bool:
        key = str(path)
        self._seen.add(key)
        try:
            stat = path.stat()
        except OSError:
            return False
        cached = self._updates.get(key) or self._rows.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return bool(cached[2])
        result = _file_mentions_project(path, markers)
        self._updates[key] = (stat.st_mtime_ns, stat.st_size, 1 if result else 0)
        return result

    def close(self) -> None:
        try:
            if self._updates:
                self._con.executemany(
                    "INSERT OR REPLACE INTO marker_scan (project, path, mtime_ns, size, hit)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [(self._project, k, m, s, h) for k, (m, s, h) in self._updates.items()],
                )
            # Prune entries for transcripts deleted from disk; entries merely
            # outside this run's scan roots are kept.
            stale = [k for k in self._rows.keys() - self._seen if not Path(k).exists()]
            if stale:
                self._con.executemany(
                    "DELETE FROM marker_scan WHERE project = ? AND path = ?",
                    [(self._project, k) for k in stale],
                )
            self._con.commit()
        except sqlite3.Error:
            pass
        finally:
            try:
                self._con.close()
            except sqlite3.Error:
                pass


def _file_mentions_project(path: Path, markers: Tuple[bytes, ...], chunk_size: int = 8 << 20) -> bool:
    if not markers:
        return True
    overlap = max(len(m) for m in markers) - 1
    tail = b""
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                window = tail + chunk
                if any(marker in window for marker in markers):
                    return True
                tail = window[-overlap:] if overlap > 0 else b""
    except OSError:
        return False
    return False


def _parse_jsonl(path: Path) -> List[Mapping[str, object]]:
    rows: List[Mapping[str, object]] = []
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
    except OSError:
        return []
    return rows


@dataclass
class _ClaudeRowsResult:
    project_match: bool
    session_id: str
    timestamps: List[str]
    title: str
    preview: str
    tools: List[str]
    commands: List[str]
    files: List[str]
    message_count: int
    branch: str
    model: str


def _parse_claude_rows(rows: Sequence[Mapping[str, object]], project: Path) -> _ClaudeRowsResult:
    """Single pass over a Claude JSONL transcript accumulating all session fields."""
    project = project.resolve()
    project_match = False
    session_id = ""
    timestamps: List[str] = []
    message_texts: List[str] = []
    tools: List[str] = []
    commands: List[str] = []
    files: List[str] = []
    message_count = 0
    branch = ""
    model = ""

    for row in rows:
        if not project_match:
            if _path_value_matches_project(row.get("cwd"), project):
                project_match = True
            else:
                payload_v = row.get("payload")
                if isinstance(payload_v, dict):
                    if _jsonish_contains_project_context(payload_v, project):
                        project_match = True
                    elif payload_v.get("type") == "function_call" and _jsonish_contains_project_context(payload_v.get("arguments"), project):
                        project_match = True
                if not project_match:
                    att_v = row.get("attachment")
                    if isinstance(att_v, dict) and _jsonish_contains_project_context(att_v, project):
                        project_match = True

        if not session_id:
            v = row.get("sessionId")
            if isinstance(v, str) and v:
                session_id = v

        ts = row.get("timestamp")
        if isinstance(ts, str):
            timestamps.append(ts)

        if not branch:
            v = row.get("gitBranch")
            if isinstance(v, str) and v:
                branch = v

        row_type = row.get("type")
        if row_type in {"user", "assistant"}:
            message_count += 1

        msg = row.get("message")
        if isinstance(msg, dict):
            if not model:
                v = msg.get("model") or msg.get("model_slug")
                if isinstance(v, str):
                    model = v
            content = msg.get("content")
            if row_type in {"user", "assistant"}:
                text = _content_to_text(content)
                if text:
                    message_texts.append(text)
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        tools.append(str(item.get("name") or "tool"))
                        _collect_activity_from_value(item.get("input"), project, commands, files)

        attachment = row.get("attachment")
        if isinstance(attachment, dict):
            command = attachment.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command.strip())
            atype = attachment.get("type")
            if isinstance(atype, str) and atype and atype not in {"hook_success", "hook_additional_context"}:
                tools.append(atype)
            _collect_activity_from_value(attachment, project, commands, files)
        _collect_activity_from_value(row, project, commands, files)

    title, preview = _title_and_preview(message_texts)
    return _ClaudeRowsResult(
        project_match=project_match,
        session_id=session_id,
        timestamps=timestamps,
        title=title,
        preview=preview,
        tools=tools,
        commands=commands,
        files=files,
        message_count=message_count,
        branch=branch,
        model=model,
    )


def _parse_claude_session(
    project: Path,
    root: Path,
    path: Path,
    dropped: Optional[Dict[str, int]] = None,
) -> Optional[HarnessSession]:
    rows = _parse_jsonl(path)
    if not rows:
        return None
    parsed = _parse_claude_rows(rows, project)
    if not parsed.project_match:
        return None
    # Tallied here, after the project gate and once per transcript: only
    # sessions this project actually imports count toward its multimodal gap.
    # ``dropped`` is None when the caller is not measuring (engine/session_tail).
    _tally_dropped_blocks_in_rows(rows, dropped)
    session_id = parsed.session_id or path.stem
    timestamps = parsed.timestamps
    started_at = min(timestamps) if timestamps else ""
    ended_at = max(timestamps) if timestamps else ""
    title, preview = parsed.title, parsed.preview
    tools, commands, files = parsed.tools, parsed.commands, parsed.files
    message_count = parsed.message_count
    branch = parsed.branch
    model = parsed.model
    slug = safe_slug(title or session_id)
    subagents = _claude_subagent_summaries(
        project, root, path, session_id, _claude_subagent_types(rows), dropped=dropped
    )
    claude_turns = _claude_turns(rows)
    metadata: Dict[str, object] = {"config_root": str(root), "transcript": str(path), "turns": claude_turns}
    if subagents:
        metadata["subagents"] = subagents
    return HarnessSession(
        errors=_errors_from_turns(claude_turns),
        id=f"claude-code:{session_id}:{path.stem}",
        slug=slug,
        harness="claude-code",
        agent_label="Claude Code",
        project_name=project.name,
        project_root=str(project),
        started_at=started_at,
        ended_at=ended_at,
        branch=branch,
        model=model,
        title=title or f"Claude Code session {path.stem}",
        summary=preview,
        message_count=message_count,
        tool_call_count=len(set(tools)) + len(_dedupe(commands)),
        tools_used=sorted(set(tools)),
        files_touched=sorted(set(files)),
        commands_run=_dedupe(commands)[:50],
        raw_transcript_path=str(path),
        redacted_preview=preview,
        metadata=metadata,
    )


# Links a parent-session tool_result back to the subagent transcript it
# spawned: the result text carries "agentId: <id>" and the child transcript is
# stored as ``subagents/agent-<id>.jsonl``.
_SUBAGENT_AGENT_ID_RE = re.compile(r"agentId:\s*([0-9A-Za-z_-]+)")


def _claude_subagent_types(rows: Sequence[Mapping[str, object]]) -> Dict[str, str]:
    """Map subagent agentId → declared ``subagent_type`` from the parent rows.

    Subagent transcripts carry no role of their own — the role lives in the
    parent's Task-style ``tool_use`` (``input['subagent_type']``, e.g.
    "reviewer" or "general-purpose"), paired with the child transcript via the
    matching ``tool_result`` text. One pass over the parent rows recovers the
    pairing so subagent summaries can carry a role-grade ``type`` field
    (consumed by ``tesserae.agent_identity.resolve_agent_key`` tier 1).
    """
    types_by_tool_use: Dict[str, str] = {}
    types_by_agent_id: Dict[str, str] = {}
    for row in rows:
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use":
                tool_input = item.get("input")
                tool_use_id = item.get("id")
                if isinstance(tool_input, dict) and isinstance(tool_use_id, str):
                    subagent_type = tool_input.get("subagent_type")
                    if isinstance(subagent_type, str) and subagent_type.strip():
                        types_by_tool_use[tool_use_id] = subagent_type.strip()
            elif item.get("type") == "tool_result":
                tool_use_id = item.get("tool_use_id")
                if not isinstance(tool_use_id, str):
                    continue
                subagent_type = types_by_tool_use.get(tool_use_id)
                if not subagent_type:
                    continue
                match = _SUBAGENT_AGENT_ID_RE.search(_content_to_text(item.get("content")))
                if match:
                    types_by_agent_id.setdefault(match.group(1), subagent_type)
    return types_by_agent_id


def _claude_subagent_summaries(
    project: Path,
    root: Path,
    parent_path: Path,
    parent_session_id: str,
    subagent_types: Optional[Mapping[str, str]] = None,
    dropped: Optional[Dict[str, int]] = None,
) -> List[Dict[str, object]]:
    subagents_dir = parent_path.with_suffix("") / "subagents"
    if not subagents_dir.exists():
        return []
    summaries: List[Dict[str, object]] = []
    for path in sorted(subagents_dir.glob("*.jsonl")):
        rows = _parse_jsonl(path)
        if not rows or not _rows_match_project(rows, project):
            continue
        _tally_dropped_blocks_in_rows(rows, dropped)
        timestamps = [v for row in rows if isinstance((v := row.get("timestamp")), str)]
        title, preview = _title_and_preview_from_claude(rows)
        tools, commands, files = _claude_activity(rows, project)
        message_count = sum(1 for row in rows if row.get("type") in {"user", "assistant"})
        summary: Dict[str, object] = {
            "id": f"claude-code:{parent_session_id}:{path.stem}",
            "title": title or f"Claude Code subagent {path.stem}",
            "started_at": min(timestamps) if timestamps else "",
            "ended_at": max(timestamps) if timestamps else "",
            "summary": preview,
            "message_count": message_count,
            "tool_call_count": len(set(tools)) + len(_dedupe(commands)),
            "tools_used": sorted(set(tools)),
            "files_touched": sorted(set(files)),
            "commands_run": _dedupe(commands)[:50],
            "raw_transcript_path": str(path),
        }
        # Role-grade type recovered from the parent transcript (file stem is
        # ``agent-<id>``). Omitted, not empty, when unmatched — consumers
        # degrade to registry match rules / "default".
        agent_id = path.stem[len("agent-"):] if path.stem.startswith("agent-") else path.stem
        subagent_type = (subagent_types or {}).get(agent_id)
        if subagent_type:
            summary["type"] = subagent_type
        summaries.append(summary)
    return sorted(summaries, key=lambda item: str(item.get("started_at") or ""))


def _parse_codex_session(
    project: Path,
    root: Path,
    path: Path,
    dropped: Optional[Dict[str, int]] = None,
) -> Optional[HarnessSession]:
    rows = _parse_jsonl(path)
    if not rows or not _rows_match_project(rows, project):
        return None
    _tally_dropped_blocks_in_rows(rows, dropped)
    session_meta = next((r.get("payload") for r in rows if r.get("type") == "session_meta" and isinstance(r.get("payload"), dict)), {})
    session_id = str(session_meta.get("id") or path.stem) if isinstance(session_meta, dict) else path.stem
    timestamps = [v for row in rows if isinstance((v := row.get("timestamp")), str)]
    started_at = min(timestamps) if timestamps else (str(session_meta.get("timestamp", "")) if isinstance(session_meta, dict) else "")
    ended_at = max(timestamps) if timestamps else started_at
    title, preview = _title_and_preview_from_codex(rows)
    tools, commands, files = _codex_activity(rows, project)
    message_count = 0
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "message" and payload.get("role") in {"user", "assistant"}:
            message_count += 1
    slug = safe_slug(title or session_id)
    model = ""
    if isinstance(session_meta, dict):
        model = str(session_meta.get("model") or session_meta.get("model_slug") or session_meta.get("model_provider") or "")
    codex_turns = _codex_turns(rows)
    return HarnessSession(
        errors=_errors_from_turns(codex_turns),
        id=f"codex:{session_id}",
        slug=slug,
        harness="codex",
        agent_label="Codex",
        project_name=project.name,
        project_root=str(project),
        started_at=started_at,
        ended_at=ended_at,
        model=model,
        title=title or f"Codex session {path.stem}",
        summary=preview,
        message_count=message_count,
        tool_call_count=len(set(tools)),
        tools_used=sorted(set(tools)),
        files_touched=sorted(set(files)),
        commands_run=_dedupe(commands)[:50],
        raw_transcript_path=str(path),
        redacted_preview=preview,
        metadata={"config_root": str(root), "transcript": str(path), "turns": codex_turns},
    )


def _claude_project_dir(project: Path) -> str:
    return str(project).replace("/", "-")


def _claude_path_matches_project(path: Path, project: Path) -> bool:
    return _claude_project_dir(project) in path.parts


def _rows_match_project(rows: Sequence[Mapping[str, object]], project: Path) -> bool:
    project = project.resolve()
    for row in rows:
        if _path_value_matches_project(row.get("cwd"), project):
            return True
        payload = row.get("payload")
        if isinstance(payload, dict):
            if _jsonish_contains_project_context(payload, project):
                return True
            if payload.get("type") == "function_call":
                args = payload.get("arguments")
                if _jsonish_contains_project_context(args, project):
                    return True
        attachment = row.get("attachment")
        if isinstance(attachment, dict) and _jsonish_contains_project_context(attachment, project):
            return True
    return False


def _path_value_matches_project(value: object, project: Path) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return Path(value).expanduser().resolve() == project
    except OSError:
        return value == str(project)


def _jsonish_contains_project_context(value: object, project: Path) -> bool:
    """Return true only for explicit cwd/workdir-style project context.

    Plain transcript text or shell commands that merely mention the focused
    project path are not enough to import a session. A discovered transcript must
    declare that the harness was running in the plugged-in project root.
    """

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return False
        return _jsonish_contains_project_context(decoded, project)
    if isinstance(value, dict):
        for key in ("cwd", "workdir", "project_root", "root"):
            if _path_value_matches_project(value.get(key), project):
                return True
        return any(_jsonish_contains_project_context(v, project) for v in value.values())
    if isinstance(value, list):
        return any(_jsonish_contains_project_context(v, project) for v in value)
    return False


def _first_str(rows: Sequence[Mapping[str, object]], key: str) -> str:
    for row in rows:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _first_message_model(rows: Sequence[Mapping[str, object]]) -> str:
    for row in rows:
        msg = row.get("message")
        if isinstance(msg, dict):
            value = msg.get("model") or msg.get("model_slug")
            if isinstance(value, str):
                return value
    return ""


_REDACT_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"sk-[A-Za-z0-9]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
)


def _redact_text(text: str) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _turn_text(text: str, limit: int = 2400) -> str:
    """The stored form of one turn's text: redacted, then truncated.

    Home paths are redacted HERE — at the single gate every minted turn passes
    through — and not at any of the five places that later copy this text. The
    copies (the Session display name, the subagent run name, the structural
    decision, the LLM finding body, ``errors``) each redact too, but they are
    copies: ``metadata["turns"]`` is itself serialized by
    :meth:`HarnessSession.to_dict` into every record in the session store,
    rendered by the site's session page, and forwarded verbatim to the
    extracting model. Redacting only what is copied out leaves the operator's
    account name in the original, which is the larger surface and the one
    written by a plain ``tesserae sessions discover`` with no LLM involved.

    Order matters: redaction runs BEFORE the cap, so a home path that straddles
    the truncation point cannot survive as a fragment, and the cap counts the
    bytes actually stored. ``redact_home_paths`` is idempotent (``~`` contains
    no second root to match), so the later copies re-applying it are harmless.
    """
    clean = redact_home_paths(_redact_text(text.strip()))
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "…"


# Default turn limit is a runaway-file backstop, NOT a context-window guard.
# Full history must be captured here: the LLM never reads this list whole — the
# extractor walks it in max_turns_per_chunk windows (per-turn text already
# capped by _turn_text), so per-call prompt size is bounded regardless of
# session length. A low default silently truncates long sessions before
# chunking ever runs (users hit this at the old default of 300).
_TURN_LIMIT_BACKSTOP = 100_000


# A tool RESULT is minted as its own turn, with the same 1200-char cap the
# invocation gets. Both numbers are load-bearing.
#
# WHY a turn at all: ``tesserae.verify`` says of this codebase, in source, that
# "tool_result is parsed solely to map subagent ids and never becomes a turn,
# so no exit code survives ingest ... this tool can say a document says so,
# never this ran and passed". Everything downstream that wants to know whether
# a command PASSED — the Event outcome stamps, the ``failure`` finding kind —
# reads turns, so the fix has to be here.
#
# WHY the same cap: results are an order of magnitude larger and far more
# skewed than inputs. Measured over the 2,331 results on the ingest corpus:
# median 1,825 chars, p99 40,082, max 2,044,054 — 14.7 MB raw, 2.0 MB at this
# cap. ``_TURN_LIMIT_BACKSTOP`` counts TURNS and gives no protection against
# bytes, so this is the only thing bounding what a runaway result stores.
# Routing through ``_turn_text`` also applies ``_redact_text``, so a secret
# echoed back by a tool is redacted on the way in.
_TOOL_TURN_LIMIT = 1200

#: Codex reports the exit status of a shell tool as a header line inside a
#: plain ``output`` string — it is a display convention, not a schema, so it is
#: matched rather than looked up, and a miss must stay a miss (see
#: ``_codex_exit_code``). Claude has no equivalent anywhere.
_CODEX_EXIT_CODE_RE = re.compile(r"^Process exited with code (-?\d+)$")

#: The line that ends Codex's header and begins the tool's own bytes. It is the
#: anchor, not a heuristic: measured over the ingest corpus, ALL 1,232 outputs
#: carrying an exit line have this marker within the first 2 KB, and the exit
#: line always precedes it (line index 2, in 1,232 of 1,232).
_CODEX_OUTPUT_MARKER = "\nOutput:\n"

#: Backstop on the header scan. The header is four short lines; a 2 KB slice is
#: already far more than it can occupy, and it bounds the work on a 2 MB result.
_CODEX_EXIT_SCAN_CHARS = 2048


def _codex_exit_code(output: str) -> Optional[int]:
    """The exit code Codex reported in its HEADER, or None when it reported none.

    Only the header — the bytes before ``Output:`` — is read, and a result with
    no header has no exit code. The body cannot be scanned for this line: a tool
    result routinely contains one that is not its own. A ``cat`` of a transcript,
    a test that asserts on the string, a ``git show`` of this very module all
    put ``Process exited with code 0`` inside the body, and the 54 results that
    genuinely have no exit line (apply_patch, MCP tools — none of them a shell)
    are exactly the ones where such a line would be believed. Stamping one there
    invents a passing run on a tool that never ran a process, which is the
    single claim this whole path exists to make honest.

    None is a real answer, not a fallback. Returning 0 for a missing header
    would manufacture "this ran and passed", and would let the coverage rot
    silently the day Codex changes its output framing — a miss must stay a miss.
    """
    head = output[:_CODEX_EXIT_SCAN_CHARS]
    marker = head.find(_CODEX_OUTPUT_MARKER)
    if marker < 0:
        return None
    for line in head[:marker].split("\n"):
        match = _CODEX_EXIT_CODE_RE.match(line.strip())
        if match is not None:
            try:
                return int(match.group(1))
            except ValueError:  # pragma: no cover - the group is \d+ already
                return None
    return None


def _tool_result_turn(
    *,
    timestamp: str,
    name: str,
    text: str,
    is_error: object = None,
    exit_code: Optional[int] = None,
    call_id: object = None,
) -> Dict[str, object]:
    """Build a ``tool_result`` turn, omitting every signal that is absent.

    ``is_error`` and ``exit_code`` are TYPED FIELDS, not something to recover
    from the truncated text. Measured over the 2,330 results in the ingest
    corpus: Claude sets ``is_error`` on 431 of its 1,044 results (41.3%) and
    NEVER writes an exit code anywhere, while Codex writes an exit code in a
    header on 1,232 of its 1,286 (95.8%) and never sets ``is_error``. Neither
    signal survives a 1,200-char cap on the wrong side of it, and a Claude
    failure has no text to re-parse at all. An absent key means "not reported"
    — never "succeeded".

    ``is_error`` is stored only when it is a real ``bool`` and ``exit_code``
    only when it is not None, so a harness that one day sends the string
    ``"false"`` records no signal rather than a failure.

    ``call_id`` is the harness's OWN identifier for the call this result
    answers — Claude's ``tool_use_id``, Codex's ``call_id``. It costs one dict
    key and it is the only thing that says WHAT this result is the result of:
    the invocation's arguments live on a different turn, and without the id a
    consumer has to re-derive the linkage by position. Position does not work.
    Measured over the ingest corpus, 746 of Codex's 1,286 results (58%) were
    issued while another call was outstanding — Codex emits a whole batch of
    ``function_call``s and then the whole batch of outputs, up to 5 deep — so
    "the result after the invocation" is wrong for the majority of them. A FIFO
    queue keyed on the tool name reproduces the harness's linkage for 2,328 of
    2,330 results, which is to say it is silently wrong twice, forever. Storing
    the id makes the question exact instead of 99.91% right.
    """
    turn: Dict[str, object] = {
        "role": "tool_result",
        "timestamp": timestamp,
        "name": name,
        "text": _turn_text(text, limit=_TOOL_TURN_LIMIT),
    }
    if isinstance(is_error, bool):
        turn["is_error"] = is_error
    if exit_code is not None:
        turn["exit_code"] = exit_code
    if isinstance(call_id, str) and call_id:
        turn["call_id"] = call_id
    return turn


def _tool_call_turn(
    *,
    timestamp: str,
    name: str,
    text: str,
    call_id: object = None,
) -> Dict[str, object]:
    """Build a ``tool`` (invocation) turn, carrying the harness's call id.

    The invocation is where the OPERAND lives — the command, the file path,
    the arguments. ``_tool_result_turn`` records how it went; this records what
    was asked for; ``call_id`` is what joins them.
    """
    turn: Dict[str, object] = {
        "role": "tool",
        "timestamp": timestamp,
        "name": name,
        "text": text,
    }
    if isinstance(call_id, str) and call_id:
        turn["call_id"] = call_id
    return turn


def _claude_tool_names(rows: Sequence[Mapping[str, object]]) -> Dict[str, str]:
    """``tool_use_id -> tool name``.

    A ``tool_result`` block names only the id of the call it answers, so
    without this map an outcome is unattributable — you would know something
    failed but not what.
    """
    names: Dict[str, str] = {}
    for row in rows:
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            tool_use_id = item.get("id")
            if isinstance(tool_use_id, str):
                names[tool_use_id] = str(item.get("name") or "tool")
    return names


def _claude_turns(rows: Sequence[Mapping[str, object]], limit: int = _TURN_LIMIT_BACKSTOP) -> List[Dict[str, object]]:
    turns: List[Dict[str, object]] = []
    tool_names = _claude_tool_names(rows)
    for row in rows:
        role = row.get("type")
        if role not in {"user", "assistant"}:
            continue
        timestamp = row.get("timestamp") if isinstance(row.get("timestamp"), str) else ""
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        text = _content_to_text(content)
        if text and not text.startswith("<environment_context>"):
            turns.append({"role": str(role), "timestamp": timestamp, "text": _turn_text(text)})
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    name = str(item.get("name") or "tool")
                    tool_text = _turn_text(json.dumps(item.get("input", {}), ensure_ascii=False, sort_keys=True), limit=_TOOL_TURN_LIMIT)
                    turns.append(
                        _tool_call_turn(
                            timestamp=timestamp,
                            name=name,
                            text=tool_text,
                            call_id=item.get("id"),
                        )
                    )
                elif item.get("type") == "tool_result":
                    tool_use_id = item.get("tool_use_id")
                    # ``content`` here is a bare string 89.5% of the time and a
                    # block list otherwise (usually images, which flatten to "").
                    result = item.get("content")
                    result_text = (
                        result if isinstance(result, str) else _content_to_text(result)
                    )
                    turns.append(
                        _tool_result_turn(
                            timestamp=timestamp,
                            name=tool_names.get(tool_use_id, "tool")
                            if isinstance(tool_use_id, str)
                            else "tool",
                            text=result_text,
                            is_error=item.get("is_error"),
                            call_id=tool_use_id,
                        )
                    )
        if len(turns) >= limit:
            break
    return turns


def _codex_turns(rows: Sequence[Mapping[str, object]], limit: int = _TURN_LIMIT_BACKSTOP) -> List[Dict[str, object]]:
    turns: List[Dict[str, object]] = []
    call_names: Dict[str, str] = {}
    for row in rows:
        timestamp = row.get("timestamp") if isinstance(row.get("timestamp"), str) else ""
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "message" and payload.get("role") in {"user", "assistant"}:
            text = _content_to_text(payload.get("content"))
            if text and not text.startswith("<environment_context>") and not text.startswith("<permissions instructions>"):
                turns.append({"role": str(payload.get("role")), "timestamp": timestamp, "text": _turn_text(text)})
        elif payload.get("type") == "function_call":
            name = str(payload.get("name") or "function_call")
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                call_names[call_id] = name
            tool_text = _turn_text(str(payload.get("arguments") or ""), limit=_TOOL_TURN_LIMIT)
            if tool_text:
                turns.append(
                    _tool_call_turn(
                        timestamp=timestamp,
                        name=name,
                        text=tool_text,
                        call_id=call_id,
                    )
                )
        elif payload.get("type") == "function_call_output":
            output = payload.get("output")
            output_text = output if isinstance(output, str) else _content_to_text(output)
            call_id = payload.get("call_id")
            turns.append(
                _tool_result_turn(
                    timestamp=timestamp,
                    name=call_names.get(call_id, "function_call")
                    if isinstance(call_id, str)
                    else "function_call",
                    text=output_text,
                    exit_code=_codex_exit_code(output_text),
                    call_id=call_id,
                )
            )
        if len(turns) >= limit:
            break
    return turns


#: What a failing tool result contributes to ``HarnessSession.errors``: enough
#: to recognise the failure, never the whole result. Bounded on both axes
#: because ``errors`` is serialized into every stored record.
_ERROR_TEXT_LIMIT = 200
_MAX_SESSION_ERRORS = 50


def _errors_from_turns(turns: Sequence[Mapping[str, object]]) -> List[str]:
    """The failures a session actually recorded, newest last.

    ``HarnessSession.errors`` was declared and round-tripped from the day it
    was written and populated by nothing — 0 of the 211 live records carried
    one. This is its only writer. A turn counts as a failure on POSITIVE
    evidence only: ``is_error`` exactly ``True``, or an integer exit code that
    is not zero. Silence is not failure, and it is not success either — and a
    run that exited 0 is a SUCCESS, so it must never land here. Dropping the
    ``!= 0`` turns every recorded exit code into a reported failure, which is
    the same over-claim as reading silence as success with the sign flipped.

    The detail is home-path redacted like every other producer that copies
    transcript text. ``errors`` is serialized into every stored session record
    and read back by anything that loads the store, so an absolute
    ``/Users/<name>/`` path here publishes the operator's account name exactly
    as the node names did.
    """
    errors: List[str] = []
    for turn in turns:
        if str(turn.get("role") or "") != "tool_result":
            continue
        exit_code = turn.get("exit_code")
        failed_exit = (
            isinstance(exit_code, int)
            and not isinstance(exit_code, bool)
            and exit_code != 0
        )
        if not (turn.get("is_error") is True or failed_exit):
            continue
        name = str(turn.get("name") or "tool")
        detail = redact_home_paths(
            " ".join(str(turn.get("text") or "").split())
        )[:_ERROR_TEXT_LIMIT]
        prefix = f"{name} exited {exit_code}" if failed_exit else f"{name} failed"
        errors.append(f"{prefix}: {detail}" if detail else prefix)
        if len(errors) >= _MAX_SESSION_ERRORS:
            break
    return errors


def _title_and_preview_from_claude(rows: Sequence[Mapping[str, object]]) -> Tuple[str, str]:
    texts: List[str] = []
    for row in rows:
        if row.get("type") not in {"user", "assistant"}:
            continue
        msg = row.get("message")
        if isinstance(msg, dict):
            text = _content_to_text(msg.get("content"))
            if text:
                texts.append(text)
    return _title_and_preview(texts)


def _title_and_preview_from_codex(rows: Sequence[Mapping[str, object]]) -> Tuple[str, str]:
    texts: List[str] = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "message" and payload.get("role") in {"user", "assistant"}:
            text = _content_to_text(payload.get("content"))
            if text and not text.startswith("<environment_context>") and not text.startswith("<permissions instructions>"):
                texts.append(text)
    return _title_and_preview(texts)


# Harness-injected preamble that every session shares — must NOT become the
# title (e.g. Codex prepends "# AGENTS.md instructions for <path>", so all
# sessions collapsed to one title and keyword search returned indistinguishable
# boilerplate). Match the AGENTS/CLAUDE/GEMINI .md instruction dump and the
# common system-context wrappers; skip them when choosing a title/preview.
_BOILERPLATE_PREAMBLE_RE = re.compile(
    r"^\s*#?\s*(?:AGENTS|CLAUDE|GEMINI|GRD)\.md\b"
    r"|^\s*<(?:system-reminder|environment_context|permissions|command-message"
    r"|command-name|local-command|user-memory-input)\b",
    re.IGNORECASE,
)


def _is_boilerplate_preamble(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return True
    first_line = stripped.splitlines()[0]
    return bool(_BOILERPLATE_PREAMBLE_RE.match(first_line)) or (
        ".md instructions for " in first_line.lower()
    )


def _title_and_preview(texts: Sequence[str]) -> Tuple[str, str]:
    if not texts:
        return "", ""
    # Prefer the first message that is the user's actual instruction, not the
    # harness-injected preamble shared across every session. Fall back to the
    # raw first text only if everything looks like boilerplate.
    meaningful = [t for t in texts if not _is_boilerplate_preamble(t)]
    pool = meaningful or list(texts)
    first_raw = pool[0].strip()
    # Redacted HERE, at the mint, because this one pair of strings fans out to
    # four published surfaces: ``session.title`` becomes the Session node's
    # display name, the same helper mints the subagent descriptor title that
    # becomes a SessionTakeaway name (51 of the 57 measured leaks), and
    # ``preview`` is stored as the field literally called ``redacted_preview``,
    # which until now redacted secrets but not the operator's home directory.
    # Redacting at any one display site would leave the other three.
    title = redact_home_paths(_clean_text(first_raw.splitlines()[0]).strip("# ")[:96])
    preview = redact_home_paths(_clean_text("\n\n".join(pool[:4]))[:1200])
    return title, preview


def _content_to_text(content: object) -> str:
    """Flatten a harness content payload to text.

    PURE, and deliberately so. What it cannot represent — images, documents,
    tool results — is counted by :func:`_tally_dropped_blocks_in_rows`, once
    per transcript, because this helper runs over the same rows several times
    per session and is therefore the wrong place to measure anything.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("input_text") or item.get("output_text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _claude_activity(rows: Sequence[Mapping[str, object]], project: Path) -> Tuple[List[str], List[str], List[str]]:
    tools: List[str] = []
    commands: List[str] = []
    files: List[str] = []
    for row in rows:
        msg = row.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        name = str(item.get("name") or "tool")
                        tools.append(name)
                        _collect_activity_from_value(item.get("input"), project, commands, files)
        attachment = row.get("attachment")
        if isinstance(attachment, dict):
            command = attachment.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command.strip())
            atype = attachment.get("type")
            if isinstance(atype, str) and atype and atype not in {"hook_success", "hook_additional_context"}:
                tools.append(atype)
            _collect_activity_from_value(attachment, project, commands, files)
        _collect_activity_from_value(row, project, commands, files)
    return tools, commands, files


def _codex_activity(rows: Sequence[Mapping[str, object]], project: Path) -> Tuple[List[str], List[str], List[str]]:
    tools: List[str] = []
    commands: List[str] = []
    files: List[str] = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, dict):
            if payload.get("type") == "function_call":
                name = str(payload.get("name") or "function_call")
                tools.append(name)
                _collect_activity_from_value(payload.get("arguments"), project, commands, files)
            elif payload.get("type") == "message":
                _collect_activity_from_value(payload.get("content"), project, commands, files)
            else:
                _collect_activity_from_value(payload, project, commands, files)
    return tools, commands, files


def _collect_activity_from_value(value: object, project: Path, commands: List[str], files: List[str]) -> None:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if decoded is not None:
            _collect_activity_from_value(decoded, project, commands, files)
        text = value
        for key in ("cmd", "command"):
            # handled below for dicts; regex catches serialized snippets.
            pass
        files.extend(_extract_project_files(text, project))
        return
    if isinstance(value, dict):
        for key in ("cmd", "command"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                commands.append(item.strip())
        for key in ("file_path", "path"):
            item = value.get(key)
            if isinstance(item, str):
                files.extend(_extract_project_files(item, project))
        for item in value.values():
            _collect_activity_from_value(item, project, commands, files)
    elif isinstance(value, list):
        for item in value:
            _collect_activity_from_value(item, project, commands, files)


def _extract_project_files(text: str, project: Path) -> List[str]:
    out: List[str] = []
    if not text:
        return out
    project_str = re.escape(str(project))
    for match in re.finditer(project_str + r"/([^\s\"'`<>),]+)", text):
        rel = match.group(1).strip()
        if rel and not rel.startswith(".tesserae/"):
            out.append(rel)
    for match in re.finditer(r"\b(?:tesserae|tests|docs|data)/[\w./-]+", text):
        out.append(match.group(0).rstrip(".,);:"))
    return _dedupe(out)[:100]


def _dedupe(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]{1,80}>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _manifest_entry(session: HarnessSession) -> Dict[str, object]:
    return {
        "id": session.id,
        "title": session.title or session.slug,
        "harness": session.harness,
        "agent_label": session.agent_label,
        "project_name": session.project_name,
        "date": session.date,
        "model": session.model,
        "message_count": session.message_count,
        "tool_call_count": session.tool_call_count,
        "token_total": session.token_total,
        "href": session.href,
    }


def render_session_markdown(session: HarnessSession) -> str:
    lines = [
        f"# {session.title or session.slug}",
        "",
        f"- Harness: {session.harness}",
        f"- Agent: {session.agent_label}",
        f"- Project: {session.project_name}",
        f"- Date: {session.started_at}",
        f"- Model: {session.model or 'unknown'}",
        f"- Messages: {session.message_count}",
        f"- Tool calls: {session.tool_call_count}",
        "",
        "## Summary",
        "",
        session.summary or session.redacted_preview or "No summary yet.",
        "",
    ]
    if session.decisions:
        lines.extend(["## Decisions", ""])
        lines.extend(f"- {item}" for item in session.decisions)
        lines.append("")
    if session.files_touched:
        lines.extend(["## Files touched", ""])
        lines.extend(f"- `{item}`" for item in session.files_touched)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
