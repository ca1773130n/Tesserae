"""Bug B regression: raganything-projected source nodes surface in the
visual graph payload under the ``sources`` group.

Previously, the raganything adapter emitted ``SOURCE_FILE`` (a code-graph
type), so ``partition_graph`` routed those nodes into ``code_graph.json``
and they never reached the visual ``payload.json``. The adapter now emits
``SOURCE_DOCUMENT``, which lands in the main graph and groups correctly.

We also assert the visual payload surfaces the ``parser`` provenance flag
so the front-end can distinguish externally-projected sources from natively
extracted ones.
"""

from __future__ import annotations

from llm_wiki.research_graph import (
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from llm_wiki.site.pages import SiteContext, build_graph_payload


def _raganything_source_node() -> ResearchNode:
    return ResearchNode(
        id="SourceDocument:raganything-doc",
        name="docs/whitepaper.pdf",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        description="Whitepaper text",
        source_path="docs/whitepaper.pdf",
        metadata={
            "parser": "raganything",
            "external_system": "rag-anything",
            "external_id": "doc-abc123",
        },
    )


def test_build_graph_payload_groups_raganything_nodes_as_sources():
    """A SOURCE_DOCUMENT node with raganything metadata is visible in the
    payload, sits in the ``sources`` group, and carries its ``parser``
    provenance flag.

    The default visual-payload filter hides every ``sources`` node, so this
    test opts back in via ``show_sources=True`` to exercise the legacy
    routing rather than the visibility gate.
    """
    graph = ResearchGraph(nodes=[_raganything_source_node()], edges=[])
    ctx = SiteContext.build(
        graph=graph, wiki_pages_by_kind={}, show_sources=True
    )

    payload = build_graph_payload(ctx)

    assert payload["nodes"], "raganything source must appear in the visual payload"
    matches = [
        n for n in payload["nodes"]
        if (n.get("metadata") or {}).get("parser") == "raganything"
    ]
    assert len(matches) == 1
    node = matches[0]
    assert node["group"] == "sources"
    assert node["type"] == ResearchNodeType.SOURCE_DOCUMENT.value
    assert node["metadata"]["external_system"] == "rag-anything"


def test_build_graph_payload_omits_parser_metadata_for_native_nodes():
    """Nodes without a ``parser`` flag in their metadata don't get one
    spuriously added by the payload assembly.

    Uses ``show_sources=True`` so the native SourceDocument node survives
    the default visual-payload filter.
    """
    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="SourceDocument:native",
                name="Architecture overview",
                type=ResearchNodeType.SOURCE_DOCUMENT,
                description="",
                source_path="docs/architecture.md",
                metadata={},
            )
        ],
        edges=[],
    )
    ctx = SiteContext.build(
        graph=graph, wiki_pages_by_kind={}, show_sources=True
    )
    payload = build_graph_payload(ctx)
    assert payload["nodes"]
    node = payload["nodes"][0]
    # ``metadata`` is always present (so the JS consumer can use a single
    # access shape) but empty for natively-extracted nodes.
    assert "metadata" in node
    assert "parser" not in node["metadata"]
