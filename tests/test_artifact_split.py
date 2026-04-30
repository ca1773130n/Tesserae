"""Artifact-split contract tests (F-11).

These tests pin the partition that ``ProjectWiki._write_artifacts`` performs
on the in-memory ``ResearchGraph`` before it lands on disk:

* ``.llm-wiki/graph.json`` — research-layer nodes only (no ``CodeProject``,
  ``SourceFile``, ``CodeModule``, ``CodeClass``, ``CodeFunction``,
  ``Dependency``).
* ``.llm-wiki/code-graph.json`` — code-graph nodes only (the same six types).
* ``.llm-wiki/combined-graph.json`` — only present when
  ``combined_graph: true`` is in the project config (or the
  ``LLM_WIKI_INCLUDE_COMBINED_GRAPH`` env var is set).

We avoid the full ``compile()`` path here because it round-trips through
``ResearchGraphExtractor`` which is being overhauled in parallel by Subagent
W. The artifact split is a pure local operation on a hand-built graph.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from llm_wiki.project import ProjectWiki
from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


# --------------------------------------------------------------------- fixtures


def _mixed_graph() -> ResearchGraph:
    """A graph with one node from every relevant layer."""
    nodes = [
        # Research layer (public)
        ResearchNode(
            id="Paper:demo",
            name="Demo Paper",
            type=ResearchNodeType.PAPER,
            description="A demo paper.",
            metadata={"title_quality": "paper_file"},
        ),
        ResearchNode(
            id="Repository:demo",
            name="demo-repo",
            type=ResearchNodeType.REPOSITORY,
            description="A demo repository.",
        ),
        ResearchNode(
            id="Concept:gs",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        ),
        ResearchNode(
            id="Synthesis:pulse",
            name="Project pulse",
            type=ResearchNodeType.SYNTHESIS,
            metadata={"synthesis_kind": "pulse"},
        ),
        # Research layer (private — assertion layer)
        ResearchNode(
            id="Claim:perf",
            name="Outperforms baseline by 5%",
            type=ResearchNodeType.PERFORMANCE_CLAIM,
        ),
        ResearchNode(
            id="EvidenceSpan:e1",
            name="evidence text",
            type=ResearchNodeType.EVIDENCE_SPAN,
        ),
        # Code-graph layer
        ResearchNode(
            id="CodeProject:LLM-Wiki",
            name="LLM-Wiki",
            type=ResearchNodeType.CODE_PROJECT,
            metadata={"layer": "project"},
        ),
        ResearchNode(
            id="SourceFile:project.py",
            name="project.py",
            type=ResearchNodeType.SOURCE_FILE,
        ),
        ResearchNode(
            id="CodeClass:ProjectWiki",
            name="ProjectWiki",
            type=ResearchNodeType.CODE_CLASS,
        ),
        ResearchNode(
            id="CodeFunction:compile",
            name="compile",
            type=ResearchNodeType.CODE_FUNCTION,
        ),
        ResearchNode(
            id="CodeModule:llm_wiki",
            name="llm_wiki",
            type=ResearchNodeType.CODE_MODULE,
        ),
        ResearchNode(
            id="Dependency:pytest",
            name="pytest",
            type=ResearchNodeType.DEPENDENCY,
        ),
    ]
    edges = [
        # research-only
        ResearchEdge(source="Paper:demo", target="Concept:gs", type="mentioned_in"),
        ResearchEdge(source="Repository:demo", target="Paper:demo", type="implemented_in"),
        ResearchEdge(source="Synthesis:pulse", target="Paper:demo", type="synthesizes"),
        # code-only
        ResearchEdge(
            source="CodeProject:LLM-Wiki", target="SourceFile:project.py", type="contains"
        ),
        ResearchEdge(
            source="SourceFile:project.py",
            target="CodeClass:ProjectWiki",
            type="defines",
        ),
        ResearchEdge(
            source="CodeClass:ProjectWiki",
            target="CodeFunction:compile",
            type="defines",
        ),
        # cross-layer (research node → code node, e.g. paper "implemented_in" project)
        ResearchEdge(
            source="Paper:demo", target="CodeProject:LLM-Wiki", type="implemented_in"
        ),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _seed_project(project_root: Path) -> ProjectWiki:
    """Init a wiki workspace under ``project_root``."""
    return ProjectWiki.init(project_root, name="artifact_split_test")


# ------------------------------------------------------------ partition helper


def test_partition_graph_separates_layers() -> None:
    """``partition_graph`` returns two disjoint ResearchGraph objects."""
    from llm_wiki.wiki_projector import partition_graph

    research, code = partition_graph(_mixed_graph())

    research_types = {n.type.value for n in research.nodes}
    code_types = {n.type.value for n in code.nodes}

    forbidden_in_research = {
        "CodeProject",
        "SourceFile",
        "CodeModule",
        "CodeClass",
        "CodeFunction",
        "Dependency",
    }
    assert research_types.isdisjoint(forbidden_in_research)
    assert code_types == forbidden_in_research

    # Research graph still includes the assertion layer (claims/evidence) so
    # MCP/Cognee consumers can read them; only code-graph nodes are removed.
    assert "PerformanceClaim" in research_types
    assert "EvidenceSpan" in research_types

    # Cross-layer edges only survive in the code-graph (so consumers that
    # rebuild a union still see them); research graph drops anything pointing
    # at a code-layer endpoint.
    research_edges = [(e.source, e.target, e.type) for e in research.edges]
    assert ("Paper:demo", "CodeProject:LLM-Wiki", "implemented_in") not in research_edges

    code_edges = [(e.source, e.target, e.type) for e in code.edges]
    assert ("Paper:demo", "CodeProject:LLM-Wiki", "implemented_in") in code_edges


# ------------------------------------------------------------ artifact files


def test_write_artifacts_splits_graph(tmp_path: Path) -> None:
    """``_write_artifacts`` lands two graph files; the union is *not* written."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki._write_artifacts(_mixed_graph())

    graph_payload = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    code_payload = json.loads(wiki.paths.code_graph.read_text(encoding="utf-8"))

    research_types = {n["type"] for n in graph_payload["nodes"]}
    code_types = {n["type"] for n in code_payload["nodes"]}

    forbidden = {
        "CodeProject",
        "SourceFile",
        "CodeModule",
        "CodeClass",
        "CodeFunction",
        "Dependency",
    }
    assert research_types.isdisjoint(forbidden), (
        f"graph.json should not contain code-graph types: {research_types & forbidden}"
    )
    assert code_types == forbidden, (
        f"code-graph.json should contain exactly the code-graph layer types; got {code_types}"
    )

    # No accidental research types in code-graph.json.
    research_only_types = {"Paper", "Repository", "MethodologicalConcept", "Synthesis"}
    assert code_types.isdisjoint(research_only_types)


def test_combined_graph_off_by_default(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki._write_artifacts(_mixed_graph())

    assert not wiki.paths.combined_graph.exists(), (
        "combined-graph.json must not be written by default"
    )


def test_combined_graph_via_config(tmp_path: Path) -> None:
    """Setting ``combined_graph: true`` in config.json materializes the union."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    cfg = wiki.config()
    cfg["combined_graph"] = True
    wiki.paths.config.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    wiki._write_artifacts(_mixed_graph())

    assert wiki.paths.combined_graph.exists(), (
        "combined-graph.json should be written when combined_graph=true in config"
    )
    payload = json.loads(wiki.paths.combined_graph.read_text(encoding="utf-8"))
    types = {n["type"] for n in payload["nodes"]}
    # Combined graph is the full union — both partitions present.
    assert "Paper" in types
    assert "CodeFunction" in types


def test_combined_graph_via_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    monkeypatch.setenv("LLM_WIKI_INCLUDE_COMBINED_GRAPH", "1")

    wiki._write_artifacts(_mixed_graph())

    assert wiki.paths.combined_graph.exists()


def test_combined_graph_cleaned_when_flag_flips_off(tmp_path: Path) -> None:
    """Stale combined graph from a previous opt-in is removed on the next compile."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    # Pretend a previous compile wrote the combined graph.
    wiki.paths.combined_graph.write_text(
        '{"nodes": [], "edges": []}\n', encoding="utf-8"
    )
    assert wiki.paths.combined_graph.exists()

    # Flag is off (default config).
    wiki._write_artifacts(_mixed_graph())

    assert not wiki.paths.combined_graph.exists()


# ------------------------------------------------------------ build-history


def test_build_history_lives_at_project_root(tmp_path: Path) -> None:
    """The build-history ledger lives at ``.llm-wiki/.build-history.jsonl``,
    *not* inside the wiped ``site/`` directory.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki._write_artifacts(_mixed_graph())

    assert wiki.paths.build_history.exists()
    # Path lives directly under .llm-wiki/, not inside site/.
    assert wiki.paths.build_history.parent == wiki.root
    # And nothing inside site/ matches the legacy in-site name.
    assert not (wiki.paths.site / ".build-history.jsonl").exists()


def test_build_history_grows_each_compile(tmp_path: Path) -> None:
    """Two consecutive compiles append two lines to the project-root ledger."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki._write_artifacts(_mixed_graph())
    wiki._write_artifacts(_mixed_graph())

    text = wiki.paths.build_history.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 2, (
        f"expected two build-history entries after two compiles; got {len(lines)}"
    )
    # Every line is a parseable JSON object with the expected keys.
    for line in lines:
        record = json.loads(line)
        assert "built_at" in record
        assert "research_nodes" in record
        assert "code_nodes" in record


# ------------------------------------------------------------ MCP / consumers


def test_graph_json_schema_unchanged(tmp_path: Path) -> None:
    """``graph.json`` keeps the same top-level shape MCP relies on."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki._write_artifacts(_mixed_graph())

    payload = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    assert "nodes" in payload
    assert "edges" in payload
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["edges"], list)
    # Per-node fields the MCP server reads (id/name/type at minimum).
    for node in payload["nodes"]:
        assert "id" in node
        assert "name" in node
        assert "type" in node
