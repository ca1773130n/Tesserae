from pathlib import Path

from tesserae.research_graph import (
    ALLOWED_EDGE_TYPES,
    ALLOWED_NODE_TYPES,
    ResearchGraphExtractor,
    ResearchNodeType,
)


SAMPLE = """
# Geometry-Grounded Gaussian Splatting

Gaussian Splatting, GS는 novel view synthesis에서 인상적인 품질과 효율성을 보여주었다.
그러나 Gaussian primitive로부터 형상을 추출하는 문제는 여전히 미해결 과제이다.
본 논문에서는 Gaussian primitive를 특정한 유형의 stochastic solid로 정립하는 엄밀한 이론적 유도를 제시한다.
stochastic solid의 volumetric 특성을 활용하여, 우리 방법은 세밀한 기하 추출을 위한 고품질 depth map을 효율적으로 렌더링한다.
실험 결과, 우리 방법은 공개 데이터셋에서 모든 Gaussian Splatting 기반 방법 가운데 가장 우수한 형상 재구성 성능을 달성하였다.
"""


def test_research_extractor_uses_controlled_node_and_edge_types():
    graph = ResearchGraphExtractor().extract_text(
        SAMPLE,
        source_path="papers/2601.17835/paper.md",
        source_kind="Paper",
    )

    assert graph.nodes
    assert graph.edges
    assert {node.type for node in graph.nodes}.issubset(ALLOWED_NODE_TYPES)
    assert {edge.type for edge in graph.edges}.issubset(ALLOWED_EDGE_TYPES)
    forbidden_types = {"software", "technique", "domain", "topic", "technology", "feature", "entity"}
    assert not ({node.type.lower() for node in graph.nodes} & forbidden_types)


def test_research_extractor_models_paper_concepts_claims_and_evidence():
    graph = ResearchGraphExtractor().extract_text(
        SAMPLE,
        source_path="papers/2601.17835/paper.md",
        source_kind="Paper",
    )

    by_name = {node.name: node for node in graph.nodes}
    assert by_name["Geometry-Grounded Gaussian Splatting"].type == ResearchNodeType.PAPER
    assert by_name["Gaussian Splatting"].type == ResearchNodeType.METHODOLOGICAL_CONCEPT
    assert by_name["Novel View Synthesis"].type == ResearchNodeType.TASK
    assert by_name["Stochastic Solid"].type == ResearchNodeType.MATHEMATICAL_CONCEPT
    assert by_name["Depth Map"].type == ResearchNodeType.TECHNICAL_TERM
    assert by_name["Shape Reconstruction"].type == ResearchNodeType.TASK

    claim_nodes = [node for node in graph.nodes if node.type == ResearchNodeType.PERFORMANCE_CLAIM]
    evidence_nodes = [node for node in graph.nodes if node.type == ResearchNodeType.EVIDENCE_SPAN]
    assert claim_nodes, "performance/result claim should be represented explicitly"
    assert evidence_nodes, "claims must be grounded in EvidenceSpan nodes"
    assert graph.has_edge_type("evidenced_by")
    assert graph.has_edge_type("uses")
    assert graph.has_edge_type("addresses")


def test_research_extractor_assigns_approach_family_for_similar_papers():
    graph = ResearchGraphExtractor().extract_text(
        SAMPLE,
        source_path="papers/2601.17835/paper.md",
        source_kind="Paper",
    )

    # The extractor identifies "Geometry-Grounded Gaussian Splatting" as both
    # a Paper and an ApproachFamily. ResearchGraphBuilder.build() collapses
    # them into the canonical Paper (Paper outranks ApproachFamily in
    # _CROSS_TYPE_MERGE_PRIORITY) and records the secondary type in
    # metadata['merged_types'] so the extractor's signal is preserved.
    match = [n for n in graph.nodes if n.name == "Geometry-Grounded Gaussian Splatting"]
    assert match, "the canonical Geometry-Grounded Gaussian Splatting node must exist"
    canonical = match[0]
    assert canonical.type == ResearchNodeType.PAPER
    merged = canonical.metadata.get("merged_types") or []
    assert ResearchNodeType.APPROACH_FAMILY.value in merged, (
        "extractor must mark this paper as also being an approach family "
        "via metadata.merged_types"
    )
    assert graph.has_edge_type("belongs_to_approach_family")


def test_graph_serializes_to_json_compatible_dict():
    graph = ResearchGraphExtractor().extract_text(SAMPLE, source_path="paper.md")
    payload = graph.model_dump()

    assert payload["nodes"]
    assert payload["edges"]
    assert payload["nodes"][0]["type"] in ALLOWED_NODE_TYPES


# ---------------------------------------------------------------------------
# Session graph schema additions — Phase 1 of the session-graph plan
# (docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md).
# ---------------------------------------------------------------------------


def test_session_node_types_in_allowed_set_and_roundtrip():
    """Seven new session node types parse round-trip via the enum + appear
    in ``ALLOWED_NODE_TYPES``. Edge types: the four new session-graph
    edge labels (``derived_from_session``, ``discussed_in``,
    ``references``, ``supersedes``) are in ``ALLOWED_EDGE_TYPES``."""
    new_node_types = {
        "Session",
        "SessionInsight",
        "SessionDecision",
        "SessionQuestion",
        "SessionTODO",
        "SessionHypothesis",
        "SessionTakeaway",
    }
    for value in new_node_types:
        assert value in ALLOWED_NODE_TYPES, f"{value} missing from ALLOWED_NODE_TYPES"
        # round-trip
        assert ResearchNodeType(value).value == value

    for edge_type in ("derived_from_session", "discussed_in", "references", "supersedes"):
        assert edge_type in ALLOWED_EDGE_TYPES


def test_is_public_research_node_session_visibility():
    """The Session envelope is private (no vault page); the six finding
    types are public (each gets its own vault page)."""
    from tesserae.research_graph import ResearchNode, is_public_research_node

    private = ResearchNode(
        id="Session:abc",
        name="2026-05-19 weekly digest",
        type=ResearchNodeType.SESSION,
    )
    assert is_public_research_node(private) is False

    for kind in (
        ResearchNodeType.SESSION_INSIGHT,
        ResearchNodeType.SESSION_DECISION,
        ResearchNodeType.SESSION_QUESTION,
        ResearchNodeType.SESSION_TODO,
        ResearchNodeType.SESSION_HYPOTHESIS,
        ResearchNodeType.SESSION_TAKEAWAY,
    ):
        public = ResearchNode(
            id=f"{kind.value}:abc",
            name="Atomic writes prevent crash corruption",
            type=kind,
        )
        assert is_public_research_node(public) is True, (
            f"{kind.value} should be public"
        )


def test_session_findings_skip_aggressive_same_type_dedup():
    """Two ``SessionDecision`` nodes with identical normalized names but
    different ``metadata.session_id`` are legitimately separate provenance.
    The aggressive same-type dedup pass must NOT collapse them — that
    would lose the "which session produced this" link.

    Regression guard for the codex-flagged risk in plan v2 Phase 1.
    """
    from tesserae.research_graph import (
        ResearchEdge,
        ResearchNode,
        merge_same_type_aliased_duplicates,
    )

    a = ResearchNode(
        id="SessionDecision:session-A:dec:abc12345",
        name="Cache findings by content hash",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "session-A"},
    )
    b = ResearchNode(
        id="SessionDecision:session-B:dec:def67890",
        name="Cache findings by content hash",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "session-B"},
    )

    nodes, edges = merge_same_type_aliased_duplicates([a, b], [])
    assert len(nodes) == 2, (
        "session findings with identical text from different sessions "
        "must survive aggressive same-type dedup"
    )
    surviving_ids = {n.id for n in nodes}
    assert a.id in surviving_ids
    assert b.id in surviving_ids
    assert edges == []  # nothing to merge

    # Sanity check: a same-named MethodologicalConcept pair WOULD collapse,
    # proving the test isn't trivially passing.
    c = ResearchNode(
        id="MethodologicalConcept:foo",
        name="Pre-Training",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
    )
    d = ResearchNode(
        id="MethodologicalConcept:bar",
        name="pretraining",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
    )
    nodes, _ = merge_same_type_aliased_duplicates([c, d], [])
    assert len(nodes) == 1, "non-session same-type pair should collapse"
