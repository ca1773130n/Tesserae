from llm_wiki.markdown_projection import GraphMarkdownProjector, slugify
from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def sample_graph():
    paper = ResearchNode(id="Paper:p:test", name="Paper A", type=ResearchNodeType.PAPER, metadata={"arxiv_id": "2601.00001"})
    method = ResearchNode(id="MethodologicalConcept:gs:test", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT, aliases=["3DGS"])
    claim = ResearchNode(id="Claim:c:test", name="Claim: improves rendering", type=ResearchNodeType.PERFORMANCE_CLAIM, description="improves rendering speed")
    evidence = ResearchNode(id="EvidenceSpan:e:test", name="Evidence: improves rendering", type=ResearchNodeType.EVIDENCE_SPAN, description="The method improves rendering speed.", source_path="paper.md")
    return ResearchGraph(
        nodes=[paper, method, claim, evidence],
        edges=[
            ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="uses Gaussian Splatting"),
            ResearchEdge(source=paper.id, target=claim.id, type="supports_claim"),
            ResearchEdge(source=claim.id, target=evidence.id, type="evidenced_by"),
        ],
    )


def test_slugify_is_stable_for_research_names():
    assert slugify("Gaussian Splatting") == "gaussian-splatting"
    assert slugify("3D/4D Vision and Reconstruction") == "3d-4d-vision-and-reconstruction"


def test_markdown_projector_writes_human_readable_projection(tmp_path):
    projector = GraphMarkdownProjector()
    written = projector.write_projection(sample_graph(), tmp_path)

    concept_path = tmp_path / "concepts" / "gaussian-splatting.md"
    paper_path = tmp_path / "papers" / "paper-a.md"
    index_path = tmp_path / "index.md"
    assert concept_path in written
    assert paper_path in written
    assert index_path in written

    concept = concept_path.read_text(encoding="utf-8")
    assert "node_id: MethodologicalConcept:gs:test" in concept
    assert "title: Gaussian Splatting" in concept
    assert "type: MethodologicalConcept" in concept
    assert "aliases: [3DGS]" in concept
    assert "[[paper-a]]" in concept

    paper = paper_path.read_text(encoding="utf-8")
    assert "node_id: Paper:p:test" in paper
    assert "arxiv_id: 2601.00001" in paper
    assert "uses → [[gaussian-splatting]]" in paper
    assert "supports_claim" in paper

    index = index_path.read_text(encoding="utf-8")
    assert "# Research Graph Projection Index" in index
    assert "[[gaussian-splatting]]" in index
    assert "[[paper-a]]" in index


def test_slugify_truncates_long_names_with_stable_hash_suffix():
    long_name = "가" * 300
    slug = slugify(long_name)

    assert len(slug.encode("utf-8")) <= 180
    assert slug == slugify(long_name)
    assert "-" in slug


def test_markdown_projector_handles_very_long_titles(tmp_path):
    graph = ResearchGraph(nodes=[ResearchNode(id="Paper:long:test", name="가" * 300, type=ResearchNodeType.PAPER)], edges=[])

    written = GraphMarkdownProjector().write_projection(graph, tmp_path)

    assert any(path.name.endswith(".md") for path in written)
    assert all(len(path.name.encode("utf-8")) <= 190 for path in written if path.name != "index.md")
