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


def test_user_notes_block_is_emitted_empty_by_default(tmp_path):
    """Fresh projection always ships the start/end markers so the user has somewhere to type."""
    GraphMarkdownProjector().write_projection(sample_graph(), tmp_path)
    page = (tmp_path / "papers" / "paper-a.md").read_text(encoding="utf-8")
    assert "<!-- user-notes:start -->" in page
    assert "<!-- user-notes:end -->" in page


def test_user_notes_block_preserves_content_on_re_projection(tmp_path):
    """The whole point of the append zone: content between markers survives recompile."""
    projector = GraphMarkdownProjector()
    projector.write_projection(sample_graph(), tmp_path)
    page_path = tmp_path / "papers" / "paper-a.md"

    # User edits the file, dropping notes inside the append zone.
    original = page_path.read_text(encoding="utf-8")
    edited = original.replace(
        "<!-- user-notes:start -->\n\n<!-- user-notes:end -->",
        "<!-- user-notes:start -->\n\nMy private notes about [[gaussian-splatting]].\n\n<!-- user-notes:end -->",
    )
    page_path.write_text(edited, encoding="utf-8")

    # Re-project — should preserve the user notes verbatim.
    projector.write_projection(sample_graph(), tmp_path)
    after = page_path.read_text(encoding="utf-8")
    assert "My private notes about [[gaussian-splatting]]." in after


def test_user_link_edges_are_filtered_from_rendered_edge_sections(tmp_path):
    """Vault-authored user_link edges live in the graph but don't double-render on the page."""
    paper = ResearchNode(id="Paper:p", name="Paper", type=ResearchNodeType.PAPER)
    method = ResearchNode(id="MethodologicalConcept:m", name="Method", type=ResearchNodeType.METHODOLOGICAL_CONCEPT)
    graph = ResearchGraph(
        nodes=[paper, method],
        edges=[
            ResearchEdge(source=paper.id, target=method.id, type="uses"),
            ResearchEdge(source=paper.id, target=method.id, type="user_link"),
        ],
    )
    GraphMarkdownProjector().write_projection(graph, tmp_path)
    page = (tmp_path / "papers" / "paper.md").read_text(encoding="utf-8")
    # The ontology edge renders normally.
    assert "uses → [[method]]" in page
    # The user_link edge stays out of the rendered ## Outgoing section.
    assert "user_link" not in page
