from tesserae.report import GraphReporter
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def sample_graph():
    paper = ResearchNode(id="Paper:p:test", name="Paper A", type=ResearchNodeType.PAPER)
    method = ResearchNode(id="MethodologicalConcept:gs:test", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT)
    trend = ResearchNode(id="Trend:gs:test", name="Trend: Gaussian Splatting", type=ResearchNodeType.TREND, metadata={"source_count": 3})
    return ResearchGraph(
        nodes=[paper, method, trend],
        edges=[
            ResearchEdge(source=paper.id, target=method.id, type="uses"),
            ResearchEdge(source=method.id, target=trend.id, type="rising_in"),
        ],
    )


def test_graph_reporter_tolerates_dangling_edge_target():
    """An edge whose target has no node (e.g. a stale finding reference) must
    not crash summarize — the sort key dereferences nodes_by_id, so dangling
    ids have to be filtered BEFORE sorting (regression: compile KeyError'd
    here on 'CodeFunction:…')."""
    insight = ResearchNode(
        id="SessionInsight:x", name="an insight", type=ResearchNodeType.SESSION_INSIGHT
    )
    graph = ResearchGraph(
        nodes=[insight],
        edges=[
            ResearchEdge(
                source=insight.id, target="CodeFunction:gone:deadbeef", type="references"
            )
        ],
    )
    report = GraphReporter().summarize(graph)  # must not raise KeyError
    top_ids = {n["id"] for n in report["top_degree_nodes"]}
    assert "CodeFunction:gone:deadbeef" not in top_ids
    assert "SessionInsight:x" in top_ids


def test_graph_reporter_summarizes_counts_and_top_nodes():
    report = GraphReporter().summarize(sample_graph())

    assert report["node_count"] == 3
    assert report["edge_count"] == 2
    assert report["node_types"] == {"MethodologicalConcept": 1, "Paper": 1, "Trend": 1}
    assert report["edge_types"] == {"rising_in": 1, "uses": 1}
    assert report["top_degree_nodes"][0]["name"] == "Gaussian Splatting"
    assert report["trends"][0]["name"] == "Trend: Gaussian Splatting"
    assert report["claim_evidence"]["total_claims"] == 0
    assert report["orphan_nodes"] == []


def test_graph_reporter_flags_claims_without_evidence_and_groups_papers_by_date():
    paper = ResearchNode(id="Paper:p:test", name="Paper A", type=ResearchNodeType.PAPER, metadata={"analysis_date": "2026-04-26"})
    claim = ResearchNode(id="Claim:c:test", name="Unsupported Claim", type=ResearchNodeType.CLAIM)
    evidence = ResearchNode(id="EvidenceSpan:e:test", name="Evidence", type=ResearchNodeType.EVIDENCE_SPAN)
    supported = ResearchNode(id="Claim:s:test", name="Supported Claim", type=ResearchNodeType.CLAIM)
    graph = ResearchGraph(
        nodes=[paper, claim, supported, evidence],
        edges=[ResearchEdge(source=supported.id, target=evidence.id, type="evidenced_by")],
    )

    report = GraphReporter().summarize(graph)

    assert report["papers_by_analysis_date"] == {"2026-04-26": 1}
    assert report["claim_evidence"] == {"total_claims": 2, "supported_claims": 1, "unsupported_claims": 1}
    assert report["claims_without_evidence"] == [{"id": claim.id, "name": claim.name, "type": "Claim"}]


def test_graph_reporter_renders_markdown():
    markdown = GraphReporter().render_markdown(GraphReporter().summarize(sample_graph()))

    assert "# Research Graph Report" in markdown
    assert "node_count: 3" in markdown
    assert "Trend: Gaussian Splatting" in markdown
