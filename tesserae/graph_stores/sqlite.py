"""SQLite :class:`GraphStore` adapter.

Wraps the same on-disk schema used by :class:`SQLiteResearchGraphStore`
(``tesserae.persistence``) behind the ``GraphStore`` protocol shape used
by the hexagonal pipeline. Both classes can read and write the same
``.sqlite`` file — :class:`SQLiteResearchGraphStore` exposes a
graph-at-a-time write API for batch projection, while
:class:`SqliteGraphStore` exposes a row-at-a-time upsert API for the
streaming extractor and the MCP query surface.

Schema discrepancy note
-----------------------
The ``GraphStore`` design comment in the integration spec mentions
``ON CONFLICT(type, name)`` upserts. The existing standalone schema
keys nodes on ``id`` (a stable canonical identifier produced by the
extractor / canonicalizer), not on ``(type, name)``. This adapter
preserves the existing primary-key-on-id schema so both classes stay
binary-compatible on the same database file. Migrating to a
``(type, name)`` key would require renumbering existing local stores
and is deferred to the Phase 1b Postgres adapter, which uses a fresh
schema and a different uniqueness story (per ``owner_user_id``).

``owner_user_id`` is silently ignored throughout — standalone SQLite
mode has no notion of users, and the spec explicitly notes that all
SQLite nodes are global.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Set, Tuple, Union
from uuid import UUID

from ..research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


class SqliteGraphStore:
    """Local SQLite-backed :class:`GraphStore` adapter.

    Opens (creating if missing) the SQLite database at ``path`` and
    ensures the shared node/edge schema exists. Operations are
    short-lived connections — every call opens, executes, commits,
    and closes — to mirror :class:`SQLiteResearchGraphStore` and stay
    safe under multi-process access.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            self._ensure_schema(con)
            con.commit()

    # ------------------------------------------------------------------ #
    # GraphStore protocol surface                                         #
    # ------------------------------------------------------------------ #

    def upsert_node(self, node: ResearchNode) -> str:
        """Insert or replace a node, keyed on its canonical ``id``."""
        self.upsert_many_nodes([node])
        return node.id

    def upsert_many_nodes(self, nodes: List[ResearchNode]) -> None:
        """Insert or replace a batch of nodes in a single connection."""
        rows = [
            (
                node.id,
                node.name,
                node.type.value,
                json.dumps(node.aliases, ensure_ascii=False),
                node.description,
                node.source_path,
                json.dumps(node.metadata, ensure_ascii=False, sort_keys=True),
            )
            for node in nodes
        ]
        with self._connect() as con:
            con.executemany(
                """
                insert or replace into nodes
                (id, name, type, aliases_json, description, source_path, metadata_json)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            con.commit()

    def upsert_edge(self, edge: ResearchEdge) -> None:
        """Insert or replace an edge, idempotent on ``(source, target, type)``."""
        self.upsert_many_edges([edge])

    def upsert_many_edges(self, edges: List[ResearchEdge]) -> None:
        """Insert or replace a batch of edges in a single connection."""
        rows = [
            (
                f"{edge.source}|{edge.type}|{edge.target}",
                edge.source,
                edge.target,
                edge.type,
                edge.evidence,
                json.dumps(edge.metadata, ensure_ascii=False, sort_keys=True),
            )
            for edge in edges
        ]
        with self._connect() as con:
            con.executemany(
                """
                insert or replace into edges
                (id, source, target, type, evidence, metadata_json)
                values (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            con.commit()

    def get_node(self, node_id: str) -> Optional[ResearchNode]:
        """Fetch a single node by id, or ``None`` if absent."""
        with self._connect() as con:
            row = con.execute(
                "select id, name, type, aliases_json, description, source_path, metadata_json"
                " from nodes where id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_node(row)

    def iterate_nodes(
        self,
        node_type: Optional[str] = None,
        owner_user_id: Optional[Union[str, UUID]] = None,
    ) -> Iterator[ResearchNode]:
        """Iterate nodes, optionally filtered by ``node_type``.

        ``owner_user_id`` is accepted for protocol parity with the
        Postgres adapter but ignored here (all SQLite nodes are global).
        """
        del owner_user_id  # unused for SQLite
        with self._connect() as con:
            if node_type is None:
                cursor = con.execute(
                    "select id, name, type, aliases_json, description, source_path, metadata_json"
                    " from nodes order by rowid"
                )
            else:
                cursor = con.execute(
                    "select id, name, type, aliases_json, description, source_path, metadata_json"
                    " from nodes where type = ? order by rowid",
                    (node_type,),
                )
            rows = cursor.fetchall()
        for row in rows:
            yield _row_to_node(row)

    def query_subgraph(self, seeds: List[str], depth: int = 1) -> ResearchGraph:
        """Return the subgraph reachable from ``seeds`` within ``depth`` hops.

        BFS is performed in Python: at each step, all edges incident to
        the current frontier (in either direction) are fetched, the new
        endpoints become the next frontier, and edges are accumulated.
        Edges are deduplicated on ``(source, target, type)``.
        """
        if depth < 0:
            raise ValueError("depth must be >= 0")

        visited: Set[str] = set(seeds)
        frontier: Set[str] = set(seeds)
        edge_keys: Set[tuple] = set()
        edges: List[ResearchEdge] = []

        with self._connect() as con:
            # BFS up to ``depth`` hops, expanding from the current frontier
            # each round. Set-based so duplicate seeds don't double-fetch.
            for _ in range(depth):
                if not frontier:
                    break
                placeholders = ",".join("?" for _ in frontier)
                rows = con.execute(
                    f"select source, target, type, evidence, metadata_json"
                    f" from edges where source in ({placeholders}) or target in ({placeholders})",
                    list(frontier) + list(frontier),
                ).fetchall()
                next_frontier: Set[str] = set()
                for source, target, edge_type, evidence, metadata_json in rows:
                    key = (source, target, edge_type)
                    if key in edge_keys:
                        continue
                    edge_keys.add(key)
                    edges.append(
                        ResearchEdge(
                            source=source,
                            target=target,
                            type=edge_type,
                            evidence=evidence,
                            metadata=json.loads(metadata_json or "{}"),
                        )
                    )
                    for endpoint in (source, target):
                        if endpoint not in visited:
                            visited.add(endpoint)
                            next_frontier.add(endpoint)
                frontier = next_frontier

            # Fetch every visited node in one shot.
            if not visited:
                return ResearchGraph(nodes=[], edges=[])
            placeholders = ",".join("?" for _ in visited)
            node_rows = con.execute(
                f"select id, name, type, aliases_json, description, source_path, metadata_json"
                f" from nodes where id in ({placeholders})",
                list(visited),
            ).fetchall()

        nodes = [_row_to_node(row) for row in node_rows]
        return ResearchGraph(nodes=nodes, edges=edges)

    def find_canonical(self, name: str, node_type: str) -> Optional[ResearchNode]:
        """Look up a canonical node by display name and type, case-insensitive."""
        with self._connect() as con:
            self._ensure_schema(con)
            row = con.execute(
                "select id, name, type, aliases_json, description, source_path, metadata_json"
                " from nodes where lower(name) = lower(?) and type = ? order by rowid limit 1",
                (name, node_type),
            ).fetchone()
        if row is None:
            return None
        return _row_to_node(row)

    # ------------------------------------------------------------------ #
    # Provenance sidecar + delete surface (CMP-02)                        #
    # ------------------------------------------------------------------ #

    def record_provenance(self, node_id: str, source_path: str, *, timestamp: str) -> None:
        """Upsert one provenance row, preserving ``first_seen_at``.

        ``timestamp`` is a REQUIRED caller-supplied ISO-8601 string — this
        method NEVER calls ``datetime.now()``. The caller derives it from
        content/source-date so two compiles produce identical provenance
        (04-RESEARCH.md Pitfall 1: timestamps must stay deterministic and
        out of graph.json). On conflict the existing ``first_seen_at`` is
        kept and only ``last_updated_at`` advances.
        """
        with self._connect() as con:
            con.execute(
                """
                insert into node_provenance
                    (node_id, source_path, first_seen_at, last_updated_at)
                values (?, ?, ?, ?)
                on conflict(node_id, source_path) do update set
                    last_updated_at = excluded.last_updated_at
                """,
                (node_id, source_path, timestamp, timestamp),
            )
            con.commit()

    def record_provenance_many(self, rows: Iterable[Tuple[str, str, str]]) -> None:
        """Bulk upsert provenance rows ``(node_id, source_path, timestamp)``.

        Throughput path for full compiles. Same deterministic-timestamp and
        first-seen-preservation semantics as :meth:`record_provenance`.
        """
        params = [
            (node_id, source_path, timestamp, timestamp)
            for node_id, source_path, timestamp in rows
        ]
        if not params:
            return
        with self._connect() as con:
            con.executemany(
                """
                insert into node_provenance
                    (node_id, source_path, first_seen_at, last_updated_at)
                values (?, ?, ?, ?)
                on conflict(node_id, source_path) do update set
                    last_updated_at = excluded.last_updated_at
                """,
                params,
            )
            con.commit()

    def delete_node(self, node_id: str) -> bool:
        """Delete a single node and its provenance rows. Returns True if a node row was removed."""
        with self._connect() as con:
            cursor = con.execute("delete from nodes where id = ?", (node_id,))
            con.execute("delete from node_provenance where node_id = ?", (node_id,))
            con.commit()
            return cursor.rowcount > 0

    def delete_nodes_by_source(self, source_paths: Set[str]) -> Set[str]:
        """Tombstone nodes whose provenance set becomes EMPTY after removing ``source_paths``.

        Candidates are nodes touched by any changed source_path. A candidate
        is KEPT (survives) if it still has a provenance row under some
        unchanged source_path — the 2400->1700 anti-collapse guarantee for
        cross-file concept nodes. The returned set is exactly the node ids
        removed from both ``nodes`` and ``node_provenance`` (NOT a count);
        the caller drops precisely these from the in-memory graph.

        Empty ``source_paths`` is a no-op (returns ``set()``) — empty SQL
        ``IN`` clauses are invalid.
        """
        if not source_paths:
            return set()

        changed = list(source_paths)
        changed_ph = ",".join("?" for _ in changed)
        with self._connect() as con:
            candidates = {
                row[0]
                for row in con.execute(
                    f"select distinct node_id from node_provenance"
                    f" where source_path in ({changed_ph})",
                    changed,
                ).fetchall()
            }
            if not candidates:
                # Still purge provenance rows for the changed sources.
                con.execute(
                    f"delete from node_provenance where source_path in ({changed_ph})",
                    changed,
                )
                con.commit()
                return set()

            keepers = {
                row[0]
                for row in con.execute(
                    f"select distinct node_id from node_provenance"
                    f" where source_path not in ({changed_ph})",
                    changed,
                ).fetchall()
            }
            to_delete = candidates - keepers

            if to_delete:
                del_ph = ",".join("?" for _ in to_delete)
                del_ids = list(to_delete)
                con.execute(f"delete from nodes where id in ({del_ph})", del_ids)
                con.execute(
                    f"delete from node_provenance where node_id in ({del_ph})", del_ids
                )
            # Purge all provenance rows referencing the changed sources.
            con.execute(
                f"delete from node_provenance where source_path in ({changed_ph})",
                changed,
            )
            con.commit()
        return to_delete

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        """Create the shared node/edge schema if it does not already exist.

        Mirrors :meth:`SQLiteResearchGraphStore._ensure_schema` so both
        classes can operate on the same database file.
        """
        con.execute(
            """
            create table if not exists nodes (
                id text primary key,
                name text not null,
                type text not null,
                aliases_json text not null,
                description text not null,
                source_path text,
                metadata_json text not null
            )
            """
        )
        con.execute("create index if not exists idx_nodes_type on nodes(type)")
        con.execute("create index if not exists idx_nodes_name on nodes(name)")
        con.execute(
            """
            create table if not exists edges (
                id text primary key,
                source text not null,
                target text not null,
                type text not null,
                evidence text,
                metadata_json text not null,
                foreign key(source) references nodes(id),
                foreign key(target) references nodes(id)
            )
            """
        )
        con.execute("create index if not exists idx_edges_type on edges(type)")
        con.execute("create index if not exists idx_edges_source on edges(source)")
        con.execute("create index if not exists idx_edges_target on edges(target)")
        con.execute(
            """
            create table if not exists node_provenance (
                node_id         text not null,
                source_path     text not null,
                first_seen_at   text not null,
                last_updated_at text not null,
                primary key (node_id, source_path)
            )
            """
        )
        con.execute(
            "create index if not exists idx_provenance_source on node_provenance(source_path)"
        )


def _row_to_node(row: tuple) -> ResearchNode:
    """Inflate a node row into a :class:`ResearchNode`.

    Row shape: ``(id, name, type, aliases_json, description, source_path,
    metadata_json)``.
    """
    return ResearchNode(
        id=row[0],
        name=row[1],
        type=ResearchNodeType(row[2]),
        aliases=json.loads(row[3] or "[]"),
        description=row[4] or "",
        source_path=row[5],
        metadata=json.loads(row[6] or "{}"),
    )
