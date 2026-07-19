"""Tests for the per-agent topic-map rollup (§12 Phase 5).

Covers:
* A topic map minted over a distilled-artifact fixture (LLM-titled clusters).
* Byte-idempotent double run (same bytes, no churn).
* Fail-loud on a missing artifact (standard distill remedy message).
* Deterministic structural fallback when no summarizer is injected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Union

import pytest

from tesserae.agent_distill import DistillError, agent_artifact_path
from tesserae.agent_topics import agent_topics_path, compile_agent_topics
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


AGENT_KEY = "claude-code:me:worker"


class _ScriptedClient:
    """LLMJsonClient stub — counts calls, returns deterministic JSON."""

    def __init__(self) -> None:
        self.calls: List[dict] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Any = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        self.calls.append({"schema_name": schema_name, "cache_key": cache_key})
        index = len(self.calls)
        return {
            "title": f"Cluster {index}",
            "description": f"Test description for cluster {index}.",
            "tags": ["alpha", "beta", "gamma", "delta", "epsilon"],
        }


def _note(nid: str, name: str, kind: str = "note") -> ResearchNode:
    return ResearchNode(
        id=nid,
        name=name,
        type=ResearchNodeType.DISTILLED_NOTE,
        description=f"Body of {name}.",
        metadata={"kind": kind, "agent": AGENT_KEY},
    )


def _anchor(nid: str, name: str) -> ResearchNode:
    return ResearchNode(id=nid, name=name, type=ResearchNodeType.CONCEPT, description=name)


def _distilled_fixture() -> ResearchGraph:
    """Two topics: notes 1/2 share anchor X, notes 3/4 share anchor Y.

    Each shared anchor bridges its two notes into a 3-member cluster; the two
    topics are disconnected so a community detector keeps them separate. An
    index meta-note (no anchors) must be ignored.
    """
    nodes = [
        _note("DistilledNote:n1", "Retry backoff"),
        _note("DistilledNote:n2", "Idempotent writes"),
        _note("DistilledNote:n3", "Louvain seeding"),
        _note("DistilledNote:n4", "Cache forking"),
        _note("DistilledNote:index", "Index", kind="index"),
        _anchor("Concept:x", "Distillation"),
        _anchor("Concept:y", "Community detection"),
    ]
    edges = [
        ResearchEdge(source="DistilledNote:n1", target="Concept:x", type="derived_from"),
        ResearchEdge(source="DistilledNote:n2", target="Concept:x", type="derived_from"),
        ResearchEdge(source="DistilledNote:n3", target="Concept:y", type="derived_from"),
        ResearchEdge(source="DistilledNote:n4", target="Concept:y", type="derived_from"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _write_artifact(root: Path, graph: ResearchGraph) -> Path:
    path = agent_artifact_path(root, AGENT_KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return path


def test_topic_map_over_distilled_fixture(tmp_path: Path) -> None:
    _write_artifact(tmp_path, _distilled_fixture())
    client = _ScriptedClient()

    out = compile_agent_topics(tmp_path, AGENT_KEY, json_client=client, min_size=2)

    assert out == agent_topics_path(tmp_path, AGENT_KEY)
    body = out.read_text(encoding="utf-8")

    # Two topics, LLM-titled, with their note names and tags.
    assert "2 topic(s)." in body
    assert "## Cluster 1" in body
    assert "## Cluster 2" in body
    assert "- Retry backoff" in body
    assert "- Idempotent writes" in body
    assert "- Louvain seeding" in body
    assert "- Cache forking" in body
    assert "Tags: alpha, beta" in body
    # Meta-note is excluded from topics.
    assert "Index" not in body.replace("index/topics pass", "")
    # One LLM call per cluster.
    assert len(client.calls) == 2


def test_byte_idempotent_double_run(tmp_path: Path) -> None:
    _write_artifact(tmp_path, _distilled_fixture())

    first = compile_agent_topics(tmp_path, AGENT_KEY, json_client=_ScriptedClient(), min_size=2)
    bytes_1 = first.read_bytes()
    second = compile_agent_topics(tmp_path, AGENT_KEY, json_client=_ScriptedClient(), min_size=2)
    bytes_2 = second.read_bytes()

    assert bytes_1 == bytes_2


def test_missing_artifact_raises(tmp_path: Path) -> None:
    with pytest.raises(DistillError) as excinfo:
        compile_agent_topics(tmp_path, AGENT_KEY, json_client=_ScriptedClient())
    msg = str(excinfo.value)
    assert "no distilled artifact" in msg
    assert f"tesserae distill --agent {AGENT_KEY}" in msg


def test_structural_fallback_without_summarizer(tmp_path: Path) -> None:
    _write_artifact(tmp_path, _distilled_fixture())

    out = compile_agent_topics(tmp_path, AGENT_KEY, json_client=None, min_size=2)
    body = out.read_text(encoding="utf-8")

    # Clusters still rendered, titled by their shared anchor (structural).
    assert "2 topic(s)." in body
    assert "## Distillation" in body
    assert "## Community detection" in body
    assert "- Retry backoff" in body
    assert "- Louvain seeding" in body
    # No LLM tags line in the structural fallback.
    assert "Tags:" not in body
    # No topics_cache written when there's no summarizer.
    assert not (agent_artifact_path(tmp_path, AGENT_KEY).parent / "topics_cache").exists()
