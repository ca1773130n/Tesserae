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


def _dense_fat_clique_bridged_to_peripheral() -> ResearchGraph:
    """An 8-node clique of fat nodes bridged by a single edge to a small
    peripheral triangle.

    The clique alone exceeds DOMAIN_MASS_CAP and is internally so dense that
    Louvain's coarsest partition never finds a strict sub-community within
    it — the same shape as
    ``nx.community.louvain_partitions(nx.complete_graph(20))`` collapsing to
    one community at its coarsest level (the reviewer's reproduction for the
    Task 7 review finding). The bridge to the peripheral triangle exists only
    so ``sections()``/``divisions()`` route the clique into a real division
    instead of intake: a fully isolated clique with no cross-section edge is
    a quotient-graph singleton and is dropped straight to intake, which never
    calls ``split()`` at all and so cannot exercise the bug — confirmed
    empirically before writing this fixture.
    """
    clique = [_fat_node(f"Concept:c{i}", 4_000) for i in range(8)]
    clique_edges = [
        ResearchEdge(source=f"Concept:c{i}", target=f"Concept:c{j}", type="shares_concept_with")
        for i in range(8) for j in range(i + 1, 8)
    ]
    peripheral = [_node(f"Concept:p{i}", f"P{i}") for i in range(3)]
    peripheral_edges = [
        ResearchEdge(source=f"Concept:p{i}", target=f"Concept:p{j}", type="shares_concept_with")
        for i in range(3) for j in range(i + 1, 3)
    ]
    bridge = [ResearchEdge(source="Concept:c0", target="Concept:p0", type="shares_concept_with")]
    return ResearchGraph(
        nodes=clique + peripheral, edges=clique_edges + peripheral_edges + bridge
    )


def test_split_stalls_on_a_dense_clique_with_no_strict_sub_partition():
    """CRITICAL regression from Task 7 review: split() only checked whether
    ZERO candidates cleared DOMAIN_MASS_FLOOR, never whether the candidates it
    DID find actually shrank the input. A dense/clique-like oversized domain
    has no internal substructure for Louvain to exploit, so it returns a
    single candidate covering every member — which is not a strict
    sub-partition and must stall, not recurse."""
    graph = _dense_fat_clique_bridged_to_peripheral()
    clique_ids = sorted(n.id for n in graph.nodes if n.id.startswith("Concept:c"))
    assert mass([n for n in graph.nodes if n.id in clique_ids]) > DOMAIN_MASS_CAP

    result = split(graph, clique_ids)
    assert result.stalled is True
    assert result.children == ()
    assert set(result.direct) == set(clique_ids)


def test_build_charter_terminates_on_a_dense_clique_and_ch01_still_holds():
    """End-to-end proof the recursion is bounded. Before the fix this raised
    RecursionError: _emit recursed into the clique child from
    test_split_stalls_on_a_dense_clique_with_no_strict_sub_partition, and
    split() kept handing back that unchanged member set as a single 'child'
    forever. CH-01 (every node in exactly one domain) must still hold once
    the clique degrades to an unsplittable leaf instead of recursing."""
    graph = _dense_fat_clique_bridged_to_peripheral()
    charter = build_charter(graph)

    seen: list[str] = []
    for entry in charter["domains"].values():
        seen.extend(entry["direct_member_ids"])
    assert sorted(seen) == sorted(n.id for n in graph.nodes)
    assert len(seen) == len(set(seen))


from tesserae.charter import succeed


def _charter_with(slug: str, anchor: str, members: list[str], seq: int = 0) -> dict:
    return {
        "version": 1,
        "reorg_seq": seq,
        "domains": {
            slug: {
                "tier": 1, "own_altitude": "division", "parent_slug": None,
                "child_slugs": [], "anchor_id": anchor,
                "direct_member_ids": sorted(members), "member_count": len(members),
                "reorg_seq": seq, "status": "live", "transition": "founded",
                "unsplittable": False,
            }
        },
        "member_index": {m: slug for m in members},
    }


def test_a_domain_keeps_its_slug_when_its_anchor_survives():
    """The whole point: one 15-node document moves ~29% of members, so
    membership cannot key identity. A hub does not move."""
    prior = _charter_with("alpha", "Concept:hub", ["Concept:hub", "Concept:x"])
    fresh = _charter_with("beta", "Concept:hub", ["Concept:hub", "Concept:y", "Concept:z"])
    merged = succeed(prior, fresh)

    assert "alpha" in merged["domains"], "slug must survive on anchor match"
    assert "beta" not in merged["domains"]
    assert merged["domains"]["alpha"]["transition"] == "stable"
    assert merged["domains"]["alpha"]["direct_member_ids"] == ["Concept:hub", "Concept:y", "Concept:z"]
    assert merged["reorg_seq"] == 1


def test_a_domain_whose_anchor_moved_gets_a_new_slug_and_the_old_is_tombstoned():
    prior = _charter_with("alpha", "Concept:gone", ["Concept:gone", "Concept:x"])
    fresh = _charter_with("beta", "Concept:new", ["Concept:new", "Concept:q"])
    merged = succeed(prior, fresh)

    assert merged["domains"]["beta"]["transition"] == "founded"
    assert merged["domains"]["alpha"]["status"] == "retired"
    assert merged["domains"]["alpha"]["superseded_by"] is None
    # A tombstone stays readable so an old citation degrades to a message
    # rather than a missing file.
    assert "alpha" in merged["domains"]


def test_succession_is_deterministic():
    prior = _charter_with("alpha", "Concept:hub", ["Concept:hub"])
    fresh = _charter_with("beta", "Concept:hub", ["Concept:hub", "Concept:y"])
    assert succeed(prior, fresh) == succeed(prior, fresh)


def test_a_slug_reused_by_an_unrelated_fresh_domain_does_not_shield_the_old_one_from_tombstoning():
    """Review finding (a): slug_for dedupes only within one build_charter
    call, so two unrelated anchors minted in different eras can land on the
    same base slug text. The prior fix decided whether to tombstone by
    ``slug in survivors`` — plain presence in the OUTPUT domains dict — so a
    founded fresh domain that coincidentally reused a dead domain's exact
    slug string silently protected that dead domain from ever being
    tombstoned. The two domains here share the slug "alpha" but nothing
    else: different anchors, no overlapping members, no real succession."""
    prior = _charter_with("alpha", "Concept:old-anchor", ["Concept:old-anchor"])
    fresh = _charter_with("alpha", "Concept:unrelated-anchor", ["Concept:unrelated-anchor"])
    merged = succeed(prior, fresh)

    live = {slug: e for slug, e in merged["domains"].items() if e["status"] == "live"}
    retired = {slug: e for slug, e in merged["domains"].items() if e["status"] == "retired"}

    # The live fresh domain must keep the human-readable slug — it is the
    # one actually in use, and a tombstone is free to move.
    assert live == {
        "alpha": merged["domains"]["alpha"]
    }, "the live fresh domain must not be displaced by an unrelated tombstone"
    assert merged["domains"]["alpha"]["anchor_id"] == "Concept:unrelated-anchor"
    assert merged["domains"]["alpha"]["transition"] == "founded"

    # The old domain must still be tombstoned SOMEWHERE, not silently
    # dropped because its slug text was already spoken for.
    assert len(retired) == 1, "the old live domain must still be tombstoned, just elsewhere"
    (retired_entry,) = retired.values()
    assert retired_entry["anchor_id"] == "Concept:old-anchor"


def test_a_fresh_domains_own_slug_colliding_with_another_fresh_domains_inherited_slug_drops_neither():
    """Review finding (b), the more serious one: pass 1 wrote
    ``domains[target] = carried`` unconditionally, so when one fresh domain's
    OWN slug equalled another fresh domain's INHERITED target, the second
    write in dict-sorted order silently overwrote the first — one live
    domain vanished from ``domains`` entirely. Worse, ``member_index`` still
    pointed the overwritten domain's members at its slug, because the
    member_index remap goes through ``rename[]`` independently of the
    collision, breaking the invariant that every member maps to a domain
    that actually holds it.

    Fixture: prior "hub-domain" is anchored on Concept:hub. In the fresh
    charter, a domain named "zzz-new" carries that same anchor (a genuine
    succession, so it inherits the slug "hub-domain"), while a SEPARATE
    fresh domain is itself literally named "hub-domain" on an unrelated
    anchor — its own base slug collides with the other domain's inherited
    target.
    """
    prior = _charter_with("hub-domain", "Concept:hub", ["Concept:hub"])
    fresh = {
        "version": 1,
        "reorg_seq": 0,
        "domains": {
            "zzz-new": {
                "tier": 1, "own_altitude": "division", "parent_slug": None,
                "child_slugs": [], "anchor_id": "Concept:hub",
                "direct_member_ids": ["Concept:hub", "Concept:y"], "member_count": 2,
                "reorg_seq": 0, "status": "live", "transition": "founded",
                "unsplittable": False,
            },
            "hub-domain": {
                "tier": 1, "own_altitude": "division", "parent_slug": None,
                "child_slugs": [], "anchor_id": "Concept:other",
                "direct_member_ids": ["Concept:other", "Concept:z"], "member_count": 2,
                "reorg_seq": 0, "status": "live", "transition": "founded",
                "unsplittable": False,
            },
        },
        "member_index": {
            "Concept:hub": "zzz-new", "Concept:y": "zzz-new",
            "Concept:other": "hub-domain", "Concept:z": "hub-domain",
        },
    }
    merged = succeed(prior, fresh)

    live = {slug: e for slug, e in merged["domains"].items() if e["status"] == "live"}
    assert len(live) == 2, "both live fresh domains must survive the slug collision"

    by_anchor = {e["anchor_id"]: slug for slug, e in live.items()}
    assert set(by_anchor) == {"Concept:hub", "Concept:other"}
    succeeded_slug = by_anchor["Concept:hub"]
    founded_slug = by_anchor["Concept:other"]
    assert succeeded_slug != founded_slug

    # The real succession keeps the exact prior slug; the coincidental one
    # yields to a different slug rather than erasing it.
    assert succeeded_slug == "hub-domain"
    assert merged["domains"][succeeded_slug]["transition"] == "stable"
    assert merged["domains"][founded_slug]["transition"] == "founded"

    # member_index must agree with direct_member_ids for EVERY member — the
    # exact invariant the review finding said this collision broke.
    for member_id, slug in merged["member_index"].items():
        assert member_id in merged["domains"][slug]["direct_member_ids"], (
            f"{member_id} maps to {slug} but that domain does not hold it"
        )
    assert set(merged["member_index"]) == {"Concept:hub", "Concept:y", "Concept:other", "Concept:z"}
