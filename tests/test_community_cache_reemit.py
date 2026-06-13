"""A no-LLM compile must RE-EMIT cached community summaries.

Churn fix: when the LLM client is momentarily unavailable, a compile must not
DROP previously-minted ``CommunitySummary`` nodes. ``compile_community_summaries``
re-emits a cached summary for every unchanged cluster (the cache is keyed on
sorted member ids) and skips only clusters that have no cache entry. Before the
fix, ``_merge_community_summaries`` returned early when no client was available,
so the summaries vanished — a recompile during an LLM outage churned the graph.

Deterministic: a scripted stub client, ``tmp_path``, no network. The stub client
is always reset via the autouse fixture so it can never leak into other tests'
compiles (a leaked client mints summaries elsewhere and breaks determinism).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tesserae.project as project_mod
from tesserae.project import ProjectWiki


class _StubCommunityClient:
    def complete_json(self, *, system, user, schema_name, cache_key=None):  # noqa: ANN001
        return {
            "title": "Cluster",
            "description": "Deterministic cluster summary fixed for testing.",
            "tags": ["a", "b", "c", "d", "e"],
        }


@pytest.fixture(autouse=True)
def _reset_community_client():
    # Reset BEFORE (in case a prior test leaked) and AFTER (so this test's stub
    # never leaks into another compile and mints non-deterministic summaries).
    project_mod.set_community_summaries_test_client(None)
    yield
    project_mod.set_community_summaries_test_client(None)


_FIELD = "Compositional Scene Understanding"


def _paper_md(idx: int) -> str:
    arxiv = f"2604.5{idx:04d}"
    return (
        f"# Paper {idx:03d}\n\n"
        f"> - arxiv: https://arxiv.org/abs/{arxiv}\n\n"
        f"저자: Ada Lovelace.\n\n"
        f"This paper studies {_FIELD} using the Transformer model.\n"
        f"Local contribution numbered {idx:03d}.\n"
    )


def _seed(root: Path, n_papers: int = 6) -> ProjectWiki:
    papers_root = root / "data" / "research" / "daily" / "2026-05-01" / "papers"
    for idx in range(n_papers):
        pdir = papers_root / f"2604.5{idx:04d}"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "paper.md").write_text(_paper_md(idx), encoding="utf-8")
    wiki = ProjectWiki.init(root, name="reemit_test")
    # Pin a low community min_size so a cluster reliably forms in the tiny corpus.
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg["community_summaries"] = {"enabled": True, "min_size": 2}
    wiki.paths.config.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return wiki


def _community_ids(wiki: ProjectWiki) -> set[str]:
    graph = json.loads(wiki.paths.graph.read_text(encoding="utf-8"))
    return {n["id"] for n in graph["nodes"] if n["type"] == "CommunitySummary"}


def test_no_llm_compile_reemits_cached_summaries(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERAE_COMMUNITY_SUMMARIES", "true")
    wiki = _seed(tmp_path / "project")

    # Compile 1: LLM available (stub) -> mint + cache CommunitySummary nodes.
    project_mod.set_community_summaries_test_client(_StubCommunityClient())
    wiki.compile()
    minted = _community_ids(wiki)
    assert minted, "stub-LLM compile should mint at least one CommunitySummary"
    assert list(wiki.paths.community_summaries.glob("*.json")), "cache must be written"

    # Compile 2: NO LLM client at all (cleared stub + builder forced to None) —
    # the intermittent-outage case. The cached summaries must SURVIVE.
    project_mod.set_community_summaries_test_client(None)
    monkeypatch.setattr(ProjectWiki, "_build_json_client", lambda self, model=None: None)
    wiki.compile()
    after = _community_ids(wiki)

    assert minted <= after, (
        f"no-LLM compile dropped cached community summaries: lost {minted - after}"
    )


def test_no_llm_no_cache_skips_cleanly(tmp_path, monkeypatch):
    """No cache + no LLM: mint nothing, don't crash (graceful skip)."""
    monkeypatch.setenv("TESSERAE_COMMUNITY_SUMMARIES", "true")
    wiki = _seed(tmp_path / "project")
    project_mod.set_community_summaries_test_client(None)
    monkeypatch.setattr(ProjectWiki, "_build_json_client", lambda self, model=None: None)
    wiki.compile()
    assert _community_ids(wiki) == set()
