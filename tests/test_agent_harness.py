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


def test_harness_context_points_agents_at_wiki_entrypoint():
    text = render_harness_context(
        "demo", harness_sample_graph(), "python3", ["-m", "tesserae.mcp_server"]
    )
    assert ".tesserae/wiki/index.md" in text
    assert "start at `.tesserae/wiki/index.md`" in text


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


# --------------------------------------------------------------- pointer install (07-09)


from tesserae.agent_harness import (
    POINTER_BEGIN,
    POINTER_END,
    install_instruction_pointer,
    render_pointer_block,
)


def test_pointer_block_is_deterministic():
    a, b = render_pointer_block("demo"), render_pointer_block("demo")
    assert a == b
    assert a.startswith(POINTER_BEGIN) and a.endswith(POINTER_END)
    assert ".tesserae/graph.json" in a and "TESSERAE.md" in a


def test_install_pointer_creates_agents_md_when_neither_exists(tmp_path):
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "created"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text == render_pointer_block("demo") + "\n"
    assert not (tmp_path / "CLAUDE.md").exists()


def test_install_pointer_appends_to_existing_files_preserving_content(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Mine\n\nkeep me\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "appended", "CLAUDE.md": "appended"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\nkeep me\n")
    assert POINTER_BEGIN in text and text.endswith(POINTER_END + "\n")


def test_install_pointer_is_idempotent_second_run_no_write(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    install_instruction_pointer(tmp_path, "demo")
    before = (tmp_path / "AGENTS.md").read_bytes()
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "current"}
    assert (tmp_path / "AGENTS.md").read_bytes() == before  # byte-idempotent


def test_install_pointer_refreshes_stale_block_in_place(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "top\n\n" + POINTER_BEGIN + "\nOLD STALE BODY\n" + POINTER_END + "\n\nbottom\n",
        encoding="utf-8",
    )
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "updated"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "OLD STALE BODY" not in text
    assert text.startswith("top\n\n") and text.endswith("\n\nbottom\n")
    assert render_pointer_block("demo") in text


def test_install_pointer_skips_claude_md_with_agents_include(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "appended", "CLAUDE.md": "skipped-include"}
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"


def test_install_pointer_leaves_malformed_markers_untouched(tmp_path):
    body = "x\n" + POINTER_BEGIN + "\nno end marker\n"
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "malformed"}
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == body


def test_install_pointer_bails_on_orphan_begin_before_complete_pair(tmp_path):
    """A stray BEGIN before a real pair must NOT splice orphan-BEGIN → first-END
    (that would silently delete the user content between them)."""
    body = (
        "docs mention " + POINTER_BEGIN + " as an example\n\n"
        "precious user content\n\n"
        + POINTER_BEGIN + "\nstale\n" + POINTER_END + "\n"
    )
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")
    before = (tmp_path / "AGENTS.md").read_bytes()
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "malformed"}
    assert (tmp_path / "AGENTS.md").read_bytes() == before  # untouched


def test_install_pointer_bails_on_duplicate_marker_pairs(tmp_path):
    body = (
        POINTER_BEGIN + "\nfirst copy\n" + POINTER_END + "\n\n"
        "between\n\n"
        + POINTER_BEGIN + "\nsecond copy\n" + POINTER_END + "\n"
    )
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")
    before = (tmp_path / "AGENTS.md").read_bytes()
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "malformed"}
    assert (tmp_path / "AGENTS.md").read_bytes() == before  # untouched


def test_install_pointer_coexists_with_foreign_marker_blocks(tmp_path):
    """Other managed blocks (e.g. HarnessSync) must survive both append and
    refresh untouched — our splice only ever moves bytes between OUR markers."""
    foreign = (
        "<!-- [harness-sync:start rules] -->\n"
        "synced rules body\n"
        "<!-- [harness-sync:end rules] -->\n"
    )
    (tmp_path / "AGENTS.md").write_text("# Mine\n\n" + foreign, encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "appended"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert foreign in text  # foreign block byte-identical
    # Now make our block stale and refresh: foreign block still untouched.
    stale = text.replace(render_pointer_block("demo"),
                         POINTER_BEGIN + "\nstale\n" + POINTER_END)
    (tmp_path / "AGENTS.md").write_text(stale, encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "updated"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert foreign in text
    assert text.startswith("# Mine\n\n" + foreign)
    assert render_pointer_block("demo") in text and "stale" not in text
