"""Tests for the multi-key path index used by the session graph extractor.

Covers the five path conventions a `source_path` might take in the
graph (absolute, project-relative, POSIX, raw loader id, basename) and
ensures the basename fallback is suppressed when the basename
collides across multiple nodes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.research_graph import (
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.session_graph_path_index import DocPathIndex


def _papers(project_root: Path) -> ResearchGraph:
    """Three Papers with three different source_path conventions, plus
    a Concept with no source_path, plus a basename-collision pair."""
    abs_path = str((project_root / "data/research/papers/abs/paper.md").resolve())
    return ResearchGraph(
        nodes=[
            # Tier 2: absolute (already resolved at extract time).
            ResearchNode(
                id="Paper:abs",
                name="Paper Abs",
                type=ResearchNodeType.PAPER,
                source_path=abs_path,
            ),
            # Tier 3: project-relative.
            ResearchNode(
                id="Paper:rel",
                name="Paper Rel",
                type=ResearchNodeType.PAPER,
                source_path="data/research/papers/rel/paper.md",
            ),
            # Tier 4: POSIX-normalized form, doc lives outside project.
            ResearchNode(
                id="Paper:posix",
                name="Paper Posix",
                type=ResearchNodeType.PAPER,
                source_path="data/research/papers/posix-doc/paper.md",
            ),
            # Tier 1: raw loader-id string (not a real fs path).
            ResearchNode(
                id="Repository:gh",
                name="Repo GH",
                type=ResearchNodeType.REPOSITORY,
                source_path="github://owner/repo",
            ),
            # No source_path — must not crash indexing.
            ResearchNode(
                id="Concept:foo",
                name="Foo",
                type=ResearchNodeType.CONCEPT,
            ),
        ],
        edges=[],
    )


def test_tier1_raw_loader_id(tmp_path: Path):
    idx = DocPathIndex.from_graph(_papers(tmp_path), tmp_path)
    assert idx.lookup("github://owner/repo") == "Repository:gh"


def test_tier2_absolute_path(tmp_path: Path):
    idx = DocPathIndex.from_graph(_papers(tmp_path), tmp_path)
    abs_path = str((tmp_path / "data/research/papers/abs/paper.md").resolve())
    assert idx.lookup(abs_path) == "Paper:abs"


def test_tier3_project_relative_path(tmp_path: Path):
    idx = DocPathIndex.from_graph(_papers(tmp_path), tmp_path)
    assert idx.lookup("data/research/papers/rel/paper.md") == "Paper:rel"
    # Same node resolves when queried as absolute too.
    rel_abs = str((tmp_path / "data/research/papers/rel/paper.md").resolve())
    assert idx.lookup(rel_abs) == "Paper:rel"


def test_tier3_query_with_dot_slash_prefix(tmp_path: Path):
    """`files_touched` often emits ``./data/...`` or ``data/...`` — both
    should resolve identically."""
    idx = DocPathIndex.from_graph(_papers(tmp_path), tmp_path)
    assert idx.lookup("./data/research/papers/rel/paper.md") == "Paper:rel"


def test_tier5_basename_only_fallback_when_unambiguous(tmp_path: Path):
    """When no higher tier matches, an unambiguous basename resolves.
    But the demo graph above has *three* `paper.md` basename collisions
    — so basename lookup must be suppressed for that name."""
    idx = DocPathIndex.from_graph(_papers(tmp_path), tmp_path)
    # 'paper.md' is ambiguous across the three Papers — must not bind.
    assert idx.lookup("paper.md") is None

    # Make a fresh, unique-basename query work.
    g = ResearchGraph(
        nodes=[
            ResearchNode(
                id="Paper:unique",
                name="Unique",
                type=ResearchNodeType.PAPER,
                source_path="data/research/papers/unique/unique-paper.md",
            ),
        ],
        edges=[],
    )
    idx2 = DocPathIndex.from_graph(g, tmp_path)
    # The basename is unique → fallback resolves.
    assert idx2.lookup("unique-paper.md") == "Paper:unique"


def test_missing_query_returns_none(tmp_path: Path):
    idx = DocPathIndex.from_graph(_papers(tmp_path), tmp_path)
    assert idx.lookup("nonexistent/path.md") is None
    assert idx.lookup("") is None
    assert idx.lookup("   ") is None


def test_node_without_source_path_is_skipped(tmp_path: Path):
    """The Concept node has no source_path — indexing must not crash and
    must not produce a lookup hit for the name."""
    idx = DocPathIndex.from_graph(_papers(tmp_path), tmp_path)
    assert idx.lookup("Foo") is None
