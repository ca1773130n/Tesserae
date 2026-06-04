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

Freshness note: a node's canonical first-seen timestamp lives in
``node_provenance.first_seen_at`` (Phase-4 sidecar) — it is NOT duplicated
here. ``node_memory`` owns only the columns that mutate after mint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

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
    """
    _store(db_path).bump_access(node_id, accessed_at)
