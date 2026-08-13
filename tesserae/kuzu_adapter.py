"""Kuzu export — a one-way projection of the graph, never a runtime store.

Kuzu sits here beside :mod:`tesserae.okf` and :mod:`tesserae.graphiti_adapter`
because that is what it is: a dependency-free projection of a compiled
``graph.json`` into a foreign shape so another tool can query it. Nothing in
Tesserae reads a Kuzu database back at runtime, and nothing may start to.

This module exists to close a question that read as open. ``KuzuResearchGraph
Store`` used to live in :mod:`tesserae.persistence` next to the real SQLite
store, reachable only through one ``extract --kuzu-output`` flag whose
dependency was declared dev-only — a half-wired second backend, which is what
made "should Tesserae adopt a graph database?" look undecided. The answer is
no, for one architectural reason rather than a licensing one (Kuzu is MIT):

* A second authoritative store can disagree with ``graph.json`` about the same
  fact, and there is no arbiter — ``graph.json`` is the source of truth and a
  store that can contradict it is a bug surface, not a feature.
* Byte-idempotence — two compiles of one corpus producing byte-identical
  ``graph.json``, pinned by ``tests/test_byte_idempotence_phase5.py`` — holds
  because compile is a sorted-key pure function. Promoting an embedded engine
  to a store would make that property depend on a database's write ordering
  instead.

As an export, neither objection applies: the database is derived output, wiped
and rewritten from the graph, and no compile path reads it.

``read_graph`` is kept for the same reason ``okf.read_okf_bundle`` exists — an
export you cannot read back is an export you cannot verify — not because
anything in the engine loads from Kuzu.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import List

from .research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


class KuzuExportUnavailableError(RuntimeError):
    """Raised when the optional ``kuzu`` dependency is not installed.

    Mirrors :class:`tesserae.graphiti_adapter.GraphitiSyncUnavailableError`:
    an optional export names its missing package instead of surfacing a bare
    ``ImportError`` from three frames down.
    """


def _kuzu_encode(obj) -> str:
    """Base64-encode a JSON payload for storage in a Kuzu STRING column.

    Kuzu 0.16.0 re-parses STRING values that look like JSON/list/struct and
    re-serialises them in its own (lossy) format on read — e.g. the stored
    ``["3DGS"]`` comes back as ``[3DGS]`` and ``{"k": "v"}`` as ``{k: v}``,
    silently corrupting aliases and metadata so ``json.loads`` later raises.
    Base64's alphabet (``A-Za-z0-9+/=``) contains no brackets/braces/quotes
    for Kuzu to mis-handle, so the round-trip is lossless. See
    ``test_kuzu_export_writes_nodes_edges_and_can_count``.
    """
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _kuzu_decode(value, default):
    """Inverse of :func:`_kuzu_encode`, tolerant of legacy/empty values."""
    if not value:
        return default
    try:
        raw = base64.b64decode(value.encode("ascii")).decode("utf-8")
    except (ValueError, AttributeError):
        # Pre-base64 databases stored plain JSON; fall back so old files read.
        raw = value
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def import_kuzu():
    try:
        import kuzu
    except ImportError as exc:
        raise KuzuExportUnavailableError(
            "kuzu is not installed. Install it with: pip install kuzu"
        ) from exc
    return kuzu


class KuzuResearchGraphAdapter:
    """Project a validated :class:`ResearchGraph` into a Kuzu database.

    One way. ``write_graph(replace=True)`` deletes and recreates the database
    so the export is a pure function of the graph handed in — the same posture
    as the OKF bundle and the Graphiti episode JSONL, and the reason an export
    can never drift out of agreement with ``graph.json``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write_graph(self, graph: ResearchGraph, replace: bool = False) -> None:
        kuzu = import_kuzu()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if replace and self.path.exists():
            if self.path.is_dir():
                shutil.rmtree(self.path)
            else:
                self.path.unlink()
        db = kuzu.Database(str(self.path))
        con = kuzu.Connection(db)
        self._ensure_schema(con)
        for node in graph.nodes:
            con.execute(
                "CREATE (:Node {id: $id, name: $name, type: $type, aliases_json: $aliases_json, description: $description, source_path: $source_path, metadata_json: $metadata_json})",
                {
                    "id": node.id,
                    "name": node.name,
                    "type": node.type.value,
                    # Base64 so Kuzu 0.16.0 doesn't re-parse/mangle the JSON.
                    "aliases_json": _kuzu_encode(node.aliases),
                    "description": node.description,
                    "source_path": node.source_path or "",
                    "metadata_json": _kuzu_encode(node.metadata),
                },
            )
        for edge in graph.edges:
            con.execute(
                """
                MATCH (a:Node {id: $source}), (b:Node {id: $target})
                CREATE (a)-[:Edge {type: $type, evidence: $evidence, metadata_json: $metadata_json}]->(b)
                """,
                {
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.type,
                    "evidence": edge.evidence or "",
                    "metadata_json": _kuzu_encode(edge.metadata),
                },
            )

    def read_graph(self) -> ResearchGraph:
        kuzu = import_kuzu()
        db = kuzu.Database(str(self.path), read_only=True)
        con = kuzu.Connection(db)
        nodes: List[ResearchNode] = []
        result = con.execute("MATCH (n:Node) RETURN n.id, n.name, n.type, n.aliases_json, n.description, n.source_path, n.metadata_json ORDER BY n.id")
        while result.has_next():
            row = result.get_next()
            nodes.append(
                ResearchNode(
                    id=row[0],
                    name=row[1],
                    type=ResearchNodeType(row[2]),
                    aliases=_kuzu_decode(row[3], []),
                    description=row[4] or "",
                    source_path=row[5] or None,
                    metadata=_kuzu_decode(row[6], {}),
                )
            )
        edges: List[ResearchEdge] = []
        result = con.execute("MATCH (a:Node)-[e:Edge]->(b:Node) RETURN a.id, b.id, e.type, e.evidence, e.metadata_json ORDER BY a.id, e.type, b.id")
        while result.has_next():
            row = result.get_next()
            edges.append(
                ResearchEdge(
                    source=row[0],
                    target=row[1],
                    type=row[2],
                    evidence=row[3] or None,
                    metadata=_kuzu_decode(row[4], {}),
                )
            )
        return ResearchGraph(nodes=nodes, edges=edges)

    def counts(self) -> dict:
        kuzu = import_kuzu()
        db = kuzu.Database(str(self.path), read_only=True)
        con = kuzu.Connection(db)
        node_count = con.execute("MATCH (n:Node) RETURN count(n)").get_next()[0]
        edge_count = con.execute("MATCH (:Node)-[e:Edge]->(:Node) RETURN count(e)").get_next()[0]
        return {"nodes": node_count, "edges": edge_count}

    def _ensure_schema(self, con) -> None:
        con.execute(
            """
            CREATE NODE TABLE IF NOT EXISTS Node(
                id STRING,
                name STRING,
                type STRING,
                aliases_json STRING,
                description STRING,
                source_path STRING,
                metadata_json STRING,
                PRIMARY KEY(id)
            )
            """
        )
        con.execute("CREATE REL TABLE IF NOT EXISTS Edge(FROM Node TO Node, type STRING, evidence STRING, metadata_json STRING)")
