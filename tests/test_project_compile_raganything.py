import json

from tesserae.project import ProjectWiki, load_graph_file
from tesserae.research_graph import ResearchNodeType
from tesserae.site.raw_view import safe_raw_slug


def _payload():
    return {
        "version": 1,
        "project": {"name": "demo"},
        "parser": "mineru",
        "documents": [
            {
                "id": "doc-deadbeef",
                "path": "data/paper.pdf",
                "sha256": "deadbeef" * 8,
                "parsed_dir": ".tesserae/external/raganything/parsed/deadbeef",
                "content_list": [
                    {"type": "text", "page_idx": 0, "text": "Mermaid rendering pipeline"},
                    {"type": "image", "page_idx": 0, "img_path": "x.png", "img_caption": ["Pipeline"]},
                ],
            }
        ],
    }


def test_project_compile_merges_configured_raganything_native_graph(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# demo\n", encoding="utf-8")
    artifact = project / ".tesserae" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")

    wiki = ProjectWiki.init(project, name="demo", sources=["README.md"])
    cfg = wiki.config()
    cfg["external_tools"] = [
        {
            "id": "raganything",
            "artifact": ".tesserae/external/raganything/manifest.json",
            "sync_mode": "native_graph",
            "enabled": True,
            "auto_refresh": False,
        }
    ]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    wiki.compile()

    # Bug B fix: raganything-projected docs are SOURCE_DOCUMENT nodes (not
    # SOURCE_FILE), so they land in the main graph -- where the visual
    # payload + public wiki can see them -- rather than in code_graph.json.
    graph = load_graph_file(wiki.paths.graph)
    sources = [
        n for n in graph.nodes
        if n.type == ResearchNodeType.SOURCE_DOCUMENT
        and n.metadata.get("parser") == "raganything"
    ]
    assert len(sources) == 1
    assert sources[0].metadata["external_refs"][0]["system"] == "rag-anything"
    sync = json.loads((project / ".tesserae" / "external" / "raganything-sync.json").read_text())
    assert sync["imported_documents"]["doc-deadbeef"] == sources[0].id


def test_project_compile_lands_artifact_nodes_in_the_main_graph(tmp_path):
    """Step 9 end-to-end: a compile with a resolvable image asset mints
    Artifact nodes + part_of edges into the MAIN graph (not the code
    partition), content-hash-seeded."""
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# demo\n", encoding="utf-8")
    artifact = project / ".tesserae" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")
    parsed = project / ".tesserae" / "external" / "raganything" / "parsed" / "deadbeef"
    parsed.mkdir(parents=True)
    (parsed / "x.png").write_bytes(b"\x89PNG\r\n\x1a\npipeline-figure")

    wiki = ProjectWiki.init(project, name="demo", sources=["README.md"])
    cfg = wiki.config()
    cfg["external_tools"] = [
        {
            "id": "raganything",
            "artifact": ".tesserae/external/raganything/manifest.json",
            "sync_mode": "native_graph",
            "enabled": True,
            "auto_refresh": False,
        }
    ]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    wiki.compile()
    first_bytes = wiki.paths.graph.read_bytes()

    graph = load_graph_file(wiki.paths.graph)
    artifacts = [n for n in graph.nodes if n.type == ResearchNodeType.ARTIFACT]
    assert len(artifacts) == 1
    figure = artifacts[0]
    assert figure.name == "Figure: Pipeline"
    assert len(figure.metadata["content_hash"]) == 64
    doc = next(
        n for n in graph.nodes
        if n.type == ResearchNodeType.SOURCE_DOCUMENT
        and n.metadata.get("parser") == "raganything"
    )
    edge = next(
        e for e in graph.edges
        if e.type == "part_of" and e.source == figure.id and e.target == doc.id
    )
    # The per-owner facts have to survive merge_graphs, both dedup passes and
    # the graph.json round-trip — every one of which rebuilds edges, and any
    # of which could drop metadata without anything else noticing.
    assert edge.metadata == {
        "kind": "image", "ordinal": 1, "page": 0, "caption": ["Pipeline"],
    }
    # No absolute path anywhere on the ARTIFACT node: the asset path is
    # project-relative and the id is content-seeded. (The compile pipeline
    # stores some absolute source_paths on OTHER nodes; those are prefixed
    # by the project root and normalized by the relocation idempotence test
    # — a digest of a path could not be, which is why the artifact id must
    # never contain one.)
    assert str(project) not in json.dumps(
        {"id": figure.id, "name": figure.name, "metadata": figure.metadata}
    )
    assert figure.metadata["asset_path"] == (
        ".tesserae/external/raganything/parsed/deadbeef/x.png"
    )

    # The ordinal is a pure function of content_list order, so a second
    # compile of the same manifest must reproduce the same bytes — no clock,
    # no path, nothing that drifts.
    wiki.compile()
    assert wiki.paths.graph.read_bytes() == first_bytes

def test_compile_serves_the_artifact_asset_bytes_on_the_site(tmp_path):
    """The graph asserts a figure's bytes exist, so the built site must carry
    them: content-addressed under ``raw-assets/`` and embedded by the figure's
    own raw page. Before this, an Artifact's asset reached no inlet at all --
    its ``source_path`` is the owning document and it has no wiki page."""
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# demo\n", encoding="utf-8")
    artifact = project / ".tesserae" / "external" / "raganything" / "manifest.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(_payload()), encoding="utf-8")
    parsed = project / ".tesserae" / "external" / "raganything" / "parsed" / "deadbeef"
    parsed.mkdir(parents=True)
    payload = b"\x89PNG\r\n\x1a\npipeline-figure"
    (parsed / "x.png").write_bytes(payload)

    wiki = ProjectWiki.init(project, name="demo", sources=["README.md"])
    cfg = wiki.config()
    cfg["external_tools"] = [
        {
            "id": "raganything",
            "artifact": ".tesserae/external/raganything/manifest.json",
            "sync_mode": "native_graph",
            "enabled": True,
            "auto_refresh": False,
        }
    ]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    wiki.compile()

    graph = load_graph_file(wiki.paths.graph)
    figure = next(n for n in graph.nodes if n.type == ResearchNodeType.ARTIFACT)
    digest = figure.metadata["content_hash"]
    asset = wiki.paths.site / "raw-assets" / f"{digest[:16]}.png"
    assert asset.read_bytes() == payload

    slug = safe_raw_slug(figure.metadata["asset_path"])
    page = (wiki.paths.site / "raw" / f"{slug}.html").read_text(encoding="utf-8")
    assert f"../raw-assets/{digest[:16]}.png" in page
