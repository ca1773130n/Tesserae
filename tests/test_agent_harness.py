import json

from tesserae.agent_harness import (
    AgentHarnessAdapter,
    SUPPORTED_AGENT_HARNESSES,
    render_harness_context,
)
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


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
        mcp_args=["-m", "tesserae.mcp_server", "--graph", "/abs/graph.json"],
        targets=["claude-code", "codex", "gemini", "kiro", "cursor", "opencode"],
    )

    assert output / "TESSERAE.md" in written
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_name"] == "demo_wiki"
    assert manifest["supported_targets"] == SUPPORTED_AGENT_HARNESSES
    assert manifest["node_count"] == 2
    assert manifest["edge_count"] == 1
    assert manifest["mcp"]["args"] == ["-m", "tesserae.mcp_server", "--graph", "/abs/graph.json"]

    assert (output / "claude" / "CLAUDE.md").exists()
    assert (output / "codex" / "AGENTS.md").exists()
    assert (output / "gemini" / "GEMINI.md").exists()
    assert (output / "kiro" / ".kiro" / "steering" / "tesserae.md").exists()
    assert (output / "cursor" / ".cursor" / "rules" / "tesserae.mdc").exists()
    assert (output / "opencode" / "AGENTS.md").exists()
    assert "Gaussian Splatting" in (output / "TESSERAE.md").read_text(encoding="utf-8")


def test_agent_harness_adapter_rejects_unknown_targets(tmp_path):
    try:
        AgentHarnessAdapter().write_harness(harness_sample_graph(), tmp_path, targets=["unknown-agent"])
    except ValueError as exc:
        assert "unknown-agent" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# --------------------------------------------------------------- topic scoping (07-04)


def harness_topic_graph():
    """A graph with two distinct topic neighborhoods so PPR scoping selects
    different node sets for different topics."""
    nodes = [
        ResearchNode(id="Paper:render", name="Gaussian Splatting Rendering",
                     type=ResearchNodeType.PAPER, description="Real-time radiance field rendering."),
        ResearchNode(id="Concept:splat", name="Gaussian Splatting",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Point-based rendering primitive."),
        ResearchNode(id="Concept:raster", name="Rasterization",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Projecting splats to screen space."),
        ResearchNode(id="Paper:lang", name="Transformer Language Modeling",
                     type=ResearchNodeType.PAPER, description="Attention-based sequence modeling."),
        ResearchNode(id="Concept:attn", name="Self Attention",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Scaled dot-product attention mechanism."),
        ResearchNode(id="Concept:token", name="Tokenization",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Splitting text into subword tokens."),
    ]
    edges = [
        ResearchEdge(source="Paper:render", target="Concept:splat", type="uses", evidence="rendering uses splatting"),
        ResearchEdge(source="Concept:splat", target="Concept:raster", type="uses", evidence="splatting rasterizes"),
        ResearchEdge(source="Paper:lang", target="Concept:attn", type="uses", evidence="LM uses attention"),
        ResearchEdge(source="Concept:attn", target="Concept:token", type="uses", evidence="attention over tokens"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_harness_default_unchanged():
    """No topic => deterministic static top-12 brief (two calls are identical)."""
    graph = harness_topic_graph()
    a = render_harness_context("demo", graph, "python3", ["-m", "tesserae.mcp_server"])
    b = render_harness_context("demo", graph, "python3", ["-m", "tesserae.mcp_server"])
    assert a == b
    # The static path lists every node (only 6, < 12) under Representative nodes.
    assert "Gaussian Splatting" in a
    assert "Self Attention" in a


def test_harness_topic_changes_brief():
    """A topic-scoped brief differs from the static (no-topic) brief."""
    graph = harness_topic_graph()
    static = render_harness_context("demo", graph, "python3", ["-m", "tesserae.mcp_server"])
    scoped = render_harness_context(
        "demo", graph, "python3", ["-m", "tesserae.mcp_server"], topic="gaussian splatting rendering"
    )
    assert scoped != static


def test_harness_topic_distinguishes_topics():
    """Two different topics over distinct neighborhoods yield different briefs."""
    graph = harness_topic_graph()
    splat = render_harness_context(
        "demo", graph, "python3", ["-m", "tesserae.mcp_server"], topic="gaussian splatting rendering"
    )
    lang = render_harness_context(
        "demo", graph, "python3", ["-m", "tesserae.mcp_server"], topic="transformer attention language"
    )
    assert splat != lang
