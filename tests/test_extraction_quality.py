"""Golden quality tests over a hand-built fixture corpus.

These tests pin the extractor's behaviour on the precise patterns reported in
the wiki bug ("tons of papers classified as concept instead of paper"):

  * digest headings of the form ``### 1. <title>, <slug>`` must NOT mint
    Concept nodes;
  * arxiv links inside digests must mint Paper nodes (one per id);
  * direct ingest of a ``papers/<id>/paper.md`` file and a digest mentioning
    the same id must collapse onto a single Paper node;
  * generic section markers ("Introduction", "Results") never become Concepts;
  * concept-shaped headings ("Volumetric Rendering") still do;
  * over a small fixture corpus, Papers must outnumber Concepts.
"""

from pathlib import Path

import pytest

from llm_wiki.research_graph import (
    ResearchGraphExtractor,
    ResearchNodeType,
)


DIGEST_TEXT = """# Daily Research Digest — 2026-04-27

## Highlights

### 1. 생성 모델이 분할과 깊이 추정까지 넘본다, Vision Banana

- 논문: [paper](papers/2604.20329/paper.md) | [arxiv](https://arxiv.org/abs/2604.20329)

### 2. 희소 시점 제약을 낮춘 3D 재구성, AnyRecon

- 논문: [paper](papers/2604.21681/paper.md) | [arxiv](https://arxiv.org/abs/2604.21681)

### 3. 단안 비디오를 다른 카메라 시점으로 다시 찍는다, Vista4D

- 논문: [paper](papers/2601.17835/paper.md) | [arxiv](https://arxiv.org/abs/2601.17835)
"""


PAPER_FILE_TEXT = """# 논문 분석: 2604.20329

> - arxiv: https://arxiv.org/abs/2604.20329
> - 분석일: 2026-04-27

# Image Generators are Generalist Vision Learners

Vision Banana shows that lightweight instruction tuning on a generative image
backbone yields strong zero-shot segmentation and metric depth estimation.
"""


def _by_type(graph, node_type: ResearchNodeType):
    return [n for n in graph.nodes if n.type == node_type]


def test_digest_numbered_headings_do_not_mint_concepts_but_do_mint_papers():
    extractor = ResearchGraphExtractor()
    graph = extractor.extract_text(
        DIGEST_TEXT,
        source_path="data/research/daily/2026-04-27/digest.md",
        source_kind="SourceDocument",
    )

    concepts = _by_type(graph, ResearchNodeType.CONCEPT)
    papers = _by_type(graph, ResearchNodeType.PAPER)

    # None of the three numbered headings ("### 1. … Vision Banana", etc.)
    # should leak into the Concepts index.
    assert concepts == [], f"unexpected Concept nodes: {[c.name for c in concepts]}"
    # Each arxiv link should mint exactly one Paper node.
    assert {p.metadata.get("arxiv_id") for p in papers} == {
        "2604.20329",
        "2604.21681",
        "2601.17835",
    }
    # The digest should record the headings as candidate paper titles for
    # downstream synthesis / paper-naming consumers.
    docs = _by_type(graph, ResearchNodeType.SOURCE_DOCUMENT)
    assert docs and "candidate_paper_titles" in docs[0].metadata
    assert any(
        "Vision Banana" in title
        for title in docs[0].metadata["candidate_paper_titles"]
    )


def test_paper_file_ingest_produces_single_paper_with_arxiv_metadata():
    graph = ResearchGraphExtractor().extract_text(
        PAPER_FILE_TEXT,
        source_path="data/research/daily/2026-04-27/papers/2604.20329/paper.md",
        source_kind="Paper",
    )

    papers = _by_type(graph, ResearchNodeType.PAPER)
    assert len(papers) == 1
    paper = papers[0]
    assert paper.metadata["arxiv_id"] == "2604.20329"
    # The display name should be the human title, with arxiv id captured as
    # an alias for cross-reference search.
    assert "Image Generators are Generalist Vision Learners" in paper.name
    assert "arXiv:2604.20329" in paper.aliases
    # Stable id is derived from the arxiv id, not from the title — so digest
    # mentions of the same id collapse onto this node.
    assert paper.id.startswith("Paper:")


def test_digest_and_paper_file_collapse_to_single_paper_node():
    extractor = ResearchGraphExtractor()
    digest_graph = extractor.extract_text(
        DIGEST_TEXT,
        source_path="data/research/daily/2026-04-27/digest.md",
        source_kind="SourceDocument",
    )
    paper_graph = extractor.extract_text(
        PAPER_FILE_TEXT,
        source_path="data/research/daily/2026-04-27/papers/2604.20329/paper.md",
        source_kind="Paper",
    )

    digest_paper_ids = {
        n.id for n in digest_graph.nodes
        if n.type == ResearchNodeType.PAPER and n.metadata.get("arxiv_id") == "2604.20329"
    }
    paper_file_paper_ids = {
        n.id for n in paper_graph.nodes
        if n.type == ResearchNodeType.PAPER and n.metadata.get("arxiv_id") == "2604.20329"
    }
    assert digest_paper_ids and paper_file_paper_ids
    # The two graphs must agree on the Paper node id for arxiv:2604.20329.
    assert digest_paper_ids == paper_file_paper_ids


def test_generic_section_headings_never_become_concepts():
    text = """# A Project Doc

## Introduction

Some intro text.

## Results

Numbers go here.

## Conclusion

Wrap-up.
"""
    graph = ResearchGraphExtractor().extract_text(
        text, source_path="docs/notes.md", source_kind="SourceDocument"
    )
    concepts = _by_type(graph, ResearchNodeType.CONCEPT)
    forbidden = {"Introduction", "Results", "Conclusion"}
    assert not (forbidden & {c.name for c in concepts})


def test_registry_term_heading_still_becomes_typed_concept():
    """Headings that exactly match a registered term canonical name are typed.

    After the F-5 fix the heading classifier no longer mints ``Concept`` nodes
    from arbitrary noun-like headings. Registry-matched headings still get
    promoted, but they take the ontology-correct node type from the registry
    entry (e.g. ``Volumetric Rendering`` -> ``MethodologicalConcept``).
    """
    text = """# Doc

## Volumetric Rendering

Discussion of volumetric rendering.
"""
    graph = ResearchGraphExtractor().extract_text(
        text, source_path="docs/notes.md", source_kind="SourceDocument"
    )
    concept_layer_types = {
        ResearchNodeType.CONCEPT,
        ResearchNodeType.TECHNICAL_TERM,
        ResearchNodeType.METHODOLOGICAL_CONCEPT,
        ResearchNodeType.MATHEMATICAL_CONCEPT,
        ResearchNodeType.ALGORITHM,
        ResearchNodeType.ARCHITECTURE_PATTERN,
        ResearchNodeType.TRAINING_PARADIGM,
        ResearchNodeType.INFERENCE_STRATEGY,
        ResearchNodeType.EVALUATION_PROTOCOL,
        ResearchNodeType.TASK,
        ResearchNodeType.CAPABILITY,
    }
    concept_names = {n.name for n in graph.nodes if n.type in concept_layer_types}
    assert "Volumetric Rendering" in concept_names


def test_papers_outnumber_concepts_on_fixture_corpus():
    """Run over the bundled wiki_corpus fixture and check the overall ratio.

    This is the headline regression test: the original bug was that the daily
    digest filled the Concepts index with paper-title fragments. After the fix,
    Papers should outnumber Concepts on this small corpus.
    """
    fixture_root = Path(__file__).parent / "fixtures" / "wiki_corpus"
    md_files = sorted(fixture_root.rglob("*.md"))
    assert md_files, "fixture corpus is empty"

    extractor = ResearchGraphExtractor()
    paper_ids: set[str] = set()
    concept_ids: set[str] = set()
    for md in md_files:
        rel = str(md.relative_to(fixture_root))
        graph = extractor.extract_file(md, source_kind="SourceDocument")
        for node in graph.nodes:
            if node.type == ResearchNodeType.PAPER:
                paper_ids.add(node.id)
            elif node.type == ResearchNodeType.CONCEPT:
                concept_ids.add(node.id)

    # The fixture corpus has 3 distinct paper artefacts (one per arxiv id /
    # paper.md file) and almost no concept-shaped headings. After the fix,
    # papers must strictly outnumber heading-derived concepts.
    assert len(paper_ids) >= 1
    assert len(paper_ids) > len(concept_ids), (
        f"expected papers > concepts on fixture corpus, "
        f"got papers={len(paper_ids)} concepts={len(concept_ids)}"
    )
