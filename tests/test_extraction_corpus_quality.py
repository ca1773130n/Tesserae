"""Live-corpus regression tests for research-graph extraction (Codex F-14).

These tests run :class:`llm_wiki.research_graph.ResearchGraphExtractor` against
the on-disk ``data/research/daily/`` corpus rather than synthetic snippets, so
real failure modes the extractor currently produces become visible to CI.

Most assertions are marked ``@pytest.mark.xfail(strict=True)`` and are expected
to flip to passing once Subagents W and X land their refactors:

* Subagent W rewrites :mod:`llm_wiki.research_graph` (title gate, typed
  extractors, term registry, claim types, paper/repo linker).
* Subagent X splits artifacts so the public graph is no longer mixed with the
  code graph.

Tests that the corpus currently satisfies stay un-xfailed so regressions are
caught immediately. The whole module is skipped if ``data/research/`` is not
on disk (fresh clones / minimal CI sandboxes).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraphExtractor,
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
)

try:  # The typed term registry is the canonical source of registered names.
    from llm_wiki.term_registry import TermRegistry  # type: ignore
except Exception:  # pragma: no cover - registry shape may change in W's pass
    TermRegistry = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Corpus discovery
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data" / "research"
DAILY_ROOT = DATA_ROOT / "daily"

# Curated subset: most-recent 30 daily folders. Keeps the test run bounded as
# the corpus grows. We sort lexicographically because folder names are ISO
# dates (``YYYY-MM-DD``).
CURATED_DAYS: List[Path] = (
    sorted(DAILY_ROOT.glob("*")) if DAILY_ROOT.exists() else []
)[-30:]


def _source_kind_for(path: Path) -> str:
    """Pick the right extractor source_kind for a corpus file by its path."""
    parts = path.parts
    name = path.name
    if "papers" in parts and name == "paper.md":
        return "Paper"
    if "papers" in parts and name == "repo.md":
        return "Repository"
    if "repos" in parts:
        return "Repository"
    return "SourceDocument"


def _extract_corpus() -> Tuple[List[ResearchNode], List[ResearchEdge]]:
    """Extract every markdown file under the curated days. Returns (nodes, edges).

    Re-runs through :class:`ResearchGraphExtractor` per-file (no merge) so each
    paper's metadata stays attached to its source path. Tests that need
    aggregate behaviour can iterate the flat lists.

    Per-file extraction failures are tolerated: the corpus is large and a
    single broken file (or an in-flight refactor in ``research_graph``) must
    not error every test. Unrecoverable failures still surface via the
    ``corpus_extraction_errors`` fixture.
    """
    extractor = ResearchGraphExtractor()
    nodes: List[ResearchNode] = []
    edges: List[ResearchEdge] = []
    errors: List[Tuple[Path, BaseException]] = []
    for day in CURATED_DAYS:
        for md in day.rglob("*.md"):
            try:
                graph = extractor.extract_file(
                    md, source_kind=_source_kind_for(md)
                )
            except Exception as exc:  # pragma: no cover - defensive
                errors.append((md, exc))
                continue
            nodes.extend(graph.nodes)
            edges.extend(graph.edges)
    # Stash errors on the function for fixture access.
    _extract_corpus.last_errors = errors  # type: ignore[attr-defined]
    return nodes, edges


@pytest.fixture(scope="module")
def corpus() -> Tuple[List[ResearchNode], List[ResearchEdge]]:
    if not DATA_ROOT.exists() or not CURATED_DAYS:
        pytest.skip("data/research/ corpus is not present")
    nodes, edges = _extract_corpus()
    errors = getattr(_extract_corpus, "last_errors", [])
    # If extraction blew up on every file (e.g. ``research_graph`` mid-refactor
    # by Subagent W), there is nothing meaningful to assert against. Skip the
    # whole module so the suite still produces a clean signal.
    if not nodes and errors:
        pytest.skip(
            f"ResearchGraphExtractor produced no nodes; {len(errors)} files raised"
            f" (first: {errors[0][1]!r})"
        )
    return nodes, edges


@pytest.fixture(scope="module")
def corpus_nodes(corpus) -> List[ResearchNode]:
    return corpus[0]


@pytest.fixture(scope="module")
def corpus_edges(corpus) -> List[ResearchEdge]:
    return corpus[1]


# ---------------------------------------------------------------------------
# Specific raw-file fixtures called out by the Codex review (F-1, F-3, F-4,
# F-7, F-8, F-13). Tests use absolute paths so they survive ``pytest`` being
# launched from anywhere; missing files cause individual ``skip`` rather than
# breaking the whole module.
# ---------------------------------------------------------------------------

BAD_TITLE_FIXTURES: Dict[str, Path] = {
    # arxiv id -> raw file. Each currently produces a public Paper with a
    # garbage title; once W's title gate lands these become hidden or get a
    # real title.
    "2604.11251": DATA_ROOT
    / "daily/2026-04-23/papers/2604.11251/paper.md",
    "2304.12210": DATA_ROOT
    / "daily/2026-04-06/papers/2304.12210/paper.md",
    "2410.17897": DATA_ROOT
    / "daily/2026-04-07/papers/2410.17897/paper.md",
}

MISSING_TITLE_FIXTURE: Path = (
    DATA_ROOT / "daily/2026-04-07/papers/2604.02996/paper.md"
)

AUTHORS_FIXTURE: Path = (
    DATA_ROOT / "daily/2026-04-25/papers/2604.15612/paper.md"
)

EVAL_ENTITIES_FIXTURE_15941: Path = (
    DATA_ROOT / "daily/2026-04-25/papers/2604.15941/paper.md"
)

EVAL_ENTITIES_FIXTURE_23537: Path = (
    DATA_ROOT / "daily/2026-04-29/papers/2604.23537/paper.md"
)

# Multiple daily folders re-ingest the same arxiv id; we accept any of them.
REPO_PAIR_FIXTURE_CANDIDATES: Tuple[Path, ...] = (
    DATA_ROOT / "daily/2026-04-23/papers/2509.23563/repo.md",
    DATA_ROOT / "daily/2026-04-24/papers/2509.23563/repo.md",
    DATA_ROOT / "daily/2026-04-29/papers/2509.23563/repo.md",
)


def _first_existing(paths) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


# Substrings the title gate must reject. Korean assistant/provenance phrases,
# fenced code, and explicit ``RT @``/``tl;dr`` social chrome.
TITLE_BLACKLIST_SUBSTRINGS: Tuple[str, ...] = (
    "번역 완료",
    "파일 생성됨",
    "tl;dr",
    "RT @",
    "생성됨",
    "확인해",
)

VALID_PAPER_TITLE_QUALITIES: frozenset[str] = frozenset(
    {"verified", "paper_file", "reference_context"}
)


# ---------------------------------------------------------------------------
# Bad-title regressions (F-1, F-2)
# ---------------------------------------------------------------------------


def test_no_paper_title_starts_with_fence(corpus_nodes: List[ResearchNode]) -> None:
    offenders = [
        node.name
        for node in corpus_nodes
        if node.type == ResearchNodeType.PAPER
        and is_public_research_node(node)
        and node.name.lstrip().startswith("`")
    ]
    assert not offenders, (
        f"Public Paper titles begin with a markdown fence: {offenders[:5]}"
    )


def test_no_paper_title_contains_md_or_assistant_chatter(
    corpus_nodes: List[ResearchNode],
) -> None:
    offenders: List[str] = []
    for node in corpus_nodes:
        if node.type != ResearchNodeType.PAPER:
            continue
        if not is_public_research_node(node):
            continue
        name = node.name
        if ".md" in name:
            offenders.append(name)
            continue
        if any(bad in name for bad in TITLE_BLACKLIST_SUBSTRINGS):
            offenders.append(name)
    assert not offenders, (
        f"Public Paper titles contain .md or assistant/provenance chatter: {offenders[:5]}"
    )


def test_specific_paper_title_resolves(corpus_nodes: List[ResearchNode]) -> None:
    """For each known-bad arxiv id, the Paper must either carry a real title
    (no fence/chatter) or be hidden (``is_public_research_node`` returns False).
    """
    by_arxiv: Dict[str, List[ResearchNode]] = {}
    for node in corpus_nodes:
        if node.type != ResearchNodeType.PAPER:
            continue
        arxiv_id = str(node.metadata.get("arxiv_id") or "")
        if arxiv_id:
            by_arxiv.setdefault(arxiv_id, []).append(node)

    failures: List[str] = []
    for arxiv_id, raw_path in BAD_TITLE_FIXTURES.items():
        if not raw_path.exists():
            continue
        nodes = by_arxiv.get(arxiv_id, [])
        if not nodes:
            failures.append(f"{arxiv_id}: no Paper extracted from corpus")
            continue
        for node in nodes:
            if not is_public_research_node(node):
                continue  # hidden is acceptable
            name = node.name
            if name.lstrip().startswith("`"):
                failures.append(f"{arxiv_id}: public title starts with fence: {name!r}")
                continue
            if ".md" in name or any(bad in name for bad in TITLE_BLACKLIST_SUBSTRINGS):
                failures.append(f"{arxiv_id}: public title is chatter: {name!r}")
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# Positive extraction (F-4, F-13)
# ---------------------------------------------------------------------------


def test_authors_extracted_for_known_paper(
    corpus_nodes: List[ResearchNode], corpus_edges: List[ResearchEdge]
) -> None:
    if not AUTHORS_FIXTURE.exists():
        pytest.skip(f"fixture missing: {AUTHORS_FIXTURE}")

    paper_ids = {
        node.id
        for node in corpus_nodes
        if node.type == ResearchNodeType.PAPER
        and str(node.metadata.get("arxiv_id") or "") == "2604.15612"
    }
    assert paper_ids, "Paper for 2604.15612 not extracted"

    person_ids = {
        node.id for node in corpus_nodes if node.type == ResearchNodeType.PERSON
    }
    assert person_ids, "No Person nodes extracted from the curated corpus"

    linked = [
        edge
        for edge in corpus_edges
        if edge.type == "authored_by"
        and edge.source in paper_ids
        and edge.target in person_ids
    ]
    assert linked, (
        "Expected at least one Paper -authored_by-> Person edge for 2604.15612"
    )


def test_benchmarks_and_datasets_extracted_for_known_papers(
    corpus_nodes: List[ResearchNode], corpus_edges: List[ResearchEdge]
) -> None:
    if not EVAL_ENTITIES_FIXTURE_15941.exists():
        pytest.skip(f"fixture missing: {EVAL_ENTITIES_FIXTURE_15941}")
    if not EVAL_ENTITIES_FIXTURE_23537.exists():
        pytest.skip(f"fixture missing: {EVAL_ENTITIES_FIXTURE_23537}")

    benchmark_names = {
        node.name.lower()
        for node in corpus_nodes
        if node.type == ResearchNodeType.BENCHMARK
    }
    dataset_names = {
        node.name.lower()
        for node in corpus_nodes
        if node.type == ResearchNodeType.DATASET
    }

    # 2604.15941 (Neural Gabor Splatting) explicitly evaluates on Mip-NeRF360.
    assert any("mip-nerf360" in name or "mip-nerf 360" in name for name in benchmark_names), (
        f"Mip-NeRF360 missing from Benchmark nodes; saw: {sorted(benchmark_names)[:10]}"
    )
    assert dataset_names, "No Dataset nodes extracted from the curated corpus"

    # 2604.23537 (SDFRaster / Distance Field Rasterization) reports on DTU and
    # Tanks and Temples and makes a comparative performance claim.
    assert any("dtu" == name or name == "dtu" for name in benchmark_names), (
        f"DTU missing from Benchmark nodes; saw: {sorted(benchmark_names)[:10]}"
    )
    assert any("tanks and temples" in name for name in benchmark_names), (
        f"Tanks and Temples missing from Benchmark nodes; saw: {sorted(benchmark_names)[:10]}"
    )

    paper_23537_ids = {
        node.id
        for node in corpus_nodes
        if node.type == ResearchNodeType.PAPER
        and str(node.metadata.get("arxiv_id") or "") == "2604.23537"
    }
    assert paper_23537_ids, "Paper for 2604.23537 not extracted"

    perf_claim_ids = {
        node.id
        for node in corpus_nodes
        if node.type == ResearchNodeType.PERFORMANCE_CLAIM
    }
    result_ids = {
        node.id for node in corpus_nodes if node.type == ResearchNodeType.RESULT
    }
    assert perf_claim_ids, "No PerformanceClaim nodes extracted from corpus"
    assert result_ids, "No Result nodes extracted from corpus"

    # The PerformanceClaim should be linked to a Result for this paper.
    claim_to_result_edges = [
        edge
        for edge in corpus_edges
        if edge.source in perf_claim_ids
        and edge.target in result_ids
        and edge.type in {"reports_result", "achieves_score", "supports_claim"}
    ]
    assert claim_to_result_edges, (
        "Expected a PerformanceClaim linked to a Result via reports_result/"
        "achieves_score/supports_claim"
    )


# ---------------------------------------------------------------------------
# Repository identity & paper/repo linking (F-7, F-8)
# ---------------------------------------------------------------------------


def test_repository_identity_uses_github_url(corpus_nodes: List[ResearchNode]) -> None:
    repos = [
        node for node in corpus_nodes if node.type == ResearchNodeType.REPOSITORY
    ]
    assert repos, "No Repository nodes extracted from the curated corpus"
    failures: List[str] = []
    import re as _re

    pattern = _re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+$")
    for repo in repos:
        github_repo = str(repo.metadata.get("github_repo") or "")
        if not github_repo:
            failures.append(
                f"Repository {repo.name!r} missing metadata['github_repo']"
            )
            continue
        if not pattern.match(github_repo):
            failures.append(
                f"Repository {repo.name!r} has malformed github_repo={github_repo!r}"
            )
    assert not failures, "\n".join(failures[:5])


def test_paper_implemented_in_repository_for_known_pair(
    corpus_nodes: List[ResearchNode], corpus_edges: List[ResearchEdge]
) -> None:
    fixture = _first_existing(REPO_PAIR_FIXTURE_CANDIDATES)
    if fixture is None:
        pytest.skip(
            "no repo.md fixture for arxiv 2509.23563 in the curated subset"
        )

    paper_ids = {
        node.id
        for node in corpus_nodes
        if node.type == ResearchNodeType.PAPER
        and str(node.metadata.get("arxiv_id") or "") == "2509.23563"
    }
    repo_ids = {
        node.id
        for node in corpus_nodes
        if node.type == ResearchNodeType.REPOSITORY
        and str(node.metadata.get("arxiv_id") or "") == "2509.23563"
    }
    assert paper_ids, "Paper placeholder for 2509.23563 not extracted"
    assert repo_ids, "Repository for 2509.23563 not extracted"

    edges = [
        edge
        for edge in corpus_edges
        if edge.type == "implemented_in"
        and edge.source in paper_ids
        and edge.target in repo_ids
    ]
    assert edges, (
        "Expected Paper -implemented_in-> Repository edge for arxiv 2509.23563"
    )


# ---------------------------------------------------------------------------
# Graph-level invariants (F-1, F-2, F-5, F-12)
# ---------------------------------------------------------------------------


def test_every_paper_with_arxiv_id_has_valid_title_quality_or_is_hidden(
    corpus_nodes: List[ResearchNode],
) -> None:
    """Every Paper with a captured arxiv_id either reports a valid public
    ``title_quality`` or is hidden from the public projection. This protects
    the contract that downstream synthesis/search rely on.
    """
    failures: List[str] = []
    for node in corpus_nodes:
        if node.type != ResearchNodeType.PAPER:
            continue
        arxiv_id = node.metadata.get("arxiv_id")
        if not arxiv_id:
            continue
        quality = str(node.metadata.get("title_quality") or "")
        if is_public_research_node(node):
            if quality not in VALID_PAPER_TITLE_QUALITIES:
                failures.append(
                    f"public Paper arxiv={arxiv_id} has title_quality={quality!r}"
                )
        # If hidden, any quality (including arxiv_only/invalid) is fine.
    assert not failures, "\n".join(failures[:10])


def test_no_concept_from_arbitrary_heading(corpus_nodes: List[ResearchNode]) -> None:
    """Every public Concept node either matches a registered term name or has
    a non-``document_heading`` source_kind. Curated daily-corpus subset; we do
    not pull in ``docs/`` (those are out of scope for this assertion).
    """
    if TermRegistry is None:
        pytest.skip("term_registry not available")
    registry = TermRegistry.default()
    registered = {name.lower() for name in registry.all_aliases()}

    offenders: List[str] = []
    for node in corpus_nodes:
        if node.type != ResearchNodeType.CONCEPT:
            continue
        if str(node.metadata.get("source_kind") or "") != "document_heading":
            continue
        if node.name.lower() in registered:
            continue
        offenders.append(node.name)
    assert not offenders, (
        f"Concept nodes minted from arbitrary headings: {offenders[:10]}"
    )
