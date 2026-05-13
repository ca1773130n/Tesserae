"""Regression tests for the filename-shaped concept filter (Bug A).

The LLM extractor occasionally emits ``Concept``-typed nodes whose names are
literally filenames or path strings (``feature-map.md``, ``pyproject.toml``,
``tests/test_x.py``). Those duplicate the ``SourceDocument`` nodes that
already represent the same files with proper titles and pollute the
concept layer of the visual graph. ``looks_like_filename_or_path`` +
``filter_filename_shaped_concepts`` strip them.

These tests pin the predicate so we never silently regress into either
dropping real concepts ("GPT-4o", "Llama 3.1") or admitting filename
noise.
"""

from __future__ import annotations

from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    filter_filename_shaped_concepts,
    looks_like_filename_or_path,
    looks_like_heading_or_sentence,
    source_path_looks_like_i18n_duplicate,
)


# ---------------------------------------------------------------------------
# predicate
# ---------------------------------------------------------------------------


def test_looks_like_filename_or_path_recognizes_common_filenames():
    assert looks_like_filename_or_path("feature-map.md") is True
    assert looks_like_filename_or_path("pyproject.toml") is True
    assert looks_like_filename_or_path("__init__.py") is True
    assert looks_like_filename_or_path("tests/test_x.py") is True
    assert looks_like_filename_or_path("docs/integrations/rag-anything.md") is True
    assert looks_like_filename_or_path("package.json") is True
    assert looks_like_filename_or_path(".gitignore") is True
    assert looks_like_filename_or_path("Makefile") is True
    assert looks_like_filename_or_path("Dockerfile") is True
    assert looks_like_filename_or_path("LICENSE") is True


def test_looks_like_filename_or_path_keeps_real_concepts():
    # These are real research-domain concept tokens. They must survive even
    # though some of them superficially resemble dotted filenames.
    assert looks_like_filename_or_path("GaussianFlow SLAM") is False
    assert looks_like_filename_or_path("Self-Supervised Learning") is False
    assert looks_like_filename_or_path("Depth Map") is False
    assert looks_like_filename_or_path("4D Gaussian Splatting") is False
    assert looks_like_filename_or_path("GPT-4o") is False
    assert looks_like_filename_or_path("Llama 3.1") is False
    assert looks_like_filename_or_path("128-D vector") is False
    assert looks_like_filename_or_path("A. M. Turing") is False


def test_looks_like_filename_or_path_handles_empty_and_whitespace():
    assert looks_like_filename_or_path("") is False
    assert looks_like_filename_or_path("   ") is False
    # Whitespace-padded filename still gets caught.
    assert looks_like_filename_or_path("  foo.md  ") is True


def test_looks_like_filename_or_path_windows_path_separator():
    assert looks_like_filename_or_path(r"tests\test_x.py") is True


# ---------------------------------------------------------------------------
# graph-level filter
# ---------------------------------------------------------------------------


def test_filename_concept_filter_drops_nodes_and_incident_edges():
    """End-to-end: a tiny graph with a filename-named Concept comes out with
    neither the offending node nor its incident edges."""
    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="c1",
                name="feature-map.md",
                type=ResearchNodeType.CONCEPT,
                description="",
            ),
            ResearchNode(
                id="c2",
                name="Depth Map",
                type=ResearchNodeType.CONCEPT,
                description="",
            ),
            ResearchNode(
                id="s1",
                name="Architecture",
                type=ResearchNodeType.SOURCE_DOCUMENT,
                description="",
            ),
        ],
        edges=[
            ResearchEdge(source="s1", target="c1", type="defines"),
            ResearchEdge(source="s1", target="c2", type="defines"),
        ],
    )
    cleaned = filter_filename_shaped_concepts(graph)
    names = {n.name for n in cleaned.nodes}
    assert "feature-map.md" not in names
    assert "Depth Map" in names
    assert "Architecture" in names
    # The edge anchored to the dropped node is also gone.
    targets = {e.target for e in cleaned.edges}
    assert "c1" not in targets
    assert "c2" in targets


def test_filename_concept_filter_preserves_source_document_with_filename_shape():
    """A SourceDocument legitimately named ``README.md`` must survive — the
    filter only targets concept-layer types."""
    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="src1",
                name="README.md",
                type=ResearchNodeType.SOURCE_DOCUMENT,
                description="",
            ),
        ],
        edges=[],
    )
    cleaned = filter_filename_shaped_concepts(graph)
    assert {n.name for n in cleaned.nodes} == {"README.md"}


def test_filename_concept_filter_no_op_when_no_offenders():
    """Hot-path: graphs without any filename-shaped concepts must come back
    unchanged (same object identity is fine; we just need byte-equivalence)."""
    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="c1",
                name="Self-Supervised Learning",
                type=ResearchNodeType.CONCEPT,
                description="",
            ),
        ],
        edges=[],
    )
    cleaned = filter_filename_shaped_concepts(graph)
    assert [n.name for n in cleaned.nodes] == ["Self-Supervised Learning"]


def test_filename_concept_filter_covers_all_conceptish_types():
    """The filter targets every concept-layer node type, not just CONCEPT."""
    conceptish_types = [
        ResearchNodeType.CONCEPT,
        ResearchNodeType.TECHNICAL_TERM,
        ResearchNodeType.METHODOLOGICAL_CONCEPT,
        ResearchNodeType.MATHEMATICAL_CONCEPT,
        ResearchNodeType.ALGORITHM,
        ResearchNodeType.CAPABILITY,
        ResearchNodeType.TASK,
        ResearchNodeType.APPROACH_FAMILY,
        ResearchNodeType.RESEARCH_TOPIC,
        ResearchNodeType.INFERENCE_STRATEGY,
        ResearchNodeType.EVALUATION_PROTOCOL,
        ResearchNodeType.TRAINING_PARADIGM,
        ResearchNodeType.OBJECTIVE_FUNCTION,
        ResearchNodeType.ARCHITECTURE_PATTERN,
    ]
    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id=f"n{i}",
                name="bogus.py",
                type=t,
                description="",
            )
            for i, t in enumerate(conceptish_types)
        ],
        edges=[],
    )
    cleaned = filter_filename_shaped_concepts(graph)
    assert cleaned.nodes == []


# ---------------------------------------------------------------------------
# heading / sentence / i18n predicates (Bug B follow-up)
# ---------------------------------------------------------------------------


def test_looks_like_heading_or_sentence_rejects_heading_shapes():
    # Stop-word leaders
    assert looks_like_heading_or_sentence("What you get after compile") is True
    assert looks_like_heading_or_sentence("Why use both") is True
    assert looks_like_heading_or_sentence("When to use this and when not to") is True
    # Imperative
    assert looks_like_heading_or_sentence("Ensure the shell command is installed") is True
    assert looks_like_heading_or_sentence("Install MinerU core extras") is True
    # Numbered list items
    assert looks_like_heading_or_sentence("1 Ejecuta el asistente de configuracion") is True
    assert looks_like_heading_or_sentence("11 Despliega en GitHub Pages") is True
    # Internal-finding prefix
    assert looks_like_heading_or_sentence("F-7 Repositories are extracted as orphan source artifacts") is True
    assert looks_like_heading_or_sentence("F-10 Reset does not reset to a fitted graph") is True
    # Sentence verbs
    assert looks_like_heading_or_sentence("The compiler is broken") is True
    # Long sentences (>60 chars)
    assert looks_like_heading_or_sentence(
        "Understand Anything companion graph RAG-Anything multimodal Cognee runtime memory"
    ) is True
    # Short CJK headings
    assert looks_like_heading_or_sentence("상태") is True
    assert looks_like_heading_or_sentence("통합") is True
    assert looks_like_heading_or_sentence("状态") is True
    assert looks_like_heading_or_sentence("管道") is True
    assert looks_like_heading_or_sentence("インテグレーション") is True
    # Sentence punctuation
    assert looks_like_heading_or_sentence(
        "research_ontology.owl, the controlled vocabulary"
    ) is True


def test_looks_like_heading_or_sentence_preserves_real_concepts():
    # Multi-word real concepts
    assert looks_like_heading_or_sentence("Self-Supervised Learning") is False
    assert looks_like_heading_or_sentence("Gaussian Splatting") is False
    assert looks_like_heading_or_sentence("Geometry-Grounded Gaussian Splatting") is False
    assert looks_like_heading_or_sentence("Depth Map") is False
    assert looks_like_heading_or_sentence("Visual SLAM") is False
    # Dimensional / version prefixes (must NOT match numbered-list rule —
    # the rule requires whitespace immediately after digits, so "4D" is safe).
    assert looks_like_heading_or_sentence("4D Gaussian Splatting") is False
    assert looks_like_heading_or_sentence("8K Resolution") is False
    # Single-word concepts / acronyms
    assert looks_like_heading_or_sentence("RLHF") is False
    assert looks_like_heading_or_sentence("SLAM") is False
    # Model variants — period followed by digit is preserved as a version
    # number, not flagged as a sentence end.
    assert looks_like_heading_or_sentence("GPT-4o") is False
    assert looks_like_heading_or_sentence("Llama 3.1") is False
    # Hyphenated technical terms
    assert looks_like_heading_or_sentence("Pre-training") is False
    assert looks_like_heading_or_sentence("Fine-tuning") is False


def test_looks_like_heading_or_sentence_handles_empty_and_whitespace():
    assert looks_like_heading_or_sentence("") is False
    assert looks_like_heading_or_sentence("   ") is False


def test_source_path_looks_like_i18n_duplicate_recognizes_localized_paths():
    assert source_path_looks_like_i18n_duplicate("README.ko.md") is True
    assert source_path_looks_like_i18n_duplicate("README.zh.md") is True
    assert source_path_looks_like_i18n_duplicate("docs/quickstart.fr.md") is True
    assert source_path_looks_like_i18n_duplicate("docs/i18n/integrations/rag-anything.ja.md") is True
    assert source_path_looks_like_i18n_duplicate("docs/i18n/README.ko.md") is True


def test_source_path_looks_like_i18n_duplicate_keeps_canonical():
    assert source_path_looks_like_i18n_duplicate("README.md") is False
    assert source_path_looks_like_i18n_duplicate("docs/quickstart.md") is False
    assert source_path_looks_like_i18n_duplicate("docs/integrations/rag-anything.md") is False
    assert source_path_looks_like_i18n_duplicate("data/research/weekly/2026-W17/digest.md") is False
    # ``docs/internal/`` is NOT the i18n directory — segment match is exact.
    assert source_path_looks_like_i18n_duplicate("docs/internal/notes.md") is False
    # Empty / None inputs are safe.
    assert source_path_looks_like_i18n_duplicate("") is False
    assert source_path_looks_like_i18n_duplicate(None) is False


def test_filter_drops_heading_and_i18n_concepts_end_to_end():
    """End-to-end: graph with mixed real concepts + bogus heading/i18n
    entries yields a clean graph after the unified post-merge filter."""
    graph = ResearchGraph(
        nodes=[
            # Real concept — must survive.
            ResearchNode(
                id="c_keep",
                name="Self-Supervised Learning",
                type=ResearchNodeType.CONCEPT,
                description="",
                source_path="docs/architecture.md",
            ),
            # Real concept with version number — must survive.
            ResearchNode(
                id="c_keep_version",
                name="Llama 3.1",
                type=ResearchNodeType.MODEL,
                description="",
                source_path="docs/architecture.md",
            ),
            # Numbered list item — drop.
            ResearchNode(
                id="c_numbered",
                name="1 Ejecuta el asistente",
                type=ResearchNodeType.CONCEPT,
                description="",
                source_path="docs/quickstart.es.md",
            ),
            # Short CJK heading — drop.
            ResearchNode(
                id="c_cjk",
                name="상태",
                type=ResearchNodeType.CONCEPT,
                description="",
                source_path="README.ko.md",
            ),
            # F-N finding prefix — drop.
            ResearchNode(
                id="c_finding",
                name="F-7 Repositories are extracted",
                type=ResearchNodeType.CONCEPT,
                description="",
            ),
            # Concept extracted from i18n duplicate source — drop solely
            # on source_path even though the name itself is fine.
            ResearchNode(
                id="c_i18n_dup",
                name="Self-Supervised Learning",
                type=ResearchNodeType.CONCEPT,
                description="",
                source_path="docs/i18n/architecture.fr.md",
            ),
            # SourceDocument with i18n-shaped filename — the filter only
            # targets concept-layer types, so this MUST survive.
            ResearchNode(
                id="src_readme_ko",
                name="README.ko.md",
                type=ResearchNodeType.SOURCE_DOCUMENT,
                description="",
                source_path="README.ko.md",
            ),
        ],
        edges=[
            ResearchEdge(source="src_readme_ko", target="c_numbered", type="defines"),
            ResearchEdge(source="src_readme_ko", target="c_cjk", type="defines"),
            ResearchEdge(source="src_readme_ko", target="c_keep", type="defines"),
        ],
    )
    cleaned = filter_filename_shaped_concepts(graph)
    kept_ids = {n.id for n in cleaned.nodes}
    assert kept_ids == {"c_keep", "c_keep_version", "src_readme_ko"}
    # Edges incident to dropped nodes are also gone.
    kept_targets = {e.target for e in cleaned.edges}
    assert "c_numbered" not in kept_targets
    assert "c_cjk" not in kept_targets
    assert "c_keep" in kept_targets
