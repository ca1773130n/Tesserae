"""CodeGraph (Option C) adapter — read .codegraph/codegraph.db into Tesserae.

Producer for ``tesserae project sync-code``. Reads the SQLite store
written by https://github.com/colbymchenry/codegraph (21-language
tree-sitter extractor + best-effort resolver) and translates it into the
typed :class:`ResearchGraph` slice that feature H's
``insight_symbol_link`` pass already consumes.

Distinct from :mod:`tesserae.code_graph_extractor` — that module mints
the same node/edge ontology from Python source via the stdlib ``ast``
module and remains the zero-dependency default. The adapter is opt-in
and adds nothing to Tesserae's runtime deps beyond the stdlib ``sqlite3``
and ``subprocess`` modules.

Design constraints (locked by ``/tmp/codegraph-research/schema.md``):

* **id_seed strategy.** CodeGraph's row id embeds ``start_line`` and is
  unstable across line-shifting refactors. The adapter reseeds with
  ``f"{file_path}:{kind}:{qualified_name}"`` so feature H's
  ``discusses`` edges survive edits.
* **Qualified names are dotted, semantic-only.** ``buildQualifiedName``
  joins parent scope ``name``s with ``"."`` and excludes the file path
  (see schema.md §4). We pass ``qualified_name`` straight through as
  the node display name so both bare-identifier and dotted-path
  candidates resolve in feature H's symbol index.
* **Edge ``extends`` is rewritten to ``inherits_from``.** Tesserae
  already carries ``inherits_from`` for code (feature A); we collapse
  the synonym so existing tooling keeps working unchanged.
* **Schema-drift tolerance.** Unknown ``kind`` values fall back to
  ``CODE_SYMBOL`` with the raw kind in ``metadata["codegraph_kind"]``;
  unknown ``edges.kind`` values are dropped with a logged warning so a
  newer CodeGraph release can't crash the adapter.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .research_graph import (
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping tables — single source of truth so the test fixture and the
# adapter agree on what each CodeGraph kind becomes.
# ---------------------------------------------------------------------------


# CodeGraph NodeKind (22 values per schema.md §2) → Tesserae node type.
# Anything missing here falls back to CODE_SYMBOL; the raw kind is
# preserved in metadata so disambiguation downstream is still cheap.
_KIND_TO_NODE_TYPE: Dict[str, ResearchNodeType] = {
    "file": ResearchNodeType.CODE_FILE,
    "module": ResearchNodeType.CODE_MODULE,
    "namespace": ResearchNodeType.CODE_NAMESPACE,
    "class": ResearchNodeType.CODE_CLASS,
    "struct": ResearchNodeType.CODE_STRUCT,
    "interface": ResearchNodeType.CODE_INTERFACE,
    "protocol": ResearchNodeType.CODE_INTERFACE,
    "trait": ResearchNodeType.CODE_TRAIT,
    "function": ResearchNodeType.CODE_FUNCTION,
    "method": ResearchNodeType.CODE_METHOD,
    "property": ResearchNodeType.CODE_FIELD,
    "field": ResearchNodeType.CODE_FIELD,
    "variable": ResearchNodeType.CODE_VARIABLE,
    "constant": ResearchNodeType.CODE_CONSTANT,
    "enum": ResearchNodeType.CODE_ENUM,
    "enum_member": ResearchNodeType.CODE_ENUM_MEMBER,
    "type_alias": ResearchNodeType.CODE_TYPE_ALIAS,
    "parameter": ResearchNodeType.CODE_PARAMETER,
    "import": ResearchNodeType.DEPENDENCY,
    "export": ResearchNodeType.CODE_SYMBOL,
    "route": ResearchNodeType.CODE_ROUTE,
    "component": ResearchNodeType.CODE_COMPONENT,
}


# CodeGraph EdgeKind (12 values per schema.md §3) → Tesserae edge type.
# ``extends`` collapses onto Tesserae's pre-existing ``inherits_from``
# so feature H's symbol/edge keys continue to line up across the older
# Python ast extractor and the new SQLite-backed source.
_EDGE_KIND_MAP: Dict[str, str] = {
    "contains": "contains",
    "calls": "calls",
    "imports": "imports",
    "exports": "exports",
    "extends": "inherits_from",
    "implements": "implements",
    "references": "references",
    "type_of": "type_of",
    "returns": "returns",
    "instantiates": "instantiates",
    "overrides": "overrides",
    "decorates": "decorates",
}


# ---------------------------------------------------------------------------
# Result dataclass (mirrors code_graph_extractor.IngestCodeResult so the
# CLI summary line is identical across the two backends).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestCodeResult:
    """CLI summary payload for the CodeGraph-backed sync."""

    graph: ResearchGraph
    processed_files: int
    skipped_dirs: int
    nodes: int
    edges: int
    languages: int = 0
    skipped_edges: int = 0


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawNode:
    id: str
    kind: str
    name: str
    qualified_name: str
    file_path: str
    language: str
    start_line: int
    end_line: int


class CodeGraphAdapter:
    """Read a CodeGraph SQLite DB and emit a typed :class:`ResearchGraph`."""

    def __init__(self, db_path: str | Path, *, project_root: Optional[Path] = None) -> None:
        self.db_path = Path(db_path)
        # ``project_root`` is only used to mint a CODE_PROJECT envelope
        # node; the adapter functions correctly without it.
        self.project_root = Path(project_root) if project_root is not None else None

    def available(self) -> bool:
        """True if the configured DB exists and is non-empty."""
        try:
            return self.db_path.is_file() and self.db_path.stat().st_size > 0
        except OSError:
            return False

    def read(self) -> IngestCodeResult:
        """Open the DB read-only and translate every row into the typed graph."""
        if not self.db_path.is_file():
            raise FileNotFoundError(
                f"CodeGraph database not found at {self.db_path}. "
                f"Install with: npx @colbymchenry/codegraph init -i"
            )

        # Read-only URI so we never accidentally mutate CodeGraph's store.
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            raw_nodes = list(self._query_nodes(conn))
            file_count, language_count = self._query_file_stats(conn)
            edges_iter = self._query_edges(conn)

            builder = ResearchGraphBuilder()
            project_node: Optional[ResearchNode] = None
            if self.project_root is not None:
                project_node = builder.add_node(
                    self.project_root.name,
                    ResearchNodeType.CODE_PROJECT,
                    description=f"Code project at {self.project_root}",
                    source_path=str(self.project_root),
                    metadata={"layer": "code-project", "backend": "codegraph"},
                )

            # codegraph_id → ResearchNode, so we can wire up edges.
            id_to_node: Dict[str, ResearchNode] = {}
            for raw in raw_nodes:
                node = self._add_node(builder, raw)
                if node is not None:
                    id_to_node[raw.id] = node
                    if project_node is not None and raw.kind == "file":
                        builder.add_edge(project_node, "contains", node)

            skipped_edges = 0
            for source_id, target_id, kind in edges_iter:
                tesserae_kind = _EDGE_KIND_MAP.get(kind)
                if tesserae_kind is None:
                    # Schema drift: a newer CodeGraph release added a kind
                    # we don't know about. Drop it (logged) so the rest of
                    # the import keeps working.
                    logger.warning(
                        "code_graph_adapter: unknown edge kind %r; dropping", kind
                    )
                    skipped_edges += 1
                    continue
                src = id_to_node.get(source_id)
                tgt = id_to_node.get(target_id)
                if src is None or tgt is None:
                    # Dangling reference (e.g. unresolved external symbol).
                    skipped_edges += 1
                    continue
                builder.add_edge(src, tesserae_kind, tgt)

            # Synthesize ``declared_in`` so feature H's path-anchored
            # navigation still works when callers ask "where is X
            # declared?". CodeGraph stores this only as ``contains``
            # edges from file/class/namespace to the child symbol.
            self._synthesize_declared_in(builder, id_to_node, raw_nodes)

            graph = builder.build()
            return IngestCodeResult(
                graph=graph,
                processed_files=file_count,
                skipped_dirs=0,
                nodes=len(graph.nodes),
                edges=len(graph.edges),
                languages=language_count,
                skipped_edges=skipped_edges,
            )
        finally:
            conn.close()

    # -- internals ----------------------------------------------------------

    def _query_nodes(self, conn: sqlite3.Connection) -> Iterator[_RawNode]:
        cur = conn.execute(
            """
            SELECT id, kind, name, qualified_name, file_path, language,
                   start_line, end_line
            FROM nodes
            ORDER BY file_path, start_line, qualified_name
            """
        )
        for row in cur:
            yield _RawNode(
                id=row["id"],
                kind=row["kind"] or "",
                name=row["name"] or "",
                qualified_name=row["qualified_name"] or row["name"] or "",
                file_path=row["file_path"] or "",
                language=row["language"] or "",
                start_line=int(row["start_line"] or 0),
                end_line=int(row["end_line"] or 0),
            )

    def _query_edges(self, conn: sqlite3.Connection) -> Iterator[Tuple[str, str, str]]:
        cur = conn.execute(
            """
            SELECT source, target, kind FROM edges ORDER BY id
            """
        )
        for row in cur:
            yield (row["source"], row["target"], row["kind"] or "")

    def _query_file_stats(self, conn: sqlite3.Connection) -> Tuple[int, int]:
        # ``files`` is optional — old DBs / partial syncs may have only
        # the ``nodes`` table populated. Fall back to deriving counts
        # from the nodes table when files is empty.
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT language) AS l FROM files"
            ).fetchone()
            if row is not None and (row["n"] or 0) > 0:
                return int(row["n"]), int(row["l"] or 0)
        except sqlite3.OperationalError:
            pass
        row = conn.execute(
            "SELECT COUNT(DISTINCT file_path) AS n, COUNT(DISTINCT language) AS l FROM nodes"
        ).fetchone()
        return int(row["n"] or 0), int(row["l"] or 0)

    def _add_node(
        self,
        builder: ResearchGraphBuilder,
        raw: _RawNode,
    ) -> Optional[ResearchNode]:
        node_type = _KIND_TO_NODE_TYPE.get(raw.kind, ResearchNodeType.CODE_SYMBOL)
        # Reseed the id so it survives line-shifting refactors. We pin on
        # (file, kind, qualified_name) — line numbers and CodeGraph's
        # ``provenance`` column intentionally excluded. ``qualified_name``
        # falls back to ``name`` for the rare file/module rows where the
        # extractor leaves it empty.
        seed_qn = raw.qualified_name or raw.name or raw.id
        id_seed = f"{raw.file_path}:{node_type.value}:{seed_qn}"
        # ``name`` mirrors qualified_name verbatim so feature H's bare-
        # identifier index (``foo``) AND its dotted-path index
        # (``A.foo``) both find the right node. The qualified form is
        # dotted-only per CodeGraph's buildQualifiedName (schema.md §4).
        display_name = raw.qualified_name or raw.name or raw.id
        metadata: Dict[str, object] = {
            "codegraph_kind": raw.kind,
            "codegraph_id": raw.id,
            "language": raw.language,
            "qualified_name": raw.qualified_name,
            "start_line": raw.start_line,
            "end_line": raw.end_line,
            "layer": "code-graph",
            "backend": "codegraph",
        }
        return builder.add_node(
            display_name,
            node_type,
            description="",
            source_path=raw.file_path or None,
            metadata=metadata,
            id_seed=id_seed,
        )

    def _synthesize_declared_in(
        self,
        builder: ResearchGraphBuilder,
        id_to_node: Dict[str, ResearchNode],
        raw_nodes: Iterable[_RawNode],
    ) -> None:
        """Mint file → symbol ``declared_in`` edges from raw nodes' file_path.

        Cheaper than re-querying ``contains`` edges and matches the
        feature-A invariant: every CodeClass / CodeFunction / CodeMethod
        in the graph has a ``declared_in`` edge pointing at its
        CodeFile (the path-anchored counterpart to ``contains``).
        """
        file_index: Dict[str, ResearchNode] = {}
        for raw in raw_nodes:
            if raw.kind == "file":
                node = id_to_node.get(raw.id)
                if node is not None:
                    # Key on the file_path the file row itself reports —
                    # other rows reference the same path string.
                    file_index[raw.file_path] = node
        if not file_index:
            return
        linkable_kinds = {"class", "function", "method", "interface", "trait", "struct", "enum"}
        for raw in raw_nodes:
            if raw.kind not in linkable_kinds:
                continue
            sym = id_to_node.get(raw.id)
            file_node = file_index.get(raw.file_path)
            if sym is None or file_node is None:
                continue
            builder.add_edge(sym, "declared_in", file_node)


# ---------------------------------------------------------------------------
# Module-level convenience: mirrors code_graph_extractor.write_code_graph
# so callers can swap producers without rewriting their dispatchers.
# ---------------------------------------------------------------------------


def _default_codegraph_db(project_root: Path) -> Path:
    """Canonical CodeGraph store location, per schema.md §6."""
    return Path(project_root) / ".codegraph" / "codegraph.db"


def _run_codegraph_sync(project_root: Path) -> bool:
    """Best-effort ``codegraph sync <root>`` invocation.

    Silent skip when the ``codegraph`` binary is missing — Tesserae must
    not hard-fail when the optional CLI isn't installed. Returns True on
    a clean exit, False otherwise.
    """
    try:
        proc = subprocess.run(
            ["codegraph", "sync", str(project_root)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        logger.warning(
            "code_graph_adapter: `codegraph` binary not found on PATH; "
            "skipping pre-sync. Install with: npx @colbymchenry/codegraph init -i"
        )
        return False
    if proc.returncode != 0:
        logger.warning(
            "code_graph_adapter: `codegraph sync` exited %s: %s",
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""],
        )
        return False
    return True


def write_code_graph_from_codegraph(
    db_path: Path,
    output_path: Path,
    *,
    project_root: Optional[Path] = None,
) -> IngestCodeResult:
    """Read ``db_path``, translate, and persist to ``output_path`` atomically."""
    adapter = CodeGraphAdapter(db_path, project_root=project_root)
    result = adapter.read()

    # PID + random suffix so concurrent sync-code invocations don't
    # collide on a shared ``.tmp`` (same pattern as
    # ``tesserae/batch.py::_write_manifest``).
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(
        output_path.suffix + f".tmp.{os.getpid()}.{secrets.token_hex(4)}"
    )
    try:
        tmp.write_text(
            result.graph.to_json(indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, output_path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return result


__all__ = [
    "CodeGraphAdapter",
    "IngestCodeResult",
    "_default_codegraph_db",
    "_run_codegraph_sync",
    "write_code_graph_from_codegraph",
]
