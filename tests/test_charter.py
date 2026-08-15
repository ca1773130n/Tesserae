# tests/test_charter.py
from __future__ import annotations

import pytest

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


def _two_fat_triangles_plus_orphan() -> ResearchGraph:
    """``_two_fat_triangles`` with an unroutable node, so the charter it
    produces has BOTH a real split AND a non-empty intake domain."""
    graph = _two_fat_triangles()
    graph.nodes.append(
        ResearchNode(id="Concept:lonely", name="Lonely", type=ResearchNodeType.CONCEPT)
    )
    return graph


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


def _intake_named_division(filler: int) -> ResearchGraph:
    """Two bridged triangles whose top-degree node is NAMED "Intake", plus one
    orphan that genuinely belongs in intake.

    The "Intake" name is what makes ``slug_for`` mint the base slug ``intake``
    for that division — the same string ``_INTAKE_SLUG`` uses. ``Concept:a0``
    wins the division anchor on degree 3, tied with ``Concept:z0`` and broken
    by id, so the anchor is deterministically the node named "Intake".

    ``filler`` selects WHICH HALF of the defect the fixture exhibits, and both
    halves need their own fixture because neither shows the other:

    * ``filler=0`` — the division stays under DOMAIN_MASS_CAP, so it is a LEAF
      holding all six members directly. The intake write erases it and those
      six members land in no domain at all: the CH-01 violation.
    * ``filler=5_000`` — the division exceeds the cap and splits into two
      departments, so it holds NO direct members and erasing it loses no
      member. What it loses instead is the parent: both departments survive
      pointing at a ``parent_slug`` the census domain never adopted.
    """
    def _n(nid: str, name: str) -> ResearchNode:
        return ResearchNode(
            id=nid, name=name, type=ResearchNodeType.CONCEPT, description="x" * filler
        )

    nodes = [
        _n("Concept:a0", "Intake"), _n("Concept:a1", "B"), _n("Concept:a2", "C"),
        _n("Concept:z0", "Z0"), _n("Concept:z1", "Z1"), _n("Concept:z2", "Z2"),
        ResearchNode(id="Concept:lonely", name="Lonely", type=ResearchNodeType.CONCEPT),
    ]
    edges = [
        ResearchEdge(source=a, target=b, type="shares_concept_with")
        for a, b in [
            ("Concept:a0", "Concept:a1"), ("Concept:a1", "Concept:a2"),
            ("Concept:a0", "Concept:a2"),
            ("Concept:z0", "Concept:z1"), ("Concept:z1", "Concept:z2"),
            ("Concept:z0", "Concept:z2"),
            ("Concept:a0", "Concept:z0"),
        ]
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_a_domain_can_never_mint_the_reserved_intake_slug():
    """CRITICAL 1 regression. ``_INTAKE_SLUG`` was never added to ``taken``,
    so a division whose anchor node is NAMED "Intake" minted the base slug
    ``intake`` for itself, and the unguarded ``domains[_INTAKE_SLUG] = {...}``
    write at the end of build_charter then ERASED that division outright.

    Measured before the fix on this exact fixture: six of seven nodes ended up
    in zero domains, and all six ``member_index`` entries pointed at ``intake``
    — a domain that did not hold them. That is CH-01, the invariant the whole
    structure rests on, silently void.

    ``intake`` is reserved unconditionally, not only when the intake set is
    non-empty: were it conditional, a graph whose intake set is empty on one
    pass would hand ``intake`` to a division, and the very next ingest that
    produced a single unroutable node would collide all over again.
    """
    charter = build_charter(_intake_named_division(filler=0))
    from tesserae.charter import _INTAKE_SLUG

    # The division named "Intake" must have yielded the reserved slug.
    named_intake = [
        slug for slug, e in charter["domains"].items()
        if e["anchor_id"] == "Concept:a0"
    ]
    assert named_intake, "the division anchored on the node named 'Intake' must exist"
    assert named_intake[0] != _INTAKE_SLUG

    # The reserved slug belongs to the census domain and nothing else.
    assert charter["domains"][_INTAKE_SLUG]["anchor_id"] == ""
    assert charter["domains"][_INTAKE_SLUG]["direct_member_ids"] == ["Concept:lonely"]


def test_intake_collision_still_partitions_every_node_exactly_once():
    """CH-01 under the Critical-1 fixture, stated separately from the slug
    assertion because the partition is the thing that actually broke."""
    graph = _intake_named_division(filler=0)
    charter = build_charter(graph)

    seen: list[str] = []
    for entry in charter["domains"].values():
        seen.extend(entry["direct_member_ids"])
    assert sorted(seen) == sorted(n.id for n in graph.nodes)
    assert len(seen) == len(set(seen)), "a node may belong to exactly one domain"

    # member_index must AGREE with direct_member_ids, not merely be populated.
    for member_id, slug in charter["member_index"].items():
        assert member_id in charter["domains"][slug]["direct_member_ids"], (
            f"{member_id} maps to {slug} but that domain does not hold it"
        )
    assert set(charter["member_index"]) == {n.id for n in graph.nodes}


def test_no_domain_is_left_pointing_at_an_erased_parent():
    """The Critical-1 overwrite left the erased division's children alive with
    ``parent_slug: "intake"`` — orphans hanging off a domain that never had
    them. Every parent_slug and child_slug must name a domain that exists, and
    every parent must actually claim the children that claim it."""
    charter = build_charter(_intake_named_division(filler=5_000))
    domains = charter["domains"]
    assert any(e["parent_slug"] for e in domains.values()), (
        "fixture must produce a parent/child pair or it proves nothing"
    )
    for slug, entry in domains.items():
        parent = entry["parent_slug"]
        if parent is not None:
            assert parent in domains, f"{slug} names a parent {parent!r} that does not exist"
            assert slug in domains[parent]["child_slugs"], (
                f"{slug} claims parent {parent!r} which does not list it as a child"
            )
        for child in entry["child_slugs"]:
            assert child in domains, f"{slug} names a child {child!r} that does not exist"


def test_no_two_domains_in_one_charter_share_an_anchor():
    """CRITICAL 2 regression, at the point where it originates.

    ``assign_anchors`` scores on GLOBAL ``undirected_degrees`` and dedupes only
    within ONE call, and build_charter calls it twice — once for divisions,
    once per split for that division's children. So a child re-picked the very
    node its parent had already anchored on: on this fixture division ``a0``
    and department ``a0-2`` both anchored ``Concept:a0``.

    Two domains sharing an anchor are indistinguishable to succession, which
    is what turned a no-op reorg into permanent slug churn.
    """
    charter = build_charter(_two_fat_triangles())
    assert len(charter["domains"]) >= 3, "fixture must actually split"

    anchors = [e["anchor_id"] for e in charter["domains"].values() if e["anchor_id"]]
    assert len(anchors) == len(set(anchors)), (
        f"anchors must be unique across the WHOLE charter, got {sorted(anchors)}"
    )

    # And specifically: no descendant may re-take an ancestor's anchor.
    domains = charter["domains"]
    for slug, entry in domains.items():
        parent = entry["parent_slug"]
        while parent is not None:
            assert domains[parent]["anchor_id"] != entry["anchor_id"] or not entry["anchor_id"]
            parent = domains[parent]["parent_slug"]


def test_a_child_that_loses_its_anchor_falls_to_its_next_highest_degree_member():
    """The losing domain must fall to another member of ITS OWN set, not to ""
    and not to a member of a sibling — an empty anchor cannot be succeeded."""
    charter = build_charter(_two_fat_triangles())
    for slug, entry in charter["domains"].items():
        assert entry["anchor_id"], f"{slug} has no anchor, so succession cannot match it"


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


def test_read_charter_raises_on_a_corrupt_file_instead_of_saying_no_charter(tmp_path: Path):
    """IMPORTANT 3: absent and unreadable are DIFFERENT conditions.

    Both used to return None, and every caller reads None as "this project has
    no charter yet". Once succession is wired into the compile path, a
    truncated or half-written charter.json would therefore make the engine
    silently RE-FOUND the whole institution: every pinned attach path broken,
    zero tombstones, no error anywhere. The one case that must stay None is a
    project that legitimately has no charter file at all.
    """
    from tesserae.charter import CharterUnreadable

    path = charter_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"domains": ', encoding="utf-8")  # truncated mid-write

    with pytest.raises(CharterUnreadable) as excinfo:
        read_charter(tmp_path)

    message = str(excinfo.value)
    assert str(path) in message, "the error must name the file the operator has to fix"
    assert "recompil" in message.lower(), (
        "the error must name a remedy, not just a failure"
    )


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
    """FIX-BEFORE-MERGE: this test used to run on two charters holding exactly
    ONE domain each, which made it blind to Critical 2 — a charter with a
    single domain has no parent/child pair, so it can never exhibit the
    ancestor/descendant anchor collision that broke succession. It now runs on
    a real SPLIT charter (a division with two departments) and asserts the
    property that actually matters: reorganising an UNCHANGED charter is a
    no-op.

    Before the fix this failed outright: division ``a0`` and department
    ``a0-2`` both anchored ``Concept:a0``, so ``anchor_to_prior`` kept only the
    last of the two, the division was retired as though its subject had
    vanished, and the department was renamed ``a0-2-2`` — on input that had not
    changed at all.
    """
    charter = build_charter(_two_fat_triangles())

    # Guard the fixture itself, so this test cannot quietly go blind again the
    # way its single-domain predecessor did.
    assert len(charter["domains"]) >= 3, "fixture must hold several domains"
    assert any(e["parent_slug"] for e in charter["domains"].values()), (
        "fixture must hold at least one parent/child pair"
    )

    assert succeed(charter, charter) == succeed(charter, charter)

    merged = succeed(charter, charter)
    retired = sorted(s for s, e in merged["domains"].items() if e["status"] == "retired")
    assert retired == [], f"a no-op reorg retired {retired}"
    assert set(merged["domains"]) == set(charter["domains"]), (
        "a no-op reorg renamed a domain"
    )
    assert merged["member_index"] == charter["member_index"]
    assert merged["reorg_seq"] == 1

    for slug, entry in charter["domains"].items():
        after = merged["domains"][slug]
        assert after["anchor_id"] == entry["anchor_id"]
        assert after["parent_slug"] == entry["parent_slug"]
        assert after["child_slugs"] == entry["child_slugs"]
        assert after["status"] == "live"
        assert after["transition"] == "stable"


def test_repeated_reorgs_on_an_unchanged_graph_never_churn_a_slug():
    """The compounding form of Critical 2, and the reason the module exists.

    Measured before the fix, on a graph that never changed:
    ``a0 -> a0-2 -> a0-2-2 -> a0-2-2-2``, one live domain retired per reorg,
    forever. An operator who pinned an agent to ``a0`` in a config lost that
    attach path on the next ingest, which is precisely the failure a versioned
    charter exists to prevent.
    """
    graph = _two_fat_triangles_plus_orphan()
    charter = build_charter(graph)
    baseline = {s for s, e in charter["domains"].items() if e["status"] == "live"}
    assert len(baseline) >= 3
    # The fixture must exercise intake too. Intake is the ONE domain with no
    # anchor, so it is the one succession cannot match the ordinary way, and a
    # fixture without it leaves that path untested — which is how this defect
    # survived the first pass at Critical 2.
    from tesserae.charter import _INTAKE_SLUG
    assert _INTAKE_SLUG in baseline

    current = charter
    for reorg in range(1, 5):
        current = succeed(current, build_charter(graph))
        live = {s for s, e in current["domains"].items() if e["status"] == "live"}
        retired = sorted(s for s, e in current["domains"].items() if e["status"] == "retired")
        assert live == baseline, f"reorg {reorg} churned the live slugs to {sorted(live)}"
        assert retired == [], f"reorg {reorg} retired {retired} on unchanged input"
        # Tombstone churn counts as churn: the charter must not GROW on input
        # that did not change.
        assert set(current["domains"]) == set(charter["domains"]), (
            f"reorg {reorg} added domains: "
            f"{sorted(set(current['domains']) - set(charter['domains']))}"
        )

    assert current["member_index"] == charter["member_index"]


def test_intake_succeeds_itself_because_its_identity_is_its_slug_not_an_anchor():
    """Intake is the one domain with ``anchor_id: ""`` — it is a census of what
    structure could not route, not a subject with a hub. Succession matches on
    anchor, and an empty anchor matches nothing, so the prior intake was
    tombstoned and the fresh one re-founded on EVERY reorg. Because the live
    fresh domain already held the slug, each tombstone was relocated, and an
    unchanged graph therefore accumulated ``intake-2``, ``intake-2-2``,
    ``intake-2-2-2`` … without bound: the same no-op-reorg churn as Critical 2,
    displaced into the tombstone space.

    Intake's identity is the RESERVED SLUG, which build_charter guarantees is
    unique and permanent, so that is what it must be matched on.
    """
    graph = _two_fat_triangles_plus_orphan()
    charter = build_charter(graph)
    from tesserae.charter import _INTAKE_SLUG

    merged = succeed(charter, build_charter(graph))
    assert merged["domains"][_INTAKE_SLUG]["status"] == "live"
    assert merged["domains"][_INTAKE_SLUG]["transition"] == "stable"
    assert [s for s in merged["domains"] if s.startswith("intake-")] == [], (
        "intake must succeed itself, not spawn a relocated tombstone"
    )


def test_intake_is_still_tombstoned_when_nothing_is_unroutable_any_more():
    """The converse of the rule above, so self-succession does not become
    "intake is immortal": if a reorg leaves nothing unroutable, the fresh
    charter has no intake domain and the prior one must retire like any other
    domain whose subject went away."""
    from tesserae.charter import _INTAKE_SLUG

    prior = build_charter(_two_fat_triangles_plus_orphan())
    assert _INTAKE_SLUG in prior["domains"]

    fresh = build_charter(_two_fat_triangles())  # no orphan, so no intake
    assert _INTAKE_SLUG not in fresh["domains"]

    merged = succeed(prior, fresh)
    assert merged["domains"][_INTAKE_SLUG]["status"] == "retired"


def test_duplicate_anchors_among_prior_domains_are_resolved_first_wins_not_last_wins():
    """``anchor_to_prior`` was a dict comprehension over the prior domains, so
    two live prior domains sharing an anchor silently resolved LAST-WINS: which
    domain kept its slug depended on nothing more than sort order, and the
    other was tombstoned with no signal that the charter was corrupt.

    build_charter can no longer produce such a charter, but charter.json is a
    file on disk that a bad hand-merge or an older buggy build can leave in
    exactly that state, so succession must resolve it DETERMINISTICALLY and
    visibly rather than by accident: first-wins on sorted slug, and the loser
    is tombstoned like any other unclaimed prior domain.
    """
    prior = {
        "version": 1,
        "reorg_seq": 3,
        "domains": {
            slug: {
                "tier": 1, "own_altitude": "division", "parent_slug": None,
                "child_slugs": [], "anchor_id": "Concept:hub",
                "direct_member_ids": ["Concept:hub"], "member_count": 1,
                "reorg_seq": 3, "status": "live", "transition": "stable",
                "unsplittable": False,
            }
            for slug in ("aaa", "zzz")
        },
        "member_index": {"Concept:hub": "zzz"},
    }
    fresh = _charter_with("whatever", "Concept:hub", ["Concept:hub", "Concept:new"])
    merged = succeed(prior, fresh)

    live = {slug: e for slug, e in merged["domains"].items() if e["status"] == "live"}
    assert set(live) == {"aaa"}, "first-wins on sorted slug, deterministically"
    assert live["aaa"]["transition"] == "stable"
    assert merged["domains"]["zzz"]["status"] == "retired"

    # And it must be stable across runs, not a coincidence of dict ordering.
    assert succeed(prior, fresh) == merged


def test_a_fresh_domain_naming_an_unknown_parent_is_refused_not_promoted_to_root():
    """IMPORTANT 4: pass 2 used ``rename.get(entry["parent_slug"])``, and a
    miss yields None — which in this schema means "I am a root division". So a
    fresh charter whose child named a parent that did not exist did not fail;
    it silently promoted that child to the top of the institution, changing
    both its altitude and what an agent routing from root would be shown."""
    prior = _charter_with("alpha", "Concept:hub", ["Concept:hub"])
    fresh = {
        "version": 1,
        "reorg_seq": 0,
        "domains": {
            "child": {
                "tier": 2, "own_altitude": "team", "parent_slug": "ghost",
                "child_slugs": [], "anchor_id": "Concept:hub",
                "direct_member_ids": ["Concept:hub"], "member_count": 1,
                "reorg_seq": 0, "status": "live", "transition": "founded",
                "unsplittable": False,
            },
        },
        "member_index": {"Concept:hub": "child"},
    }
    with pytest.raises(ValueError) as excinfo:
        succeed(prior, fresh)
    assert "ghost" in str(excinfo.value)


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


from tesserae.charter import _INTAKE_SLUG


def _hub_pair_starved_of_anchors() -> ResearchGraph:
    """A graph whose deepest split proposes a cluster made ENTIRELY of anchors
    its own ancestors already took.

    Minimised by delta-debugging from a random graph that reproduced the
    defect (11 of 400 random graphs did), down to the smallest shape that
    still exhibits it. Read it as a four-node core with two light tails:

        t2 - t1 - t0 - hub-a - hub-b - t3 - t4
                          |
                       mass-a - mass-b

    ``hub-a`` has degree 3 and is the only node that does, so it anchors the
    single division. ``hub-b`` has degree 2 and sorts before every other
    degree-2 node, so once ``hub-a`` is claimed it wins the tie and anchors
    the department below. The department's members are exactly the four core
    nodes, whose mass clears DOMAIN_MASS_CAP only because ``mass-a`` and
    ``mass-b`` are fat — so the department splits, and splitting the 4-path
    ``hub-b - hub-a - mass-a - mass-b`` down its middle edge proposes
    ``{hub-a, hub-b}``: a cluster whose every member is already an ancestor's
    anchor. That is the starved set.

    The tails carry almost no mass, so their own clusters fall below
    DOMAIN_MASS_FLOOR and land in the division's direct block rather than
    becoming domains — which is what keeps the fixture at nine nodes.

    Every node is a CONCEPT, so ``build_charter``'s synthesis filter is a
    no-op here and the graph it splits is this graph.
    """
    nodes = [
        _fat_node("Concept:hub-a", 2_000),
        _fat_node("Concept:hub-b", 2_000),
        _fat_node("Concept:mass-a", 12_000),
        _fat_node("Concept:mass-b", 12_000),
    ] + [_fat_node(f"Concept:t{i}", 400) for i in range(5)]
    edges = [
        ResearchEdge(source=a, target=b, type="shares_concept_with")
        for a, b in (
            ("Concept:hub-b", "Concept:hub-a"),
            ("Concept:hub-a", "Concept:mass-a"),
            ("Concept:mass-a", "Concept:mass-b"),
            ("Concept:hub-a", "Concept:t0"),
            ("Concept:t0", "Concept:t1"),
            ("Concept:t1", "Concept:t2"),
            ("Concept:hub-b", "Concept:t3"),
            ("Concept:t3", "Concept:t4"),
        )
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _anchor_starved_clusters(graph: ResearchGraph, charter: dict) -> list[tuple[str, tuple[str, ...]]]:
    """Every cluster ``split`` proposes whose members are all already anchors
    of the proposing domain or one of its ancestors — i.e. exactly the clusters
    ``assign_anchors`` must return "" for.

    Deliberately derived from the CHARTER rather than from build_charter's
    internals, so it reads the SAME before and after the fold: folding moves
    such a cluster's members from a child domain into the parent's direct
    block, but a domain's total member set is unchanged either way, so
    ``split`` proposes the identical cluster. That is what lets it guard the
    fixture in both worlds — a fixture guard that only holds after the fix
    would make the regression test fail for its own setup rather than for the
    defect.
    """
    domains = charter["domains"]

    def members_of(slug):
        entry = domains[slug]
        held = set(entry["direct_member_ids"])
        for child in entry["child_slugs"]:
            held |= members_of(child)
        return held

    def anchors_at_and_above(slug):
        held = set()
        cursor = slug
        while cursor:
            held.add(domains[cursor]["anchor_id"])
            cursor = domains[cursor]["parent_slug"]
        return held - {""}

    starved: list[tuple[str, tuple[str, ...]]] = []
    for slug, entry in sorted(domains.items()):
        if entry["status"] != "live" or slug == _INTAKE_SLUG:
            continue
        claimed = anchors_at_and_above(slug)
        for cluster in split(graph, sorted(members_of(slug))).children:
            if set(cluster) <= claimed:
                starved.append((slug, cluster))
    return starved


def test_a_cluster_whose_members_are_all_ancestor_anchors_folds_into_its_parent():
    """``assign_anchors`` returns "" for a cluster every one of whose members
    an ancestor already anchored — honestly, because no anchor exists. Emitting
    a domain from that empty string is what broke: ``slug_for("")`` finds no
    ASCII base, hashes the EMPTY string, and hands every such domain the same
    slug ``domain-e3b0c442``; and since ``succeed`` matches on anchor and skips
    empty ones, that domain can never inherit its own prior identity.

    Such a cluster is tiny by construction — member sets partition the graph,
    so a member can only have been claimed by an ANCESTOR, which bounds the
    cluster by tree depth — so its members fold into the parent's direct block
    instead. CH-01 survives the fold: the node moves into the parent's direct
    set rather than into a child's.
    """
    graph = _hub_pair_starved_of_anchors()
    charter = build_charter(graph)

    # Guard the fixture: it must actually reach the starved path. A fixture
    # that cannot express the failing shape is how four earlier defects in
    # this module hid.
    starved = _anchor_starved_clusters(graph, charter)
    assert starved == [("concept-hub-b", ("Concept:hub-a", "Concept:hub-b"))], (
        f"fixture no longer starves a cluster of anchors: {starved}"
    )

    empty_anchored = sorted(
        slug for slug, entry in charter["domains"].items()
        if entry["status"] == "live" and slug != _INTAKE_SLUG and not entry["anchor_id"]
    )
    assert empty_anchored == [], (
        f"{empty_anchored} were emitted with no anchor; every one of them minted "
        "the slug domain-e3b0c442 and can never succeed itself"
    )

    # The starved cluster's members are held DIRECTLY by the domain that
    # proposed it, not by a child of it.
    parent = charter["domains"]["concept-hub-b"]
    assert "Concept:hub-a" in parent["direct_member_ids"]
    assert "Concept:hub-b" in parent["direct_member_ids"]
    assert charter["member_index"]["Concept:hub-a"] == "concept-hub-b"
    assert charter["member_index"]["Concept:hub-b"] == "concept-hub-b"
    # member_count counts the whole subtree, so folding must not change it.
    assert parent["member_count"] == 4

    # CH-01 still holds, and every member_index entry names a domain that
    # actually holds the member.
    held = [m for e in charter["domains"].values() for m in e["direct_member_ids"]]
    assert sorted(held) == sorted(n.id for n in graph.nodes)
    assert len(held) == len(set(held))
    for member_id, slug in charter["member_index"].items():
        assert member_id in charter["domains"][slug]["direct_member_ids"]


def test_repeated_reorgs_never_churn_when_a_cluster_is_starved_of_anchors():
    """The compounding failure, and the reason the fold is not cosmetic.

    Measured before the fix on this exact fixture, on a graph that never
    changed: reorg 1 retired ``domain-e3b0c442-2``, reorg 2 added
    ``domain-e3b0c442-2-2``, reorg 3 ``domain-e3b0c442-2-2-2`` — one new
    tombstone per reorg, forever, because an empty anchor matches nothing in
    ``anchor_to_prior`` so the domain re-founded every time and its tombstone
    was relocated by ``_claim``. The live graph reached depth 5. This is the
    same unbounded relocation the intake and Critical-2 fixes closed, arriving
    through a third door.
    """
    graph = _hub_pair_starved_of_anchors()
    charter = build_charter(graph)

    assert _anchor_starved_clusters(graph, charter), "fixture must starve a cluster"
    baseline = {s for s, e in charter["domains"].items() if e["status"] == "live"}
    assert len(baseline) >= 3

    current = charter
    for reorg in range(1, 5):
        current = succeed(current, build_charter(graph))
        live = {s for s, e in current["domains"].items() if e["status"] == "live"}
        retired = sorted(s for s, e in current["domains"].items() if e["status"] == "retired")
        assert live == baseline, f"reorg {reorg} churned the live slugs to {sorted(live)}"
        assert retired == [], f"reorg {reorg} retired {retired} on unchanged input"
        assert set(current["domains"]) == set(charter["domains"]), (
            f"reorg {reorg} added domains: "
            f"{sorted(set(current['domains']) - set(charter['domains']))}"
        )
        assert current["member_index"] == charter["member_index"], (
            f"reorg {reorg} moved members between domains on unchanged input"
        )


# ---------------------------------------------------------------------------
# the anchor selector: a slug is permanent, so the namer is fixed by picking a
# nameable anchor, never by rewriting the name
# ---------------------------------------------------------------------------

from tesserae.charter import (
    _ANCHOR_DEMOTED_TYPES,
    _verify_partition,
    is_noop_reorg,
    worth_chartering,
)
from tesserae.hierarchy import undirected_degrees


def _typed(nid: str, name: str, node_type: ResearchNodeType) -> ResearchNode:
    return ResearchNode(id=nid, name=name, type=node_type)


def _hub_of_type_beats(node_type: ResearchNodeType) -> ResearchGraph:
    """One cluster whose HIGHEST-degree member is ``node_type`` and whose
    second-highest is a Concept, with the degrees deliberately UNEQUAL."""
    nodes = [
        _typed("X:hub", "Hub Document", node_type),
        _typed("Concept:runner", "Runner Up", ResearchNodeType.CONCEPT),
    ]
    nodes += [_typed(f"Concept:leaf{i}", f"Leaf {i}", ResearchNodeType.CONCEPT) for i in range(6)]
    edges = []
    # hub touches all six leaves (degree 6); runner-up touches four (degree 4).
    for i in range(6):
        edges.append(ResearchEdge(source="X:hub", target=f"Concept:leaf{i}", type="shares_concept_with"))
    for i in range(4):
        edges.append(ResearchEdge(source="Concept:runner", target=f"Concept:leaf{i}", type="shares_concept_with"))
    return ResearchGraph(nodes=nodes, edges=edges)


@pytest.mark.parametrize(
    "demoted_type",
    [
        ResearchNodeType.SOURCE_DOCUMENT,
        ResearchNodeType.TECHNICAL_TERM,
        ResearchNodeType.EVIDENCE_SPAN,
        ResearchNodeType.SESSION,
        ResearchNodeType.EVENT,
        ResearchNodeType.AGENT,
        ResearchNodeType.STUB,
        ResearchNodeType.SESSION_INSIGHT,
    ],
)
def test_a_demoted_type_loses_the_anchor_even_when_it_has_the_higher_degree(
    demoted_type: ResearchNodeType,
):
    """The roadmap proposed node type as a MIDDLE sort key — a tie-break on
    equal degree. Measured on the live graph that is a no-op: the SourceDocument
    heading a 7,955-member division has degree 116 against 112 for the next
    candidate, and the TechnicalTerm ``Python`` 112 against 108, so a tie-break
    never runs and both unusable slugs survive. Type has to outrank degree.

    This fixture reproduces that shape exactly — strictly higher degree on the
    demoted node — so a tie-break implementation fails it.
    """
    graph = _hub_of_type_beats(demoted_type)
    members = [n.id for n in graph.nodes]

    degrees = undirected_degrees(graph)
    assert degrees["X:hub"] > degrees["Concept:runner"], "fixture must not be a tie"

    (anchor,) = assign_anchors(graph, [members])
    assert anchor == "Concept:runner", (
        f"a {demoted_type.value} outranked every nameable member on degree alone"
    )


def test_a_demoted_type_still_anchors_when_nothing_else_can():
    """Demotion is a preference, not a ban. A domain whose every member is a
    demoted type must still get an anchor — an empty anchor is the anchorless
    slug ``domain-e3b0c442`` that can never succeed itself, and folding the
    domain away would drop its members from the partition."""
    nodes = [
        _typed(f"SourceDocument:d{i}", f"Doc {i}", ResearchNodeType.SOURCE_DOCUMENT)
        for i in range(4)
    ]
    edges = [
        ResearchEdge(source="SourceDocument:d0", target=f"SourceDocument:d{i}", type="shares_concept_with")
        for i in range(1, 4)
    ]
    graph = ResearchGraph(nodes=nodes, edges=edges)

    (anchor,) = assign_anchors(graph, [[n.id for n in nodes]])
    assert anchor == "SourceDocument:d0", "top-degree member must still win when all are demoted"


def test_demoting_never_changes_which_members_a_domain_holds():
    """The anchor is an identity and a name, not a member filter. Whatever the
    selector picks, CH-01 must hold over the same node universe."""
    graph = _two_fat_triangles_plus_orphan()
    charter = build_charter(graph)

    held = [m for e in charter["domains"].values() for m in e["direct_member_ids"]]
    assert sorted(held) == sorted(n.id for n in graph.nodes)
    assert len(held) == len(set(held))


def test_every_demoted_type_is_a_real_node_type():
    """A typo in the frozenset would silently demote nothing — the failure
    would be invisible, because an unmatched entry just never matches."""
    for node_type in _ANCHOR_DEMOTED_TYPES:
        assert isinstance(node_type, ResearchNodeType)
    assert ResearchNodeType.SOURCE_DOCUMENT in _ANCHOR_DEMOTED_TYPES
    assert ResearchNodeType.TECHNICAL_TERM in _ANCHOR_DEMOTED_TYPES
    # Producer-owned knowledge types must stay eligible, or the four readable
    # divisions on the live graph lose their names too.
    for keep in (
        ResearchNodeType.RESEARCH_TOPIC,
        ResearchNodeType.RESEARCH_FIELD,
        ResearchNodeType.PAPER,
        ResearchNodeType.PROJECT,
    ):
        assert keep not in _ANCHOR_DEMOTED_TYPES


# ---------------------------------------------------------------------------
# CH-01 at runtime
# ---------------------------------------------------------------------------


def test_a_void_partition_raises_instead_of_being_returned():
    """CH-01 was true by construction the three previous times it was voided.
    ``build_charter`` now checks the charter it actually produced, so a fourth
    regression fails at the point of corruption rather than shipping a
    member_index naming domains that do not hold those members."""
    graph = _two_fat_triangles()
    ids = [n.id for n in graph.nodes]

    # held by two domains
    with pytest.raises(RuntimeError, match="CH-01 violated"):
        _verify_partition(
            graph,
            {"a": {"direct_member_ids": ids}, "b": {"direct_member_ids": ids[:1]}},
            {mid: "a" for mid in ids},
        )
    # held by none
    with pytest.raises(RuntimeError, match="CH-01 violated"):
        _verify_partition(
            graph,
            {"a": {"direct_member_ids": ids[:-1]}},
            {mid: "a" for mid in ids[:-1]},
        )
    # member_index disagreeing with the direct blocks — the exact shape the
    # intake-slug overwrite produced
    with pytest.raises(RuntimeError, match="CH-01 violated"):
        _verify_partition(
            graph,
            {"a": {"direct_member_ids": ids}},
            {mid: "intake" for mid in ids},
        )
    # and the healthy case passes
    _verify_partition(graph, {"a": {"direct_member_ids": ids}}, {mid: "a" for mid in ids})


# ---------------------------------------------------------------------------
# the founding bound, and the no-op reorg that keeps charter.json idempotent
# ---------------------------------------------------------------------------


def test_a_graph_that_fits_one_read_is_not_worth_chartering():
    from tesserae.agent_distill import ARTIFACT_CHAR_BUDGET

    small = ResearchGraph(nodes=[_node("Concept:a", "Alpha")], edges=[])
    assert not worth_chartering(small)
    assert not worth_chartering(ResearchGraph(nodes=[], edges=[]))

    # Grown one node at a time rather than asserting on a fixture's incidental
    # size: _two_fat_triangles is 30,000 chars, i.e. BELOW the budget, so a
    # test that used it would have been asserting the bound is never reached.
    big = ResearchGraph(nodes=[], edges=[])
    while mass(big.nodes) < ARTIFACT_CHAR_BUDGET:
        big.nodes.append(_fat_node(f"Concept:big{len(big.nodes)}", 5_000))
    assert worth_chartering(big)
    big.nodes.pop()
    assert not worth_chartering(big), "the bound must be the budget, not merely near it"


def test_a_reorg_that_moves_nothing_is_recognised_as_a_no_op():
    """``succeed`` is unconditional: it bumps reorg_seq and re-stamps every
    domain ``stable`` even on an unchanged graph. Writing that every compile
    would make charter.json the one output that can never be byte-idempotent,
    and would turn reorg_seq into a compile counter."""
    graph = _two_fat_triangles_plus_orphan()
    founded = build_charter(graph)
    merged = succeed(founded, build_charter(graph))

    assert merged != founded, "succeed must still bump its bookkeeping"
    assert merged["reorg_seq"] == founded["reorg_seq"] + 1
    assert is_noop_reorg(founded, merged), "an unchanged graph is not a reorg"


def test_a_reorg_that_moves_a_member_is_not_a_no_op():
    graph = _two_fat_triangles_plus_orphan()
    founded = build_charter(graph)

    moved = json.loads(json.dumps(founded))
    victim = sorted(moved["member_index"])[0]
    moved["member_index"][victim] = "somewhere-else"
    assert not is_noop_reorg(founded, moved)

    renamed = json.loads(json.dumps(founded))
    slug = sorted(renamed["domains"])[0]
    renamed["domains"]["renamed-" + slug] = renamed["domains"].pop(slug)
    assert not is_noop_reorg(founded, renamed)

    retired = json.loads(json.dumps(founded))
    retired["domains"][sorted(retired["domains"])[0]]["status"] = "retired"
    assert not is_noop_reorg(founded, retired)


def test_repeated_no_op_reorgs_never_advance_past_the_first():
    """The property the compile relies on: derive, succeed, decline to write,
    forever. If ``is_noop_reorg`` ever went False on unchanged input, every
    compile would rewrite charter.json."""
    graph = _two_fat_triangles_plus_orphan()
    on_disk = build_charter(graph)
    for _ in range(4):
        assert is_noop_reorg(on_disk, succeed(on_disk, build_charter(graph)))


# ---------------------------------------------------------------------------
# the corpus clock
# ---------------------------------------------------------------------------
from tesserae.charter import _CLOCK_KEYS, _domain_clock, refresh_clocks


def _stamped(nid: str, stamp: str | None = None, **fields) -> ResearchNode:
    """A concept node carrying at most one rung of the ``_source_ts`` ladder."""
    return ResearchNode(
        id=nid,
        name=fields.pop("name", nid),
        type=ResearchNodeType.CONCEPT,
        metadata={"first_seen_at": stamp} if stamp else {},
        **fields,
    )


def _with_stamps(graph: ResearchGraph, stamps: dict[str, str]) -> ResearchGraph:
    """A copy of ``graph`` with ``first_seen_at`` stamped on the named nodes.

    ``dataclasses.replace`` rather than assignment: ResearchNode is frozen.
    """
    import dataclasses

    return ResearchGraph(
        nodes=[
            dataclasses.replace(
                node, metadata={**(node.metadata or {}), "first_seen_at": stamps[node.id]}
            )
            if node.id in stamps
            else node
            for node in graph.nodes
        ],
        edges=list(graph.edges),
    )


def _subtree_members(charter: dict, slug: str) -> list[str]:
    entry = charter["domains"][slug]
    members = list(entry["direct_member_ids"])
    for child in entry["child_slugs"]:
        members.extend(_subtree_members(charter, child))
    return members


def test_the_clock_is_the_latest_stamp_and_counts_what_it_could_not_date():
    clock, undated = _domain_clock(
        [
            _stamped("Concept:m1", "2026-04-06T11:02:31Z"),
            _stamped("Concept:m2", "2026-04-09"),
            _stamped("Concept:m3"),
        ]
    )
    assert clock == "2026-04-09", "the latest stamp, returned verbatim"
    assert undated == 1, (
        "a max taken over a strict subset must say so — the same reason "
        "facts_as_of returns undated_included"
    )


def test_the_clock_orders_by_instant_not_by_string():
    """Sources spell timestamps differently, and lexical order is not time
    order across offsets: 11:02+09:00 is 02:02Z, i.e. EARLIER than 05:00Z
    while sorting later as a string. Asserting the guarantee ``_latest_ts``
    makes rather than what one corpus's spellings happen to do."""
    clock, undated = _domain_clock(
        [
            _stamped("Concept:m1", "2026-04-06T11:02:31+09:00"),
            _stamped("Concept:m2", "2026-04-06T05:00:00Z"),
        ]
    )
    assert clock == "2026-04-06T05:00:00Z"
    assert undated == 0


def test_the_clock_walks_the_whole_ladder_and_the_path_rung_needs_a_root():
    """``metadata['first_seen_at']`` alone covers 2.6% of the live graph, which
    is why the spec's literal ``max(first_seen_at)`` would have raised on 738
    of 778 domains. The name and path rungs are what lift it to 81.5%.

    The path rung is gated on a declared project root: an absolute path under
    no root is a path this project's ingest did not lay out, and reading a date
    off it would let the dated worktree directory a compile happens to run from
    date the whole corpus — a wall clock one indirection removed.
    """
    named = _stamped("Concept:named", name="2026-05-01 daily digest")
    pathed = _stamped("Concept:pathed", source_path="/repo/data/2026-05-03/paper.md")

    assert _domain_clock([named]) == ("2026-05-01", 0)
    assert _domain_clock([pathed], roots=("/repo",)) == ("2026-05-03", 0)
    assert _domain_clock([pathed]) == (None, 1), (
        "no declared root must disable the path rung, not fall back to the "
        "absolute string"
    )


def test_a_domain_nothing_dates_degrades_instead_of_raising():
    """The spec hard-failed here. Measured, that denies 48 of 780 domains a
    date permanently — frozen not because they changed but because nothing
    dated them."""
    assert _domain_clock([_stamped("Concept:x"), _stamped("Concept:y")]) == (None, 2)


def test_a_router_is_dated_by_its_own_direct_block_as_well_as_its_children():
    """The spec's router rule is ``max(children.distilled_through)`` alone.
    Children partition everything below a router EXCEPT its direct block, so
    that rule dates a router earlier than content the router itself holds."""
    assert _domain_clock(
        [_stamped("Concept:own", "2026-06-02")], ["2026-01-01", None]
    ) == ("2026-06-02", 0)
    assert _domain_clock([], [None, "2026-01-01"]) == ("2026-01-01", 0), (
        "an undated child must absorb, not poison its parent's clock"
    )
    assert _domain_clock([], [None, None]) == (None, 0)


def test_every_domain_is_dated_by_the_latest_stamp_anywhere_beneath_it():
    stamps = {
        "Concept:hub-a": "2026-03-04",
        "Concept:mass-b": "2026-01-01",
        "Concept:t4": "2026-02-02",
    }
    charter = build_charter(_with_stamps(_hub_pair_starved_of_anchors(), stamps))

    routers = [s for s, e in charter["domains"].items() if e["child_slugs"]]
    assert routers, "fixture must produce a domain with children to date"

    for slug, entry in charter["domains"].items():
        members = _subtree_members(charter, slug)
        assert len(members) == entry["member_count"]
        dated = sorted(stamps[m] for m in members if m in stamps)
        assert entry["distilled_through"] == (dated[-1] if dated else None)
        assert entry["undated_member_count"] == len(members) - len(dated)
        assert entry["quality"] == ("dated" if dated else "undated")

    # hub-a is held DIRECTLY by concept-hub-b (it folded there, having been an
    # ancestor's anchor) and carries the latest stamp in the whole fixture, so
    # a router dated only by its children would report 2026-01-01 here.
    router = charter["domains"]["concept-hub-b"]
    assert "Concept:hub-a" in router["direct_member_ids"]
    assert router["distilled_through"] == "2026-03-04"
    assert max(
        charter["domains"][c]["distilled_through"] or "" for c in router["child_slugs"]
    ) < "2026-03-04"


def test_a_corpus_nothing_dates_still_produces_a_whole_charter():
    charter = build_charter(_two_fat_triangles_plus_orphan())
    assert charter["domains"], "no clock is not a reason to refuse an institution"
    for entry in charter["domains"].values():
        assert entry["distilled_through"] is None
        assert entry["quality"] == "undated"
        assert entry["undated_member_count"] == entry["member_count"]


def test_the_clock_is_never_invented_and_never_a_wall_clock():
    """Both rungs read bytes already in the graph, so the only values a charter
    can carry are values the graph carries. A ``datetime.now()`` fallback is
    the byte-idempotence leak class this repo has taken four times."""
    from datetime import date, timezone

    stamps = {"Concept:hub-a": "2026-03-04", "Concept:t4": "2026-02-02"}
    charter = build_charter(_with_stamps(_hub_pair_starved_of_anchors(), stamps))

    written = {e["distilled_through"] for e in charter["domains"].values()} - {None}
    assert written <= set(stamps.values()), (
        f"{written - set(stamps.values())} appears in no node of the graph"
    )
    today = date.today().isoformat()
    assert not any(v.startswith(today) for v in written), "a wall clock reached the charter"

    again = build_charter(_with_stamps(_hub_pair_starved_of_anchors(), stamps))
    assert again == charter, "two builds any wall-time apart must agree"


def test_a_clock_that_moved_without_the_institution_is_not_a_reorg():
    """reorg_seq counts reorganisations. A re-extraction that stamps a member
    with a later date while moving nobody between domains is a corpus that
    moved, not an institution that did."""
    prior = build_charter(
        _with_stamps(_hub_pair_starved_of_anchors(), {"Concept:hub-a": "2026-01-01"})
    )
    fresh = build_charter(
        _with_stamps(_hub_pair_starved_of_anchors(), {"Concept:hub-a": "2026-09-09"})
    )
    assert prior["domains"]["concept-hub-a"]["distilled_through"] == "2026-01-01"
    assert fresh["domains"]["concept-hub-a"]["distilled_through"] == "2026-09-09"

    assert is_noop_reorg(prior, succeed(prior, fresh))


def test_a_no_op_reorg_still_carries_the_clock_forward():
    """The counterpart to the exclusion above: excluded from identity AND
    never written is a clock that goes stale forever."""
    prior = build_charter(
        _with_stamps(_hub_pair_starved_of_anchors(), {"Concept:hub-a": "2026-01-01"})
    )
    fresh = build_charter(
        _with_stamps(_hub_pair_starved_of_anchors(), {"Concept:hub-a": "2026-09-09"})
    )
    carried = refresh_clocks(prior, succeed(prior, fresh))

    assert carried["reorg_seq"] == prior["reorg_seq"], "a clock is not a reorg"
    assert carried["domains"]["concept-hub-a"]["distilled_through"] == "2026-09-09"
    for slug, entry in carried["domains"].items():
        assert entry["transition"] == prior["domains"][slug]["transition"]
        assert {k: v for k, v in entry.items() if k not in _CLOCK_KEYS} == {
            k: v for k, v in prior["domains"][slug].items() if k not in _CLOCK_KEYS
        }, f"{slug}: refresh_clocks touched something that is not the clock"

    unchanged = build_charter(
        _with_stamps(_hub_pair_starved_of_anchors(), {"Concept:hub-a": "2026-01-01"})
    )
    assert refresh_clocks(prior, succeed(prior, unchanged)) == prior, (
        "an unchanged corpus must produce the bytes already on disk, so the "
        "compile writes nothing"
    )


def test_a_charter_written_before_the_clock_existed_gains_one_without_a_reorg():
    """The migration case, and the one that decides whether this feature is
    reachable at all: every project chartered before the clock has a
    charter.json with no clock keys, and its next compile reorganises nothing.
    If that compile declined to write, those projects would report themselves
    undated forever."""
    graph = _with_stamps(_hub_pair_starved_of_anchors(), {"Concept:hub-a": "2026-01-01"})
    prior = build_charter(graph)
    for entry in prior["domains"].values():
        for key in _CLOCK_KEYS:
            entry.pop(key)

    merged = succeed(prior, build_charter(graph))
    assert is_noop_reorg(prior, merged), "gaining a clock is not a reorganisation"

    carried = refresh_clocks(prior, merged)
    assert carried != prior, "the clock must reach disk on this compile"
    assert carried["reorg_seq"] == prior["reorg_seq"]
    assert all("distilled_through" in e for e in carried["domains"].values())
