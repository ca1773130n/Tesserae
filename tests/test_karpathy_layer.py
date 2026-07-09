"""Tests for the wiki-root index.md agent entrypoint (KarpathyLayerWriter)."""

from tesserae.karpathy_layer import KarpathyLayerWriter
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)


def _node(name, node_type, **kwargs):
    return ResearchNode(
        id=kwargs.pop("id", stable_id(node_type.value, name)),
        name=name,
        type=node_type,
        aliases=kwargs.pop("aliases", []),
        source_path=kwargs.pop("source_path", None),
        metadata=kwargs.pop("metadata", {}),
        description=kwargs.pop("description", ""),
    )


def _writer(tmp_path):
    return KarpathyLayerWriter(wiki_root=tmp_path, project_name="demo-project")


def _graph(nodes=(), edges=()):
    return ResearchGraph(nodes=list(nodes), edges=list(edges))


def test_index_is_agent_entrypoint_with_query_guidance(tmp_path):
    concept = _node("Gaussian Splatting", ResearchNodeType.CONCEPT)
    body = _writer(tmp_path)._render_index(_graph([concept]))
    assert body.startswith("# Index")
    assert "demo-project" in body
    assert "## How to query" in body
    # MCP-first steering, wiki-browsing second.
    assert "compile_context" in body and "search_nodes" in body
    assert "## Kinds" in body and "| concepts | 1 |" in body
    assert "`concepts/`" in body  # relative wiki-dir column
    assert "[schema.md](schema.md)" in body
    assert "[purpose.md](purpose.md)" in body


def test_index_links_contradictions_page_only_when_disputes_exist(tmp_path):
    a = _node("X beats Y", ResearchNodeType.PERFORMANCE_CLAIM)
    b = _node("Y beats X", ResearchNodeType.PERFORMANCE_CLAIM)
    w = _writer(tmp_path)
    without = w._render_index(_graph([a, b]))
    assert "contradictions.md" not in without
    with_disputes = w._render_index(_graph(
        [a, b],
        [ResearchEdge(source=a.id, target=b.id, type="contradicts_claim")],
    ))
    assert "(questions/contradictions.md)" in with_disputes


def test_index_lists_top_communities_sorted_and_capped(tmp_path):
    comms = [
        _node(f"Community {i:02d}", ResearchNodeType.COMMUNITY_SUMMARY)
        for i in range(12)
    ]
    body = _writer(tmp_path)._render_index(_graph(comms))
    assert "## Communities" in body
    assert "(communities/community-00.md)" in body
    assert "community-09" in body and "community-10" not in body  # capped at 10
    # No section at all when there are no communities.
    assert "## Communities" not in _writer(tmp_path)._render_index(_graph([]))


def test_index_render_is_deterministic_across_node_order(tmp_path):
    nodes = [
        _node("Zeta", ResearchNodeType.CONCEPT),
        _node("Alpha", ResearchNodeType.COMMUNITY_SUMMARY),
        _node("Mid", ResearchNodeType.PAPER),
    ]
    w = _writer(tmp_path)
    assert w._render_index(_graph(nodes)) == w._render_index(_graph(list(reversed(nodes))))
