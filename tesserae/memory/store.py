"""Store-agnostic accessor layer over the ``node_memory`` SQLite sidecar.

Phase-5 KB-01 foundation. This is the SINGLE place project.py and
mcp_server.py read/write mutable memory state — decay_score, access_count,
last_accessed_at, confidence, superseded — so no call site embeds raw SQL.
All state lives in the ``node_memory`` table inside ``.tesserae/sqlite.db``
(created on demand by :class:`SqliteGraphStore`); NOTHING mutable is written
to ``graph.json``, which must stay byte-identical across compiles.

Kept deliberately import-light (only :class:`SqliteGraphStore`, no heavy
project/orchestration imports) so ``mcp_server`` can call :func:`bump_access`
cheaply on every read.

Also home to the opt-in READ AUDIT (``TESSERAE_READ_AUDIT``), which names the
actor behind a read that ``bump_access`` can only count. Same sidecar, same
never-in-graph.json rule; off by default because an always-on audit turns every
read into a write.

Freshness note: a node's canonical first-seen timestamp lives in
``node_provenance.first_seen_at`` (Phase-4 sidecar) — it is NOT duplicated
here. ``node_memory`` owns only the columns that mutate after mint.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union

from ..graph_stores.sqlite import SqliteGraphStore

PathLike = Union[str, Path]


@dataclass
class NodeMemoryRow:
    """One row of mutable memory state, keyed on ``node_id``.

    Mirrors the ``node_memory`` columns the compile and MCP surfaces share.
    ``updated_at`` is the compile-supplied deterministic reference timestamp
    (never ``datetime.now()`` — see :mod:`tesserae.memory.decay`).
    """

    node_id: str
    decay_score: float = 1.0
    access_count: int = 0
    last_accessed_at: Optional[str] = None
    confidence: Optional[str] = None
    superseded: bool = False
    updated_at: Optional[str] = None


def _store(db_path: PathLike) -> SqliteGraphStore:
    """Open (creating the table if missing) the sidecar at ``db_path``."""
    return SqliteGraphStore(db_path)


def read_memory(db_path: PathLike) -> Dict[str, NodeMemoryRow]:
    """Return ``{node_id: NodeMemoryRow}`` for every persisted memory row.

    Opens a :class:`SqliteGraphStore` (which CREATEs ``node_memory`` if it
    does not yet exist) and inflates each raw row into a :class:`NodeMemoryRow`.
    """
    raw = _store(db_path).read_node_memory()
    return {
        node_id: NodeMemoryRow(
            node_id=node_id,
            decay_score=data["decay_score"],
            access_count=data["access_count"],
            last_accessed_at=data["last_accessed_at"],
            confidence=data["confidence"],
            superseded=bool(data["superseded"]),
        )
        for node_id, data in raw.items()
    }


def write_memory(db_path: PathLike, rows: Iterable[NodeMemoryRow]) -> None:
    """Persist compile-owned columns for each :class:`NodeMemoryRow`.

    Delegates to :meth:`SqliteGraphStore.write_node_memory_many`, which
    overwrites decay_score/confidence/superseded/updated_at on conflict while
    PRESERVING any MCP-accumulated access_count/last_accessed_at.
    """
    payload: List[Tuple[str, float, int, Optional[str], Optional[str], int, str]] = [
        (
            row.node_id,
            float(row.decay_score),
            int(row.access_count),
            row.last_accessed_at,
            row.confidence,
            1 if row.superseded else 0,
            row.updated_at or "",
        )
        for row in rows
    ]
    if not payload:
        return
    _store(db_path).write_node_memory_many(payload)


def bump_access(db_path: PathLike, node_id: str, accessed_at: str) -> None:
    """Atomically record one read of ``node_id`` (the MCP-read write path).

    ``accessed_at`` is a caller-supplied ISO-8601 string. Delegates to the
    store's atomic ``access_count = access_count + 1`` upsert — never
    read-modify-write (05-RESEARCH Pitfall 3).

    Says nothing about WHO read the node; :func:`record_read` is the opt-in
    layer that does.
    """
    _store(db_path).bump_access(node_id, accessed_at)


# --------------------------------------------------------------------------- #
# Read audit — opt-in attribution for the reads that already count             #
# --------------------------------------------------------------------------- #
#
# ``bump_access`` above counts a read and stamps when it happened; nothing
# records who caused it. That matters because ``agent_distill``'s
# forgetting-by-disuse consumes exactly that count, so one chatty agent polling
# a node and a human reading it once are indistinguishable inputs to what gets
# absorbed or demoted.
#
# DEFAULT OFF, and that is not decoration: an always-on ledger across ~32 MCP
# tools turns every read into a write. With the flag unset the cost here is one
# environment lookup and no connection is opened at all — a read stays a read.

#: Env flag that turns the audit on. Same truthy vocabulary as
#: ``tesserae.project._env_truthy`` (1/true/yes/on), which is the convention
#: this repo already uses for opt-in passes like ``TESSERAE_SCHEMA_DRIFT_APPLY``.
#: Spelled out here rather than imported because this module is deliberately
#: import-light — importing ``project`` would drag the whole compile pipeline
#: onto the MCP read path. ``tests/test_read_audit.py`` pins the two vocabularies
#: against each other so they cannot drift apart silently.
READ_AUDIT_ENV = "TESSERAE_READ_AUDIT"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Row shape, bumped whenever a column's meaning changes. Stamped on every row
#: beside the release version so a reader can decide whether it understands a
#: row without first mapping releases to shapes.
READ_AUDIT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReadAuditRow:
    """One recorded read: which tool, which actor, which nodes, when."""

    at: str
    tool: str
    actor: str
    node_ids: Tuple[str, ...]
    tesserae_version: str
    schema_version: int


def read_audit_enabled() -> bool:
    """True when ``TESSERAE_READ_AUDIT`` is set to a documented truthy value.

    Read from the environment on every call rather than cached at import: a
    long-lived MCP server must be able to have the audit turned on without a
    restart, and a cached flag is a value that silently outlives its config.
    """
    return os.environ.get(READ_AUDIT_ENV, "").strip().lower() in _TRUTHY


def _reader_version() -> str:
    """Release string stamped on every audit row.

    Lazily imported: ``importlib.metadata`` is not free, and a disabled audit
    must not pay for it.
    """
    from ..cli_tree import package_version

    return package_version()


# The tool and actor are known at MCP dispatch, several frames above the
# ``bump_access`` call that is being attributed. A ContextVar carries them
# down instead of threading two more arguments through every read surface —
# the same pattern (and the same reason) as ``merge_ledger._COLLECTOR``, and a
# ContextVar rather than a module global so two concurrent tool calls cannot
# read each other's actor.
_READER: ContextVar[Tuple[str, str]] = ContextVar("tesserae_read_reader", default=("", ""))


@contextmanager
def reading_as(tool: str, actor: str) -> Iterator[None]:
    """Attribute every read inside the block to ``(tool, actor)``.

    Restores the previous pair on exit, so a nested call (a tool that calls
    another tool's helper) cannot leave its own attribution behind.
    """
    token = _READER.set((str(tool or ""), str(actor or "")))
    try:
        yield
    finally:
        _READER.reset(token)


def current_reader() -> Tuple[str, str]:
    """The ``(tool, actor)`` in scope, or ``("", "")`` outside any block."""
    return _READER.get()


def record_read(
    db_path: PathLike,
    node_ids: Iterable[str],
    at: str,
    *,
    tool: str = "",
    actor: str = "",
) -> bool:
    """Record ONE read event, returning whether a row was written.

    The gate lives here, ahead of everything else, so no call site can make a
    read write by forgetting to check it — including the connection itself,
    which would CREATE the sidecar tables just by being opened.

    ``at`` is caller-supplied (the same instant stamped on the accompanying
    :func:`bump_access`) so a row and the access count it explains can never
    disagree about when the read happened. ``tool`` / ``actor`` default to the
    :func:`reading_as` scope. Ids are deduplicated with order preserved: one
    tool call that surfaced a node twice is one read of it, and the order is
    the order the caller ranked them in.
    """
    if not read_audit_enabled():
        return False
    seen: set = set()
    ids: List[str] = []
    for raw in node_ids:
        nid = str(raw) if raw else ""
        if not nid or nid in seen:
            continue
        seen.add(nid)
        ids.append(nid)
    if not ids:
        # An audit row naming no node explains no access count. Silence is the
        # honest record of a read that touched nothing.
        return False
    ctx_tool, ctx_actor = current_reader()
    _store(db_path).append_read_audit(
        at,
        tool or ctx_tool,
        actor or ctx_actor,
        ids,
        _reader_version(),
        READ_AUDIT_SCHEMA_VERSION,
    )
    return True


def read_audit_rows(
    db_path: PathLike,
    *,
    limit: int = 100,
    actor: str = "",
    tool: str = "",
    node_id: str = "",
) -> List[ReadAuditRow]:
    """Most-recent-first audit rows, optionally narrowed to one actor/tool/node.

    Readable whether or not the audit is currently enabled: turning the flag
    off must not hide what was already recorded. ``node_id`` is confirmed
    against the parsed id list here — the store's ``like`` only narrows the
    scan, so a node id that happens to be a substring of another cannot leak
    into the answer.
    """
    raw = _store(db_path).read_audit_rows(limit=limit, actor=actor, tool=tool, node_id=node_id)
    rows: List[ReadAuditRow] = []
    for _id, at, row_tool, row_actor, node_ids_json, _count, version, schema in raw:
        try:
            ids = json.loads(node_ids_json or "[]")
        except ValueError:
            ids = []
        ids = tuple(str(n) for n in ids if isinstance(n, (str, int)))
        if node_id and node_id not in ids:
            continue
        rows.append(
            ReadAuditRow(
                at=str(at or ""),
                tool=str(row_tool or ""),
                actor=str(row_actor or ""),
                node_ids=ids,
                tesserae_version=str(version or ""),
                schema_version=int(schema or 0),
            )
        )
    return rows


def read_audit_actors(rows: Iterable[ReadAuditRow]) -> List[Dict[str, object]]:
    """Per-actor tally over ``rows``: reads, nodes touched, tools used.

    This is the question the audit exists to answer — whose demand is driving
    the access counts forgetting reads — so it is computed here rather than by
    each caller. Sorted by reads descending then actor, so the answer is stable
    for equal counts.
    """
    reads: Dict[str, int] = {}
    nodes: Dict[str, set] = {}
    tools: Dict[str, set] = {}
    for row in rows:
        reads[row.actor] = reads.get(row.actor, 0) + 1
        nodes.setdefault(row.actor, set()).update(row.node_ids)
        if row.tool:
            tools.setdefault(row.actor, set()).add(row.tool)
    out: List[Dict[str, object]] = [
        {
            "actor": actor,
            "reads": count,
            "nodes": len(nodes.get(actor, ())),
            "tools": sorted(tools.get(actor, ())),
        }
        for actor, count in reads.items()
    ]
    out.sort(key=lambda entry: (-int(entry["reads"]), str(entry["actor"])))  # type: ignore[arg-type]
    return out
