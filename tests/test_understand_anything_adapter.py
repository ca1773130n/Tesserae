import json
from pathlib import Path

from llm_wiki.project import ProjectWiki, load_graph_file
from llm_wiki.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from llm_wiki.understand_anything_adapter import UnderstandAnythingGraphAdapter, merge_understand_anything_graph


def test_understand_anything_adapter_imports_nodes_edges_and_manifest(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    artifact = project / ".understand-anything" / "knowledge-graph.json"
    artifact.parent.mkdir()
    artifact.write_text(
        json.dumps(
            {
                "project": {"name": "demo"},
                "nodes": [
                    {"id": "file:src/app.py", "name": "src/app.py", "type": "file", "summary": "Entry file", "filePath": "src/app.py"},
                    {"id": "concept:Mermaid rendering", "name": "Mermaid rendering", "type": "concept", "summary": "Diagram rendering path"},
                    {"id": "fn:render_mermaid", "name": "render_mermaid", "type": "function", "summary": "Renders diagrams", "filePath": "src/app.py"},
                ],
                "edges": [
                    {"source": "file:src/app.py", "target": "fn:render_mermaid", "type": "contains"},
                    {"source": "fn:render_mermaid", "target": "concept:Mermaid rendering", "type": "uses"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = UnderstandAnythingGraphAdapter(project).import_artifact(artifact)

    by_name = {node.name: node for node in result.graph.nodes}
    assert by_name["src/app.py"].type == ResearchNodeType.SOURCE_FILE
    assert by_name["render_mermaid"].type == ResearchNodeType.CODE_FUNCTION
    concept = by_name["Mermaid rendering"]
    assert concept.type == ResearchNodeType.CONCEPT
    assert concept.metadata["external_refs"][0]["system"] == "understand-anything"
    assert concept.metadata["external_refs"][0]["id"] == "concept:Mermaid rendering"
    assert any(edge.type == "contains" for edge in result.graph.edges)
    assert any(edge.type == "uses" for edge in result.graph.edges)
    assert result.manifest["artifact_sha256"]
    assert result.manifest["imported_nodes"]["concept:Mermaid rendering"] == concept.id


def test_understand_anything_concepts_merge_with_existing_llm_wiki_concepts(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    ua_graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="Concept:mermaid-rendering",
                name="Mermaid rendering",
                type=ResearchNodeType.CONCEPT,
                description="Existing compiled concept",
            )
        ],
        edges=[],
    )
    artifact = project / ".understand-anything" / "knowledge-graph.json"
    artifact.parent.mkdir()
    artifact.write_text(
        json.dumps({"nodes": [{"id": "ua-1", "name": "Mermaid Rendering", "type": "concept", "summary": "UA view"}], "edges": []}),
        encoding="utf-8",
    )

    merged, manifest = merge_understand_anything_graph(ua_graph, project_root=project, artifact=artifact)

    concepts = [node for node in merged.nodes if node.type == ResearchNodeType.CONCEPT and node.name.lower() == "mermaid rendering"]
    assert len(concepts) == 1
    concept = concepts[0]
    assert concept.description == "Existing compiled concept"
    assert concept.metadata["external_refs"][0]["id"] == "ua-1"
    assert manifest["imported_nodes"]["ua-1"] == concept.id


def test_project_compile_merges_configured_understand_anything_native_graph(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n\nMermaid rendering is documented here.\n", encoding="utf-8")
    artifact = project / ".understand-anything" / "knowledge-graph.json"
    artifact.parent.mkdir()
    artifact.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "ua-concept", "name": "Mermaid rendering", "type": "concept", "summary": "UA concept"},
                    {"id": "ua-fn", "name": "render_mermaid", "type": "function", "filePath": "src/app.py"},
                ],
                "edges": [{"source": "ua-fn", "target": "ua-concept", "type": "uses"}],
            }
        ),
        encoding="utf-8",
    )

    wiki = ProjectWiki.init(project, name="demo", sources=["README.md"])
    cfg = wiki.config()
    cfg["external_tools"] = [
        {
            "id": "understand-anything",
            "artifact": ".understand-anything/knowledge-graph.json",
            "source": ".llm-wiki/external/understand-anything.md",
            "sync_mode": "native_graph",
            "enabled": True,
            "auto_refresh": False,
        }
    ]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    result = wiki.compile(cognify=None)

    assert result["node_count"] >= 3
    graph = load_graph_file(wiki.paths.graph)
    concept = next(node for node in graph.nodes if node.name == "Mermaid rendering" and node.type == ResearchNodeType.CONCEPT)
    assert concept.metadata["external_refs"][0]["system"] == "understand-anything"
    sync_manifest = project / ".llm-wiki" / "external" / "understand-anything-sync.json"
    assert sync_manifest.exists()
    manifest = json.loads(sync_manifest.read_text(encoding="utf-8"))
    assert manifest["imported_nodes"]["ua-concept"] == concept.id
