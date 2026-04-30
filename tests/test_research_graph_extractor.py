"""Unit tests for the new typed extractors and gates introduced by F-1..F-13.

Each test pins a specific extractor / gate. The fixtures here are tiny and
deterministic — corpus-level regressions live in
``test_extraction_corpus_quality.py``.
"""

from __future__ import annotations

import json
import os

import pytest

from llm_wiki.research_graph import (
    ResearchGraphExtractor,
    ResearchNodeType,
    TitleQuality,
    classify_paper_title_candidate,
    extract_authors,
    extract_comparison_claims,
    extract_contribution_claims,
    extract_eval_entities,
    extract_method_entities,
    extract_open_questions,
    extract_organizations,
    extract_title,
    is_public_research_node,
    is_verified_paper_title,
    link_paper_repo_pairs,
    resolve_missing_paper_title,
)
from llm_wiki.term_registry import TermEntry, TermRegistry


# ---------------------------------------------------------------------------
# F-1: title gate — classify_paper_title_candidate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "```markdown",
        "```",
        "```python",
        "번역 완료. 파일 생성됨: `data/daily/2026-04-06/papers/2304.12210/paper_ko.md`",
        "실제 중국어 논문 분석이 들어있는 파일을 확인해야 합니다. `prompt.txt` 파일을 확인해보겠습니다.",
        "RT @SomeUser: check this paper",
        "TL;DR: super fast inference",
        "📄 the paper of the year",
        "Authors: Alice Bob",
        "저자: 김아무개",
        "[paper](https://arxiv.org/abs/2401.00001)",
        "https://arxiv.org/abs/2401.00001",
        "arXiv:2401.00001",
        "Note that this is broken.",
        "Please check the file.",
        "data/daily/x.md",
        "We propose a new method.",  # action verb
    ],
)
def test_title_gate_rejects_known_garbage(line: str) -> None:
    assert not classify_paper_title_candidate(line, in_fence=False)


@pytest.mark.parametrize(
    "line",
    [
        "Distance Field Rasterization for End-to-End Mesh Reconstruction",
        "Neural Gabor Splatting: Enhanced Gaussian Splatting with Neural Gabor for High-frequency Surface Reconstruction",
        "GaussianFlow SLAM: Monocular Gaussian Splatting SLAM Guided by GaussianFlow",
        "FullCircle: Casual한 360° 촬영으로 손쉬운 3D 재구성",
        "WorldCompass: Reinforcement Learning for Long-Horizon World Models",
    ],
)
def test_title_gate_accepts_real_paper_titles(line: str) -> None:
    assert classify_paper_title_candidate(line, in_fence=False)


def test_title_gate_rejects_lines_inside_fenced_block() -> None:
    assert not classify_paper_title_candidate(
        "Distance Field Rasterization for End-to-End Mesh Reconstruction",
        in_fence=True,
    )


def test_extract_title_skips_fence_marker_and_picks_real_title() -> None:
    """F-1 reproducer: ```markdown fence + real title under ## #1."""
    text = """# 논문 분석: 2604.11251

> - arxiv: https://arxiv.org/abs/2604.11251

```markdown
CLAW: Composable Language-Annotated Whole-body Motion Generation | Cool Papers - 몰입형 논문 탐색

## 2604.11251

Total: 1

## #1

# CLAW: Composable Language-Annotated Whole-body Motion Generation
"""
    title = extract_title(text, "data/research/daily/2026-04-23/papers/2604.11251/paper.md")
    assert title == "CLAW: Composable Language-Annotated Whole-body Motion Generation"


def test_extract_title_skips_translation_status_line() -> None:
    """F-1 reproducer: '번역 완료. 파일 생성됨: ...' is never a title."""
    text = """# 논문 분석: 2304.12210

번역 완료. 파일 생성됨: `data/daily/2026-04-06/papers/2304.12210/paper_ko.md`
"""
    title = extract_title(text, "data/research/daily/2026-04-06/papers/2304.12210/paper.md")
    # No real title in the file -> falls back to the path stem.
    assert title == "paper"


def test_extract_title_skips_assistant_chatter_line() -> None:
    """F-1 reproducer: 'paper.txt 파일을 확인해보겠습니다.' is never a title."""
    text = """# 논문 분석: 2410.17897

`paper_prompt.txt` 파일에 중국어 논문 분석 내용이 없습니다. 이 파일은 영문 웹페이지 스크랩(Cool Papers 사이트의 "Value Residual Learning" 논문 정보)입니다.

실제 중국어 논문 분석이 들어있는 파일을 확인해야 합니다. `prompt.txt` 파일을 확인해보겠습니다.
"""
    title = extract_title(text, "data/research/daily/2026-04-07/papers/2410.17897/paper.md")
    assert title == "paper"


# ---------------------------------------------------------------------------
# F-2: TitleQuality enum + persistence
# ---------------------------------------------------------------------------


def test_title_quality_enum_persists_as_string_value() -> None:
    text = """# 논문 분석: 2604.20329

> - arxiv: https://arxiv.org/abs/2604.20329

# Image Generators are Generalist Vision Learners

Authors: A B
"""
    g = ResearchGraphExtractor().extract_text(
        text,
        source_path="data/research/daily/2026-04-27/papers/2604.20329/paper.md",
        source_kind="Paper",
    )
    paper = next(n for n in g.nodes if n.type == ResearchNodeType.PAPER)
    assert isinstance(paper.metadata["title_quality"], str)
    assert paper.metadata["title_quality"] == TitleQuality.PAPER_FILE.value


def test_invalid_title_quality_marks_paper_private() -> None:
    """F-2: Failed paper.md extractions become NEEDS_METADATA + private."""
    text = """# 논문 분석: 2604.99999

> - arxiv: https://arxiv.org/abs/2604.99999

번역 완료. 파일 생성됨: foo.md
"""
    g = ResearchGraphExtractor().extract_text(
        text,
        source_path="data/research/daily/2026-04-27/papers/2604.99999/paper.md",
        source_kind="Paper",
    )
    paper = next(n for n in g.nodes if n.type == ResearchNodeType.PAPER)
    assert paper.metadata["title_quality"] == TitleQuality.NEEDS_METADATA.value
    assert not is_public_research_node(paper)


# ---------------------------------------------------------------------------
# F-3: resolve_missing_paper_title — offline cache + needs_metadata fallback
# ---------------------------------------------------------------------------


def test_resolve_missing_paper_title_uses_offline_cache(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "arxiv-cache.json"
    cache.write_text(
        json.dumps({"2604.02996": {"title": "Cached Real Title"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_WIKI_ARXIV_CACHE", str(cache))

    resolved = resolve_missing_paper_title("2604.02996", "no title here", {})
    assert resolved.title == "Cached Real Title"
    assert resolved.quality == TitleQuality.REFERENCE_CONTEXT


def test_resolve_missing_paper_title_returns_needs_metadata_when_no_cache(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LLM_WIKI_ARXIV_CACHE", str(tmp_path / "missing.json"))
    resolved = resolve_missing_paper_title("2604.02996", "no title", {})
    assert resolved.title is None
    assert resolved.quality == TitleQuality.NEEDS_METADATA


def test_resolve_missing_paper_title_does_not_invent_from_abstract(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("LLM_WIKI_ARXIV_CACHE", str(tmp_path / "missing.json"))
    abstract = "We propose a method called ABCNet for cool things."
    resolved = resolve_missing_paper_title("2604.99999", abstract, {})
    assert resolved.title is None


# ---------------------------------------------------------------------------
# F-4: typed extractors
# ---------------------------------------------------------------------------


def test_extract_authors_handles_korean_block() -> None:
    text = """저자:
Dong-Uk Seo,
Jinwoo Jeon,
Eungchang Mason Lee,
Hyun Myung

Subject:
Robotics
"""
    assert extract_authors(text) == [
        "Dong-Uk Seo",
        "Eungchang Mason Lee",
        "Hyun Myung",
        "Jinwoo Jeon",
    ]


def test_extract_authors_handles_english_inline() -> None:
    text = "Authors: Jane Doe, John Smith and Alice Wonderland"
    assert extract_authors(text) == [
        "Alice Wonderland",
        "Jane Doe",
        "John Smith",
    ]


def test_extract_organizations_picks_affiliation_block() -> None:
    text = "Affiliation: MIT, Stanford"
    assert extract_organizations(text) == ["MIT", "Stanford"]


def test_extract_eval_entities_picks_dtu_and_tanks_and_temples() -> None:
    text = (
        "We evaluate on DTU and Tanks and Temples and report PSNR and SSIM. "
        "Our PSNR=32.5 on DTU is 1.0 above the prior SOTA."
    )
    datasets, benchmarks, metrics, results = extract_eval_entities(text)
    assert "DTU" in benchmarks
    assert "Tanks and Temples" in benchmarks
    assert "PSNR" in metrics
    assert "SSIM" in metrics
    # The metric=value pattern parses one explicit Result.
    assert any(r["metric"] == "PSNR" and r["benchmark"] == "DTU" for r in results)


def test_extract_method_entities_captures_novel_korean_method() -> None:
    text = "우리는 GaussianFlow SLAM을 제안한다."
    algorithms, _, _, _ = extract_method_entities(text)
    assert "GaussianFlow SLAM" in algorithms


def test_extract_method_entities_captures_we_propose_english() -> None:
    text = "we propose SDFRaster for end-to-end mesh reconstruction."
    algorithms, _, _, _ = extract_method_entities(text)
    assert "SDFRaster" in algorithms


def test_extract_method_entities_emits_models_from_registry() -> None:
    text = "We extend Stable Diffusion XL on top of CLIP features."
    _, models, _, _ = extract_method_entities(text)
    assert "Stable Diffusion XL" in models
    assert "CLIP" in models


# ---------------------------------------------------------------------------
# F-5: heading classifier (no Concept pollution)
# ---------------------------------------------------------------------------


def test_heading_to_concept_only_for_registry_match() -> None:
    """Generic ``Frontend`` heading must not become a Concept node."""
    text = """# Frontend Notes

## Frontend

Static HTML site generation.

## Volumetric Rendering

Real research term.
"""
    g = ResearchGraphExtractor().extract_text(
        text, source_path="docs/notes.md", source_kind="SourceDocument"
    )
    names = {n.name for n in g.nodes}
    # Volumetric Rendering is registered -> typed concept emitted.
    assert "Volumetric Rendering" in names
    # Frontend is not registered -> never minted as a node.
    assert "Frontend" not in names


def test_source_document_metadata_records_all_headings_as_sections() -> None:
    text = """# Top

## Concepts

## Volumetric Rendering

## Whatever
"""
    g = ResearchGraphExtractor().extract_text(
        text, source_path="docs/notes.md", source_kind="SourceDocument"
    )
    doc = next(n for n in g.nodes if n.type == ResearchNodeType.SOURCE_DOCUMENT)
    sections = doc.metadata.get("sections")
    assert isinstance(sections, list)
    section_texts = [s["text"] for s in sections]
    assert "Volumetric Rendering" in section_texts
    assert "Whatever" in section_texts


# ---------------------------------------------------------------------------
# F-6: typed registry — at least one Algorithm extraction
# ---------------------------------------------------------------------------


def test_registry_construction_rejects_generic_uses_without_optin() -> None:
    """The registry must not silently let an entry default to ``uses``."""
    with pytest.raises(ValueError):
        TermRegistry(
            entries=(
                TermEntry(
                    canonical_name="Bogus",
                    node_type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
                    relation="uses",
                ),
            )
        )
        # Re-validate via the public API path
    with pytest.raises(ValueError):
        from llm_wiki.term_registry import _validate_entry  # type: ignore

        _validate_entry(
            TermEntry(
                canonical_name="Bogus",
                node_type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
                relation="uses",
            )
        )


def test_default_registry_resolves_typed_algorithm_from_paper_body() -> None:
    """At least one ``Algorithm`` extraction lands from the corpus pattern."""
    text = """# 논문 분석: 2604.15612

> - arxiv: https://arxiv.org/abs/2604.15612

# GaussianFlow SLAM: Monocular Gaussian Splatting SLAM Guided by GaussianFlow

저자:
Dong-Uk Seo

우리는 GaussianFlow SLAM을 제안한다.
"""
    g = ResearchGraphExtractor().extract_text(
        text,
        source_path="data/research/daily/2026-04-25/papers/2604.15612/paper.md",
        source_kind="Paper",
    )
    algos = [n for n in g.nodes if n.type == ResearchNodeType.ALGORITHM]
    assert algos, "at least one Algorithm node should be extracted"


# ---------------------------------------------------------------------------
# F-7 + F-8: Repository identity + Paper -> implemented_in -> Repository
# ---------------------------------------------------------------------------


def test_repository_identity_uses_github_owner_repo_seed() -> None:
    text = """# GitHub 분석: SkalskiP/top-cvpr-2026-papers

> - URL: https://github.com/SkalskiP/top-cvpr-2026-papers
"""
    g = ResearchGraphExtractor().extract_text(
        text,
        source_path="data/research/daily/2026-04-23/papers/2604.11251/repo.md",
        source_kind="Repository",
    )
    repos = [n for n in g.nodes if n.type == ResearchNodeType.REPOSITORY]
    assert repos
    repo = repos[0]
    assert repo.metadata["github_repo"] == "skalskip/top-cvpr-2026-papers"
    assert repo.metadata["repo_url"] == "https://github.com/SkalskiP/top-cvpr-2026-papers"
    assert "skalskip/top-cvpr-2026-papers" in repo.aliases


def test_repo_md_with_arxiv_id_emits_paper_implemented_in_edge() -> None:
    text = """# GitHub 분석: someorg/some-repo

> - URL: https://github.com/someorg/some-repo
> - arXiv: 2604.11251
"""
    g = ResearchGraphExtractor().extract_text(
        text,
        source_path="data/research/daily/2026-04-23/papers/2604.11251/repo.md",
        source_kind="Repository",
    )
    paper = next(n for n in g.nodes if n.type == ResearchNodeType.PAPER)
    repo = next(n for n in g.nodes if n.type == ResearchNodeType.REPOSITORY)
    assert any(
        e.source == paper.id
        and e.target == repo.id
        and e.type == "implemented_in"
        for e in g.edges
    )


def test_link_paper_repo_pairs_post_pass_is_idempotent() -> None:
    """Cross-file: a paper.md graph + a repo.md graph merge under one arxiv id."""
    extractor = ResearchGraphExtractor()
    paper_text = """# 논문 분석: 2604.20329

> - arxiv: https://arxiv.org/abs/2604.20329

# Cool Paper Title
"""
    repo_text = """# GitHub 분석: x/y

> - URL: https://github.com/x/y
> - arxiv: 2604.20329
"""
    paper_graph = extractor.extract_text(
        paper_text,
        source_path="data/research/daily/2026-04-27/papers/2604.20329/paper.md",
        source_kind="Paper",
    )
    repo_graph = extractor.extract_text(
        repo_text,
        source_path="data/research/daily/2026-04-27/papers/2604.20329/repo.md",
        source_kind="Repository",
    )
    from llm_wiki.batch import merge_graphs

    merged = merge_graphs([paper_graph, repo_graph])
    impls = [e for e in merged.edges if e.type == "implemented_in"]
    assert len(impls) == 1
    # Calling the post-pass again must not duplicate the edge.
    merged2 = link_paper_repo_pairs(merged)
    impls2 = [e for e in merged2.edges if e.type == "implemented_in"]
    assert len(impls2) == 1


# ---------------------------------------------------------------------------
# F-12: doc headings stop minting Concepts
# ---------------------------------------------------------------------------


def test_design_doc_does_not_emit_section_heading_concepts() -> None:
    text = """# Design

## 4.1 Synthesis layers

## /graph.json

## The Karpathy three-layer model
"""
    g = ResearchGraphExtractor().extract_text(
        text,
        source_path="docs/superpowers/specs/2026-04-27-wiki-frontend-redesign-design.md",
        source_kind="SourceDocument",
    )
    concept_nodes = [
        n for n in g.nodes
        if n.type == ResearchNodeType.CONCEPT
        and str(n.metadata.get("source_kind") or "") == "document_heading"
    ]
    assert concept_nodes == []


# ---------------------------------------------------------------------------
# F-13: typed claim extractors
# ---------------------------------------------------------------------------


def test_extract_contribution_claims_picks_we_propose_and_korean() -> None:
    text = (
        "We propose a new method for X. "
        "본 논문은 새로운 방법을 제안한다. "
        "Some unrelated sentence."
    )
    claims = extract_contribution_claims(text)
    assert any("we propose" in c.lower() for c in claims)
    assert any("제안한다" in c for c in claims)


def test_extract_comparison_claims_picks_outperforms() -> None:
    text = "Our method outperforms prior SOTA on DTU. We also compared with the latest models."
    claims = extract_comparison_claims(text)
    assert claims, "at least one comparison claim expected"
    assert any("outperform" in c.lower() for c in claims)


def test_extract_open_questions_picks_future_work() -> None:
    text = "Future work will explore higher-resolution synthesis. Limitations include slow inference."
    questions = extract_open_questions(text)
    assert questions
    assert any("future work" in q.lower() for q in questions)


def test_paper_extraction_emits_typed_claim_nodes() -> None:
    text = """# 논문 분석: 2604.20329

> - arxiv: https://arxiv.org/abs/2604.20329

# Cool Paper

We propose CoolNet, which outperforms prior methods on DTU. Future work
will explore extension to dynamic scenes.
"""
    g = ResearchGraphExtractor().extract_text(
        text,
        source_path="data/research/daily/2026-04-27/papers/2604.20329/paper.md",
        source_kind="Paper",
    )
    types = {n.type for n in g.nodes}
    assert ResearchNodeType.CONTRIBUTION_CLAIM in types
    assert ResearchNodeType.COMPARISON_CLAIM in types
    assert ResearchNodeType.OPEN_QUESTION in types
