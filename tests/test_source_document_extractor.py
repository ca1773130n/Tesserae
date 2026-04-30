from llm_wiki.research_graph import ResearchGraphExtractor, ResearchNodeType


def test_repository_document_extraction_uses_source_and_heading_nodes_not_research_claims():
    text = """# Feature Map

This document mentions Gaussian Splatting as an example string, not as a paper claim.

## Volumetric Rendering

A concept-shaped heading that survives the filter.

## Frontend

Static HTML site generation.
"""

    graph = ResearchGraphExtractor().extract_text(text, source_path="docs/feature-map.md", source_kind="SourceDocument")
    types = {node.type for node in graph.nodes}
    names = {node.name for node in graph.nodes}

    assert ResearchNodeType.SOURCE_DOCUMENT in types
    assert "Feature Map" in names
    # Headings that exactly match a registered term get the registry's typed
    # node type — ``Volumetric Rendering`` is registered as a
    # ``MethodologicalConcept``. After F-5 we no longer mint generic
    # ``Concept`` nodes from arbitrary headings.
    assert "Volumetric Rendering" in names
    # Generic single-word section markers like "Frontend" are intentionally
    # NOT promoted to typed concept nodes — they are not in the registry.
    assert "Frontend" not in names
    assert ResearchNodeType.CLAIM not in types
    assert ResearchNodeType.EVIDENCE_SPAN not in types
    # SourceDocument extraction does not run the body claim/evidence pass, so
    # the registry-promoted heading is the only typed concept-layer node.
    assert ResearchNodeType.METHODOLOGICAL_CONCEPT in types
