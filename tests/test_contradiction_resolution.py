"""KB-04: LLM-arbitrated contradiction resolution mints a resolved_by edge.

A scripted LLM client arbitrates a detected contradicting pair into a
deterministic ``resolved_by`` edge (source=loser, target=winner). The
verdict is cached on disk content-keyed, so a second pass with a fresh
empty client mints the SAME edge with ZERO LLM calls. lint then demotes
the resolved pair to ``info`` and raises an unresolved pair to ``warning``.

Deterministic: scripted client (no network), fixed node content, no
wall-clock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Union

import pytest

from tesserae.lint import WikiLinter
from tesserae.memory.contradiction import (
    RESOLVED_BY_EDGE,
    detect_contradicting_pairs,
    run_contradiction_resolution,
)
from tesserae.research_graph import (
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


class _ScriptedClient:
    """LLMJsonClient stub returning scripted ``complete_json`` responses."""

    def __init__(self, responses: List[Optional[Union[dict, list]]]):
        self._responses = list(responses)
        self.calls: int = 0

    def complete_json(self, **kwargs: Any) -> Optional[Union[dict, list]]:
        self.calls += 1
        if not self._responses:
            return None
        return self._responses.pop(0)


def _claim(node_id: str, name: str, desc: str, source_path: str) -> ResearchNode:
    return ResearchNode(
        id=node_id,
        name=name,
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description=desc,
        source_path=source_path,
    )


@pytest.fixture
def contradicting_graph() -> ResearchGraph:
    # left carries "outperforms"; right carries "is outperformed by"; they
    # share the model+benchmark topic and come from different sources.
    a = _claim(
        "PerformanceClaim:a",
        "Model X beats Y on GLUE",
        "Model X outperforms Model Y on the GLUE benchmark.",
        source_path="docs/paper-a.md",
    )
    b = _claim(
        "PerformanceClaim:b",
        "Model X loses to Y on GLUE",
        "Model X is outperformed by Model Y on the GLUE benchmark.",
        source_path="docs/paper-b.md",
    )
    return ResearchGraph(nodes=[a, b], edges=[])


def test_detect_finds_the_pair(contradicting_graph: ResearchGraph) -> None:
    pairs = detect_contradicting_pairs(contradicting_graph)
    assert len(pairs) == 1
    left, right = pairs[0]
    assert left.id == "PerformanceClaim:a"
    assert right.id == "PerformanceClaim:b"


def test_resolution_mints_resolved_by_edge(
    contradicting_graph: ResearchGraph, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "contradiction_cache"
    client = _ScriptedClient(
        [
            {
                "winner_id": "PerformanceClaim:a",
                "loser_id": "PerformanceClaim:b",
                "rationale": "Paper A used the standard split.",
            }
        ]
    )
    out, conf = run_contradiction_resolution(
        contradicting_graph, llm=client, cache_dir=cache_dir
    )
    assert client.calls == 1

    resolved = [e for e in out.edges if e.type == RESOLVED_BY_EDGE]
    assert len(resolved) == 1
    edge = resolved[0]
    # source == loser, target == winner.
    assert edge.source == "PerformanceClaim:b"
    assert edge.target == "PerformanceClaim:a"
    assert conf["PerformanceClaim:a"] == "high"
    assert conf["PerformanceClaim:b"] == "low"


def test_warm_cache_mints_same_edge_with_zero_llm_calls(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "contradiction_cache"

    # Cold run populates the content-keyed cache.
    cold_graph = _two_claim_graph()
    cold_client = _ScriptedClient(
        [
            {
                "winner_id": "PerformanceClaim:a",
                "loser_id": "PerformanceClaim:b",
                "rationale": "A is canonical.",
            }
        ]
    )
    run_contradiction_resolution(cold_graph, llm=cold_client, cache_dir=cache_dir)
    assert cold_client.calls == 1
    assert list(cache_dir.glob("*.json"))

    # Warm run: a FRESH graph, an EMPTY scripted client -> verdict from disk.
    warm_graph = _two_claim_graph()
    warm_client = _ScriptedClient([])
    out, _conf = run_contradiction_resolution(
        warm_graph, llm=warm_client, cache_dir=cache_dir
    )
    assert warm_client.calls == 0, "warm cache must skip the LLM"
    resolved = [e for e in out.edges if e.type == RESOLVED_BY_EDGE]
    assert len(resolved) == 1
    assert resolved[0].source == "PerformanceClaim:b"
    assert resolved[0].target == "PerformanceClaim:a"


def test_no_client_is_no_op(contradicting_graph: ResearchGraph, tmp_path: Path) -> None:
    out, conf = run_contradiction_resolution(
        contradicting_graph, llm=None, cache_dir=tmp_path / "cache"
    )
    assert [e for e in out.edges if e.type == RESOLVED_BY_EDGE] == []
    assert conf == {}


def test_lint_severity_resolved_info_unresolved_warning(
    tmp_path: Path,
) -> None:
    # Resolved pair -> the lint check demotes to info.
    resolved_graph = _two_claim_graph()
    client = _ScriptedClient(
        [
            {
                "winner_id": "PerformanceClaim:a",
                "loser_id": "PerformanceClaim:b",
                "rationale": "A wins.",
            }
        ]
    )
    resolved_graph, _ = run_contradiction_resolution(
        resolved_graph, llm=client, cache_dir=tmp_path / "cache"
    )
    resolved_findings = _contradiction_findings(resolved_graph)
    assert len(resolved_findings) == 1
    assert resolved_findings[0].severity == "info"

    # Unresolved pair (no resolved_by edge) -> warning.
    unresolved_findings = _contradiction_findings(_two_claim_graph())
    assert len(unresolved_findings) == 1
    assert unresolved_findings[0].severity == "warning"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _two_claim_graph() -> ResearchGraph:
    a = _claim(
        "PerformanceClaim:a",
        "Model X beats Y on GLUE",
        "Model X outperforms Model Y on the GLUE benchmark.",
        source_path="docs/paper-a.md",
    )
    b = _claim(
        "PerformanceClaim:b",
        "Model X loses to Y on GLUE",
        "Model X is outperformed by Model Y on the GLUE benchmark.",
        source_path="docs/paper-b.md",
    )
    return ResearchGraph(nodes=[a, b], edges=[])


def _contradiction_findings(graph: ResearchGraph, tmp_path: Path = None):
    """Run lint's contradiction check directly over the graph's dicts.

    Avoids scaffolding a whole project: ``_check_contradicting_claims`` is a
    pure function of ``nodes_by_id`` + ``edges`` dicts (the same shape
    WikiLinter.run() feeds it from graph.json).
    """
    import tempfile

    linter = WikiLinter(tempfile.gettempdir())
    nodes_by_id = {n.id: _node_dict(n) for n in graph.nodes}
    edges = [
        {"source": e.source, "target": e.target, "type": e.type}
        for e in graph.edges
    ]
    return [
        f
        for f in linter._check_contradicting_claims(nodes_by_id, edges)
        if f.code == "CONTRADICTING_CLAIMS"
    ]


def _node_dict(node: ResearchNode) -> dict:
    return {
        "id": node.id,
        "name": node.name,
        "type": node.type.value,
        "description": node.description,
        "source_path": node.source_path,
    }
