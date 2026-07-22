"""Connection-discovery ("associate") pass for the engine sleep cycle.

Covers the four surfaces of :mod:`tesserae.memory.associate`:
discovery (intra-project + cross-agent, honest stub skip, deterministic),
persistence (accumulate + dedup + byte-stable), the read-time overlay merge
(edges-only, never mutates graph.json), and the never-raises daemon entrypoint.

Run: /Users/neo/Developer/Projects/Tesserae/.venv/bin/python -m pytest \
        tests/test_associate.py tests/test_federation.py -q
"""

from __future__ import annotations

from tesserae import federation as F
from tesserae.memory import associate as A
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


class _FakeBackend:
    """Deterministic orthogonal-unit embeddings keyed on content (no model2vec).

    ``add_semantic_links`` embeds ``"{name}. {description}"`` so the keywords can
    live in either field. Being a distinct class (not ``HashEmbeddingBackend``)
    is what satisfies the real-backend gate.
    """

    name = "fake-associate"

    def embed(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            if "pagerank" in tl or "ppr" in tl:
                out.append([1.0, 0.0, 0.0])
            elif "banana" in tl:
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return out


def _concept_graph(nodes):
    return ResearchGraph(
        nodes=[
            ResearchNode(id=i, name=n, type=ResearchNodeType.CONCEPT, description=d)
            for i, n, d in nodes
        ],
        edges=[],
    )


# --- (1) discovery -------------------------------------------------------- #

def test_discover_links_intra_project_bridges_related_nodes():
    g = _concept_graph([
        ("Concept:ppr:1", "Personalized PageRank", "ranking"),
        ("Concept:ppr:2", "PPR variant", "another pagerank node"),
        ("Concept:ban", "Banana", "fruit"),
    ])
    links = A.discover_links(g, backend=_FakeBackend(), min_cosine=0.5)
    # the two PPR concepts bridge (cosine 1.0); banana matches nothing.
    assert links == [("Concept:ppr:1", "Concept:ppr:2", 1.0)]


def test_discover_links_is_deterministic():
    g = _concept_graph([
        ("Concept:ppr:1", "Personalized PageRank", "ranking"),
        ("Concept:ppr:2", "PPR variant", "pagerank"),
        ("Concept:ban", "Banana", "fruit"),
    ])
    a = A.discover_links(g, backend=_FakeBackend(), min_cosine=0.5)
    b = A.discover_links(g, backend=_FakeBackend(), min_cosine=0.5)
    assert a == b


def test_discover_links_cross_agent_excludes_same_agent():
    base = _concept_graph([("Concept:banana", "Banana", "fruit")])
    agent_a = _concept_graph([
        ("Concept:ppr", "Personalized PageRank", "ranking"),
        ("Concept:ppr2", "PPR again", "pagerank variant"),
    ])
    agent_b = _concept_graph([("Concept:ppr", "PPR algorithm", "pagerank ranking")])
    links = A.discover_links(
        base, backend=_FakeBackend(),
        agents=[("agentA", agent_a), ("agentB", agent_b)], min_cosine=0.5,
    )
    pairs = {(s, t) for s, t, _ in links}
    # different agents' related notes bridge...
    assert ("agentA::Concept:ppr", "agentB::Concept:ppr") in pairs
    assert ("agentA::Concept:ppr2", "agentB::Concept:ppr") in pairs
    # ...same-agent notes NEVER do (shared provenance alias).
    assert ("agentA::Concept:ppr", "agentA::Concept:ppr2") not in pairs
    # banana (base) matches no agent note.
    assert not any("banana" in s.lower() or "banana" in t.lower() for s, t in pairs)


def test_discover_links_skips_hash_stub():
    from tesserae.retrieval.hybrid import HashEmbeddingBackend

    g = _concept_graph([
        ("Concept:a", "Alpha PPR", "pagerank"),
        ("Concept:b", "Beta PPR", "pagerank"),
    ])
    # stub backend → similarities are noise → honest no-op, no links.
    assert A.discover_links(g, backend=HashEmbeddingBackend()) == []


def test_add_semantic_links_scope_intra_vs_cross_default():
    """The federation change is opt-in: default scope stays byte-behavior."""
    g = _concept_graph([
        ("Concept:a", "Alpha PPR", "pagerank"),
        ("Concept:b", "Beta PPR", "pagerank"),
    ])
    # default cross scope: no provenance → no links (federation invariant intact).
    _, s_cross = F.add_semantic_links(g, backend=_FakeBackend(), min_cosine=0.5)
    assert s_cross["semantic_added"] == 0
    # intra scope: empty-provenance intra-project nodes DO link.
    out, s_intra = F.add_semantic_links(g, backend=_FakeBackend(), min_cosine=0.5, scope="intra")
    assert s_intra["semantic_added"] == 1
    assert [(e.source, e.target) for e in out.edges if e.type == "shares_concept_with"] == [
        ("Concept:a", "Concept:b")
    ]


# --- (2) persistence — accumulate + dedup + byte-stable ------------------- #

def test_persist_links_accumulates_and_dedups(tmp_path):
    assert A.persist_links(tmp_path, [("a", "b", 0.9)]) == 1
    assert A.persist_links(tmp_path, [("c", "d", 0.8)]) == 2  # accumulate across cycles
    assert A.persist_links(tmp_path, [("a", "b", 0.9)]) == 2  # dedup, no growth
    raw = A._load_overlay_raw(tmp_path)
    assert sorted((s, t) for s, t, _ in raw) == [("a", "b"), ("c", "d")]


def test_persist_links_is_byte_stable(tmp_path):
    A.persist_links(tmp_path, [("a", "b", 0.9), ("c", "d", 0.8)])
    b1 = A._overlay_path(tmp_path).read_bytes()
    A.persist_links(tmp_path, [("c", "d", 0.8), ("a", "b", 0.9)])  # different arg order
    b2 = A._overlay_path(tmp_path).read_bytes()
    assert b1 == b2  # sorted + compact → order-independent, byte-identical


def test_persist_links_dedup_keeps_higher_cosine(tmp_path):
    A.persist_links(tmp_path, [("a", "b", 0.6)])
    A.persist_links(tmp_path, [("a", "b", 0.9)])
    assert A._load_overlay_raw(tmp_path) == [["a", "b", 0.9]]


def test_persist_links_skips_self_links(tmp_path):
    assert A.persist_links(tmp_path, [("a", "a", 1.0)]) == 0
    assert A._load_overlay_raw(tmp_path) == []


# --- (3) read-time overlay merge — in-memory, never graph.json ------------ #

def test_load_overlay_edges_shape(tmp_path):
    A.persist_links(tmp_path, [("Concept:a", "Concept:b", 0.9)])
    edges = A.load_overlay_edges(tmp_path)
    assert len(edges) == 1
    e = edges[0]
    assert e.type == "shares_concept_with"
    assert e.metadata["federation_semantic"] is True
    assert e.metadata["associate_overlay"] is True
    assert e.metadata["cosine"] == 0.9


def test_apply_overlay_adds_only_edges_and_skips_absent_endpoints(tmp_path):
    g = _concept_graph([("Concept:a", "Alpha", "x"), ("Concept:b", "Beta", "y")])
    A.persist_links(tmp_path, [
        ("Concept:a", "Concept:b", 0.9),
        ("Concept:a", "Concept:ghost", 0.7),  # endpoint not in the graph
    ])
    merged = A.apply_overlay(tmp_path, g)
    assert len(merged.nodes) == len(g.nodes)  # node count unchanged — edges only
    sc = [(e.source, e.target) for e in merged.edges if e.type == "shares_concept_with"]
    assert sc == [("Concept:a", "Concept:b")]  # ghost-endpoint link skipped
    assert len(g.edges) == 0  # input graph never mutated


def test_apply_overlay_no_overlay_returns_same_instance(tmp_path):
    g = _concept_graph([("Concept:a", "Alpha", "x")])
    assert A.apply_overlay(tmp_path, g) is g  # nothing to add → identical object


def test_apply_overlay_dedups_existing_edge(tmp_path):
    g = ResearchGraph(
        nodes=[
            ResearchNode(id="Concept:a", name="A", type=ResearchNodeType.CONCEPT),
            ResearchNode(id="Concept:b", name="B", type=ResearchNodeType.CONCEPT),
        ],
        edges=[ResearchEdge(source="Concept:a", target="Concept:b", type="shares_concept_with")],
    )
    A.persist_links(tmp_path, [("Concept:a", "Concept:b", 0.9)])
    merged = A.apply_overlay(tmp_path, g)
    sc = [e for e in merged.edges if e.type == "shares_concept_with"]
    assert len(sc) == 1  # already present → not duplicated


def test_corrupt_overlay_is_ignored_not_poison(tmp_path):
    path = A._overlay_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"not": "a triple list"}', encoding="utf-8")  # valid JSON, wrong shape
    assert A.load_overlay_edges(tmp_path) == []  # treated as a miss, never raises
    g = _concept_graph([("Concept:a", "Alpha", "x")])
    assert A.apply_overlay(tmp_path, g) is g  # safe no-op
    assert A.persist_links(tmp_path, [("Concept:a", "Concept:b", 0.9)]) == 1  # recovers


# --- (4) daemon entrypoint — discover → persist, never raises ------------- #

def test_consolidate_never_touches_graph_json(tmp_path):
    tess = tmp_path / ".tesserae"
    tess.mkdir()
    g = _concept_graph([
        ("Concept:ppr:1", "Personalized PageRank", "ranking"),
        ("Concept:ppr:2", "PPR variant", "pagerank"),
    ])
    gjson = tess / "graph.json"
    gjson.write_text(g.to_json(), encoding="utf-8")
    before = gjson.read_bytes()

    stats = A.consolidate_associations(tmp_path, g, backend=_FakeBackend(), min_cosine=0.5)
    assert stats["associate_added"] == 1
    assert gjson.read_bytes() == before  # graph.json byte-identical
    assert A._overlay_path(tmp_path).is_file()  # discovered link went to the overlay


def test_consolidate_is_idempotent(tmp_path):
    g = _concept_graph([
        ("Concept:ppr:1", "Personalized PageRank", "ranking"),
        ("Concept:ppr:2", "PPR variant", "pagerank"),
    ])
    A.consolidate_associations(tmp_path, g, backend=_FakeBackend(), min_cosine=0.5)
    b1 = A._overlay_path(tmp_path).read_bytes()
    A.consolidate_associations(tmp_path, g, backend=_FakeBackend(), min_cosine=0.5)
    b2 = A._overlay_path(tmp_path).read_bytes()
    assert b1 == b2  # unchanged input → overlay byte-identical


def test_consolidate_skips_stub_backend(tmp_path):
    from tesserae.retrieval.hybrid import HashEmbeddingBackend

    g = _concept_graph([
        ("Concept:a", "Alpha PPR", "pagerank"),
        ("Concept:b", "Beta PPR", "pagerank"),
    ])
    stats = A.consolidate_associations(tmp_path, g, backend=HashEmbeddingBackend())
    assert stats["associate_added"] == 0
    assert "associate_skipped" in stats
    assert not A._overlay_path(tmp_path).is_file()  # nothing written on a stub


def test_consolidate_never_raises_on_backend_error(tmp_path):
    class _BoomBackend:
        name = "boom"

        def embed(self, texts):
            raise RuntimeError("embedding exploded")

    g = _concept_graph([
        ("Concept:a", "Alpha PPR", "pagerank"),
        ("Concept:b", "Beta PPR", "pagerank"),
    ])
    stats = A.consolidate_associations(tmp_path, g, backend=_BoomBackend())
    assert stats["associate_added"] == 0
    assert "associate_error" in stats  # swallowed, never propagated into the daemon
