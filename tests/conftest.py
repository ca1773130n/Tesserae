"""Shared pytest fixtures for the Tesserae test suite."""
from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.project import merge_graphs
from tesserae.research_graph import ResearchGraph, ResearchGraphExtractor


WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


def _source_kind_for(path: Path) -> str:
    """Pick the right ResearchGraphExtractor source_kind for a fixture file."""
    parts = path.parts
    name = path.name
    if "papers" in parts and name == "paper.md":
        return "Paper"
    if "papers" in parts and name == "repo.md":
        return "Repository"
    if "repos" in parts:
        return "Repository"
    return "SourceDocument"


@pytest.fixture
def wiki_sample_graph() -> ResearchGraph:
    """Build a ResearchGraph from tests/fixtures/wiki_corpus/.

    Walks the data/research/ and docs/ trees, classifies each markdown file
    via the same path heuristic the production pipeline uses (Paper for
    papers/*/paper.md, Repository for *repo.md and repos/*.md, SourceDocument
    otherwise), runs ResearchGraphExtractor.extract_file on each, and merges
    the resulting per-file graphs via project.merge_graphs.
    """
    extractor = ResearchGraphExtractor()
    roots = [
        WIKI_CORPUS_ROOT / "data" / "research",
        WIKI_CORPUS_ROOT / "docs",
    ]
    graphs = []
    for root in roots:
        if not root.exists():
            continue
        for md_path in sorted(root.rglob("*.md")):
            graphs.append(extractor.extract_file(md_path, source_kind=_source_kind_for(md_path)))
    return merge_graphs(graphs)


@pytest.fixture(autouse=True)
def _isolate_global_registry(tmp_path_factory, monkeypatch):
    """Stop tests from reading the developer's global project registry.

    ``ProjectRegistry(None)`` falls back to ``DEFAULT_REGISTRY_PATH`` —
    ``~/.tesserae/registry.json`` — which on a developer machine may carry an
    ``active`` project pointing at an unrelated graph. ``LLMWikiMCPServer``
    resolves that active project *before* its ``default_graph_path`` argument,
    so an unrelated active project silently shadows the per-test graph and
    research-graph tools (``graph_summary``/``search_nodes``/…) report zero
    matching nodes.

    CI passes only because it has no such registry. Point the default at a
    fresh, non-existent path so every test starts from an empty registry,
    matching CI and any clean checkout. Tests that need a populated registry
    create their own under ``tmp_path`` and pass ``registry_path`` explicitly,
    so they are unaffected.
    """
    isolated = tmp_path_factory.mktemp("registry") / "registry.json"
    monkeypatch.setattr(
        "tesserae.mcp_server.DEFAULT_REGISTRY_PATH", isolated, raising=True
    )
