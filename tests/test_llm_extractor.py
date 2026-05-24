import json

import pytest

from tesserae.llm_extractor import (
    ClaudeCLIResearchExtractor,
    GraphJSONValidationError,
    extract_json_object,
    graph_from_llm_payload,
)
from tesserae.research_graph import ResearchNodeType


def test_graph_from_llm_payload_validates_controlled_schema_and_resolves_keys():
    payload = {
        "nodes": [
            {"key": "paper", "name": "Schema-Grounded Retrieval", "type": "Paper"},
            {"key": "method", "name": "Ontology-Constrained Extraction", "type": "MethodologicalConcept", "aliases": ["schema constrained extraction"]},
            {"key": "claim", "name": "Claim: constrained extraction reduces taxonomy drift", "type": "Claim", "description": "constrained extraction reduces taxonomy drift"},
            {"key": "ev1", "name": "Evidence: constrained extraction reduces taxonomy drift", "type": "EvidenceSpan", "description": "constrained extraction reduces taxonomy drift"},
        ],
        "edges": [
            {"source": "paper", "target": "method", "type": "uses", "evidence": "uses ontology-constrained extraction"},
            {"source": "paper", "target": "claim", "type": "supports_claim"},
            {"source": "claim", "target": "ev1", "type": "evidenced_by"},
        ],
    }

    graph = graph_from_llm_payload(payload, source_path="paper.md", source_kind="Paper")

    by_name = {node.name: node for node in graph.nodes}
    assert by_name["Ontology-Constrained Extraction"].type == ResearchNodeType.METHODOLOGICAL_CONCEPT
    assert by_name["Ontology-Constrained Extraction"].aliases == ["schema constrained extraction"]
    assert graph.has_edge_type("evidenced_by")
    assert all(edge.source.startswith(tuple(t.value for t in ResearchNodeType)) for edge in graph.edges)


def test_graph_from_llm_payload_rejects_freeform_node_and_edge_types():
    bad_payload = {
        "nodes": [
            {"key": "paper", "name": "Bad Extraction", "type": "Paper"},
            {"key": "thing", "name": "Some Tool", "type": "software"},
        ],
        "edges": [{"source": "paper", "target": "thing", "type": "related_to"}],
    }

    with pytest.raises(GraphJSONValidationError, match="Unsupported node type"):
        graph_from_llm_payload(bad_payload, source_path="bad.md", source_kind="Paper")


def test_extract_json_object_handles_claude_result_wrapper_and_markdown_fences():
    wrapped = json.dumps({"type": "result", "result": "```json\n{\"nodes\": [], \"edges\": []}\n```"})
    assert extract_json_object(wrapped) == {"nodes": [], "edges": []}


def test_claude_cli_research_extractor_uses_runner_and_validates_output():
    calls = []

    def fake_runner(prompt: str, config_dir: str, model: str, timeout: int) -> str:
        calls.append({"prompt": prompt, "config_dir": config_dir, "model": model, "timeout": timeout})
        return json.dumps(
            {
                "nodes": [
                    {"key": "paper", "name": "LLM Wiki Paper", "type": "Paper"},
                    {"key": "method", "name": "Evidence-Grounded Claim Extraction", "type": "MethodologicalConcept"},
                    {"key": "evidence", "name": "Evidence: claim is grounded", "type": "EvidenceSpan", "description": "claim is grounded"},
                ],
                "edges": [
                    {"source": "paper", "target": "method", "type": "uses", "evidence": "uses evidence-grounded extraction"},
                    {"source": "method", "target": "evidence", "type": "evidenced_by", "evidence": "claim is grounded"},
                ],
            }
        )

    extractor = ClaudeCLIResearchExtractor(
        runner=fake_runner,
        config_dirs=["/Users/neo/.claude-personal1", "/Users/neo/.claude-personal2"],
        model="sonnet",
        timeout=7,
    )
    graph = extractor.extract_text("# LLM Wiki Paper\nUses evidence-grounded claim extraction.", source_path="paper.md", source_kind="Paper")

    assert calls
    assert calls[0]["config_dir"] == "/Users/neo/.claude-personal1"
    assert calls[0]["model"] == "sonnet"
    assert calls[0]["timeout"] == 7
    assert "Return ONLY one valid JSON object" in calls[0]["prompt"]
    assert "Entity" in calls[0]["prompt"] and "software" in calls[0]["prompt"]
    assert any(node.name == "Evidence-Grounded Claim Extraction" for node in graph.nodes)


def test_run_claude_cli_pops_env_for_default_config_dir(monkeypatch):
    """Codex PR #19 P2 fix — when config_dir is ~/.claude (the default),
    `run_claude_cli` must NOT export CLAUDE_CONFIG_DIR (the CLI's quirk:
    explicitly setting it to the default breaks auth lookup). Other
    dirs continue to receive the explicit env.
    """
    import subprocess as _subprocess
    from pathlib import Path
    from tesserae import llm_extractor

    seen_envs = []

    class FakeCompleted:
        returncode = 0
        stdout = '{"nodes": [], "edges": []}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen_envs.append(dict(kwargs.get("env") or {}))
        return FakeCompleted()

    monkeypatch.setattr(_subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/should/be/popped")

    # Default dir → env popped.
    default = str(Path.home() / ".claude")
    llm_extractor.run_claude_cli("prompt", default, "sonnet", 10)
    assert "CLAUDE_CONFIG_DIR" not in seen_envs[-1], (
        f"expected env popped for default dir; got {seen_envs[-1].get('CLAUDE_CONFIG_DIR')!r}"
    )

    # Non-default dir → env explicitly set.
    llm_extractor.run_claude_cli("prompt", "/tmp/.claude-personal1", "sonnet", 10)
    assert seen_envs[-1].get("CLAUDE_CONFIG_DIR") == "/tmp/.claude-personal1"
