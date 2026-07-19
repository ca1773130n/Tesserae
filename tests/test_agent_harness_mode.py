"""Phase-5 per-agent harness mode (spec §9). Work package 1.

Covers: agent-mode dir contents (worker vs manager scoping), pointer-block
byte-purity across two invocations over a grown corpus, unknown-agent fail-loud,
and graceful degrade when a worker has no distilled artifact yet.
"""

import json

import pytest

from tesserae.agent_harness import (
    POINTER_BEGIN,
    POINTER_END,
    AgentHarnessAdapter,
    render_pointer_block,
)
from tesserae.agent_identity import sanitize_agent_key
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

WORKER_KEY = "claude-code:me:reviewer"


def _agent_graph(extra_concepts: int = 0) -> ResearchGraph:
    """A graph with one worker Agent + its ExpertiseProfile citing two concepts.

    ``extra_concepts`` grows the corpus (more nodes/edges) without changing the
    agent's identity or profile — the setup for the pointer-purity test.
    """
    nodes = [
        ResearchNode(
            id="Agent:reviewer",
            name=WORKER_KEY,
            type=ResearchNodeType.AGENT,
            metadata={"agent_key": WORKER_KEY, "label": "Code reviewer", "role": "reviewer"},
        ),
        ResearchNode(
            id="Concept:idempotence",
            name="Byte idempotence",
            type=ResearchNodeType.CONCEPT,
        ),
        ResearchNode(
            id="Concept:distill",
            name="Distillation",
            type=ResearchNodeType.CONCEPT,
        ),
        ResearchNode(
            id="Profile:reviewer",
            name=f"Expertise: {WORKER_KEY}",
            type=ResearchNodeType.EXPERTISE_PROFILE,
            metadata={
                "agent": WORKER_KEY,
                "session_count": 3,
                "finding_counts": {"SessionDecision": 2},
                "top_concepts": ["Concept:idempotence", "Concept:distill"],
            },
        ),
    ]
    edges = [ResearchEdge(source="Profile:reviewer", target="Concept:idempotence", type="uses")]
    for i in range(extra_concepts):
        cid = f"Concept:extra{i}"
        nodes.append(ResearchNode(id=cid, name=f"Extra {i}", type=ResearchNodeType.CONCEPT))
        edges.append(ResearchEdge(source="Agent:reviewer", target=cid, type="uses"))
    return ResearchGraph(nodes=nodes, edges=edges)


def _harness_output(tmp_path):
    """A ``.tesserae/agent_harness`` layout so the project root resolves."""
    return tmp_path / ".tesserae" / "agent_harness"


def _claude_mcp_args(agent_dir):
    settings = json.loads((agent_dir / "claude" / ".claude" / "settings.json").read_text("utf-8"))
    return settings["mcpServers"]["demo"]["args"]


# --------------------------------------------------------------- worker mode


def test_agent_worker_keeps_project_graph_and_instructs_scoping(tmp_path):
    output = _harness_output(tmp_path)
    sanitized = sanitize_agent_key(WORKER_KEY)
    artifact = tmp_path / ".tesserae" / "agents" / sanitized / "distilled.graph.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{}", encoding="utf-8")

    written = AgentHarnessAdapter(project_name="demo").write_harness(
        graph=_agent_graph(),
        output_dir=output,
        mcp_command="python3",
        agent=WORKER_KEY,
    )

    agent_dir = output / "agents" / sanitized
    assert (agent_dir / "TESSERAE.md") in written
    assert (agent_dir / "manifest.json").exists()
    assert (agent_dir / "purpose.md").exists()
    # Every provider file lands under the agent subdir.
    assert (agent_dir / "claude" / "CLAUDE.md").exists()
    assert (agent_dir / "codex" / "AGENTS.md").exists()

    manifest = json.loads((agent_dir / "manifest.json").read_text("utf-8"))
    assert manifest["agent"] == WORKER_KEY
    assert manifest["mode"] == "worker"
    assert manifest["artifact_present"] is True

    # Worker MCP config → the L0 project graph, NOT the L1-only artifact: the
    # §8.1 worker view (L0 ∪ own-L1 + absorption overlay) has no serialized file
    # and is built at read time from the agent="<key>" scoping instruction.
    # --graph pinned at the artifact would strand the worker on L1-only.
    args = _claude_mcp_args(agent_dir)
    assert args[args.index("--graph") + 1] == ".tesserae/graph.json"
    assert manifest["graph_arg"] == ".tesserae/graph.json"
    # The instruction file tells the worker to pass agent="<key>" for scoping.
    brief = (agent_dir / "TESSERAE.md").read_text("utf-8")
    assert f'agent="{WORKER_KEY}"' in brief


def test_agent_worker_purpose_derives_from_expertise_profile(tmp_path):
    output = _harness_output(tmp_path)
    AgentHarnessAdapter(project_name="demo").write_harness(
        graph=_agent_graph(), output_dir=output, agent=WORKER_KEY
    )
    purpose = (output / "agents" / sanitize_agent_key(WORKER_KEY) / "purpose.md").read_text("utf-8")
    assert "Code reviewer" in purpose  # registry/graph label
    assert "Byte idempotence" in purpose and "Distillation" in purpose  # top_concepts
    from tesserae.karpathy_layer import PURPOSE_MARKER

    assert PURPOSE_MARKER in purpose  # seed-once, user-editable below the marker


def test_agent_worker_purpose_is_seed_once(tmp_path):
    output = _harness_output(tmp_path)
    adapter = AgentHarnessAdapter(project_name="demo")
    adapter.write_harness(graph=_agent_graph(), output_dir=output, agent=WORKER_KEY)
    path = output / "agents" / sanitize_agent_key(WORKER_KEY) / "purpose.md"
    path.write_text("# edited by user\n", encoding="utf-8")
    adapter.write_harness(graph=_agent_graph(extra_concepts=5), output_dir=output, agent=WORKER_KEY)
    assert path.read_text("utf-8") == "# edited by user\n"  # user edit preserved


# --------------------------------------------------------------- manager mode


def test_agent_manager_keeps_project_graph_and_instructs_scoping(tmp_path):
    output = _harness_output(tmp_path)
    written = AgentHarnessAdapter(project_name="demo").write_harness(
        graph=_agent_graph(), output_dir=output, agent="org:root"
    )
    agent_dir = output / "agents" / "org:root"
    assert (agent_dir / "TESSERAE.md") in written
    manifest = json.loads((agent_dir / "manifest.json").read_text("utf-8"))
    assert manifest["mode"] == "manager"
    assert manifest["graph_arg"] == ".tesserae/graph.json"

    # Manager view has no serialized file → --graph stays the project graph and
    # the instruction file tells the agent to pass agent="<key>" for scoping.
    args = _claude_mcp_args(agent_dir)
    assert args[args.index("--graph") + 1] == ".tesserae/graph.json"
    brief = (agent_dir / "TESSERAE.md").read_text("utf-8")
    assert 'agent="org:root"' in brief


def test_agent_org_pseudo_key_is_org_mode(tmp_path):
    output = _harness_output(tmp_path)
    AgentHarnessAdapter(project_name="demo").write_harness(
        graph=_agent_graph(), output_dir=output, agent="org"
    )
    manifest = json.loads((output / "agents" / "org" / "manifest.json").read_text("utf-8"))
    assert manifest["mode"] == "org"
    assert manifest["graph_arg"] == ".tesserae/graph.json"


# --------------------------------------------- pointer-block byte-purity


def test_pointer_block_agent_is_pure_function_of_agent_key():
    a = render_pointer_block("demo", WORKER_KEY)
    b = render_pointer_block("demo", WORKER_KEY)
    assert a == b
    assert a.startswith(POINTER_BEGIN) and a.endswith(POINTER_END)
    assert WORKER_KEY in a
    # No corpus-derived content — the determinism invariant (spec §9).
    assert "Nodes:" not in a and "Edges:" not in a
    # Distinct agents yield distinct blocks.
    assert render_pointer_block("demo", "codex:me:planner") != a


def test_agent_instruction_files_are_byte_stable_across_grown_corpus(tmp_path):
    output = _harness_output(tmp_path)
    adapter = AgentHarnessAdapter(project_name="demo")

    adapter.write_harness(graph=_agent_graph(extra_concepts=0), output_dir=output, agent=WORKER_KEY)
    agent_dir = output / "agents" / sanitize_agent_key(WORKER_KEY)
    before_tesserae = (agent_dir / "TESSERAE.md").read_bytes()
    before_claude = (agent_dir / "claude" / "CLAUDE.md").read_bytes()

    # Grow the corpus substantially, then re-emit.
    adapter.write_harness(graph=_agent_graph(extra_concepts=25), output_dir=output, agent=WORKER_KEY)
    assert (agent_dir / "TESSERAE.md").read_bytes() == before_tesserae
    assert (agent_dir / "claude" / "CLAUDE.md").read_bytes() == before_claude


# --------------------------------------------------------------- fail loud


def test_agent_unknown_key_fails_loud(tmp_path):
    output = _harness_output(tmp_path)
    with pytest.raises(ValueError) as exc:
        AgentHarnessAdapter(project_name="demo").write_harness(
            graph=_agent_graph(), output_dir=output, agent="claude-code:me:ghost"
        )
    assert "Unknown agent" in str(exc.value)
    assert "ghost" in str(exc.value)


def test_agent_empty_key_fails_loud(tmp_path):
    output = _harness_output(tmp_path)
    with pytest.raises(ValueError):
        AgentHarnessAdapter(project_name="demo").write_harness(
            graph=_agent_graph(), output_dir=output, agent="   "
        )


# --------------------------------------------------------------- graceful degrade


def test_agent_worker_without_artifact_degrades_gracefully(tmp_path):
    output = _harness_output(tmp_path)
    sanitized = sanitize_agent_key(WORKER_KEY)
    # No distilled.graph.json on disk.
    written = AgentHarnessAdapter(project_name="demo").write_harness(
        graph=_agent_graph(), output_dir=output, agent=WORKER_KEY
    )
    agent_dir = output / "agents" / sanitized
    assert written  # harness still written
    manifest = json.loads((agent_dir / "manifest.json").read_text("utf-8"))
    assert manifest["artifact_present"] is False
    # --graph stays the L0 project graph; the resolved worker view is built at
    # read time via agent="<key>", which needs the L1 artifact — hence the note.
    assert manifest["graph_arg"] == ".tesserae/graph.json"
    purpose = (agent_dir / "purpose.md").read_text("utf-8")
    assert f"tesserae distill --agent {WORKER_KEY}" in purpose
