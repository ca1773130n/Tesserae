"""Graph View v1 — build-time ``family`` + ``importance`` encoding tests.

These exercise :func:`tesserae.site.pages.build_graph_payload` (the visual
graph payload serializer) for the per-node scalars the interactive view's
colour + size encoding read off each node (spec §A/§B):

* ``family`` — one of 8 families (or ``"other"`` fallback for unknown types).
* ``importance`` — a positive per-type raw signal.
* ``member_count`` — present on CommunitySummary (outgoing ``summarizes``).
"""

from __future__ import annotations

from tesserae.research_graph import ResearchGraphBuilder, ResearchNodeType
from tesserae.site.pages import (
    SiteContext,
    _family_for_node_type,
    _is_hidden_group_node,
    build_graph_payload,
)


def _payload_by_name(graph) -> dict:
    ctx = SiteContext.build(graph=graph, wiki_pages_by_kind={}, show_sources=True)
    payload = build_graph_payload(ctx)
    return {n["name"]: n for n in payload["nodes"]}


def test_family_map_covers_each_family_and_falls_back_to_other():
    # Spot-check one representative type per family.
    assert _family_for_node_type(ResearchNodeType.RESEARCH_TOPIC) == "taxonomy"
    assert _family_for_node_type(ResearchNodeType.PAPER) == "sources"
    assert _family_for_node_type(ResearchNodeType.CODE_FUNCTION) == "code"
    assert _family_for_node_type(ResearchNodeType.METHODOLOGICAL_CONCEPT) == "concepts"
    assert _family_for_node_type(ResearchNodeType.OPEN_QUESTION) == "claims"
    assert _family_for_node_type(ResearchNodeType.COMMUNITY_SUMMARY) == "synthesis"
    assert _family_for_node_type(ResearchNodeType.SESSION_INSIGHT) == "sessions"
    assert _family_for_node_type(ResearchNodeType.ORGANIZATION) == "actors"
    # Unknown / unmapped type → neutral "other".
    assert _family_for_node_type(ResearchNodeType.STUB) == "other"


def test_payload_emits_family_and_positive_importance_per_type():
    b = ResearchGraphBuilder()
    # CommunitySummary with 3 outgoing ``summarizes`` edges → member_count 3.
    community = b.add_node("Community A", ResearchNodeType.COMMUNITY_SUMMARY)
    m1 = b.add_node("Member 1", ResearchNodeType.CONCEPT)
    m2 = b.add_node("Member 2", ResearchNodeType.CONCEPT)
    m3 = b.add_node("Member 3", ResearchNodeType.CONCEPT)
    b.add_edge(community, "summarizes", m1)
    b.add_edge(community, "summarizes", m2)
    b.add_edge(community, "summarizes", m3)

    # CodeFunction with 2 incoming ``calls`` → fan-in importance 2.
    fn = b.add_node("target_fn", ResearchNodeType.CODE_FUNCTION)
    caller_a = b.add_node("caller_a", ResearchNodeType.CODE_FUNCTION)
    caller_b = b.add_node("caller_b", ResearchNodeType.CODE_FUNCTION)
    b.add_edge(caller_a, "calls", fn)
    b.add_edge(caller_b, "calls", fn)

    # SessionInsight with a decay_score in metadata.
    b.add_node(
        "Insight X",
        ResearchNodeType.SESSION_INSIGHT,
        metadata={"decay_score": 0.75},
    )

    # Paper with degree 4 (four references out to four concepts).
    paper = b.add_node("Big Paper", ResearchNodeType.PAPER)
    for i in range(4):
        c = b.add_node(f"Concept {i}", ResearchNodeType.CONCEPT)
        b.add_edge(paper, "references", c)

    by_name = _payload_by_name(b.build())

    # Families.
    assert by_name["Community A"]["family"] == "synthesis"
    assert by_name["target_fn"]["family"] == "code"
    assert by_name["Insight X"]["family"] == "sessions"
    assert by_name["Big Paper"]["family"] == "sources"

    # Importance is present + positive on each.
    for name in ("Community A", "target_fn", "Insight X", "Big Paper"):
        assert by_name[name]["importance"] > 0, name

    # Per-type raw signal.
    assert by_name["Community A"]["member_count"] == 3
    assert by_name["Community A"]["importance"] == 3  # outgoing summarizes
    assert by_name["target_fn"]["importance"] == 2     # incoming calls fan-in
    assert by_name["Insight X"]["importance"] == 0.75  # decay_score
    assert by_name["Big Paper"]["importance"] == 4      # weighted degree


def test_member_count_only_on_community_summary():
    b = ResearchGraphBuilder()
    paper = b.add_node("Solo Paper", ResearchNodeType.PAPER)
    concept = b.add_node("Solo Concept", ResearchNodeType.CONCEPT)
    b.add_edge(paper, "references", concept)
    by_name = _payload_by_name(b.build())
    assert "member_count" not in by_name["Solo Paper"]
    assert "member_count" not in by_name["Solo Concept"]


def test_source_file_node_survives_default_show_sources_false():
    """Regression — codex PR #21 P2.

    ``SOURCE_FILE`` is a code-graph node type even though its family/kind
    maps to ``"sources"`` for visual coherence. Default
    ``show_sources=False`` MUST NOT strip it — otherwise code-ingested
    projects render with floating functions and no parent file nodes.
    """
    # The only ``sources``-group hide-by-default today is SOURCE_DOCUMENT;
    # SOURCE_FILE is a code-graph type that previously got swept up by the
    # shared "sources" kind label. Carve-out keeps it visible.
    b = ResearchGraphBuilder()
    doc_node = b.add_node("Hide Me Doc", ResearchNodeType.SOURCE_DOCUMENT)
    file_node = b.add_node("keep_me.py", ResearchNodeType.SOURCE_FILE)
    fn_node = b.add_node("keep_me_fn", ResearchNodeType.CODE_FUNCTION)
    b.add_edge(file_node, "contains", fn_node)
    graph = b.build()

    assert _is_hidden_group_node(doc_node) is True
    assert _is_hidden_group_node(file_node) is False

    # Default-knob payload: SOURCE_FILE survives, SOURCE_DOCUMENT does not.
    ctx = SiteContext.build(graph=graph, wiki_pages_by_kind={})  # show_sources defaults False
    payload = build_graph_payload(ctx)
    names = {n["name"] for n in payload["nodes"]}
    assert "keep_me.py" in names
    assert "keep_me_fn" in names
    assert "Hide Me Doc" not in names

    # The incident ``contains`` edge must survive too.
    by_name = {n["name"]: n["id"] for n in payload["nodes"]}
    edge_pairs = {(e["source"], e["target"]) for e in payload["links"]}
    assert (by_name["keep_me.py"], by_name["keep_me_fn"]) in edge_pairs
