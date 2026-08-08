"""Artifact-split contract tests (F-11).

These tests pin the partition that ``ProjectWiki._write_artifacts`` performs
on the in-memory ``ResearchGraph`` before it lands on disk:

* ``.tesserae/graph.json`` — research-layer nodes only (no ``CodeProject``,
  ``SourceFile``, ``CodeModule``, ``CodeClass``, ``CodeFunction``,
  ``Dependency``).
* ``.tesserae/code-graph.json`` — code-graph nodes only (the same six types).
* ``.tesserae/combined-graph.json`` — only present when
  ``combined_graph: true`` is in the project config (or the
  ``TESSERAE_INCLUDE_COMBINED_GRAPH`` env var is set).

We avoid the full ``compile()`` path here because it round-trips through
``ResearchGraphExtractor`` which is being overhauled in parallel by Subagent
W. The artifact split is a pure local operation on a hand-built graph.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tesserae.project import ProjectWiki
from tesserae.research_graph import (
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
            id="CodeProject:Tesserae",
            name="Tesserae",
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
            id="CodeModule:tesserae",
            name="tesserae",
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
            source="CodeProject:Tesserae", target="SourceFile:project.py", type="contains"
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
            source="Paper:demo", target="CodeProject:Tesserae", type="implemented_in"
        ),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _seed_project(project_root: Path) -> ProjectWiki:
    """Init a wiki workspace with the code layer switched on.

    This module is entirely about the research/code SPLIT, and
    ``_write_artifacts`` only emits ``code-graph.json`` for a project that
    opted into a code layer — with the layer off it deletes the file instead.
    So the opt-in is part of the fixture, not incidental setup: without it
    every test here would be asserting the split of a graph that has no second
    half. ``test_code_graph_json_is_removed_when_the_layer_is_off`` covers the
    other direction.
    """
    wiki = ProjectWiki.init(project_root, name="artifact_split_test")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["external_tools"] = [*(cfg.get("external_tools") or []), {"id": "codegraph"}]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return wiki


# ------------------------------------------------------------ partition helper


def test_partition_graph_separates_layers() -> None:
    """``partition_graph`` returns two disjoint ResearchGraph objects."""
    from tesserae.wiki_projector import partition_graph

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
    assert ("Paper:demo", "CodeProject:Tesserae", "implemented_in") not in research_edges

    code_edges = [(e.source, e.target, e.type) for e in code.edges]
    assert ("Paper:demo", "CodeProject:Tesserae", "implemented_in") in code_edges


# ------------------------------------------------------------ artifact files


def test_code_graph_json_is_removed_when_the_layer_is_off(tmp_path: Path) -> None:
    """The artifact follows the opt-in in BOTH directions.

    Writing it only when enabled is the easy half. The half that matters is
    what happens on the way back: a project that turns the code layer off, or
    never had it, must not be left holding a ``code-graph.json`` from an
    earlier compile. That file carries no timestamp and nothing marks it
    stale, so it goes on answering reads with a snapshot of a repo that has
    since moved — the failure mode is silent and gets more wrong over time.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)  # opted in
    wiki._write_artifacts(_mixed_graph())
    assert wiki.paths.code_graph.exists(), "precondition: the enabled compile wrote it"

    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["external_tools"] = [{"id": "codegraph", "enabled": False}]
    wiki.paths.config.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    wiki._write_artifacts(_mixed_graph())

    assert not wiki.paths.code_graph.exists(), (
        "a disabled code layer must remove the artifact, not leave the last one"
    )
    # graph.json is still written, and still carries only the research layer.
    graph_payload = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    assert graph_payload["nodes"], "graph.json must still be written"
    assert not [n for n in graph_payload["nodes"] if n["type"].startswith("Code")]


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
    monkeypatch.setenv("TESSERAE_INCLUDE_COMBINED_GRAPH", "1")

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
    """The build-history ledger lives at ``.tesserae/.build-history.jsonl``,
    *not* inside the wiped ``site/`` directory.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki._write_artifacts(_mixed_graph())

    assert wiki.paths.build_history.exists()
    # Path lives directly under .tesserae/, not inside site/.
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


# ------------------------------------------------------- concurrent publishing


def test_publish_never_uses_one_fixed_temp_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No publish may go through ``<artifact>.tmp``.

    ``with_suffix(".tmp")`` REPLACES the suffix, so every publish of
    ``graph.json`` used to run through the single path ``.tesserae/graph.tmp``.
    Two publishers holding that one path — two hosts on a shared disk, or the
    engine daemon and an interactive compile — interleave their writes into it
    and rename the mixture into place. Occupying each fixed name with a
    *directory* is how the test tells the two implementations apart: the old
    code raises ``IsADirectoryError`` on the write, the per-writer name does
    not notice.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    monkeypatch.setenv("TESSERAE_INCLUDE_COMBINED_GRAPH", "1")

    for fixed in ("graph.tmp", "code-graph.tmp", "combined-graph.tmp"):
        (wiki.root / fixed).mkdir()

    wiki._write_artifacts(_mixed_graph())

    for target in (wiki.paths.graph, wiki.paths.code_graph, wiki.paths.combined_graph):
        assert json.loads(target.read_text(encoding="utf-8"))["nodes"], (
            f"{target.name} should have been published past the occupied fixed temp name"
        )
    # And the scratch file is gone once the rename lands — a per-writer name
    # that is never cleaned up is a leak, not a fix.
    for stem in ("graph", "code-graph", "combined-graph"):
        assert list(wiki.root.glob(f"{stem}.tmp.*")) == []


def test_publish_removes_its_temp_file_when_the_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed publish must not leave its unique scratch file on disk.

    A fixed temp name was at least reused by the next attempt; a per-writer
    name accumulates one orphan per failure inside ``.tesserae/`` forever, so
    the cleanup is load-bearing for the change above.
    """
    from tesserae import project as project_module

    def _fail(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _fail)

    target = tmp_path / "graph.json"
    with pytest.raises(OSError):
        project_module._publish_atomically(target, '{"nodes": []}\n')

    assert not target.exists()
    assert list(tmp_path.glob("graph.tmp*")) == []


def test_sidecar_publishes_never_use_one_fixed_temp_path(tmp_path: Path) -> None:
    """The manifest and hierarchy sidecars share the graph publishes' hazard.

    Both live in ``.tesserae/`` — the directory the whole fleet shares — and
    both used to write through a single fixed name (``manifest.tmp``,
    ``hierarchy.tmp``). The manifest is the worse of the two: ``_load_manifest``
    calls ``json.loads`` with no guard, so an interleaved manifest raises
    ``JSONDecodeError`` out of every later compile on every host rather than
    degrading to a re-extraction. Occupying each fixed name with a *directory*
    is what tells the old and new implementations apart.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    (wiki.root / "manifest.tmp").mkdir()
    (wiki.root / "hierarchy.tmp").mkdir()

    wiki._write_manifest({"paper.md": {"hash": "abc123"}})
    wiki._write_hierarchy_sidecar(_mixed_graph())

    assert json.loads(wiki.paths.manifest.read_text(encoding="utf-8"))["files"] == {
        "paper.md": {"hash": "abc123"}
    }
    assert json.loads(wiki.paths.hierarchy.read_text(encoding="utf-8"))["schema_version"] == 1
    for stem in ("manifest", "hierarchy"):
        assert list(wiki.root.glob(f"{stem}.tmp.*")) == []
