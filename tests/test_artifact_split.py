"""Graph-artifact contract tests for ``ProjectWiki._write_artifacts``.

This file used to pin the F-11 *artifact split*: ``graph.json`` carried the
research layer, ``code-graph.json`` the CodeProject / SourceFile / CodeClass /
CodeFunction / Dependency layer, and ``combined-graph.json`` the union for
anyone who opted in. The split is retired — nothing mints a code layer any
more — so what is left to pin is:

* ``graph.json`` is the compiled graph, whole, in the shape MCP reads.
* a ``code-graph.json`` left by an earlier release is DELETED, not inherited.
* the build-history ledger lives at the project-wiki root and grows per build.
* every publish goes through a per-writer temp name.

We avoid the full ``compile()`` path here because it round-trips through
``ResearchGraphExtractor``; these are pure local operations on a hand-built
graph.
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


def _graph() -> ResearchGraph:
    """A graph with one node from every layer a compile can still produce.

    Public research nodes plus the private assertion layer — the assertion
    nodes are the reason ``graph.json`` is not just ``public_nodes()``: MCP
    consumers read them, so they must survive into the artifact.
    """
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
    ]
    edges = [
        ResearchEdge(source="Paper:demo", target="Concept:gs", type="mentioned_in"),
        ResearchEdge(source="Repository:demo", target="Paper:demo", type="implemented_in"),
        ResearchEdge(source="Synthesis:pulse", target="Paper:demo", type="synthesizes"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _seed_project(project_root: Path) -> ProjectWiki:
    """Init a wiki workspace under ``project_root``."""
    return ProjectWiki.init(project_root, name="artifact_split_test")


# ------------------------------------------------------------ artifact files


def test_write_artifacts_writes_the_whole_graph(tmp_path: Path) -> None:
    """``graph.json`` carries every node handed to ``_write_artifacts``.

    The split used to subtract a code layer here; nothing subtracts now. A
    SUPERSET rather than equality because ``_write_artifacts`` also runs the
    synthesis projector, which legitimately mints pages of its own. The
    assertion layer in particular must not be filtered out on the way to disk
    — MCP reads claims and evidence spans out of ``graph.json``.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    graph = _graph()
    wiki._write_artifacts(graph)

    payload = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    written_ids = {n["id"] for n in payload["nodes"]}
    assert {n.id for n in graph.nodes} <= written_ids
    assert {e.type for e in graph.edges} <= {e["type"] for e in payload["edges"]}
    assert {"PerformanceClaim", "EvidenceSpan"} <= {n["type"] for n in payload["nodes"]}


def test_a_stale_code_graph_is_deleted(tmp_path: Path) -> None:
    """``code-graph.json`` from an earlier release must not survive a compile.

    Nothing writes the file any more, so leaving it on disk would leave an
    unowned artifact that still answers reads — and that still feeds the
    output-snapshot graph hash — forever.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki.paths.code_graph.write_text(
        '{"nodes": [{"id": "SourceFile:project.py", "name": "project.py", '
        '"type": "SourceFile"}], "edges": []}\n',
        encoding="utf-8",
    )

    wiki._write_artifacts(_graph())

    assert not wiki.paths.code_graph.exists()


def test_no_code_graph_is_written_when_none_existed(tmp_path: Path) -> None:
    """The delete is conditional — a clean project stays clean, no empty file."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)

    wiki._write_artifacts(_graph())

    assert not wiki.paths.code_graph.exists()


# ------------------------------------------------------------ build-history


def test_build_history_lives_at_project_root(tmp_path: Path) -> None:
    """The build-history ledger lives at ``.tesserae/.build-history.jsonl``,
    *not* inside the wiped ``site/`` directory.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki._write_artifacts(_graph())

    assert wiki.paths.build_history.exists()
    # Path lives directly under .tesserae/, not inside site/.
    assert wiki.paths.build_history.parent == wiki.root
    # And nothing inside site/ matches the legacy in-site name.
    assert not (wiki.paths.site / ".build-history.jsonl").exists()


def test_build_history_grows_each_compile(tmp_path: Path) -> None:
    """Two consecutive compiles append two lines to the project-root ledger."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki._write_artifacts(_graph())
    wiki._write_artifacts(_graph())

    text = wiki.paths.build_history.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 2, (
        f"expected two build-history entries after two compiles; got {len(lines)}"
    )
    # Every line is a parseable JSON object with the expected keys. ``code_nodes``
    # is deliberately absent: it counted the retired half of the split, and a
    # permanent zero would assert a layer exists.
    for line in lines:
        record = json.loads(line)
        assert "built_at" in record
        assert record["research_nodes"] >= len(_graph().nodes)
        assert "research_edges" in record
        assert "code_nodes" not in record
        assert "code_edges" not in record


# ------------------------------------------------------------ MCP / consumers


def test_graph_json_schema_unchanged(tmp_path: Path) -> None:
    """``graph.json`` keeps the same top-level shape MCP relies on."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    wiki._write_artifacts(_graph())

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

    for fixed in ("graph.tmp", "combined-graph.tmp"):
        (wiki.root / fixed).mkdir()

    wiki._write_artifacts(_graph())

    for target in (wiki.paths.graph, wiki.paths.combined_graph):
        assert json.loads(target.read_text(encoding="utf-8"))["nodes"], (
            f"{target.name} should have been published past the occupied fixed temp name"
        )
    # And the scratch file is gone once the rename lands — a per-writer name
    # that is never cleaned up is a leak, not a fix.
    for stem in ("graph", "combined-graph"):
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
    wiki._write_hierarchy_sidecar(_graph())

    assert json.loads(wiki.paths.manifest.read_text(encoding="utf-8"))["files"] == {
        "paper.md": {"hash": "abc123"}
    }
    assert json.loads(wiki.paths.hierarchy.read_text(encoding="utf-8"))["schema_version"] == 1
    for stem in ("manifest", "hierarchy"):
        assert list(wiki.root.glob(f"{stem}.tmp.*")) == []
