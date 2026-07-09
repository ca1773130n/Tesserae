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


def test_has_contradictions_true_only_for_dispute_edges_with_known_endpoints():
    from tesserae.wiki_projector import has_contradictions

    # Full dispute graph -> True.
    assert has_contradictions(_contradiction_graph())

    # No dispute edges -> False.
    a = _node("A", ResearchNodeType.PERFORMANCE_CLAIM)
    b = _node("B", ResearchNodeType.PERFORMANCE_CLAIM)
    plain = ResearchGraph(nodes=[a, b], edges=[
        ResearchEdge(source=a.id, target=b.id, type="uses"),
    ])
    assert not has_contradictions(plain)

    # Dispute edge with a missing endpoint -> False (matches page emission).
    dangling = ResearchGraph(nodes=[a], edges=[
        ResearchEdge(source=a.id, target="missing", type="contradicts_claim"),
    ])
    assert not has_contradictions(dangling)


# --- Community-page ``## Sources`` footer ------------------------------------


def _community_graph(member_paths, extra_members_without_paths=0):
    members = [
        _node(f"Member {i:02d}", ResearchNodeType.CONCEPT, source_path=p)
        for i, p in enumerate(member_paths)
    ]
    members += [
        _node(f"Pathless {i:02d}", ResearchNodeType.CONCEPT)
        for i in range(extra_members_without_paths)
    ]
    community = _node("Cluster Alpha", ResearchNodeType.COMMUNITY_SUMMARY,
                      description="A cluster.")
    edges = [
        ResearchEdge(source=community.id, target=m.id, type="summarizes")
        for m in members
    ]
    return community, ResearchGraph(nodes=[community, *members], edges=edges)


def test_community_page_lists_sorted_deduped_member_source_files(tmp_path):
    community, graph = _community_graph(
        ["data/b.md", "data/a.md", "data/b.md"]  # unsorted + duplicate
    )
    store = WikiPageStore(tmp_path)
    WikiLayerProjector(store).project(graph)
    page = store.read_page(store.path_for("communities", store.slug_for(community.name)))
    assert "## Sources" in page.body
    assert page.body.index("- `data/a.md`") < page.body.index("- `data/b.md`")
    assert page.body.count("- `data/b.md`") == 1
    assert page.frontmatter["sources"] == ["data/a.md", "data/b.md"]


def test_community_sources_capped_with_deterministic_more_line(tmp_path):
    community, graph = _community_graph([f"data/{i:03d}.md" for i in range(30)])
    store = WikiPageStore(tmp_path)
    WikiLayerProjector(store).project(graph)
    page = store.read_page(store.path_for("communities", store.slug_for(community.name)))
    assert "- `data/024.md`" in page.body and "- `data/025.md`" not in page.body
    assert "…and 5 more" in page.body
    assert len(page.frontmatter["sources"]) == 30  # frontmatter uncapped


def test_community_page_omits_sources_when_members_lack_source_path(tmp_path):
    community, graph = _community_graph([], extra_members_without_paths=3)
    store = WikiPageStore(tmp_path)
    WikiLayerProjector(store).project(graph)
    page = store.read_page(store.path_for("communities", store.slug_for(community.name)))
    assert "## Sources" not in page.body
    assert "sources" not in page.frontmatter


def test_non_community_pages_gain_no_sources_section(tmp_path):
    concept = _node("Plain Concept", ResearchNodeType.CONCEPT,
                    source_path="data/x.md")
    store = WikiPageStore(tmp_path)
    WikiLayerProjector(store).project(ResearchGraph(nodes=[concept], edges=[]))
    page = store.read_page(store.path_for("concepts", store.slug_for(concept.name)))
    assert "## Sources" not in page.body
    assert "sources" not in page.frontmatter  # source_path frontmatter is enough


def test_community_sources_deterministic_across_node_and_edge_order(tmp_path):
    community, graph = _community_graph(["data/c.md", "data/a.md", "data/b.md"])
    reordered = ResearchGraph(
        nodes=list(reversed(graph.nodes)), edges=list(reversed(graph.edges))
    )
    store_a = WikiPageStore(tmp_path / "a")
    store_b = WikiPageStore(tmp_path / "b")
    WikiLayerProjector(store_a).project(graph)
    WikiLayerProjector(store_b).project(reordered)
    slug = store_a.slug_for(community.name)
    text_a = store_a.path_for("communities", slug).read_text(encoding="utf-8")
    text_b = store_b.path_for("communities", slug).read_text(encoding="utf-8")
    assert text_a == text_b
