"""Tests for the MCP ``ingest`` tool — web-clip ingestion on the active project.

The tool resolves the active project to a ``ProjectWiki`` and calls
``tesserae.clip.ingest_clip`` with the supplied clip payload.

No real network and no real LLM are exercised:

* The summarizer (``tesserae.clip._summarize``) is monkeypatched to a fixed
  string, so the TL;DR path never shells out to the Claude CLI.
* ``tesserae.clip.ingest_sources`` is monkeypatched with a recorder that
  *writes a node into graph.json* and returns a report — so no real
  ``wiki.compile()`` / extraction runs, yet we can assert the on-disk graph
  genuinely gained a node.
"""

import json
from pathlib import Path

import pytest


def _write_minimal_project(project: Path) -> None:
    """Create a minimal .tesserae layout with an empty graph.json the registry accepts."""
    cfg_dir = project / ".tesserae"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": "demo",
        "sources": ["README.md"],
        "external_tools": [],
        "memory_backends": {
            "raganything": {"enabled": False},
            "cognee": {"enabled": False},
        },
    }
    (cfg_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (cfg_dir / "graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )
    (project / "README.md").write_text("# demo", encoding="utf-8")


def test_mcp_lists_ingest_tool():
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = LLMWikiMCPServer().list_tools()
    by_name = {tool["name"]: tool for tool in tools}
    assert "ingest" in by_name
    schema = by_name["ingest"]["inputSchema"]
    assert "content" in schema["properties"]
    # content is the required field per the agreed interface.
    assert "content" in schema.get("required", [])
    # Optional knobs the contract documents.
    for opt in ("url", "title", "note", "tags", "tldr"):
        assert opt in schema["properties"]


def test_mcp_ingest_ingests_clip_and_graph_gains_node(tmp_path, monkeypatch):
    from tesserae.mcp_server import LLMWikiMCPServer

    project = tmp_path / "demo"
    _write_minimal_project(project)

    graph_path = project / ".tesserae" / "graph.json"

    # No real LLM: fixed TL;DR.
    import tesserae.clip as clip
    monkeypatch.setattr(clip, "_summarize", lambda content: "fixed tldr")

    # No real compile: the recorder writes a node into graph.json, mimicking a
    # compile that ingested the clip, then reports the new counts.
    # ``ingest_clip`` lazily imports ``ingest_sources`` from the orchestrator,
    # so we patch it on its defining module.
    def _fake_ingest_sources(wiki, inputs, **kwargs):
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["nodes"].append({"id": "doc:web-clip", "kind": "Document"})
        graph_path.write_text(json.dumps(graph), encoding="utf-8")
        return {
            "path_taken": "incremental",
            "sources": list(inputs),
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "processed_files": list(inputs),
            "skipped_files": [],
            "graph_path": str(graph_path),
        }

    monkeypatch.setattr(
        "tesserae.ingest.orchestrator.ingest_sources", _fake_ingest_sources
    )

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(project), name="demo")

    result = server.call_tool(
        "ingest",
        {
            "content": "The article body to clip.",
            "url": "https://example.com/post",
            "title": "A Post",
            "project": "demo",
        },
    )

    # The tool reports success and the documented report keys.
    assert result["status"] == "ok"
    assert result["node_count"] == 1
    assert result.get("tldr") == "fixed tldr"

    # The on-disk graph genuinely gained a node.
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["id"] == "doc:web-clip"

    # A clip markdown file landed under data/ingested/.
    written = Path(result["path"])
    assert written.exists()
    assert written.parent == (project / "data" / "ingested")


def test_mcp_ingest_requires_a_resolvable_project(tmp_path):
    """With no active/registered project, the tool refuses (no silent no-op)."""
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    with pytest.raises(ValueError, match="no project specified"):
        server.call_tool("ingest", {"content": "some text"})
