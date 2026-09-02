"""Per-document mention density for a node, and the ranked node -> documents read.

``node_provenance`` answers "which documents mention this entity" but says
nothing about WHICH of them elaborates it. Measured on 2026-09-02: a bridge
entity's provenance set holds the elaborating document ~60% of the time, yet
ranking that set converts to a hit only ~10% of the time, because every
document in it looks identical from the sidecar. ``Model:BERT`` seen in 30
papers cannot be told apart without a count.

These tests pin the count (word-boundary, aliases included, synthetic sources
skipped), the ranked read, and the invariant that keeps the two sidecars from
drifting: the reader is driven by ``node_provenance``, so a mention row whose
pair is gone can never surface, and a provenance row with no mention row still
appears with 0.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tesserae.graph_stores.sqlite import SqliteGraphStore
from tesserae.project import ProjectWiki, compute_mention_density
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

TS = "det:0000000000000000"


def _node(node_id: str, name: str, *, aliases=(), type=ResearchNodeType.MODEL) -> ResearchNode:
    return ResearchNode(id=node_id, name=name, type=type, aliases=list(aliases))


# --------------------------------------------------------------------------- #
# compute_mention_density
# --------------------------------------------------------------------------- #


def test_counts_name_and_aliases_per_document(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    # BERT three times in a (one as an alias form), once in b.
    a.write_text("BERT is used. We fine-tune BERT.\nThe bert encoder helps.", encoding="utf-8")
    b.write_text("We compare against BERT only here.", encoding="utf-8")
    graph = ResearchGraph(nodes=[_node("Model:bert", "BERT")], edges=[])
    rows = compute_mention_density(
        [("Model:bert", str(a), TS), ("Model:bert", str(b), TS)], graph
    )
    assert dict(((n, s), m) for n, s, m in rows) == {
        ("Model:bert", str(a)): 3,
        ("Model:bert", str(b)): 1,
    }


def test_counts_are_word_bounded_and_include_aliases(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    # "BERTology" and "ALBERT" must NOT count; the alias "BERT-base" must.
    doc.write_text(
        "BERTology studies ALBERT. BERT-base is the alias. BERT itself appears once.",
        encoding="utf-8",
    )
    graph = ResearchGraph(nodes=[_node("Model:bert", "BERT", aliases=["BERT-base"])], edges=[])
    rows = compute_mention_density([("Model:bert", str(doc), TS)], graph)
    assert rows == [("Model:bert", str(doc), 2)]


def test_a_name_overlapping_its_own_alias_counts_the_place_once(tmp_path: Path) -> None:
    """``-`` is a word boundary, so ``BERT`` also matches inside ``BERT-base``.

    Summing per-pattern hits would score that single mention twice and rank a
    document by how many aliases a node happens to carry.
    """
    doc = tmp_path / "doc.md"
    doc.write_text("BERT-base appears exactly once.", encoding="utf-8")
    graph = ResearchGraph(nodes=[_node("Model:bert", "BERT", aliases=["BERT-base"])], edges=[])
    assert compute_mention_density([("Model:bert", str(doc), TS)], graph) == [
        ("Model:bert", str(doc), 1)
    ]


def test_synthetic_sources_and_unknown_nodes_are_skipped(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("BERT appears here.", encoding="utf-8")
    graph = ResearchGraph(nodes=[_node("Model:bert", "BERT")], edges=[])
    rows = compute_mention_density(
        [
            ("Model:bert", "__synthesis__", TS),      # generated layer: no file
            ("Model:bert", "__session__", TS),        # producer-minted: no file
            ("Model:ghost", str(doc), TS),            # not in the graph
            ("Model:bert", str(tmp_path / "gone.md"), TS),  # unreadable
            ("Model:bert", str(doc), TS),
        ],
        graph,
    )
    assert rows == [("Model:bert", str(doc), 1)]


def test_a_node_whose_name_is_too_short_to_match_is_skipped(tmp_path: Path) -> None:
    """A 1-2 character name matches everywhere and ranks nothing.

    ``AI`` in a corpus about AI is in every document at every density, so its
    count carries no ordering information and costs a scan to produce.
    """
    doc = tmp_path / "doc.md"
    doc.write_text("AI AI AI everywhere.", encoding="utf-8")
    graph = ResearchGraph(nodes=[_node("Concept:ai", "AI")], edges=[])
    assert compute_mention_density([("Concept:ai", str(doc), TS)], graph) == []


def test_rows_are_deterministic_and_sorted(tmp_path: Path) -> None:
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    for p in (a, b):
        p.write_text("BERT and GPT appear.", encoding="utf-8")
    graph = ResearchGraph(
        nodes=[_node("Model:bert", "BERT"), _node("Model:gpt", "GPT")], edges=[]
    )
    pairs = [("Model:gpt", str(b), TS), ("Model:bert", str(b), TS),
             ("Model:gpt", str(a), TS), ("Model:bert", str(a), TS)]
    first = compute_mention_density(pairs, graph)
    assert first == compute_mention_density(list(reversed(pairs)), graph)
    assert first == sorted(first)


# --------------------------------------------------------------------------- #
# The store surface
# --------------------------------------------------------------------------- #


def _store(tmp_path: Path) -> SqliteGraphStore:
    return SqliteGraphStore(tmp_path / "graph.sqlite")


def test_node_documents_ranks_by_mentions_then_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_provenance_many(
        [("n", "a.md", TS), ("n", "b.md", TS), ("n", "c.md", TS)]
    )
    store.record_mentions_many([("n", "a.md", 2), ("n", "b.md", 9), ("n", "c.md", 2)])
    assert store.node_documents("n") == [("b.md", 9), ("a.md", 2), ("c.md", 2)]
    assert store.node_documents("n", limit=1) == [("b.md", 9)]
    assert store.node_documents("absent") == []


def test_a_provenance_row_with_no_mention_row_still_appears_with_zero(tmp_path: Path) -> None:
    """Ranking must never silently drop a document the sidecar knows about."""
    store = _store(tmp_path)
    store.record_provenance_many([("n", "a.md", TS), ("n", "b.md", TS)])
    store.record_mentions_many([("n", "a.md", 4)])
    assert store.node_documents("n") == [("a.md", 4), ("b.md", 0)]


def test_a_stale_mention_row_can_never_surface(tmp_path: Path) -> None:
    """The reader is driven by provenance, so the two sidecars cannot drift.

    A recompile that drops (n, b.md) from provenance leaves b.md's mention row
    behind; without the join it would keep ranking a document that no longer
    contains the node.
    """
    store = _store(tmp_path)
    store.record_provenance_many([("n", "a.md", TS), ("n", "b.md", TS)])
    store.record_mentions_many([("n", "a.md", 1), ("n", "b.md", 99)])
    store.reconcile_provenance([("n", "a.md", TS)], [])
    assert store.node_documents("n") == [("a.md", 1)]


def test_record_mentions_many_is_idempotent_and_updates_counts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_provenance_many([("n", "a.md", TS)])
    store.record_mentions_many([("n", "a.md", 3)])
    store.record_mentions_many([("n", "a.md", 3)])
    assert store.node_documents("n") == [("a.md", 3)]
    store.record_mentions_many([("n", "a.md", 7)])          # the file grew
    assert store.node_documents("n") == [("a.md", 7)]
    with store._connect() as con:
        assert con.execute("select count(*) from node_mentions").fetchone()[0] == 1


def test_an_old_database_without_the_table_gains_it(tmp_path: Path) -> None:
    """The sidecar is additive: an existing sqlite.db must not need a migration.

    Models the upgrade exactly — a database written by a version that had
    provenance but no counts, reopened by this one.
    """
    db = tmp_path / "graph.sqlite"
    old = SqliteGraphStore(db)
    old.record_provenance_many([("n", "a.md", TS)])
    with old._connect() as con:
        con.execute("drop table node_mentions")
        con.commit()
        assert con.execute(
            "select name from sqlite_master where type='table' and name='node_mentions'"
        ).fetchone() is None

    store = SqliteGraphStore(db)                     # reopened by this version
    assert store.node_documents("n") == [("a.md", 0)]
    store.record_mentions_many([("n", "a.md", 2)])
    assert store.node_documents("n") == [("a.md", 2)]


# --------------------------------------------------------------------------- #
# Through a real compile
# --------------------------------------------------------------------------- #


#: A term the DETERMINISTIC extractor's registry knows, so this fixture needs
#: no LLM and still produces a real cross-document concept node.
_TERM = "Gaussian Splatting"


def _corpus(root: Path) -> None:
    """Two documents naming the same concept: b elaborates it, a mentions it once."""
    docs = root / "data" / "research" / "daily" / "2026-05-01" / "papers"
    for name, body in (
        ("a", "# Paper A\n\n> - arxiv: https://arxiv.org/abs/2604.50000\n\n"
              f"We compare against {_TERM} once.\n"),
        ("b", "# Paper B\n\n> - arxiv: https://arxiv.org/abs/2604.50001\n\n"
              f"{_TERM} is our subject. We analyse {_TERM} in depth, "
              f"and evaluate {_TERM} again.\n"),
    ):
        d = docs / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "paper.md").write_text(body, encoding="utf-8")


def test_a_compile_writes_mention_rows_and_ranks_the_elaborating_document(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _corpus(root)
    wiki = ProjectWiki.init(root, name="mentions_test")
    wiki.compile()

    store = SqliteGraphStore(wiki.paths.sqlite)
    graph = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    term = [n for n in graph["nodes"] if n["name"].casefold() == _TERM.casefold()]
    assert term, f"fixture no longer produces a {_TERM} node"

    ranked = store.node_documents(term[0]["id"])
    assert len(ranked) == 2, f"expected both papers in the ranking, got {ranked}"
    # b says it three times, a once — the elaborating document ranks first.
    assert Path(ranked[0][0]).parent.name == "b"
    assert Path(ranked[1][0]).parent.name == "a"
    assert ranked[0][1] > ranked[1][1] >= 1
