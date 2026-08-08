# tests/test_charter.py
from __future__ import annotations

from tesserae.charter import DOMAIN_MASS_CAP, DOMAIN_MASS_FLOOR, mass
from tesserae.research_graph import ResearchNode, ResearchNodeType


def _node(nid: str, name: str, description: str = "") -> ResearchNode:
    return ResearchNode(
        id=nid, name=name, type=ResearchNodeType.CONCEPT, description=description
    )


def test_mass_counts_the_bytes_the_distill_prompt_would_consume():
    nodes = [_node("Concept:a", "Alpha", "first"), _node("Concept:b", "Beta", "second")]
    # mass is the sum of rendered member blocks — the same text the prompt packs.
    assert mass(nodes) > 0
    assert mass(nodes) == mass(list(reversed(nodes))), "mass must be order-free"
    assert mass([]) == 0


def test_mass_constants_are_literals_not_derived_from_chunk_budget():
    # Deriving the cap from CHUNK_CHAR_BUDGET would let TESSERAE_LLM_CHUNK_CHARS
    # reshape the tree — the leak class agent_distill.py:150-155 warns about.
    assert DOMAIN_MASS_CAP == 24_000
    assert DOMAIN_MASS_FLOOR == 3_000


from tesserae.charter import sections
from tesserae.research_graph import ResearchEdge, ResearchGraph


def _two_triangles_plus_orphan() -> ResearchGraph:
    """Two triangles bridged once, plus one node no edge touches."""
    nodes = [
        ResearchNode(id=f"Concept:a{i}", name=f"A{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [
        ResearchNode(id=f"Concept:b{i}", name=f"B{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [ResearchNode(id="Concept:lonely", name="Lonely", type=ResearchNodeType.CONCEPT)]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(ResearchEdge(source=f"Concept:a{i}", target=f"Concept:a{j}", type="shares_concept_with"))
            edges.append(ResearchEdge(source=f"Concept:b{i}", target=f"Concept:b{j}", type="shares_concept_with"))
    edges.append(ResearchEdge(source="Concept:a0", target="Concept:b0", type="shares_concept_with"))
    return ResearchGraph(nodes=nodes, edges=edges)


def test_sections_returns_clusters_and_reports_dropped_singletons():
    graph = _two_triangles_plus_orphan()
    clusters, dropped = sections(graph)

    assert len(clusters) == 2
    assert all(c == sorted(c) for c in clusters), "members must be sorted"
    # THE TRAP: detect_communities drops singletons at community_summaries.py:106,
    # so clusters do NOT partition the node set. sections() must report the
    # remainder explicitly or those nodes vanish from the institution.
    assert dropped == ["Concept:lonely"]
    covered = {nid for c in clusters for nid in c}
    assert covered | set(dropped) == {n.id for n in graph.nodes}


def test_sections_is_deterministic():
    graph = _two_triangles_plus_orphan()
    assert sections(graph) == sections(graph)


from tesserae.charter import divisions, quotient_graph


def test_quotient_graph_nodes_and_edges_are_both_present():
    """_undirected_projection drops edges whose endpoints are not in
    graph.nodes (community_summaries.py:131-132), so a quotient graph that
    carries edges but not their synthetic nodes is INVISIBLE to Louvain."""
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    q = quotient_graph(graph, clusters)

    assert len(q.nodes) == len(clusters)
    node_ids = {n.id for n in q.nodes}
    for edge in q.edges:
        assert edge.source in node_ids and edge.target in node_ids
    # The single a0-b0 bridge becomes exactly one cross-section edge.
    assert len(q.edges) == 1
    assert all(e.type == "part_of" for e in q.edges)


def test_quotient_edge_type_is_allowed():
    """ResearchEdge.__post_init__ raises ValueError for a type outside
    ALLOWED_EDGE_TYPES. 'part_of' is valid; 'quotient_of' is not."""
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    quotient_graph(graph, clusters)  # must not raise


def test_divisions_group_sections_that_share_edges():
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    groups = divisions(graph, clusters)
    # Both sections are bridged, so they land in one division.
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1]


def test_divisions_is_deterministic():
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    assert divisions(graph, clusters) == divisions(graph, clusters)


from tesserae.charter import intake_members


def test_intake_collects_singletons_and_edge_isolated_sections():
    """Two disjoint triangles with NO bridge: both sections are quotient
    singletons, so neither joins a division and both fall to intake, along
    with the orphan node detection dropped entirely."""
    nodes = [
        ResearchNode(id=f"Concept:a{i}", name=f"A{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [
        ResearchNode(id=f"Concept:b{i}", name=f"B{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [ResearchNode(id="Concept:lonely", name="Lonely", type=ResearchNodeType.CONCEPT)]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(ResearchEdge(source=f"Concept:a{i}", target=f"Concept:a{j}", type="shares_concept_with"))
            edges.append(ResearchEdge(source=f"Concept:b{i}", target=f"Concept:b{j}", type="shares_concept_with"))
    graph = ResearchGraph(nodes=nodes, edges=edges)

    clusters, dropped = sections(graph)
    groups = divisions(graph, clusters)
    members = intake_members(graph, clusters, groups)

    assert groups == [], "no cross-section edge means no division"
    assert "Concept:lonely" in members
    assert set(members) == {n.id for n in graph.nodes}
    assert members == sorted(members), "intake membership must be sorted"


def test_intake_is_empty_when_every_section_is_routed():
    graph = _two_triangles_plus_orphan()
    clusters, _ = sections(graph)
    groups = divisions(graph, clusters)
    members = intake_members(graph, clusters, groups)
    # Only the true orphan is unroutable.
    assert members == ["Concept:lonely"]
