"""Tests for the synthetic contradictions page emitted by WikiLayerProjector."""

from tesserae.project import ProjectWiki
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)
from tesserae.wiki_projector import WikiLayerProjector
from tesserae.wiki_store import WikiPageStore


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


def _contradiction_graph():
    open_a = _node("GaussFlow outperforms SplatMix on PSNR", ResearchNodeType.PERFORMANCE_CLAIM)
    open_b = _node("GaussFlow is outperformed by SplatMix on PSNR", ResearchNodeType.PERFORMANCE_CLAIM)
    loser = _node("NeRFa beats NeRFb on speed", ResearchNodeType.COMPARISON_CLAIM)
    winner = _node("NeRFb beats NeRFa on speed", ResearchNodeType.COMPARISON_CLAIM)
    older = _node("Use the v1 chunking strategy", ResearchNodeType.SESSION_INSIGHT)
    newer = _node("Use the v2 chunking strategy", ResearchNodeType.SESSION_INSIGHT)
    return ResearchGraph(
        nodes=[open_a, open_b, loser, winner, older, newer],
        edges=[
            ResearchEdge(source=open_a.id, target=open_b.id, type="contradicts_claim"),
            ResearchEdge(source=loser.id, target=winner.id, type="contradicts_claim"),
            ResearchEdge(
                source=loser.id,
                target=winner.id,
                type="resolved_by",
                evidence="newer benchmark run wins",
            ),
            # ``source supersedes target`` — source is the newer finding.
            ResearchEdge(source=newer.id, target=older.id, type="supersedes"),
        ],
    )


def test_contradictions_page_renders_open_resolved_and_obsoleted_sections(tmp_path):
    graph = _contradiction_graph()

    written = WikiLayerProjector(WikiPageStore(tmp_path)).project(graph)

    path = tmp_path / "questions" / "contradictions.md"
    assert path.exists()
    assert any(page.slug == "contradictions" for page in written)
    text = path.read_text(encoding="utf-8")

    assert "## Open" in text
    # Open pairs are unordered (rendered in node-id sort order): assert both
    # sides are present rather than pinning a direction.
    open_section = text.split("## Open", 1)[1].split("## Resolved", 1)[0]
    assert "**GaussFlow outperforms SplatMix on PSNR**" in open_section
    assert "**GaussFlow is outperformed by SplatMix on PSNR**" in open_section
    assert "↔" in open_section
    # The resolved pair must not be listed as open.
    assert "NeRFa beats NeRFb" not in open_section

    assert "## Resolved" in text
    assert "**NeRFa beats NeRFb on speed** → resolved by **NeRFb beats NeRFa on speed** — newer benchmark run wins" in text

    assert "## Obsoleted" in text
    assert "**Use the v1 chunking strategy** → superseded by **Use the v2 chunking strategy**" in text


def test_contradictions_page_is_byte_idempotent_across_compiles(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    WikiLayerProjector(WikiPageStore(first_root)).project(_contradiction_graph())
    WikiLayerProjector(WikiPageStore(second_root)).project(_contradiction_graph())
    first = (first_root / "questions" / "contradictions.md").read_bytes()
    second = (second_root / "questions" / "contradictions.md").read_bytes()
    assert first == second

    # Re-projecting into the same store must not rewrite the page.
    rewritten = WikiLayerProjector(WikiPageStore(first_root)).project(_contradiction_graph())
    assert not any(page.slug == "contradictions" for page in rewritten)
    assert (first_root / "questions" / "contradictions.md").read_bytes() == first


def test_compile_reflects_memory_pass_resolutions_in_same_compile(tmp_path, monkeypatch):
    """A ``resolved_by`` edge minted by the KB-04 memory pass during compile N
    must land in compile N's contradictions.md (the full wiki projection runs
    BEFORE the memory passes), and two compiles over the same corpus must
    produce byte-identical output."""
    monkeypatch.setenv("TESSERAE_COMMUNITY_SUMMARIES", "false")
    project = tmp_path / "proj"
    (project / "data").mkdir(parents=True)
    (project / "data" / "a.md").write_text(
        "---\ntype: paper\n---\n# Graph Neural Networks\n\nbody a\n", encoding="utf-8"
    )
    (project / "data" / "b.md").write_text(
        "---\ntype: paper\n---\n# Splat Transport Fields\n\nbody b\n", encoding="utf-8"
    )
    wiki = ProjectWiki.init(project, name="contradictions_tail")

    real_passes = ProjectWiki._run_memory_passes

    def _minting_passes(self, graph, json_client, store=None):
        graph, rows = real_passes(self, graph, json_client, store=store)
        # Deterministically mint one resolved_by edge between the two
        # lowest-id nodes, exactly as the contradiction pass would (idempotent
        # across compiles: skip if the edge already exists).
        loser, winner = sorted(graph.nodes, key=lambda node: node.id)[:2]
        if not any(
            edge.source == loser.id and edge.target == winner.id and edge.type == "resolved_by"
            for edge in graph.edges
        ):
            graph.edges.append(
                ResearchEdge(
                    source=loser.id,
                    target=winner.id,
                    type="resolved_by",
                    evidence="minted during this compile",
                )
            )
        return graph, rows

    monkeypatch.setattr(ProjectWiki, "_run_memory_passes", _minting_passes)

    wiki.compile()
    page = wiki.paths.wiki / "questions" / "contradictions.md"
    assert page.exists(), "contradictions.md missing after compile"
    first = page.read_bytes()
    assert b"## Resolved" in first
    assert b"minted during this compile" in first

    wiki.compile()
    assert page.read_bytes() == first, (
        "contradictions.md not byte-identical across two compiles of the same corpus"
    )


def test_no_contradictions_page_when_graph_has_no_dispute_edges(tmp_path):
    concept = _node("Gaussian Splatting", ResearchNodeType.CONCEPT)
    paper = _node(
        "Geometry-Grounded Gaussian Splatting",
        ResearchNodeType.PAPER,
        source_path="data/research/daily/2026-04-15/papers/2601.17835/paper.md",
        metadata={"arxiv_id": "2601.17835", "title_quality": "paper_file"},
    )
    graph = ResearchGraph(
        nodes=[concept, paper],
        edges=[ResearchEdge(source=paper.id, target=concept.id, type="uses")],
    )

    WikiLayerProjector(WikiPageStore(tmp_path)).project(graph)

    assert not (tmp_path / "questions" / "contradictions.md").exists()
