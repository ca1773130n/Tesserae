"""0.42 — open vocabulary, lenient load, edge confidence, content resolver.

One module per release theme, covering T1-T10 of the 0.42 plan. Every test
resets the process type registry, because the registry is process-global by
design (a host registers its types once at startup).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import pytest

from tesserae.agent_write import record_agent_write, replay_agent_writes, validate_write
from tesserae.context_compiler import citation_dict, compile_context
from tesserae.federation import federate_graphs, identity_key
from tesserae.graph_filters import suppressed_ids
from tesserae.llm_extractor import GraphJSONValidationError
from tesserae.ports import (
    ContentResolver,
    ContentView,
    GraphStore,
    store_iterate_edges,
    store_neighbors,
)
from tesserae.project import load_graph_file
from tesserae.research_graph import (
    EXTRACTABLE_EDGE_TYPES,
    PRODUCER_ONLY_EDGE_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    extractable_edge_types,
    graph_from_payload,
)
from tesserae.retrieval.hybrid import HashEmbeddingBackend
from tesserae.retrieval.ppr import personalized_pagerank
from tesserae.schema_drift import (
    analyze_schema_drift,
    apply_ledger_to_registry,
    cluster_by_embedding,
    read_proposal_ledger,
)
from tesserae.type_registry import (
    TypeRegistry,
    TypeSpec,
    get_registry,
    register_types,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.delenv("TESSERAE_TYPE_REGISTRY", raising=False)
    monkeypatch.delenv("TESSERAE_TYPE_MODE", raising=False)
    reset_registry()
    yield
    reset_registry()


def _node(node_id: str, name: str, node_type=ResearchNodeType.CONCEPT, **metadata):
    return ResearchNode(id=node_id, name=name, type=node_type, metadata=dict(metadata))


# --------------------------------------------------------------------------- #
# T1 — registry                                                               #
# --------------------------------------------------------------------------- #


def test_registry_edge_type_is_accepted_only_while_active() -> None:
    with pytest.raises(ValueError, match="Unsupported research edge type"):
        ResearchEdge(source="a", target="b", type="uses_math_of")
    register_types([{"name": "uses_math_of", "kind": "edge", "core_parent": "uses"}])
    assert ResearchEdge(source="a", target="b", type="uses_math_of").type == "uses_math_of"
    register_types([{"name": "uses_math_of", "kind": "edge", "core_parent": "uses",
                     "status": "vetoed"}])
    with pytest.raises(ValueError):
        ResearchEdge(source="a", target="b", type="uses_math_of")


def test_registry_refuses_specs_that_cannot_reach_the_core() -> None:
    registry = TypeRegistry()
    with pytest.raises(ValueError, match="no core edge ancestor"):
        registry.register(TypeSpec("floating", "edge", "not_a_type"))
    assert registry.get("floating") is None
    with pytest.raises(ValueError, match="already a core"):
        registry.register(TypeSpec("uses", "edge", "uses"))
    registry.register(TypeSpec("uses", "edge", "uses", status="core"))  # descriptive
    with pytest.raises(ValueError, match="unknown status"):
        TypeSpec("x", "edge", "uses", status="maybe")


def test_registry_parent_chains_resolve_and_producer_only_is_forced() -> None:
    registry = TypeRegistry()
    registry.register(TypeSpec("MathStructure", "node", "MathematicalConcept"))
    registry.register(TypeSpec("Group", "node", "MathStructure"))
    assert registry.core_parent("Group", "node") == "MathematicalConcept"
    spec = TypeSpec("transfers_to", "edge", "uses", extractable=True)
    assert spec.extractable is False


def test_registry_loads_from_json_file_and_env(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "types.json"
    path.write_text(json.dumps({"types": [
        {"name": "Property", "kind": "node", "core_parent": "Concept",
         "definition": "A guarantee something has."},
        {"name": "has_property", "kind": "edge", "core_parent": "uses"},
    ]}))
    monkeypatch.setenv("TESSERAE_TYPE_REGISTRY", str(path))
    reset_registry()
    assert get_registry().is_active("has_property", "edge")
    assert get_registry().get("Property").definition.startswith("A guarantee")


def test_subtype_rides_in_metadata_and_is_validated() -> None:
    register_types([{"name": "Property", "kind": "node", "core_parent": "Concept"}])
    node = _node("n", "SE(3) equivariance").with_subtype("Property")
    assert node.subtype == "Property"
    assert node.metadata["subtype"] == "Property"
    assert set(node.model_dump()) == {"id", "name", "type", "aliases", "description",
                                      "source_path", "metadata"}
    with pytest.raises(ValueError, match="resolves to core type"):
        _node("p", "A paper", ResearchNodeType.PAPER).with_subtype("Property")
    assert node.with_subtype(None).subtype is None


def test_extractable_union_never_contains_producer_only() -> None:
    register_types([
        {"name": "uses_math_of", "kind": "edge", "core_parent": "uses"},
        {"name": "transfers_to", "kind": "edge", "core_parent": "uses"},
    ])
    union = extractable_edge_types()
    assert "uses_math_of" in union
    assert not (union & PRODUCER_ONLY_EDGE_TYPES)
    assert not (EXTRACTABLE_EDGE_TYPES & PRODUCER_ONLY_EDGE_TYPES)  # T9


# --------------------------------------------------------------------------- #
# T2 — strict / lenient load                                                  #
# --------------------------------------------------------------------------- #


def _payload_with_unknowns() -> dict:
    return {
        "nodes": [
            {"id": "a", "name": "A", "type": "Concept"},
            {"id": "b", "name": "B", "type": "Property"},
            {"id": "c", "name": "C", "type": "Gizmo"},
        ],
        "edges": [
            {"source": "a", "target": "b", "type": "has_property", "evidence": "x"},
            {"source": "a", "target": "c", "type": "wobbles"},
            {"source": "b", "target": "c", "type": "uses"},
        ],
    }


def test_strict_load_still_raises_on_unknown_types() -> None:
    with pytest.raises(ValueError):
        graph_from_payload(_payload_with_unknowns())
    with pytest.raises(ValueError):
        graph_from_payload(_payload_with_unknowns(), type_mode="strict")


def test_lenient_load_maps_to_parents_and_counts(monkeypatch) -> None:
    register_types([
        {"name": "Property", "kind": "node", "core_parent": "Concept"},
        {"name": "has_property", "kind": "edge", "core_parent": "uses", "status": "vetoed"},
    ])
    monkeypatch.setenv("TESSERAE_TYPE_MODE", "lenient")
    graph = graph_from_payload(_payload_with_unknowns())
    by_id = {n.id: n for n in graph.nodes}
    assert by_id["b"].type == ResearchNodeType.CONCEPT
    assert by_id["b"].subtype == "Property"
    assert by_id["b"].metadata["raw_type"] == "Property"
    assert by_id["c"].type == ResearchNodeType.CONCEPT
    assert by_id["c"].subtype is None
    types = {(e.source, e.target): e for e in graph.edges}
    # vetoed => read as the parent again
    assert types[("a", "b")].type == "uses"
    assert types[("a", "b")].metadata["raw_type"] == "has_property"
    assert types[("a", "c")].type == "references"
    assert graph.load_stats["unknown_types"] == {
        "edge:has_property": 1, "edge:wobbles": 1, "node:Gizmo": 1, "node:Property": 1,
    }
    assert len(graph.nodes) == 3 and len(graph.edges) == 3  # nothing dropped


def test_type_mode_typo_fails_loud(monkeypatch) -> None:
    monkeypatch.setenv("TESSERAE_TYPE_MODE", "lenent")
    with pytest.raises(ValueError, match="lenent"):
        graph_from_payload({"nodes": [], "edges": []})


def test_strict_round_trip_of_a_041_graph_is_byte_identical(tmp_path: Path) -> None:
    """A graph without any 0.42 field must serialize exactly as 0.41 did."""
    graph = ResearchGraph(
        nodes=[_node("a", "A"), _node("b", "B", ResearchNodeType.PAPER, arxiv_id="1")],
        edges=[ResearchEdge(source="a", target="b", type="uses", evidence="e",
                            metadata={"k": 1})],
    )
    text = graph.to_json(indent=2)
    assert '"confidence"' not in text and '"status"' not in text
    path = tmp_path / "graph.json"
    path.write_text(text)
    assert load_graph_file(path).to_json(indent=2) == text


# --------------------------------------------------------------------------- #
# T4 — edge confidence / status / provenance                                  #
# --------------------------------------------------------------------------- #


def test_edge_optional_fields_round_trip_and_validate() -> None:
    edge = ResearchEdge(source="a", target="b", type="uses", confidence=0.4,
                        status="attested", asserted_by="digest:v1:m",
                        valid_from="2024-01-02", provenance={"run": "r1"})
    dumped = edge.model_dump()
    assert dumped["confidence"] == 0.4 and dumped["status"] == "attested"
    assert "invalid_at" not in dumped
    graph = graph_from_payload({"nodes": [], "edges": [dumped]})
    assert graph.edges[0] == edge
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        ResearchEdge(source="a", target="b", type="uses", confidence=1.5)
    with pytest.raises(ValueError, match="status"):
        ResearchEdge(source="a", target="b", type="uses", status="maybe")


def _star() -> ResearchGraph:
    return ResearchGraph(
        nodes=[_node(x, x.upper()) for x in ("s", "hi", "lo", "cand")],
        edges=[
            ResearchEdge(source="s", target="hi", type="uses", confidence=0.9),
            ResearchEdge(source="s", target="lo", type="uses", confidence=0.1),
            ResearchEdge(source="s", target="cand", type="uses", status="candidate"),
        ],
    )


def test_ppr_weights_by_confidence_and_skips_candidates() -> None:
    ranked = dict(personalized_pagerank(_star(), ["s"], top_k=10))
    assert ranked["hi"] > ranked["lo"]
    assert "cand" not in ranked
    opted = dict(personalized_pagerank(_star(), ["s"], top_k=10,
                                       include_edge_statuses=["candidate"]))
    assert opted["cand"] > 0.0


def test_ppr_without_t4_fields_is_unchanged() -> None:
    plain = ResearchGraph(
        nodes=[_node(x, x) for x in ("a", "b", "c")],
        edges=[ResearchEdge(source="a", target="b", type="uses"),
               ResearchEdge(source="b", target="c", type="extends")],
    )
    explicit_full = ResearchGraph(
        nodes=plain.nodes,
        edges=[ResearchEdge(source="a", target="b", type="uses", confidence=1.0),
               ResearchEdge(source="b", target="c", type="extends", status="attested")],
    )
    assert personalized_pagerank(plain, ["a"]) == personalized_pagerank(explicit_full, ["a"])


def test_refuted_supersession_does_not_suppress() -> None:
    graph = ResearchGraph(
        nodes=[_node("new", "New"), _node("old", "Old")],
        edges=[ResearchEdge(source="new", target="old", type="supersedes")],
    )
    assert suppressed_ids(graph) == {"old"}
    refuted = ResearchGraph(
        nodes=graph.nodes,
        edges=[ResearchEdge(source="new", target="old", type="supersedes",
                            status="refuted")],
    )
    assert suppressed_ids(refuted) == set()


def test_merge_passes_keep_t4_fields() -> None:
    from tesserae.graph_repair import repair_graph

    graph = ResearchGraph(
        nodes=[_node("a", "Alpha"), _node("b", "Beta")],
        edges=[ResearchEdge(source="a", target="b", type="uses", confidence=0.3,
                            status="validated")],
    )
    repaired, _stats = repair_graph(graph)
    edge = next(e for e in repaired.edges if e.type == "uses")
    assert (edge.confidence, edge.status) == (0.3, "validated")


# --------------------------------------------------------------------------- #
# T5 — content resolver                                                       #
# --------------------------------------------------------------------------- #


class _Resolver:
    def __init__(self, views, texts, fail: bool = False) -> None:
        self.views = views
        self.texts = texts
        self.fail = fail
        self.fetched: List[str] = []

    def list_views(self, node):
        if self.fail:
            raise RuntimeError("host down")
        return self.views.get(node.id, [])

    def fetch(self, ref: str, max_chars: int) -> str:
        self.fetched.append(ref)
        return self.texts[ref][: max_chars + 50]  # misbehave: overshoot the cap


def _resolver_graph() -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            _node("p", "Gaussian Splatting Paper", ResearchNodeType.PAPER),
            _node("c", "Splatting", ResearchNodeType.CONCEPT),
        ],
        edges=[ResearchEdge(source="p", target="c", type="introduces")],
    )


def test_compile_context_without_resolver_is_unchanged() -> None:
    graph = _resolver_graph()
    a = compile_context(graph, query="splatting", backend=HashEmbeddingBackend())
    b = compile_context(graph, query="splatting", backend=HashEmbeddingBackend(),
                        content_resolver=None)
    assert a.body == b.body
    assert all("content_refs" not in citation_dict(c) for c in a.citations)


def test_compile_context_serves_bodies_cheapest_first() -> None:
    resolver = _Resolver(
        views={"p": [
            ContentView(ref="hp://paper/p/fulltext", kind="fulltext", est_chars=9000,
                        prose=True),
            ContentView(ref="hp://paper/p/tldr/en", kind="tldr", est_chars=120),
            ContentView(ref="hp://paper/p/key_ideas/en", kind="key_ideas", est_chars=300),
        ]},
        texts={
            "hp://paper/p/fulltext": "FULLTEXT " * 2000,
            "hp://paper/p/tldr/en": "TLDR: splats are fast.",
            "hp://paper/p/key_ideas/en": "KEY: anisotropic gaussians.",
        },
    )
    assert isinstance(resolver, ContentResolver)
    bundle = compile_context(_resolver_graph(), query="splatting", budget=1800,
                             backend=HashEmbeddingBackend(), content_resolver=resolver)
    assert "TLDR: splats are fast." in bundle.body
    assert "KEY: anisotropic gaussians." in bundle.body
    # 1800 // 5 = 360 chars per node is below the prose crossover: no full text.
    assert "hp://paper/p/fulltext" not in resolver.fetched
    paper = next(c for c in bundle.citations if c.node_id == "p")
    assert paper.content_refs == ("hp://paper/p/tldr/en", "hp://paper/p/key_ideas/en")
    assert tuple(citation_dict(paper)["content_refs"]) == paper.content_refs


def test_a_failing_resolver_falls_back_to_the_description() -> None:
    graph = ResearchGraph(nodes=[
        ResearchNode(id="p", name="Splat", type=ResearchNodeType.PAPER,
                     description="Description body about splatting.")
    ])
    bundle = compile_context(graph, query="splat", backend=HashEmbeddingBackend(),
                             content_resolver=_Resolver({}, {}, fail=True))
    assert "Description body about splatting." in bundle.body


# --------------------------------------------------------------------------- #
# T6 — concept identity in federation                                         #
# --------------------------------------------------------------------------- #


def test_identity_key_concept_qid_msc_and_trend_exclusion() -> None:
    assert identity_key(_node("x", "OT", concept_id="c-1")) == ("concept", "c-1")
    assert identity_key(_node("x", "OT", wikidata_qid="q42")) == ("qid", "Q42")
    assert identity_key(_node("x", "OT", msc2020="49q22")) == ("msc", "49Q22")
    trend = _node("t", "Trend: OT", ResearchNodeType.TREND, concept_id="Concept:ot")
    assert identity_key(trend) is None
    paper = _node("p", "P", ResearchNodeType.PAPER, arxiv_id="2401.1", concept_id="c-9")
    assert identity_key(paper) == ("Paper", "2401.1")  # type rules win


def test_identity_resolver_federates_without_editing_projects() -> None:
    a = ResearchGraph(nodes=[_node("ot", "Optimal transport")])
    b = ResearchGraph(nodes=[_node("wasserstein", "Wasserstein distance")])
    plain, _ = federate_graphs([("a", a), ("b", b)])
    assert len(plain.nodes) == 2
    links = {("a", "ot"): "concept-ot", ("b", "wasserstein"): "concept-ot"}
    fed, stats = federate_graphs(
        [("b", b), ("a", a)],
        identity_resolver=lambda alias, node: links.get((alias, node.id)),
    )
    assert len(fed.nodes) == 1 and stats["merged_groups"] == 1
    assert sorted(fed.nodes[0].metadata["federation_members"]) == ["a", "b"]
    assert "concept_id" not in a.nodes[0].metadata  # inputs untouched
    with pytest.raises(TypeError):
        federate_graphs([("a", a)], identity_resolver=lambda alias, node: 7)


# --------------------------------------------------------------------------- #
# T7 — schema drift for edges, embedding clustering, registry apply           #
# --------------------------------------------------------------------------- #


class _ScriptedLLM:
    def __init__(self, payloads) -> None:
        self.payloads = list(payloads)
        self.calls: List[dict] = []

    def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
        self.calls.append({"system": system, "schema_name": schema_name})
        return self.payloads.pop(0)


class _KeywordEmbedder:
    """Two orthogonal directions: anything mentioning 'equivar' vs the rest."""

    def embed(self, texts):
        return [[1.0, 0.0] if "equivar" in t.lower() else [0.0, 1.0] for t in texts]


def test_edge_drift_proposes_snake_case_with_gate_metrics(tmp_path: Path) -> None:
    nodes = [_node(f"n{i}", f"N{i}") for i in range(12)]
    edges = [
        ResearchEdge(source=f"n{i}", target=f"n{i + 6}", type="references",
                     metadata={"relation_label": f"is equivariant under rotation {i}"})
        for i in range(6)
    ]
    graph = ResearchGraph(nodes=nodes, edges=edges)
    llm = _ScriptedLLM([{"sub_types": [{"name": "Has Symmetry Property",
                                        "description": "x has symmetry y",
                                        "examples": [f"n0|references|n6"]}]}])
    _path, reports = analyze_schema_drift(
        graph, tesserae_dir=tmp_path, llm=llm, kind="edge",
        host_edge_types=["references"], min_volume=5, min_cluster_size=5,
        embedder=_KeywordEmbedder(),
    )
    assert reports[0].kind == "edge" and reports[0].clustering == "embedding"
    assert llm.calls[0]["schema_name"] == "schema-drift-subrelations-v1"
    ledger = read_proposal_ledger(tmp_path)
    assert ledger[0]["name"] == "has_symmetry_property"
    assert ledger[0]["kind"] == "edge"
    assert ledger[0]["gate"]["n_instances"] == 6
    assert ledger[0]["gate"]["n_sources"] == 6
    assert ledger[0]["gate"]["cohesion"] == pytest.approx(1.0)


def test_embedding_clustering_groups_what_jaccard_cannot() -> None:
    items = [_node(f"a{i}", name) for i, name in enumerate(
        ["SE(3) equivariance", "rotation equivariant", "equivariant nets",
         "Lie-group equivariance", "equivariance prior"])]
    items += [_node(f"b{i}", f"other thing {i}") for i in range(2)]
    clusters = cluster_by_embedding(items, _KeywordEmbedder(), threshold=0.9,
                                    min_cluster_size=5)
    assert [sorted(n.id for n in c) for c in clusters] == [[f"a{i}" for i in range(5)]]


def test_apply_ledger_writes_registry_and_never_retypes(tmp_path: Path) -> None:
    registry_path = tmp_path / "types.json"
    records = [
        {"approved": True, "host_type": "uses", "kind": "edge",
         "proposed_type": "uses_math_of", "name": "uses_math_of",
         "description": "relies on the structure"},
        {"approved": True, "host_type": "Concept", "proposed_type": "Property",
         "name": "Property", "description": "a guarantee"},
        {"approved": True, "host_type": "Concept", "proposed_type": "Paper",
         "name": "Paper"},  # core name: skipped
        {"approved": False, "host_type": "Concept", "proposed_type": "Assumption",
         "name": "Assumption"},
    ]
    written = apply_ledger_to_registry(records, registry_path)
    assert {w["name"] for w in written} == {"uses_math_of", "Property"}
    payload = json.loads(registry_path.read_text())
    assert {t["name"]: t["status"] for t in payload["types"]} == {
        "Property": "shadow", "uses_math_of": "shadow"}
    # A later lifecycle decision is never reverted by a re-run.
    payload["types"][0]["status"] = "vetoed"
    registry_path.write_text(json.dumps(payload))
    apply_ledger_to_registry(records, registry_path)
    statuses = {t["name"]: t["status"] for t in json.loads(registry_path.read_text())["types"]}
    assert statuses["Property"] == "vetoed"


# --------------------------------------------------------------------------- #
# T8 — capped neighbours                                                      #
# --------------------------------------------------------------------------- #


class _MinimalStore:
    """A 0.41-shaped GraphStore with no neighbours/iterate_edges methods."""

    def __init__(self, graph: ResearchGraph) -> None:
        self.graph = graph

    def upsert_node(self, node):  # pragma: no cover - protocol filler
        return node.id

    def upsert_edge(self, edge):  # pragma: no cover
        return None

    def get_node(self, node_id):  # pragma: no cover
        return None

    def iterate_nodes(self, node_type=None, owner_user_id=None):
        return iter(self.graph.nodes)

    def query_subgraph(self, seeds, depth=1):
        seeds = set(seeds)
        edges = [e for e in self.graph.edges if e.source in seeds or e.target in seeds]
        return ResearchGraph(nodes=list(self.graph.nodes), edges=edges)

    def find_canonical(self, name, node_type):  # pragma: no cover
        return None

    def delete_node(self, node_id):  # pragma: no cover
        return False

    def delete_nodes_by_source(self, source_paths):  # pragma: no cover
        return set()


def test_store_neighbors_falls_back_and_caps_per_type() -> None:
    graph = ResearchGraph(
        nodes=[_node(x, x) for x in ("s", "a", "b", "c", "d")],
        edges=[
            ResearchEdge(source="s", target="a", type="uses", confidence=0.2),
            ResearchEdge(source="s", target="b", type="uses", confidence=0.9),
            ResearchEdge(source="s", target="c", type="uses"),
            ResearchEdge(source="d", target="s", type="extends", status="candidate"),
        ],
    )
    store = _MinimalStore(graph)
    assert isinstance(store, GraphStore)  # the protocol did not grow
    got = store_neighbors(store, "s", limit_per_type=2)
    assert [(e.target, e.type) for e in got] == [("c", "uses"), ("b", "uses")]
    assert store_neighbors(store, "s", direction="in") == []
    assert len(store_neighbors(store, "s", direction="in",
                               include_statuses=["candidate"])) == 1
    assert [e.target for e in store_neighbors(store, "s", min_confidence=0.5)] == ["c", "b"]
    with pytest.raises(NotImplementedError):
        list(store_iterate_edges(store))
    assert len(list(store_iterate_edges(store, ["uses"], allow_full_scan=True))) == 3


# --------------------------------------------------------------------------- #
# T10 — agent_write maps registry types onto core parents                     #
# --------------------------------------------------------------------------- #

_PROV = {"agent": "feed", "url": "https://example.org/x"}


def _write_payload(edge_type="uses_math_of", node_type="Concept", **edge_extra):
    return {
        "nodes": [
            {"name": "Sinkhorn OT loss", "type": "Algorithm"},
            {"name": "Optimal transport", "type": node_type},
        ],
        "edges": [{"source": "Sinkhorn OT loss", "target": "Optimal transport",
                   "type": edge_type, "evidence": "Eq. 3 uses OT.", **edge_extra}],
        "provenance": dict(_PROV),
    }


def test_registry_types_are_written_as_core_parents(tmp_path: Path) -> None:
    register_types([
        {"name": "uses_math_of", "kind": "edge", "core_parent": "uses"},
        {"name": "MathStructure", "kind": "node", "core_parent": "MathematicalConcept"},
    ])
    validated = validate_write(_write_payload(node_type="MathStructure"), "feed")
    node = validated.nodes[1]
    assert node["type"] == "MathematicalConcept"
    assert node["metadata"]["subtype"] == "MathStructure"
    edge = validated.edges[0]
    assert edge["type"] == "uses" and edge["metadata"] == {"relation": "uses_math_of"}

    path = tmp_path / "agent-writes.jsonl"
    record_agent_write(path, _write_payload(node_type="MathStructure"), "feed")
    # The JSONL holds core types only: a reader with an EMPTY registry
    # (a 0.41 install) replays it.
    reset_registry()
    replayed = replay_agent_writes(path)
    uses = next(e for e in replayed.edges if e.type == "uses")
    assert uses.metadata["relation"] == "uses_math_of"
    assert uses.metadata["agent_key"] == "feed"


def test_producer_only_types_and_reserved_metadata_are_refused() -> None:
    register_types([{"name": "transfers_to", "kind": "edge", "core_parent": "uses"}])
    with pytest.raises(GraphJSONValidationError, match="producer-only"):
        validate_write(_write_payload(edge_type="transfers_to"), "feed")
    with pytest.raises(GraphJSONValidationError, match="may not set"):
        validate_write(_write_payload(edge_type="uses",
                                      metadata={"agent_write_id": "forged"}), "feed")
    with pytest.raises(GraphJSONValidationError, match="unsupported edge type"):
        validate_write(_write_payload(edge_type="not_registered"), "feed")


def test_core_edge_with_relation_metadata_is_accepted() -> None:
    validated = validate_write(
        _write_payload(edge_type="shares_concept_with",
                       metadata={"relation": "transfers_to", "concept_id": "c1"}),
        "feed",
    )
    assert validated.edges[0]["metadata"] == {"concept_id": "c1", "relation": "transfers_to"}


def test_pre_042_write_ids_are_unchanged() -> None:
    """An edge without metadata must hash exactly as before (no new key)."""
    validated = validate_write(_write_payload(edge_type="uses"), "feed")
    assert "metadata" not in validated.edges[0]
