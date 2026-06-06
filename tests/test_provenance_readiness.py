"""Plan 04.1-02 unit tests: edge-aware readiness, full-compile reconcile,
all-producer provenance coverage, orphan-vault filtering, and the graph.json
sidecar-only invariant.

All tests are deterministic (``tmp_path``, fixed corpus, no wall-clock, no
network). They exercise the four blockers Plan 02 closes in
``tesserae/project.py``:

* #1 reconcile-on-full — a full compile REPLACES the provenance row-set, so a
  stale ``(node, file)`` row for a still-live cross-file node is deleted while
  ``first_seen_at`` is preserved.
* #2/#5 edge-aware readiness — both ``_sqlite_provenance_ready`` and
  ``_provenance_ready`` require edge coverage + the full edge-aware surface.
* #6 all-producer provenance — every final-graph node/edge carries >=1 sidecar
  provenance row after a compile with producers enabled.
* #4 orphan-prune ordering — a vault page whose ``node_id`` is absent from the
  live graph contributes no override.

The graph.json sidecar-only guard asserts no provenance / ``__producer__``
marker leaks into the written canonical artifact.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

import pytest

import tesserae.project as project_mod
from tesserae.project import ProjectWiki
from tesserae.graph_stores.sqlite import SqliteGraphStore


# --------------------------------------------------------------------------- #
# Community-summary client pin (mirror test_incremental_parity) so compiles are
# deterministic even if TESSERAE_COMMUNITY_SUMMARIES is set in the environment.
# --------------------------------------------------------------------------- #
class _ScriptedCommunityClient:
    def complete_json(self, *, system, user, schema_name, cache_key=None):  # noqa: ANN001
        return {
            "title": "Cluster",
            "description": "Deterministic cluster summary fixed for testing.",
            "tags": ["readiness", "fixture"],
        }


@pytest.fixture(autouse=True)
def _pin_community_client():
    project_mod.set_community_summaries_test_client(_ScriptedCommunityClient())
    try:
        yield
    finally:
        project_mod.set_community_summaries_test_client(None)


# --------------------------------------------------------------------------- #
# Corpus helpers
# --------------------------------------------------------------------------- #
_SHARED_FIELD = "Compositional Scene Understanding"


def _paper_md(idx: int, *, mention_field: bool = True) -> str:
    arxiv = f"2604.5{idx:04d}"
    field_line = (
        f"This paper studies {_SHARED_FIELD} using the Transformer model.\n"
        if mention_field
        else "This paper studies an unrelated standalone topic only.\n"
    )
    return (
        f"# Paper {idx:03d}\n\n"
        f"> - arxiv: https://arxiv.org/abs/{arxiv}\n\n"
        f"저자: Ada Lovelace.\n\n"
        f"{field_line}"
        f"Local contribution numbered {idx:03d}.\n"
    )


def _build_corpus(root: Path, n_papers: int = 3) -> List[Path]:
    root.mkdir(parents=True, exist_ok=True)
    papers_root = root / "data" / "research" / "daily" / "2026-05-01" / "papers"
    files: List[Path] = []
    for idx in range(n_papers):
        arxiv = f"2604.5{idx:04d}"
        pdir = papers_root / arxiv
        pdir.mkdir(parents=True, exist_ok=True)
        pf = pdir / "paper.md"
        pf.write_text(_paper_md(idx), encoding="utf-8")
        files.append(pf)
    return files


def _seed_wiki(root: Path, *, incremental: bool = True) -> ProjectWiki:
    wiki = ProjectWiki.init(root, name="readiness_test")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["incremental_compile"] = incremental
    wiki.paths.config.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return wiki


def _graph_json(wiki: ProjectWiki) -> dict:
    return json.loads(wiki.paths.graph.read_text(encoding="utf-8"))


def _node_prov_rows(db: Path):
    with sqlite3.connect(str(db)) as con:
        return con.execute(
            "select node_id, source_path, first_seen_at from node_provenance"
        ).fetchall()


def _edge_prov_rows(db: Path):
    with sqlite3.connect(str(db)) as con:
        has = con.execute(
            "select name from sqlite_master where type='table' and name='edge_provenance'"
        ).fetchone()
        if has is None:
            return []
        return con.execute(
            "select source, type, target from edge_provenance"
        ).fetchall()


# --------------------------------------------------------------------------- #
# Fake injected stores for _provenance_ready surface checks
# --------------------------------------------------------------------------- #
class _FullSurfaceStore:
    """Exposes the COMPLETE edge-aware surface; coverage configurable."""

    def __init__(self, *, has_rows=True, covers_nodes=True, covers_edges=True):
        self._has_rows = has_rows
        self._covers_nodes = covers_nodes
        self._covers_edges = covers_edges

    def delete_nodes_by_source(self, *a, **k):  # noqa: ANN001
        return set()

    def record_provenance_many(self, *a, **k):  # noqa: ANN001
        return None

    def delete_nodes_by_source_with_edges(self, *a, **k):  # noqa: ANN001
        return set(), set()

    def record_edge_provenance_many(self, *a, **k):  # noqa: ANN001
        return None

    def provenance_covers_edges(self, *a, **k):  # noqa: ANN001
        return self._covers_edges

    def has_node_provenance_rows(self):
        return self._has_rows

    def provenance_covers_nodes(self, *a, **k):  # noqa: ANN001
        return self._covers_nodes


class _NodeOnlyStore:
    """Missing record_edge_provenance_many + provenance_covers_edges +
    delete_nodes_by_source_with_edges — must be rejected (#5)."""

    def delete_nodes_by_source(self, *a, **k):  # noqa: ANN001
        return set()

    def record_provenance_many(self, *a, **k):  # noqa: ANN001
        return None

    def has_node_provenance_rows(self):
        return True

    def provenance_covers_nodes(self, *a, **k):  # noqa: ANN001
        return True


# --------------------------------------------------------------------------- #
# #2: SQLite readiness rejects an edge-uncovered sidecar
# --------------------------------------------------------------------------- #
def test_sqlite_readiness_requires_edge_coverage(tmp_path: Path) -> None:
    db = tmp_path / "sqlite.db"
    store = SqliteGraphStore(db)
    # Node coverage present, edge_provenance EMPTY.
    store.record_provenance_many([("n1", "a.md", "det:aaa"), ("n2", "a.md", "det:bbb")])

    prior_nodes = ["n1", "n2"]
    prior_edges = [("n1", "rel", "n2")]

    # No edge coverage -> not ready.
    assert (
        ProjectWiki._sqlite_provenance_ready(db, prior_nodes, prior_edge_triples=prior_edges)
        is False
    )
    # Node-only readiness (no edge triples requested) still True.
    assert ProjectWiki._sqlite_provenance_ready(db, prior_nodes) is True

    # Add covering edge provenance -> ready.
    store.record_edge_provenance_many([("n1", "rel", "n2", "a.md", "det:ccc")])
    assert (
        ProjectWiki._sqlite_provenance_ready(db, prior_nodes, prior_edge_triples=prior_edges)
        is True
    )

    # Partially-covering edges (missing a triple) -> not ready.
    assert (
        ProjectWiki._sqlite_provenance_ready(
            db, prior_nodes, prior_edge_triples=[("n1", "rel", "n2"), ("n2", "rel", "n1")]
        )
        is False
    )


# --------------------------------------------------------------------------- #
# #5: injected-store readiness requires the full edge-aware surface
# --------------------------------------------------------------------------- #
def test_injected_store_requires_edge_surface() -> None:
    prior_nodes = ["n1"]
    prior_edges = [("n1", "rel", "n2")]

    # Node-only store: missing edge surface -> rejected even with no edge triples.
    assert ProjectWiki._provenance_ready(_NodeOnlyStore(), prior_nodes) is False

    full = _FullSurfaceStore()
    assert (
        ProjectWiki._provenance_ready(full, prior_nodes, prior_edge_triples=prior_edges)
        is True
    )

    # Full surface but edge coverage fails -> rejected.
    no_edge_cov = _FullSurfaceStore(covers_edges=False)
    assert (
        ProjectWiki._provenance_ready(
            no_edge_cov, prior_nodes, prior_edge_triples=prior_edges
        )
        is False
    )

    # Full surface but node coverage fails -> rejected.
    no_node_cov = _FullSurfaceStore(covers_nodes=False)
    assert (
        ProjectWiki._provenance_ready(
            no_node_cov, prior_nodes, prior_edge_triples=prior_edges
        )
        is False
    )

    # No provenance rows at all -> rejected.
    no_rows = _FullSurfaceStore(has_rows=False)
    assert (
        ProjectWiki._provenance_ready(no_rows, prior_nodes, prior_edge_triples=prior_edges)
        is False
    )


# --------------------------------------------------------------------------- #
# #1: full compile reconciles the row-set — stale source row deleted,
# first_seen_at preserved.
# --------------------------------------------------------------------------- #
def test_full_compile_reconcile_drops_stale_source_row(tmp_path: Path) -> None:
    """A FULL compile reconciles the exact row-set: a stale ``(node, b.md)`` row
    for a still-LIVE node (now owned only by a.md) is deleted, while the
    surviving ``(node, a.md)`` row keeps its original ``first_seen_at``.

    Exercises ``_record_provenance(full_compile=True)`` directly with a
    hand-built graph + real SqliteGraphStore so the assertion isolates the
    reconcile semantics from extractor heuristics.
    """
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

    db = tmp_path / "sqlite.db"
    store = SqliteGraphStore(db)

    node_id = "Concept:shared"
    a_md, b_md = "/corpus/a.md", "/corpus/b.md"

    # ---- Compile 1: node co-owned by a.md AND b.md (cross-file). ----
    graph1 = ResearchGraph(
        nodes=[
            ResearchNode(
                id=node_id, name="Shared", type=ResearchNodeType.CONCEPT, source_path=a_md
            )
        ],
        edges=[],
    )
    extraction_prov1 = (
        [(node_id, a_md, "det:seen-a"), (node_id, b_md, "det:seen-b")],
        [],
    )
    ProjectWiki._record_provenance(
        store, graph1, extraction_prov1, producer_prov=None, full_compile=True
    )

    rows1 = _node_prov_rows(db)
    by_src1 = {src: fs for (nid, src, fs) in rows1 if nid == node_id}
    assert set(by_src1) == {a_md, b_md}, "compile-1 should own the node from BOTH files"
    a_first_seen = by_src1[a_md]

    # ---- Compile 2 (FULL): b.md stopped contributing; node still LIVE via a.md. ----
    extraction_prov2 = ([(node_id, a_md, "det:seen-a2")], [])
    ProjectWiki._record_provenance(
        store, graph1, extraction_prov2, producer_prov=None, full_compile=True
    )

    rows2 = _node_prov_rows(db)
    by_src2 = {src: fs for (nid, src, fs) in rows2 if nid == node_id}

    # Stale (node, b.md) row GONE (false-keeper killed); (node, a.md) survives.
    assert b_md not in by_src2, "stale (node, b.md) row survived full-compile reconcile"
    assert a_md in by_src2, "live (node, a.md) row was wrongly deleted"
    # first_seen_at preserved on the surviving row.
    assert by_src2[a_md] == a_first_seen, "first_seen_at not preserved across reconcile"


def test_incremental_compile_does_not_reconcile(tmp_path: Path) -> None:
    """On an INCREMENTAL compile (full_compile=False) an unchanged-file row must
    NOT be deleted — reconcile is gated to full compiles only (Pitfall 4)."""
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

    db = tmp_path / "sqlite.db"
    store = SqliteGraphStore(db)

    node_id = "Concept:shared"
    a_md, b_md = "/corpus/a.md", "/corpus/b.md"

    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id=node_id, name="Shared", type=ResearchNodeType.CONCEPT, source_path=a_md
            )
        ],
        edges=[],
    )
    # Seed both files (full).
    ProjectWiki._record_provenance(
        store,
        graph,
        ([(node_id, a_md, "det:a"), (node_id, b_md, "det:b")], []),
        producer_prov=None,
        full_compile=True,
    )
    # Incremental run extracting ONLY a.md: must NOT delete the b.md row, because
    # the node is still in the final graph (prune keeps it).
    ProjectWiki._record_provenance(
        store,
        graph,
        ([(node_id, a_md, "det:a2")], []),
        producer_prov=None,
        full_compile=False,
    )
    rows = _node_prov_rows(db)
    sources = {src for (nid, src, _fs) in rows if nid == node_id}
    assert sources == {a_md, b_md}, (
        "incremental compile wrongly deleted the unchanged-file (b.md) row "
        "(reconcile must be full-only)"
    )


# --------------------------------------------------------------------------- #
# #6: every final-graph node and edge carries >=1 sidecar provenance row after
# a compile with the session producer enabled.
# --------------------------------------------------------------------------- #
def test_all_producers_have_provenance_coverage(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _build_corpus(root, n_papers=3)
    wiki = _seed_wiki(root)
    wiki.compile()

    db = wiki.paths.sqlite
    graph = _graph_json(wiki)
    node_ids = {n["id"] for n in graph.get("nodes", [])}

    covered_nodes = {nid for (nid, _src, _ts) in _node_prov_rows(db)}
    uncovered = node_ids - covered_nodes
    assert not uncovered, f"final-graph nodes with NO provenance row: {sorted(uncovered)}"

    # Edge coverage: every research-graph edge triple has an edge_provenance row.
    edge_triples = {
        (e["source"], e["type"], e["target"]) for e in graph.get("edges", [])
    }
    covered_edges = set(_edge_prov_rows(db))
    uncovered_edges = edge_triples - covered_edges
    assert not uncovered_edges, (
        f"final-graph edges with NO provenance row: {sorted(uncovered_edges)}"
    )


# --------------------------------------------------------------------------- #
# #4: an orphan vault page (node_id not in the live graph) contributes no
# override.
# --------------------------------------------------------------------------- #
def test_orphan_vault_page_contributes_no_override(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _build_corpus(root, n_papers=3)
    wiki = _seed_wiki(root)
    wiki.compile()  # seed: vault projected, snapshot written

    vault = wiki.effective_obsidian_vault()
    if not vault.exists():
        pytest.skip("obsidian vault not projected in this configuration")

    graph = _graph_json(wiki)
    live_titles = {n.get("name") for n in graph.get("nodes", [])}

    # Write an ORPHAN page carrying a node_id absent from the live graph, whose
    # title would (if honored) mutate a node.
    orphan = vault / "ORPHAN-PAGE.md"
    orphan.write_text(
        "---\n"
        "node_id: Concept:this-id-does-not-exist-in-graph\n"
        "title: HIJACKED ORPHAN TITLE\n"
        "type: Concept\n"
        "---\n\n"
        "Body of an orphan page.\n",
        encoding="utf-8",
    )

    # Recompile: the orphan must be filtered BEFORE compute_overrides.
    wiki.compile(changed_only=True, changed_paths=[])
    graph_after = _graph_json(wiki)
    titles_after = {n.get("name") for n in graph_after.get("nodes", [])}

    assert "HIJACKED ORPHAN TITLE" not in titles_after, (
        "orphan vault page injected a node name override"
    )
    # Live node titles unchanged.
    assert live_titles <= titles_after | {None}


# --------------------------------------------------------------------------- #
# graph.json sidecar-only invariant: no provenance / __producer__ leakage.
# --------------------------------------------------------------------------- #
def test_graph_json_has_no_provenance_or_producer_markers(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _build_corpus(root, n_papers=3)
    wiki = _seed_wiki(root)
    wiki.compile()

    raw = wiki.paths.graph.read_text(encoding="utf-8")
    for marker in (
        "__producer__",
        "__session_graph__",
        "__code_graph__",
        "__understand_anything__",
        "__raganything__",
        "__vault_overlay__",
        "node_provenance",
        "edge_provenance",
        "first_seen_at",
        "provenance",
    ):
        assert marker not in raw, f"leaked sidecar marker {marker!r} into graph.json"


# --------------------------------------------------------------------------- #
# Byte-idempotence guard: two identical full compiles -> identical graph.json.
# --------------------------------------------------------------------------- #
def test_two_full_compiles_byte_identical_graph_json(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _build_corpus(root, n_papers=3)
    wiki = _seed_wiki(root)

    wiki.compile()
    first = wiki.paths.graph.read_text(encoding="utf-8")
    wiki.compile()
    second = wiki.paths.graph.read_text(encoding="utf-8")
    assert first == second, "graph.json not byte-identical across two full compiles"
