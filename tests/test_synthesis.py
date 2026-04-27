"""Tests for `llm_wiki.synthesis.SynthesisProjector`.

`WikiPageStore` is being implemented in parallel by a sibling subagent; this
test module subclasses the stub with a minimal idempotent on-disk
implementation so the synthesis tests are independent of merge order. When the
real `WikiPageStore` lands, the production `SynthesisProjector` will use it
directly without any change to these tests.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path
from typing import Dict, List

from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)
from llm_wiki.synthesis import SynthesisProjector
from llm_wiki.wiki_store import WikiPage, WikiPageStore


# ---------------------------------------------------------------------------
# Test-local WikiPageStore (mirrors the public surface declared in §7.1)
# ---------------------------------------------------------------------------


class _LocalWikiPageStore(WikiPageStore):
    """Minimal idempotent on-disk implementation for tests.

    Mirrors the contract subagent A is implementing: `write_page` returns True
    only when the body changes (idempotent by hashing body content excluding
    the frontmatter `generated_at` field).
    """

    def slug_for(self, name: str) -> str:
        safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
        while "--" in safe:
            safe = safe.replace("--", "-")
        return safe or hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _stable_body(body: str) -> str:
        return re.sub(r"^generated_at:.*$", "generated_at: <stable>", body, count=1, flags=re.MULTILINE)

    def write_page(self, page: WikiPage) -> bool:
        path = page.path
        path.parent.mkdir(parents=True, exist_ok=True)
        new_stable = self._stable_body(page.body)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if self._stable_body(existing) == new_stable:
                return False
        path.write_text(page.body, encoding="utf-8")
        return True

    def read_page(self, path):
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        # Tests do not need full frontmatter parsing; body is sufficient.
        return WikiPage(
            kind=path.parent.name,
            slug=path.stem,
            title=path.stem,
            body=text,
            path=path,
            frontmatter={},
        )

    def list_pages(self, kind: str) -> List[WikiPage]:
        directory = self.root / kind
        if not directory.exists():
            return []
        return [self.read_page(p) for p in sorted(directory.glob("*.md"))]


# ---------------------------------------------------------------------------
# Fixture: tiny in-memory ResearchGraph
# ---------------------------------------------------------------------------


def _node(name: str, ntype: ResearchNodeType, source_path: str | None = None, **metadata) -> ResearchNode:
    node_id = stable_id(ntype.value, name)
    return ResearchNode(
        id=node_id,
        name=name,
        type=ntype,
        aliases=[],
        description="",
        source_path=source_path,
        metadata=metadata,
    )


def _build_fixture_graph() -> ResearchGraph:
    field = _node("3D/4D Vision and Reconstruction", ResearchNodeType.RESEARCH_FIELD)

    paper_a = _node(
        "Geometry-Grounded Gaussian Splatting",
        ResearchNodeType.PAPER,
        source_path="data/research/daily/2026-04-25/paper-a.md",
        analysis_date="2026-04-25",
    )
    paper_b = _node(
        "Stochastic Solid Surfaces",
        ResearchNodeType.PAPER,
        source_path="data/research/daily/2026-04-25/paper-b.md",
        analysis_date="2026-04-25",
    )
    paper_c = _node(
        "Volumetric Rendering Revisited",
        ResearchNodeType.PAPER,
        source_path="data/research/daily/2026-04-26/paper-c.md",
        analysis_date="2026-04-26",
    )
    paper_w = _node(
        "Weekly Survey of Splatting Methods",
        ResearchNodeType.PAPER,
        source_path="data/research/weekly/2026-W17/survey.md",
        analysis_date="2026-04-27",
    )

    family = _node("Geometry-Grounded Gaussian Splatting Family", ResearchNodeType.APPROACH_FAMILY)
    other_family = _node("Volumetric Rendering Family", ResearchNodeType.APPROACH_FAMILY)

    task = _node("Novel View Synthesis", ResearchNodeType.TASK)
    concept = _node("Stochastic Solid", ResearchNodeType.MATHEMATICAL_CONCEPT)
    concept_b = _node("Depth Map", ResearchNodeType.TECHNICAL_TERM)

    nodes = [
        field,
        paper_a, paper_b, paper_c, paper_w,
        family, other_family,
        task, concept, concept_b,
    ]

    def edge(src, etype, tgt) -> ResearchEdge:
        return ResearchEdge(source=src.id, target=tgt.id, type=etype, evidence=None, metadata={})

    edges = [
        edge(paper_a, "part_of", field),
        edge(paper_b, "part_of", field),
        edge(paper_c, "part_of", field),
        edge(paper_w, "part_of", field),

        # Family connects 3 papers (>=3 -> qualifies for topic synthesis)
        edge(paper_a, "belongs_to_approach_family", family),
        edge(paper_b, "belongs_to_approach_family", family),
        edge(paper_c, "belongs_to_approach_family", family),

        # Other family with one paper, sharing a Task with `family`
        edge(paper_w, "belongs_to_approach_family", other_family),

        # Both families address the same Task -> drives a comparison synthesis
        edge(family, "addresses", task),
        edge(other_family, "addresses", task),

        # Concepts referenced by sources
        edge(paper_a, "uses", concept),
        edge(paper_b, "uses", concept),
        edge(paper_c, "uses", concept_b),
    ]

    return ResearchGraph(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_project_emits_pulse_field_overview_and_synthesis_edges(tmp_path: Path):
    graph = _build_fixture_graph()
    store = _LocalWikiPageStore(tmp_path / "wiki")

    projector = SynthesisProjector(store)
    new_graph, written = projector.project(graph)

    synth_nodes = [n for n in new_graph.nodes if n.type == ResearchNodeType.SYNTHESIS]
    kinds = [n.metadata.get("synthesis_kind") for n in synth_nodes]

    # Exactly one pulse synthesis.
    assert kinds.count("pulse") == 1

    # At least one field overview synthesis.
    assert any(k == "field_overview" for k in kinds)

    # New edges exist: synthesizes + summarizes.
    edge_types = {e.type for e in new_graph.edges}
    assert "synthesizes" in edge_types
    assert "summarizes" in edge_types

    # Topic synthesis should have been emitted (3 papers in the family).
    assert any(k == "topic" for k in kinds)

    # Daily digests for both 2026-04-25 and 2026-04-26.
    daily_kinds = [
        n for n in synth_nodes if n.metadata.get("synthesis_kind") == "daily_digest"
    ]
    assert len(daily_kinds) == 2

    # Weekly synthesis for the W17 source.
    assert any(k == "weekly" for k in kinds)

    # All synthesis pages were written on the first call.
    written_kinds = {p.frontmatter["synthesis_kind"] for p in written}
    assert {"pulse", "field_overview", "topic", "daily_digest", "weekly"} <= written_kinds


def test_project_is_idempotent_when_graph_unchanged(tmp_path: Path):
    graph = _build_fixture_graph()
    store = _LocalWikiPageStore(tmp_path / "wiki")

    projector = SynthesisProjector(store)
    projector.project(graph)
    _, written_second = projector.project(graph)

    assert written_second == [], "second project() should produce zero rewrites"


def test_pulse_rewrites_when_input_node_name_changes(tmp_path: Path):
    graph = _build_fixture_graph()
    store = _LocalWikiPageStore(tmp_path / "wiki")

    projector = SynthesisProjector(store)
    projector.project(graph)

    # Mutate one paper's name: produce a fresh graph with the rename.
    new_nodes: List[ResearchNode] = []
    for node in graph.nodes:
        if node.name == "Volumetric Rendering Revisited":
            new_nodes.append(replace(node, name="Volumetric Rendering Reconsidered"))
        else:
            new_nodes.append(node)
    mutated = ResearchGraph(nodes=new_nodes, edges=graph.edges)

    _, written_third = projector.project(mutated)
    rewritten_kinds = {p.frontmatter["synthesis_kind"] for p in written_third}
    assert "pulse" in rewritten_kinds, (
        "pulse must rewrite when an input concept/paper name changes; "
        f"got rewrites for {rewritten_kinds}"
    )


def test_comparison_emits_when_two_families_share_task(tmp_path: Path):
    graph = _build_fixture_graph()
    store = _LocalWikiPageStore(tmp_path / "wiki")

    projector = SynthesisProjector(store)
    new_graph, _ = projector.project(graph)
    kinds = [
        n.metadata.get("synthesis_kind")
        for n in new_graph.nodes
        if n.type == ResearchNodeType.SYNTHESIS
    ]
    assert any(k == "comparison" for k in kinds)
