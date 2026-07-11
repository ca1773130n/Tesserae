import json

from tesserae.project import ProjectWiki, load_graph_file
from tesserae.research_graph import ResearchNodeType


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
