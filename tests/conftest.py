"""Shared pytest fixtures for the Tesserae test suite."""
from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.project import merge_graphs
from tesserae.research_graph import ResearchGraph, ResearchGraphExtractor


WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


@pytest.fixture(autouse=True)
def _no_real_llm_extraction(monkeypatch):
    """The doc extractor now defaults to 'llm', so a bare CLI `compile` in a test
    would shell out to the developer's REAL codex/claude — slow, non-deterministic,
    token-burning. Force the no-backend path so tests get the deterministic
    fallback by default; tests that exercise the LLM extractor override this with
    their own fake client."""
    monkeypatch.setattr("tesserae.llm_json.build_default_json_client", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _isolated_discovery_scan_cache(tmp_path_factory, monkeypatch):
    """Keep harness-discovery marker-scan caching out of the user's ~/.cache."""
    cache_dir = tmp_path_factory.mktemp("discovery-cache")
    monkeypatch.setenv("TESSERAE_DISCOVERY_CACHE", str(cache_dir / "discovery_scan.sqlite"))


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
    # Same trap, different file: ``~/.tesserae/config.json`` carries the
    # developer's machine-wide LLM defaults (llm_provider/llm_codex_home).
    # ``resolve_llm_client_settings`` falls back to it, which would make
    # provider-selection tests depend on the dev box. Point it at a fresh
    # non-existent path; tests that need a global config monkeypatch it
    # explicitly to their own tmp file.
    monkeypatch.setattr(
        "tesserae.llm_json.GLOBAL_CONFIG_PATH",
        tmp_path_factory.mktemp("llm-global") / "config.json",
        raising=True,
    )
