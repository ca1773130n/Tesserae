from pathlib import Path

from tesserae.code_graph import CodeGraphExtractor
from tesserae.research_graph import ResearchNodeType


def test_code_graph_extractor_models_project_files_symbols_and_imports(tmp_path):
    project = tmp_path / "demo-app"
    package = project / "app"
    package.mkdir(parents=True)
    (package / "service.py").write_text(
        """import json
from pathlib import Path

class UserService:
    def load_user(self, path: Path):
        return json.loads(path.read_text())

def helper(value):
    return value
""",
        encoding="utf-8",
    )

    graph = CodeGraphExtractor(project).extract_paths([package])

    by_name = {node.name: node for node in graph.nodes}
    assert by_name["demo-app"].type == ResearchNodeType.CODE_PROJECT
    assert by_name["app/service.py"].type == ResearchNodeType.SOURCE_FILE
    assert by_name["UserService"].type == ResearchNodeType.CODE_CLASS
    assert by_name["UserService.load_user"].type == ResearchNodeType.CODE_FUNCTION
    assert by_name["helper"].type == ResearchNodeType.CODE_FUNCTION
    assert by_name["json"].type == ResearchNodeType.DEPENDENCY
    assert graph.has_edge_type("contains")
    assert graph.has_edge_type("defines")
    assert graph.has_edge_type("imports")


def test_code_graph_extractor_respects_tesserae_philosophy_metadata(tmp_path):
    project = tmp_path / "demo-app"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "api.py").write_text("def route():\n    return 'ok'\n", encoding="utf-8")

    graph = CodeGraphExtractor(project).extract_paths([src])
    file_node = next(node for node in graph.nodes if node.type == ResearchNodeType.SOURCE_FILE)

    assert file_node.metadata["layer"] == "raw-code"
    assert file_node.metadata["language"] == "python"
    assert file_node.metadata["sha256"]
