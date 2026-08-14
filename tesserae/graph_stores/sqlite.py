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
import struct
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union
from uuid import UUID

from ..research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType

# Bound-parameter chunk for the node_vectors ``in (...)`` lookup. Older SQLite
# builds cap a statement at 999 variables; two are already spent on the backend
# key, so 500 leaves room without a per-build feature check.
_VECTOR_QUERY_CHUNK = 500


def _encode_vector(vector: Sequence[float]) -> bytes:
    """Pack a vector as little-endian float64 for the ``node_vectors`` blob.

    Doubles rather than JSON or float32: a cached vector MUST decode to the
    exact value the backend produced, because the cache is only allowed to
    change what retrieval COSTS, never what it returns. float32 would round,
    and a warm cache would then score differently from a cold one.
    """
    values = [float(v) for v in vector]
    return struct.pack(f"<{len(values)}d", *values)


def _decode_vector(blob: bytes) -> List[float]:
    """Inverse of :func:`_encode_vector`."""
    count = len(blob) // 8
    return list(struct.unpack(f"<{count}d", blob[: count * 8]))


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

    def record_edge_provenance_many(self, rows: Iterable[Tuple[str, str, str, str, str]]) -> None:
        """Bulk upsert edge provenance rows ``(source, type, target, source_path, timestamp)``.

        Mirrors :meth:`record_provenance_many` (deterministic caller-supplied
        timestamp, ``first_seen_at`` preserved on conflict). SQLite-only —
        never enters graph.json. Edge provenance lets the incremental differ
        tombstone an edge whose only asserting file changed and stopped
        asserting it, even when both endpoints survive (Codex B1).
        """
        params = [
            (source, etype, target, source_path, timestamp, timestamp)
            for source, etype, target, source_path, timestamp in rows
        ]
        if not params:
            return
        with self._connect() as con:
            con.executemany(
                """
                insert into edge_provenance
                    (source, type, target, source_path, first_seen_at, last_updated_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(source, type, target, source_path) do update set
                    last_updated_at = excluded.last_updated_at
                """,
                params,
            )
            con.commit()

    def reconcile_provenance(
        self,
        node_rows: Iterable[Tuple[str, str, str]],
        edge_rows: Iterable[Tuple[str, str, str, str, str]],
    ) -> None:
        """Transactionally replace the provenance sidecar with the freshly
        computed (node + edge) row set, PRESERVING ``first_seen_at`` for rows
        that survive (Codex M5).

        Rows absent from the new set are DELETED — without this, sources that
        no longer contribute linger as false "keepers" and defeat tombstoning.
        Retained rows keep their original ``first_seen_at`` (only
        ``last_updated_at`` advances), so the sidecar stays byte-stable across
        recompiles of an unchanged corpus. Deterministic caller-supplied
        timestamps only; never ``datetime.now()``.
        """
        node_params = [
            (node_id, source_path, timestamp)
            for node_id, source_path, timestamp in node_rows
        ]
        edge_params = [
            (source, etype, target, source_path, timestamp)
            for source, etype, target, source_path, timestamp in edge_rows
        ]
        node_keys = {(n[0], n[1]) for n in node_params}
        edge_keys = {(e[0], e[1], e[2], e[3]) for e in edge_params}
        with self._connect() as con:
            # Upsert the new set (first_seen_at preserved on conflict).
            if node_params:
                con.executemany(
                    """
                    insert into node_provenance
                        (node_id, source_path, first_seen_at, last_updated_at)
                    values (?, ?, ?, ?)
                    on conflict(node_id, source_path) do update set
                        last_updated_at = excluded.last_updated_at
                    """,
                    [(nid, sp, ts, ts) for nid, sp, ts in node_params],
                )
            if edge_params:
                con.executemany(
                    """
                    insert into edge_provenance
                        (source, type, target, source_path, first_seen_at, last_updated_at)
                    values (?, ?, ?, ?, ?, ?)
                    on conflict(source, type, target, source_path) do update set
                        last_updated_at = excluded.last_updated_at
                    """,
                    [(s, t, tg, sp, ts, ts) for s, t, tg, sp, ts in edge_params],
                )
            # Delete rows absent from the new set.
            for (node_id, source_path) in [
                row
                for row in con.execute(
                    "select node_id, source_path from node_provenance"
                ).fetchall()
                if (row[0], row[1]) not in node_keys
            ]:
                con.execute(
                    "delete from node_provenance where node_id = ? and source_path = ?",
                    (node_id, source_path),
                )
            for (source, etype, target, source_path) in [
                row
                for row in con.execute(
                    "select source, type, target, source_path from edge_provenance"
                ).fetchall()
                if (row[0], row[1], row[2], row[3]) not in edge_keys
            ]:
                con.execute(
                    "delete from edge_provenance"
                    " where source = ? and type = ? and target = ? and source_path = ?",
                    (source, etype, target, source_path),
                )
            con.commit()

    def prune_provenance_to_graph(self, graph: "ResearchGraph") -> None:
        """Delete provenance rows whose node/edge is absent from ``graph``,
        PRESERVING ``first_seen_at`` for retained rows (Codex M5 reconcile).

        Each compile, provenance must be reconciled so rows for sources that no
        longer contribute (a node a file solely owned and then dropped, a stale
        cross-file edge) do not persist as false "keepers" that defeat future
        tombstoning. Membership is by node id and edge triple — both arms (full
        + incremental) converge on the same final graph, so the surviving
        sidecar is identical. Retained rows are untouched (first_seen_at and
        last_updated_at preserved), so an unchanged-corpus recompile is a no-op.
        """
        live_nodes = {node.id for node in graph.nodes}
        live_edges = {(edge.source, edge.type, edge.target) for edge in graph.edges}
        with self._connect() as con:
            stale_nodes = [
                (row[0], row[1])
                for row in con.execute(
                    "select node_id, source_path from node_provenance"
                ).fetchall()
                if row[0] not in live_nodes
            ]
            for node_id, source_path in stale_nodes:
                con.execute(
                    "delete from node_provenance where node_id = ? and source_path = ?",
                    (node_id, source_path),
                )
            stale_edges = [
                (row[0], row[1], row[2], row[3])
                for row in con.execute(
                    "select source, type, target, source_path from edge_provenance"
                ).fetchall()
                if (row[0], row[1], row[2]) not in live_edges
            ]
            for source, etype, target, source_path in stale_edges:
                con.execute(
                    "delete from edge_provenance"
                    " where source = ? and type = ? and target = ? and source_path = ?",
                    (source, etype, target, source_path),
                )
            con.commit()

    def has_node_provenance_rows(self) -> bool:
        """True when the ``node_provenance`` table has at least one row.

        Coverage precheck for the incremental differ (Codex B3): an EXISTING
        but EMPTY sidecar must not be mistaken for a provenance-ready DB.
        """
        try:
            with self._connect() as con:
                row = con.execute(
                    "select 1 from node_provenance limit 1"
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def provenance_covers_nodes(self, node_ids: Iterable[str]) -> bool:
        """True when EVERY id in ``node_ids`` has at least one provenance row.

        The differ trusts the sidecar only if it covers the prior graph; a
        partially-populated sidecar (old DB, interrupted compile) would leave
        uncovered nodes un-tombstoned, so the caller must fall back to a full
        recompile (Codex B3).
        """
        ids = list(dict.fromkeys(node_ids))
        if not ids:
            return True
        try:
            with self._connect() as con:
                covered = {
                    row[0]
                    for row in con.execute(
                        "select distinct node_id from node_provenance"
                    ).fetchall()
                }
        except sqlite3.Error:
            return False
        return all(nid in covered for nid in ids)

    def provenance_covers_edges(
        self, edge_triples: Iterable[Tuple[str, str, str]]
    ) -> bool:
        """True when EVERY ``(source, type, target)`` triple has at least one
        ``edge_provenance`` row.

        Edge analog of :meth:`provenance_covers_nodes` (major #5): the Plan-02
        readiness gate asks whether the sidecar's ``edge_provenance`` covers the
        prior graph's edge triples before trusting the differ to tombstone
        edges; an uncovered triple (old DB, interrupted compile) forces a full
        recompile rather than leaving a stale cross-file edge un-tombstoned.

        Empty input is vacuously covered (returns ``True``). On any SQLite error
        the sidecar is treated as untrustworthy (returns ``False``), matching
        the conservative fall-back in :meth:`provenance_covers_nodes`.
        """
        triples = list(dict.fromkeys(edge_triples))
        if not triples:
            return True
        try:
            with self._connect() as con:
                covered = {
                    (row[0], row[1], row[2])
                    for row in con.execute(
                        "select distinct source, type, target from edge_provenance"
                    ).fetchall()
                }
        except sqlite3.Error:
            return False
        return all(t in covered for t in triples)

    def delete_node(self, node_id: str) -> bool:
        """Delete a single node, its provenance, and all incident edges + edge
        provenance, in one transaction. Returns True if a node row was removed.

        Incident-edge cascade (Codex M6): deleting a node without its incident
        edges leaves dangling edges in ``edges`` / ``edge_provenance`` whose
        endpoint no longer exists.
        """
        with self._connect() as con:
            cursor = con.execute("delete from nodes where id = ?", (node_id,))
            con.execute("delete from node_provenance where node_id = ?", (node_id,))
            con.execute(
                "delete from edges where source = ? or target = ?",
                (node_id, node_id),
            )
            con.execute(
                "delete from edge_provenance where source = ? or target = ?",
                (node_id, node_id),
            )
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
        with self._connect() as con:
            to_delete = self._delete_nodes_by_source_txn(con, source_paths)
            con.commit()
        return to_delete

    def _delete_nodes_by_source_txn(
        self, con: sqlite3.Connection, source_paths: Set[str]
    ) -> Set[str]:
        """Pure-DB node tombstoning on an OPEN connection — does NOT commit.

        Atomic-tombstone helper (blocker #7): ``delete_nodes_by_source`` (one
        commit) and ``delete_nodes_by_source_with_edges`` (one combined commit)
        both reuse this body so node + edge tombstones land in a single
        transaction and no concurrent reader observes a node tombstone before
        its edge tombstone. Caller is responsible for committing.

        Returns the set of node ids removed from ``nodes`` / ``node_provenance``.
        """
        if not source_paths:
            return set()

        changed = list(source_paths)
        changed_ph = ",".join("?" for _ in changed)
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
            # Incident-edge cascade (Codex M6): a tombstoned node's
            # incident edges + their edge provenance must go too, or the
            # injected-store path leaves dangling edges.
            con.execute(
                f"delete from edges where source in ({del_ph}) or target in ({del_ph})",
                del_ids + del_ids,
            )
            con.execute(
                f"delete from edge_provenance"
                f" where source in ({del_ph}) or target in ({del_ph})",
                del_ids + del_ids,
            )
        # Purge all provenance rows referencing the changed sources.
        con.execute(
            f"delete from node_provenance where source_path in ({changed_ph})",
            changed,
        )
        return to_delete

    def delete_edges_by_source(self, source_paths: Set[str]) -> Set[Tuple[str, str, str]]:
        """Tombstone edges whose provenance set becomes EMPTY after removing
        ``source_paths`` (Codex B1).

        An edge survives if some UNCHANGED file still asserts it. The returned
        set is exactly the edge triples ``(source, type, target)`` removed from
        both ``edges`` and ``edge_provenance`` — the caller drops precisely
        these from the in-memory graph so a stale cross-file edge no longer
        lingers when the asserting file stops emitting it.

        Empty ``source_paths`` is a no-op (returns ``set()``).
        """
        if not source_paths:
            return set()
        with self._connect() as con:
            to_delete = self._delete_edges_by_source_txn(con, source_paths)
            con.commit()
        return to_delete

    def _delete_edges_by_source_txn(
        self, con: sqlite3.Connection, source_paths: Set[str]
    ) -> Set[Tuple[str, str, str]]:
        """Pure-DB edge tombstoning on an OPEN connection — does NOT commit.

        Atomic-tombstone helper (blocker #7): shared by
        ``delete_edges_by_source`` (one commit) and
        ``delete_nodes_by_source_with_edges`` (one combined commit). Caller
        commits. Returns the removed ``(source, type, target)`` triples.
        """
        if not source_paths:
            return set()

        changed = list(source_paths)
        changed_ph = ",".join("?" for _ in changed)
        candidates = {
            (row[0], row[1], row[2])
            for row in con.execute(
                f"select distinct source, type, target from edge_provenance"
                f" where source_path in ({changed_ph})",
                changed,
            ).fetchall()
        }
        if not candidates:
            con.execute(
                f"delete from edge_provenance where source_path in ({changed_ph})",
                changed,
            )
            return set()

        keepers = {
            (row[0], row[1], row[2])
            for row in con.execute(
                f"select distinct source, type, target from edge_provenance"
                f" where source_path not in ({changed_ph})",
                changed,
            ).fetchall()
        }
        to_delete = candidates - keepers

        for source, etype, target in to_delete:
            con.execute(
                "delete from edges where source = ? and type = ? and target = ?",
                (source, etype, target),
            )
            con.execute(
                "delete from edge_provenance"
                " where source = ? and type = ? and target = ?",
                (source, etype, target),
            )
        con.execute(
            f"delete from edge_provenance where source_path in ({changed_ph})",
            changed,
        )
        return to_delete

    def delete_nodes_by_source_with_edges(
        self, source_paths: Set[str]
    ) -> Tuple[Set[str], Set[Tuple[str, str, str]]]:
        """Convenience: tombstone nodes then edges by changed source in ONE
        atomic differ step. Returns ``(removed_node_ids, removed_edge_keys)``.

        Single-transaction (blocker #7): node + edge tombstones share ONE
        connection and ONE ``con.commit()`` so a concurrent daemon reader never
        observes a node tombstone before its edge tombstone (atomic-read EC-4).

        Order matters: node tombstoning cascades incident edges first, then
        edge tombstoning handles edges whose endpoints SURVIVED but whose only
        asserting file changed (the stale-cross-file-edge case, Codex B1).
        """
        with self._connect() as con:
            removed_nodes = self._delete_nodes_by_source_txn(con, source_paths)
            removed_edges = self._delete_edges_by_source_txn(con, source_paths)
            con.commit()
        return removed_nodes, removed_edges

    def surviving_source_paths(self, node_ids: Set[str]) -> Dict[str, str]:
        """Return ``{node_id: canonical source_path}`` for the given surviving nodes.

        A cross-file node (a shared author / field) survives a subtractive edit
        because some UNCHANGED file still asserts it — but the prior graph node
        we keep may carry a ``source_path`` that pointed at the now-changed file
        (the file that originally won attribution). That scalar is stale: a full
        compile re-derives ``source_path`` from the FIRST file (in deterministic
        sorted-path order) that still extracts the node, via ``prefer_research_node``
        keeping the earliest-merged owner. We reproduce that exact choice here by
        returning the LEXICOGRAPHICALLY SMALLEST surviving provenance
        ``source_path`` per node — ``iter_markdown_files`` yields files in sorted
        order, so first-seen == min path. The caller re-points only nodes whose
        kept ``source_path`` belonged to a changed file, so an incremental and a
        full compile converge on byte-identical node scalars (Phase-4 subtractive
        gate). Call AFTER tombstoning so changed-file rows are already purged.

        Empty ``node_ids`` is a no-op (returns ``{}``).
        """
        if not node_ids:
            return {}
        ids = list(node_ids)
        out: Dict[str, str] = {}
        with self._connect() as con:
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                ph = ",".join("?" for _ in chunk)
                for node_id, src in con.execute(
                    f"select node_id, min(source_path) from node_provenance"
                    f" where node_id in ({ph}) group by node_id",
                    chunk,
                ).fetchall():
                    if src is not None:
                        out[node_id] = src
        return out

    # ------------------------------------------------------------------ #
    # node_memory sidecar (Phase-5 KB-01)                                 #
    # ------------------------------------------------------------------ #

    def read_node_memory(self) -> Dict[str, dict]:
        """Return ``{node_id: {decay_score, access_count, last_accessed_at,
        confidence, superseded}}`` for every row in ``node_memory``.

        The single read surface for all mutable memory state. ``superseded``
        is returned as a Python ``bool`` (stored as 0/1 INTEGER).
        """
        out: Dict[str, dict] = {}
        with self._connect() as con:
            for node_id, decay_score, access_count, last_accessed_at, confidence, superseded in con.execute(
                "select node_id, decay_score, access_count, last_accessed_at,"
                " confidence, superseded from node_memory"
            ).fetchall():
                out[node_id] = {
                    "decay_score": decay_score,
                    "access_count": access_count,
                    "last_accessed_at": last_accessed_at,
                    "confidence": confidence,
                    "superseded": bool(superseded),
                }
        return out

    def write_node_memory_many(
        self,
        rows: Iterable[Tuple[str, float, int, Optional[str], Optional[str], int, str]],
    ) -> None:
        """Bulk upsert compile-owned memory columns, keyed on ``node_id``.

        Each row is ``(node_id, decay_score, access_count, last_accessed_at,
        confidence, superseded, updated_at)``. The compile OWNS
        ``decay_score`` / ``confidence`` / ``superseded`` / ``updated_at`` and
        overwrites them on conflict. It does NOT own MCP-accumulated reads, so
        on conflict the existing ``access_count`` / ``last_accessed_at`` are
        PRESERVED — never clobbered by the access_count carried in ``rows``
        (which is only used for the first INSERT of a brand-new node).
        """
        params = list(rows)
        if not params:
            return
        with self._connect() as con:
            con.executemany(
                """
                insert into node_memory
                    (node_id, decay_score, access_count, last_accessed_at,
                     confidence, superseded, updated_at)
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(node_id) do update set
                    decay_score = excluded.decay_score,
                    confidence  = excluded.confidence,
                    superseded  = excluded.superseded,
                    updated_at  = excluded.updated_at
                """,
                params,
            )
            con.commit()

    def bump_access(self, node_id: str, accessed_at: str) -> None:
        """ATOMIC access bump for ``node_id`` (Phase-5 KB-02).

        Increments ``access_count`` and stamps ``last_accessed_at`` in ONE SQL
        statement (``access_count = access_count + 1``) — never read-modify-write
        (05-RESEARCH Pitfall 3), so concurrent MCP reads don't lose increments.
        Creates the row at count 1 on first access.
        """
        with self._connect() as con:
            con.execute(
                """
                insert into node_memory
                    (node_id, access_count, last_accessed_at, updated_at)
                values (?, 1, ?, ?)
                on conflict(node_id) do update set
                    access_count     = access_count + 1,
                    last_accessed_at = excluded.last_accessed_at,
                    updated_at       = excluded.updated_at
                """,
                (node_id, accessed_at, accessed_at),
            )
            con.commit()

    def append_read_audit(
        self,
        at: str,
        tool: str,
        actor: str,
        node_ids: Sequence[str],
        tesserae_version: str,
        schema_version: int,
    ) -> None:
        """Append ONE read-audit row: who read which nodes, through which tool.

        Append-only and never updated — an audit row that can be rewritten is
        not an audit row. The caller supplies ``at`` (the same instant it
        stamped on the ``bump_access`` it is attributing) so the audit and the
        access count it explains cannot disagree about when the read happened.

        Called only when the audit is enabled (:func:`tesserae.memory.store`
        owns that gate), because opening this connection is itself a write on
        what is otherwise a read path.
        """
        with self._connect() as con:
            con.execute(
                """
                insert into read_audit
                    (at, tool, actor, node_ids_json, node_count,
                     tesserae_version, schema_version)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    at,
                    tool,
                    actor,
                    json.dumps(list(node_ids), ensure_ascii=False, sort_keys=False),
                    len(node_ids),
                    tesserae_version,
                    int(schema_version),
                ),
            )
            con.commit()

    def read_audit_rows(
        self,
        *,
        limit: int = 100,
        actor: str = "",
        tool: str = "",
        node_id: str = "",
    ) -> List[Tuple[int, str, str, str, str, int, str, int]]:
        """Most-recent-first audit rows, optionally narrowed.

        Row shape: ``(id, at, tool, actor, node_ids_json, node_count,
        tesserae_version, schema_version)``.

        ``node_id`` is a PREFILTER in SQL and nothing more: the ids live in a
        JSON array column, so ``like`` can only say "these bytes appear
        somewhere in the row". Membership is confirmed against the parsed list
        by the caller (:func:`tesserae.memory.store.read_audit_rows`) — the
        LIKE narrows the scan, it does not decide the answer.
        """
        clauses: List[str] = []
        params: List[object] = []
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if tool:
            clauses.append("tool = ?")
            params.append(tool)
        if node_id:
            clauses.append("node_ids_json like ?")
            params.append(f'%"{node_id}"%')
        where = f" where {' and '.join(clauses)}" if clauses else ""
        params.append(max(0, int(limit)))
        try:
            with self._connect() as con:
                cur = con.execute(
                    "select id, at, tool, actor, node_ids_json, node_count,"
                    " tesserae_version, schema_version from read_audit"
                    f"{where} order by id desc limit ?",
                    params,
                )
                return [tuple(row) for row in cur.fetchall()]  # type: ignore[misc]
        except sqlite3.Error:
            # A locked or corrupt sidecar reads as "no rows" here: this is a
            # reporting surface over opt-in state, and raising out of it would
            # make asking who read a node fail the caller's whole query.
            return []

    def has_node_memory_rows(self) -> bool:
        """True when ``node_memory`` has at least one row.

        Mirrors :meth:`has_node_provenance_rows` — an EXISTING but EMPTY
        sidecar must not be mistaken for a populated memory store.
        """
        try:
            with self._connect() as con:
                row = con.execute("select 1 from node_memory limit 1").fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------ #
    # node_vectors sidecar (embedding cache)                              #
    # ------------------------------------------------------------------ #

    def read_node_vector_blobs(
        self,
        backend_name: str,
        backend_dim: int,
        text_hashes: Iterable[str],
    ) -> Dict[str, bytes]:
        """Return ``{text_sha256: packed_vector}`` for the requested hashes.

        The blobs are handed back undecoded because the stored layout — a
        contiguous run of little-endian float64 — is already what a vectorised
        reader wants: ``numpy.frombuffer`` over the joined blobs reconstructs
        the corpus matrix in single-digit milliseconds, where decoding 47k rows
        into Python lists and re-materialising them as an array costs ~370 ms.
        :meth:`read_node_vectors` is the decoding wrapper for callers that want
        floats, so both share ONE query.

        Only rows matching BOTH ``backend_name`` and ``backend_dim`` are
        returned — a vector produced by another model lives in another space
        and must never be served as this one's.

        The hash list is queried in chunks so a corpus-sized read stays under
        SQLite's bound-parameter limit instead of raising on large graphs.
        """
        wanted = [h for h in dict.fromkeys(text_hashes) if h]
        if not wanted:
            return {}
        out: Dict[str, bytes] = {}
        with self._connect() as con:
            for start in range(0, len(wanted), _VECTOR_QUERY_CHUNK):
                chunk = wanted[start : start + _VECTOR_QUERY_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = con.execute(
                    "select text_sha256, vector from node_vectors"
                    " where backend_name = ? and backend_dim = ?"
                    f" and text_sha256 in ({placeholders})",
                    (backend_name, int(backend_dim), *chunk),
                ).fetchall()
                for text_sha256, blob in rows:
                    out[text_sha256] = bytes(blob)
        return out

    def read_node_vectors(
        self,
        backend_name: str,
        backend_dim: int,
        text_hashes: Iterable[str],
    ) -> Dict[str, List[float]]:
        """:meth:`read_node_vector_blobs`, decoded to float lists."""
        return {
            text_sha256: _decode_vector(blob)
            for text_sha256, blob in self.read_node_vector_blobs(
                backend_name, backend_dim, text_hashes
            ).items()
        }

    def write_node_vector_blobs_many(
        self,
        backend_name: str,
        backend_dim: int,
        rows: Iterable[Tuple[str, bytes]],
    ) -> None:
        """Persist ``(text_sha256, packed_vector)`` pairs for one backend.

        Takes packed bytes rather than floats so a caller that already holds
        the encoded form — the vectorised embedding lane does, because it needs
        the same bytes to build its matrix — writes them without a second
        encode. :meth:`write_node_vectors_many` is the encoding wrapper.

        ``insert or ignore``: the row is a pure function of its key, so a
        concurrent writer that got there first has written the same bytes and
        there is nothing to update. That also keeps this write path free of
        read-modify-write, like :meth:`bump_access`.
        """
        params = [
            (backend_name, int(backend_dim), text_sha256, blob)
            for text_sha256, blob in rows
        ]
        if not params:
            return
        with self._connect() as con:
            con.executemany(
                "insert or ignore into node_vectors"
                " (backend_name, backend_dim, text_sha256, vector)"
                " values (?, ?, ?, ?)",
                params,
            )
            con.commit()

    def write_node_vectors_many(
        self,
        backend_name: str,
        backend_dim: int,
        rows: Iterable[Tuple[str, Sequence[float]]],
    ) -> None:
        """:meth:`write_node_vector_blobs_many`, encoding floats on the way in."""
        self.write_node_vector_blobs_many(
            backend_name,
            backend_dim,
            ((text_sha256, _encode_vector(vector)) for text_sha256, vector in rows),
        )

    def count_node_vectors(self, backend_name: str, backend_dim: int) -> int:
        """Number of cached vectors for one ``(backend_name, backend_dim)`` key.

        Reported by ``embedding_status`` so a silently-cold cache cannot look
        like a fast path.
        """
        with self._connect() as con:
            row = con.execute(
                "select count(*) from node_vectors"
                " where backend_name = ? and backend_dim = ?",
                (backend_name, int(backend_dim)),
            ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ #
    # bm25_docs / bm25_postings sidecar (the inverted index)               #
    # ------------------------------------------------------------------ #

    def read_bm25_docs(self) -> Dict[str, Tuple[int, int]]:
        """``{text_key: (doc_id, doc_len)}`` for every indexed document.

        The WHOLE table, not the caller's keys. BM25's ``avgdl`` is a mean over
        every candidate, so a query needs ``doc_len`` for all of them — and
        measured on this project's 46,926-node candidate set, one scan costs
        16.7 ms against 85.4 ms for the same keys fetched in 500-parameter
        ``in (...)`` chunks. The cost of that choice is that documents left
        behind by an earlier corpus are read too; they are never *consulted*,
        because a score only ever looks up a candidate's own key, and this file
        is classified a cache precisely so the accumulation is droppable.
        """
        with self._connect() as con:
            rows = con.execute(
                "select text_key, doc_id, doc_len from bm25_docs"
            ).fetchall()
        return {row[0]: (int(row[1]), int(row[2])) for row in rows}

    def read_bm25_postings(self, terms: Iterable[str]) -> Dict[str, Dict[int, int]]:
        """``{term: {doc_id: tf}}`` for the requested terms.

        One statement per term rather than an ``in (...)``: a query carries a
        handful of terms, and per-term statements keep the primary-key prefix
        scan exact instead of asking SQLite to plan a mixed range.
        """
        wanted = [term for term in dict.fromkeys(terms) if term]
        if not wanted:
            return {}
        out: Dict[str, Dict[int, int]] = {}
        with self._connect() as con:
            for term in wanted:
                rows = con.execute(
                    "select doc_id, tf from bm25_postings where term = ?",
                    (term,),
                ).fetchall()
                out[term] = {int(row[0]): int(row[1]) for row in rows}
        return out

    def write_bm25_docs_many(
        self,
        rows: Iterable[Tuple[str, int, Dict[str, int]]],
    ) -> None:
        """Index ``(text_key, doc_len, {term: tf})`` documents, atomically.

        BOTH tables commit in one transaction. A ``bm25_docs`` row visible
        without its postings would read as a document that contains no terms —
        it would score 0.0 forever while still counting in ``n_docs`` and
        ``avgdl``, so a crash between two commits would not degrade retrieval,
        it would silently change everybody's scores. That is the failure this
        whole module is not allowed to have.

        ``insert or ignore``: every row is a pure function of its key, so a
        writer that got there first wrote the same bytes and there is nothing
        to update — the same read-modify-write-free posture as
        :meth:`write_node_vectors_many`.
        """
        pending = [(text_key, int(doc_len), postings) for text_key, doc_len, postings in rows]
        if not pending:
            return
        with self._connect() as con:
            con.executemany(
                "insert or ignore into bm25_docs (text_key, doc_len) values (?, ?)",
                [(text_key, doc_len) for text_key, doc_len, _ in pending],
            )
            # Resolve ids AFTER the insert so a row another writer already
            # created is reused rather than duplicated (text_key is unique, so
            # the insert above was a no-op for it).
            ids: Dict[str, int] = {}
            keys = [text_key for text_key, _, _ in pending]
            for start in range(0, len(keys), _VECTOR_QUERY_CHUNK):
                chunk = keys[start : start + _VECTOR_QUERY_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                for row in con.execute(
                    f"select text_key, doc_id from bm25_docs where text_key in ({placeholders})",
                    chunk,
                ).fetchall():
                    ids[row[0]] = int(row[1])
            posting_rows = [
                (term, ids[text_key], int(tf))
                for text_key, _, postings in pending
                if text_key in ids
                for term, tf in postings.items()
            ]
            if posting_rows:
                con.executemany(
                    "insert or ignore into bm25_postings (term, doc_id, tf)"
                    " values (?, ?, ?)",
                    posting_rows,
                )
            con.commit()

    def count_bm25_docs(self) -> int:
        """Number of indexed documents, for status reporting."""
        with self._connect() as con:
            row = con.execute("select count(*) from bm25_docs").fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ #
    # fact_observed sidecar (transaction time)                            #
    # ------------------------------------------------------------------ #

    def read_fact_observed(self) -> Dict[Tuple[str, str, str], Tuple[str, str]]:
        """``{(subject_id, predicate, object_id): (first, last)}`` for every row."""
        with self._connect() as con:
            rows = con.execute(
                "select subject_id, predicate, object_id,"
                " first_compile_at, last_seen_compile_at from fact_observed"
            ).fetchall()
        return {(r[0], r[1], r[2]): (r[3], r[4]) for r in rows}

    def write_fact_observed_many(
        self, rows: Iterable[Tuple[str, str, str]], observed_at: str
    ) -> int:
        """Stamp ``observed_at`` on each fact key, first sighting write-once.

        ``first_compile_at`` is deliberately NOT in the conflict update: it
        answers "when did we first learn this" and a row that already exists
        has already answered it. Overwriting it on every compile would turn
        the whole axis into a duplicate of ``last_seen_compile_at``, which is
        the failure this table exists to avoid.

        ``observed_at`` is one value for the entire batch: the caller stamps
        the compile boundary once, so the transaction clock ticks per compile
        and cannot vary between two facts of the same compile.
        """
        params = [(s, p, o, observed_at, observed_at) for s, p, o in rows]
        if not params:
            return 0
        with self._connect() as con:
            con.executemany(
                "insert into fact_observed"
                " (subject_id, predicate, object_id,"
                "  first_compile_at, last_seen_compile_at)"
                " values (?, ?, ?, ?, ?)"
                " on conflict(subject_id, predicate, object_id) do update set"
                "  last_seen_compile_at = excluded.last_seen_compile_at",
                params,
            )
            con.commit()
        return len(params)

    def count_fact_observed(self) -> int:
        """Number of observed fact keys.

        Read before an ``observed_as_of`` pivot runs: an empty ledger means
        the transaction axis has never been written, and answering the pivot
        from it would hand back the whole corpus wearing an "as we knew it
        on DATE" label.
        """
        with self._connect() as con:
            row = con.execute("select count(*) from fact_observed").fetchone()
        return int(row[0]) if row else 0

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
        # Edge provenance sidecar (Codex B1). An edge between two surviving
        # nodes whose ASSERTING file changed must be tombstoned when no
        # remaining file asserts it — node-only provenance can't express that.
        # SQLite-only; never enters graph.json. Keyed on the canonical edge
        # triple (source, type, target) + the asserting source_path.
        con.execute(
            """
            create table if not exists edge_provenance (
                source          text not null,
                type            text not null,
                target          text not null,
                source_path     text not null,
                first_seen_at   text not null,
                last_updated_at text not null,
                primary key (source, type, target, source_path)
            )
            """
        )
        con.execute(
            "create index if not exists idx_edge_provenance_source on edge_provenance(source_path)"
        )
        # node_memory sidecar (Phase-5 KB-01). The single home for ALL mutable
        # memory state — decay_score, access_count, last_accessed_at,
        # confidence, superseded. Mirrors the node_provenance discipline:
        # CREATE TABLE IF NOT EXISTS, SQLite-only, NEVER serialized into
        # graph.json (which must stay byte-identical across compiles).
        con.execute(
            """
            create table if not exists node_memory (
                node_id          text primary key,
                decay_score      real default 1.0,
                access_count     integer default 0,
                last_accessed_at text,
                confidence       text,
                superseded       integer default 0,
                updated_at       text
            )
            """
        )
        # node_vectors sidecar (embedding cache). Same discipline as
        # node_memory: CREATE TABLE IF NOT EXISTS, SQLite-only, NEVER
        # serialized into graph.json. Keyed on
        # (backend_name, backend_dim, text_sha256) and deliberately NOT on
        # node id: identity here is the embedded TEXT, so a renamed or
        # re-described node misses (and re-embeds) while an unchanged node
        # hits even after its project moves or its id is rewritten by
        # canonicalization. The backend name and dim are part of the key
        # because vectors from two different models share no space —
        # mixing them would silently corrupt cosine.
        con.execute(
            """
            create table if not exists node_vectors (
                backend_name text not null,
                backend_dim  integer not null,
                text_sha256  text not null,
                vector       blob not null,
                primary key (backend_name, backend_dim, text_sha256)
            )
            """
        )
        # bm25_docs / bm25_postings sidecar (the inverted index). Same
        # discipline as node_memory and node_vectors: CREATE TABLE IF NOT
        # EXISTS, SQLite-only, NEVER serialized into graph.json.
        #
        # Keyed on ``text_key`` = sha256 of the BM25 document text, and
        # deliberately NOT on node id, for the same reason node_vectors is
        # not: identity here is the TEXT that was tokenised. A renamed or
        # re-described node produces different text, misses, and is re-indexed;
        # an unchanged node hits after a relocation, a from-scratch recompile,
        # or a canonicalization rewrite of its id.
        #
        # ``doc_id`` is a surrogate, and it earns its keep: the postings table
        # carries one row per (term, document) — 1.0M rows on this project's
        # own graph — so repeating a 64-char hex key in every one of them costs
        # 98 MB against 26 MB for an integer. It never leaves the sidecar and
        # no score depends on it, which is what makes a surrogate acceptable
        # here where it would not be in an artifact.
        #
        # A document with NO postings is a legitimate state (empty text →
        # doc_len 0 → scores 0.0, exactly as the in-memory lane scores it), so
        # the presence of the bm25_docs row — never the presence of postings —
        # is what "this document is indexed" means. That is precisely why the
        # writer commits both tables in ONE transaction: a doc row visible
        # without its postings would be read as a document containing no terms,
        # and would silently score 0 and depress every other document's IDF.
        con.execute(
            """
            create table if not exists bm25_docs (
                doc_id   integer primary key autoincrement,
                text_key text not null unique,
                doc_len  integer not null
            )
            """
        )
        con.execute(
            """
            create table if not exists bm25_postings (
                term   text not null,
                doc_id integer not null,
                tf     integer not null,
                primary key (term, doc_id)
            ) without rowid
            """
        )
        # read_audit sidecar (opt-in, TESSERAE_READ_AUDIT). node_memory counts
        # reads and cannot say WHO produced them, so the demand signal driving
        # forgetting-by-disuse treats one chatty agent and a human as the same
        # input. This table names the reader. Same discipline as node_memory:
        # CREATE TABLE IF NOT EXISTS, SQLite-only, NEVER serialized into
        # graph.json. Rows are only ever appended.
        #
        # TWO version columns, and neither is redundant. ``tesserae_version``
        # is the release that wrote the row, so a bad release is attributable
        # after the fact; ``schema_version`` is the ROW SHAPE, so a future
        # reader can tell whether it can parse the row without keeping a
        # release->shape table. ``node_count`` is denormalized from
        # ``node_ids_json`` so a per-actor tally never has to parse JSON.
        con.execute(
            """
            create table if not exists read_audit (
                id               integer primary key autoincrement,
                at               text not null,
                tool             text not null default '',
                actor            text not null default '',
                node_ids_json    text not null default '[]',
                node_count       integer not null default 0,
                tesserae_version text not null default '',
                schema_version   integer not null default 1
            )
            """
        )
        con.execute("create index if not exists idx_read_audit_actor on read_audit(actor)")

        # fact_observed sidecar (transaction time). Same discipline as
        # node_memory and node_vectors: CREATE TABLE IF NOT EXISTS,
        # SQLite-only, NEVER serialized into graph.json OR into
        # temporal_facts.jsonl.
        #
        # This is the ONLY wall-clock axis in the temporal model, and it is
        # here rather than in either artifact for exactly that reason: a
        # timestamp stamped from now() inside graph.json means the same
        # sources compile to different bytes on Tuesday than on Monday, which
        # is the leak class this repo has hit four times.
        #
        # Keyed on the fact's stable identity — (subject_id, predicate,
        # object_id) — and NOT on TemporalFact.id, which hashes the evidence
        # string too: an edge whose evidence was re-extracted with different
        # wording is the same fact learned at the same time, and re-keying it
        # would reset its first sighting.
        con.execute(
            """
            create table if not exists fact_observed (
                subject_id           text not null,
                predicate            text not null,
                object_id            text not null,
                first_compile_at     text not null,
                last_seen_compile_at text not null,
                primary key (subject_id, predicate, object_id)
            )
            """
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
