"""Shared pytest fixtures for the Tesserae test suite."""
from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.project import merge_graphs
from tesserae.research_graph import ResearchGraph, ResearchGraphExtractor


WIKI_CORPUS_ROOT = Path(__file__).parent / "fixtures" / "wiki_corpus"


@pytest.fixture(autouse=True)
def _no_real_llm_extraction(request, monkeypatch):
    """The doc extractor now defaults to 'llm', so a bare CLI `compile` in a test
    would shell out to the developer's REAL codex/claude — slow, non-deterministic,
    token-burning. Default every test to the no-backend path (deterministic
    fallback). `test_llm_json` exercises the client layer itself, so it opts out;
    extractor tests that want a backend re-stub `build_default_json_client` after
    this fixture runs."""
    if any(m in request.node.nodeid for m in ("test_llm_json", "test_llm_provider_config")):
        return  # these exercise the client builder itself
    monkeypatch.setattr("tesserae.llm_json.build_default_json_client", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _dry_run_e2e_ask(request, monkeypatch):
    """`ask` now defaults to the LLM-planned answer, so an e2e CLI/MCP ask in a
    test would build the rotating CLI client and hit the developer's REAL
    claude/codex login. Pin the e2e ask surfaces to TESSERAE_QUERY_DRY_RUN=1
    (query.py's gate skips the planner and returns the deterministic stub).
    Scoped to the e2e modules only — planner unit tests (test_ask_planner)
    assert real planner behavior against fake clients and must NOT inherit
    the dry-run short-circuit."""
    e2e_ask_modules = (
        "test_cli_top_level_ask",
        "test_cli_ask_scope",
        "test_mcp_server_ask",
    )
    if any(m in request.node.nodeid for m in e2e_ask_modules):
        monkeypatch.setenv("TESSERAE_QUERY_DRY_RUN", "1")


@pytest.fixture(autouse=True)
def _isolated_discovery_scan_cache(tmp_path_factory, monkeypatch):
    """Keep harness-discovery marker-scan caching out of the user's ~/.cache."""
    cache_dir = tmp_path_factory.mktemp("discovery-cache")
    monkeypatch.setenv("TESSERAE_DISCOVERY_CACHE", str(cache_dir / "discovery_scan.sqlite"))


@pytest.fixture(autouse=True)
def _isolated_cli_llm_cache(tmp_path, monkeypatch):
    """Keep the CLI response cache out of the operator's ~/.tesserae/llm_cache.

    ``llm_json._CLI_CACHE_DIR`` is a real directory under $HOME with no
    eviction, and it is keyed on the caller's ``cache_key``. Tests that pass a
    short literal key (``cache_key="k"``) therefore share one entry ACROSS
    RUNS: the first run stores an answer, every later run gets a cache hit and
    ``_run_cli`` is never called. That — not the production code — is why
    ``test_codex_reasoning_effort`` failed on a developer box and passed in CI
    (and passed under ``TESSERAE_LLM_CACHE=0``). Repoint at a fresh per-test
    tmp dir so the suite can neither read nor write operator state; tests that
    exercise the cache itself set the same name with an in-body
    ``monkeypatch.setattr``, which wins because it is applied later.
    """
    monkeypatch.setattr(
        "tesserae.llm_json._CLI_CACHE_DIR", tmp_path / "llm-cache", raising=True
    )


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
    # TESSERAE_REGISTRY is honoured ahead of DEFAULT_REGISTRY_PATH, so an
    # operator who exports it in their shell would route the whole suite —
    # including the registrations `init`/`compile` now perform — at their real
    # registry, straight past the isolation above.
    monkeypatch.delenv("TESSERAE_REGISTRY", raising=False)
    # Same reasoning for the machine's host identity: tests that assert on
    # session provenance must not depend on which machine runs them.
    monkeypatch.delenv("TESSERAE_HOST_ID", raising=False)
    monkeypatch.setattr("tesserae.harness_sessions._HOST_ID_CACHE", None, raising=False)
    monkeypatch.setattr(
        "tesserae.harness_sessions.HOST_ID_PATH",
        tmp_path_factory.mktemp("host-id") / "host_id",
        raising=True,
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
