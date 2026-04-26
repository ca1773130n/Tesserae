import json

from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from llm_wiki.temporal import TemporalFactProjector, render_competitive_report


def temporal_sample_graph():
    paper = ResearchNode(
        id="Paper:a",
        name="Paper A",
        type=ResearchNodeType.PAPER,
        source_path="papers/a.md",
        metadata={"analysis_date": "2026-04-20"},
    )
    method = ResearchNode(
        id="Method:gs",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
    )
    claim_old = ResearchNode(
        id="Claim:old",
        name="Claim: old result",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="Old result claim",
        source_path="papers/a.md",
        metadata={"confidence": "medium"},
    )
    claim_new = ResearchNode(
        id="Claim:new",
        name="Claim: new result",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="New result claim",
        source_path="papers/b.md",
        metadata={"analysis_date": "2026-04-27", "confidence": "high"},
    )
    return ResearchGraph(
        nodes=[paper, method, claim_old, claim_new],
        edges=[
            ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="Paper A uses Gaussian Splatting"),
            ResearchEdge(source=paper.id, target=claim_old.id, type="supports_claim", evidence="old evidence"),
            ResearchEdge(source=claim_new.id, target=claim_old.id, type="contradicts_claim", evidence="new evidence contradicts old evidence"),
        ],
    )


def test_temporal_fact_projector_adds_provenance_validity_and_current_status():
    facts = TemporalFactProjector().project(temporal_sample_graph())

    uses_fact = next(fact for fact in facts if fact.predicate == "uses")
    contradiction = next(fact for fact in facts if fact.predicate == "contradicts_claim")
    old_claim_fact = next(fact for fact in facts if fact.object_id == "Claim:old" and fact.predicate == "supports_claim")

    assert uses_fact.valid_from == "2026-04-20"
    assert uses_fact.provenance["source_path"] == "papers/a.md"
    assert uses_fact.evidence == "Paper A uses Gaussian Splatting"
    assert contradiction.confidence == "high"
    assert old_claim_fact.invalidated_by == [contradiction.id]
    assert old_claim_fact.current is False


def test_temporal_fact_projector_writes_jsonl(tmp_path):
    output = tmp_path / "temporal_facts.jsonl"

    facts = TemporalFactProjector().write_jsonl(temporal_sample_graph(), output)

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(facts)
    assert rows[0]["subject_name"]
    assert "provenance" in rows[0]


def test_competitive_report_documents_absorbed_open_source_advantages():
    report = render_competitive_report()

    assert "MegaMem" in report
    assert "Graphiti" in report
    assert "temporal facts" in report
    assert "controlled ontology" in report
    assert "no API key" in report
