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

from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.site.pages import SiteContext, build_graph_payload


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


def test_build_graph_payload_hides_artifact_evidence_nodes():
    """An Artifact (roadmap step 9) stays off the interactive canvas.

    Decided rather than deferred: an Artifact is evidence, bucketed with
    EvidenceSpan in ``ASSERTION_LAYER_TYPES``, and the whole assertion layer
    is off-canvas. ``show_sources=True`` here so the owning document IS
    visible — that isolates the type allow-list as the only thing keeping
    the Artifact out, and proves the ``part_of`` edge goes with it (an edge
    whose endpoint was dropped must not survive into the payload and dangle).

    Without this test the exclusion is indistinguishable from the oversight
    the ``_GRAPH_VIEW_EXTRA_TYPES`` comment warns about for finding types:
    a node type absent from the allow-list is invisible on the site with
    nothing to notice it by, so a later reader "fixes" it back in.
    """
    doc = _raganything_source_node()
    artifact = ResearchNode(
        id="Artifact:cafe",
        name="Figure: pipeline overview",
        type=ResearchNodeType.ARTIFACT,
        description="",
        metadata={"parser": "raganything", "kind": "image",
                  "content_hash": "cafe" * 16},
    )
    graph = ResearchGraph(
        nodes=[doc, artifact],
        edges=[ResearchEdge(source=artifact.id, target=doc.id, type="part_of")],
    )
    ctx = SiteContext.build(
        graph=graph, wiki_pages_by_kind={}, show_sources=True
    )

    payload = build_graph_payload(ctx)

    types = {n["type"] for n in payload["nodes"]}
    assert ResearchNodeType.SOURCE_DOCUMENT.value in types
    assert ResearchNodeType.ARTIFACT.value not in types
    assert not [e for e in payload["links"] if e.get("type") == "part_of"]


def test_assertion_layer_stays_off_the_graph_canvas():
    """No evidence type may drift onto the canvas via the allow-list.

    Artifact's exclusion is one instance of a rule that covers every Claim
    variant and EvidenceSpan too, so pin the rule: adding any assertion-layer
    type to ``_GRAPH_VIEW_EXTRA_TYPES`` fails here, and a new evidence type
    added to ``ASSERTION_LAYER_TYPES`` inherits the guarantee for free.
    """
    from tesserae.site.pages import _FAMILY_BY_TYPE, _GRAPH_VIEW_TYPES
    from tesserae.wiki_projector import ASSERTION_LAYER_TYPES

    assertion_values = {t.value for t in ASSERTION_LAYER_TYPES}
    assert ResearchNodeType.ARTIFACT.value in assertion_values
    assert not (assertion_values & _GRAPH_VIEW_TYPES)
    # No family entry either — ``_FAMILY_BY_TYPE`` is consumed only by the
    # payload, and its keys must be mirrored by FAMILY_COLORS/FAMILY_HSL/
    # FAMILY_LABELS in js.py. An entry for a type the payload never emits is
    # dead weight on a hand-mirrored map.
    assert ResearchNodeType.ARTIFACT.value not in _FAMILY_BY_TYPE
