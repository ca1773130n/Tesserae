"""Persistence adapters for validated ResearchGraph objects.

A *store* here is something Tesserae itself reads back. That is SQLite and
nothing else — the Kuzu projection moved to :mod:`tesserae.kuzu_adapter`,
beside the OKF and Graphiti exports, because it was never read at runtime and
a second authoritative store can disagree with ``graph.json``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

from .research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


class SQLiteResearchGraphStore:
    """Small local graph store using stdlib SQLite.

    Intentionally simple and dependency-free. This is the durable local
    substrate, full stop — not a placeholder for a graph engine to replace.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def write_graph(self, graph: ResearchGraph, replace: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as con:
            self._ensure_schema(con)
            if replace:
                con.execute("delete from edges")
                con.execute("delete from nodes")
            for node in graph.nodes:
                con.execute(
                    """
                    insert or replace into nodes
                    (id, name, type, aliases_json, description, source_path, metadata_json)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.id,
                        node.name,
                        node.type.value,
                        json.dumps(node.aliases, ensure_ascii=False),
                        node.description,
                        node.source_path,
                        json.dumps(node.metadata, ensure_ascii=False, sort_keys=True),
                    ),
                )
            for edge in graph.edges:
                edge_id = f"{edge.source}|{edge.type}|{edge.target}"
                con.execute(
                    """
                    insert or replace into edges
                    (id, source, target, type, evidence, metadata_json)
                    values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        edge.source,
                        edge.target,
                        edge.type,
                        edge.evidence,
                        json.dumps(edge.metadata, ensure_ascii=False, sort_keys=True),
                    ),
                )
            con.commit()

    def read_graph(self) -> ResearchGraph:
        with sqlite3.connect(self.path) as con:
            self._ensure_schema(con)
            nodes = [
                ResearchNode(
                    id=row[0],
                    name=row[1],
                    type=ResearchNodeType(row[2]),
                    aliases=json.loads(row[3] or "[]"),
                    description=row[4] or "",
                    source_path=row[5],
                    metadata=json.loads(row[6] or "{}"),
                )
                for row in con.execute("select id, name, type, aliases_json, description, source_path, metadata_json from nodes order by rowid")
            ]
            edges = [
                ResearchEdge(
                    source=row[0],
                    target=row[1],
                    type=row[2],
                    evidence=row[3],
                    metadata=json.loads(row[4] or "{}"),
                )
                for row in con.execute("select source, target, type, evidence, metadata_json from edges order by rowid")
            ]
            return ResearchGraph(nodes=nodes, edges=edges)

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
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
