import json

from llm_wiki.agent_harness import AgentHarnessAdapter, SUPPORTED_AGENT_HARNESSES
from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def harness_sample_graph():
    paper = ResearchNode(id="Paper:harness", name="Harness Paper", type=ResearchNodeType.PAPER, source_path="notes/harness.md")
    method = ResearchNode(id="Method:gs", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT)
    return ResearchGraph(
        nodes=[paper, method],
        edges=[ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="Harness Paper uses Gaussian Splatting.")],
    )


def test_agent_harness_adapter_writes_common_manifest_and_provider_files(tmp_path):
    output = tmp_path / "agent_harness"

    written = AgentHarnessAdapter(project_name="demo_wiki").write_harness(
        graph=harness_sample_graph(),
        output_dir=output,
        mcp_command="python3",
        mcp_args=["-m", "llm_wiki.mcp_server", "--graph", "/abs/graph.json"],
        targets=["claude-code", "codex", "gemini", "kiro", "cursor", "opencode"],
    )

    assert output / "LLM_WIKI.md" in written
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_name"] == "demo_wiki"
    assert manifest["supported_targets"] == SUPPORTED_AGENT_HARNESSES
    assert manifest["node_count"] == 2
    assert manifest["edge_count"] == 1
    assert manifest["mcp"]["args"] == ["-m", "llm_wiki.mcp_server", "--graph", "/abs/graph.json"]

    assert (output / "claude" / "CLAUDE.md").exists()
    assert (output / "codex" / "AGENTS.md").exists()
    assert (output / "gemini" / "GEMINI.md").exists()
    assert (output / "kiro" / ".kiro" / "steering" / "llm-wiki.md").exists()
    assert (output / "cursor" / ".cursor" / "rules" / "llm-wiki.mdc").exists()
    assert (output / "opencode" / "AGENTS.md").exists()
    assert "Gaussian Splatting" in (output / "LLM_WIKI.md").read_text(encoding="utf-8")


def test_agent_harness_adapter_rejects_unknown_targets(tmp_path):
    try:
        AgentHarnessAdapter().write_harness(harness_sample_graph(), tmp_path, targets=["unknown-agent"])
    except ValueError as exc:
        assert "unknown-agent" in str(exc)
    else:
        raise AssertionError("expected ValueError")
