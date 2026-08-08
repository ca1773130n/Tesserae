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


import pytest

from tesserae.charter import SplitResult, induced_subgraph, split


def _fat_node(nid: str, filler: int) -> ResearchNode:
    return ResearchNode(
        id=nid, name=nid, type=ResearchNodeType.CONCEPT, description="x" * filler
    )


def _two_fat_triangles() -> ResearchGraph:
    """Two triangles, each heavy enough that the pair exceeds DOMAIN_MASS_CAP
    and each side clears DOMAIN_MASS_FLOOR."""
    nodes = [_fat_node(f"Concept:a{i}", 5_000) for i in range(3)]
    nodes += [_fat_node(f"Concept:b{i}", 5_000) for i in range(3)]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(ResearchEdge(source=f"Concept:a{i}", target=f"Concept:a{j}", type="shares_concept_with"))
            edges.append(ResearchEdge(source=f"Concept:b{i}", target=f"Concept:b{j}", type="shares_concept_with"))
    edges.append(ResearchEdge(source="Concept:a0", target="Concept:b0", type="shares_concept_with"))
    return ResearchGraph(nodes=nodes, edges=edges)


def test_induced_subgraph_keeps_only_internal_edges():
    graph = _two_fat_triangles()
    sub = induced_subgraph(graph, ["Concept:a0", "Concept:a1", "Concept:a2"])
    assert {n.id for n in sub.nodes} == {"Concept:a0", "Concept:a1", "Concept:a2"}
    for edge in sub.edges:
        assert edge.source in {n.id for n in sub.nodes}
        assert edge.target in {n.id for n in sub.nodes}
    assert len(sub.edges) == 3  # the a0-b0 bridge is excluded


def test_split_divides_an_oversized_domain_by_sub_community():
    graph = _two_fat_triangles()
    members = [n.id for n in graph.nodes]
    assert mass(graph.nodes) > DOMAIN_MASS_CAP

    result = split(graph, members)
    assert isinstance(result, SplitResult)
    assert not result.stalled
    assert len(result.children) == 2
    # Children are sorted by (-mass, first id) so the result is stable.
    assert result.children[0] == ("Concept:a0", "Concept:a1", "Concept:a2") or \
           result.children[0] == ("Concept:b0", "Concept:b1", "Concept:b2")
    # CH-01: children plus direct exactly reconstruct the input.
    covered = {mid for child in result.children for mid in child} | set(result.direct)
    assert covered == set(members)


def test_split_stalls_loudly_rather_than_raising_when_it_cannot_divide():
    """One node too big to split has no sub-community. It must be flagged
    unsplittable and degrade, not raise — the artifact layer already has a
    counted-remainder path for this."""
    graph = ResearchGraph(nodes=[_fat_node("Concept:huge", 30_000)], edges=[])
    result = split(graph, ["Concept:huge"])
    assert result.stalled is True
    assert result.children == ()
    assert result.direct == ("Concept:huge",)


def test_split_leaves_a_small_domain_alone():
    graph = _two_triangles_plus_orphan()
    members = [n.id for n in graph.nodes]
    result = split(graph, members)
    assert result.children == ()
    assert set(result.direct) == set(members)
    assert result.stalled is False


def test_split_is_deterministic():
    graph = _two_fat_triangles()
    members = [n.id for n in graph.nodes]
    assert split(graph, members) == split(graph, members)


from tesserae.charter import assign_anchors, slug_for


def test_anchors_are_top_degree_and_never_shared_between_siblings():
    graph = _two_triangles_plus_orphan()
    a = ["Concept:a0", "Concept:a1", "Concept:a2"]
    b = ["Concept:b0", "Concept:b1", "Concept:b2"]
    anchors = assign_anchors(graph, [a, b])
    assert len(anchors) == 2
    assert len(set(anchors)) == 2, "siblings must not claim the same anchor"
    assert anchors[0] in a and anchors[1] in b


def test_anchor_assignment_is_deterministic():
    graph = _two_triangles_plus_orphan()
    sets = [["Concept:a0", "Concept:a1", "Concept:a2"], ["Concept:b0", "Concept:b1", "Concept:b2"]]
    assert assign_anchors(graph, sets) == assign_anchors(graph, sets)


def test_overlapping_member_sets_never_produce_a_duplicate_anchor():
    """The greedy pass gives "Concept:a0" to whichever set claims it first;
    the LOSING set must fall back to an unclaimed member of its own (or "")
    rather than to sorted(members)[0] unconditionally — that member is, by
    construction, already claimed, and returning it anyway would let two
    domains share one anchor, making them indistinguishable to succession."""
    graph = _two_triangles_plus_orphan()
    anchors = assign_anchors(graph, [["Concept:a0"], ["Concept:a0"]])
    assert len(anchors) == 2
    non_empty = [a for a in anchors if a]
    assert len(set(non_empty)) == len(non_empty), "no two sets may share an anchor"


def test_empty_member_set_gets_no_anchor_and_steals_none():
    graph = _two_triangles_plus_orphan()
    sets = [[], ["Concept:a0", "Concept:a1", "Concept:a2"]]
    anchors = assign_anchors(graph, sets)
    assert anchors[0] == "", "an empty set has no member to anchor on"
    assert anchors[1] in sets[1]
    non_empty = [a for a in anchors if a]
    assert len(set(non_empty)) == len(non_empty)


def test_slug_is_stable_and_deduped():
    taken: set[str] = set()
    first = slug_for("3D Gaussian Splatting", taken)
    taken.add(first)
    assert first == "3d-gaussian-splatting"
    second = slug_for("3D Gaussian Splatting", taken)
    assert second == "3d-gaussian-splatting-2", "a collision must not overwrite"


def test_slug_handles_non_ascii_without_collapsing_to_empty():
    taken: set[str] = set()
    assert slug_for("한 줄 요약", taken) != ""


import json
from pathlib import Path

from tesserae.charter import build_charter, charter_path, read_charter, write_charter


def test_build_charter_partitions_every_node_exactly_once():
    """CH-01, the invariant the whole structure rests on."""
    graph = _two_triangles_plus_orphan()
    charter = build_charter(graph)

    seen: list[str] = []
    for entry in charter["domains"].values():
        seen.extend(entry["direct_member_ids"])
    assert sorted(seen) == sorted(n.id for n in graph.nodes)
    assert len(seen) == len(set(seen)), "a node may belong to exactly one domain"


def test_build_charter_excludes_synthesis_nodes_by_default():
    """Measured on the live graph, leaving these in makes roughly half the
    institution an org chart of Tesserae's own output: division anchors came
    out as 'Project Pulse' and '한 줄 요약'."""
    graph = _two_triangles_plus_orphan()
    graph.nodes.append(
        ResearchNode(id="Synthesis:pulse", name="Project Pulse", type=ResearchNodeType.SYNTHESIS)
    )
    charter = build_charter(graph)
    everyone = {
        mid for e in charter["domains"].values() for mid in e["direct_member_ids"]
    }
    assert "Synthesis:pulse" not in everyone

    kept = build_charter(graph, exclude_synthesis=False)
    everyone_kept = {
        mid for e in kept["domains"].values() for mid in e["direct_member_ids"]
    }
    assert "Synthesis:pulse" in everyone_kept


def test_charter_round_trips_and_is_byte_stable(tmp_path: Path):
    graph = _two_triangles_plus_orphan()
    charter = build_charter(graph)
    path = write_charter(tmp_path, charter)
    assert path == charter_path(tmp_path)
    assert read_charter(tmp_path) == charter

    first = path.read_bytes()
    write_charter(tmp_path, build_charter(graph))
    assert path.read_bytes() == first, "same input must produce identical bytes"


def test_charter_has_no_timestamps():
    """A wall-clock field would break byte-idempotence on every rebuild."""
    charter = build_charter(_two_triangles_plus_orphan())
    blob = json.dumps(charter)
    assert "timestamp" not in blob and "generated_at" not in blob
    assert charter["reorg_seq"] == 0


def test_read_charter_returns_none_when_absent(tmp_path: Path):
    assert read_charter(tmp_path) is None
