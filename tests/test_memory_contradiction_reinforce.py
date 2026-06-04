"""KB-04 contradiction resolution + KB-05 recurring-insight reinforcement.

Covers:
1. ``run_contradiction_resolution`` mints a deterministic ``resolved_by``
   edge under a scripted LLM, and a warm content-keyed cache mints the
   same edge with ZERO further LLM calls.
2. ``compute_recurring_confidence`` returns ``high`` for insights recurring
   across >= threshold distinct sessions.
3. ``infer_confidence`` honours a node_memory-sourced confidence override
   (stamped onto node metadata) and is unchanged on the no-override path.
"""

from __future__ import annotations

from typing import Any, List, Optional, Union

from tesserae.memory.contradiction import (
    detect_contradicting_pairs,
    run_contradiction_resolution,
)
from tesserae.memory.reinforce import compute_recurring_confidence
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.temporal import infer_confidence


class _ScriptedClient:
    """LLMJsonClient stub returning scripted responses in order."""

    def __init__(self, responses: List[Optional[Union[dict, list]]]):
        self._responses = list(responses)
        self.calls: int = 0

    def complete_json(self, **kwargs: Any) -> Optional[Union[dict, list]]:
        self.calls += 1
        if not self._responses:
            return None
        return self._responses.pop(0)


def _claim(node_id: str, name: str, desc: str, source: str) -> ResearchNode:
    return ResearchNode(
        id=node_id,
        name=name,
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description=desc,
        source_path=source,
    )


def _contradicting_graph() -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            _claim(
                "claim-a",
                "Model X outperforms Model Y on DTU",
                "Model X outperforms Model Y on the DTU benchmark.",
                "data/research/paper_a.md",
            ),
            _claim(
                "claim-b",
                "Model X is outperformed by Model Y on DTU",
                "Model X is outperformed by Model Y on the DTU benchmark.",
                "data/research/paper_b.md",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Contradiction resolution
# ---------------------------------------------------------------------------


def test_detects_the_narrow_contradicting_pair():
    pairs = detect_contradicting_pairs(_contradicting_graph())
    assert len(pairs) == 1
    left, right = pairs[0]
    assert {left.id, right.id} == {"claim-a", "claim-b"}


def test_no_op_when_llm_none():
    graph = _contradicting_graph()
    out, conf = run_contradiction_resolution(graph, llm=None, cache_dir="/tmp/x")
    assert out.edges == []
    assert conf == {}


def test_mints_deterministic_resolved_by_edge(tmp_path):
    cache = tmp_path / "contradiction_cache"
    client = _ScriptedClient(
        [{"winner_id": "claim-a", "loser_id": "claim-b", "rationale": "A better evidenced"}]
    )
    graph = _contradicting_graph()
    out, conf = run_contradiction_resolution(graph, llm=client, cache_dir=cache)

    resolved = [e for e in out.edges if e.type == "resolved_by"]
    assert len(resolved) == 1
    assert resolved[0].source == "claim-b"  # loser
    assert resolved[0].target == "claim-a"  # winner
    assert conf == {"claim-a": "high", "claim-b": "low"}
    assert client.calls == 1
    assert list(cache.glob("*.json"))  # cached


def test_warm_cache_mints_same_edge_with_zero_llm_calls(tmp_path):
    cache = tmp_path / "cc"
    warm = _ScriptedClient(
        [{"winner_id": "claim-a", "loser_id": "claim-b", "rationale": "r"}]
    )
    run_contradiction_resolution(_contradicting_graph(), llm=warm, cache_dir=cache)
    assert warm.calls == 1

    # Second run: cache is warm -> no LLM call, identical edge.
    cold = _ScriptedClient([])  # would error if called
    out2, conf2 = run_contradiction_resolution(
        _contradicting_graph(), llm=cold, cache_dir=cache
    )
    assert cold.calls == 0
    resolved = [e for e in out2.edges if e.type == "resolved_by"]
    assert len(resolved) == 1
    assert (resolved[0].source, resolved[0].target) == ("claim-b", "claim-a")
    assert conf2 == {"claim-a": "high", "claim-b": "low"}


def test_detects_pair_in_reverse_id_order():
    """codex MAJOR 2: the contradiction must be found regardless of how the
    two node ids sort. Here the ``is outperformed by`` claim has the
    SMALLER id, which the old id-order-dependent loop silently skipped."""
    graph = ResearchGraph(
        nodes=[
            # id "claim-a" sorts FIRST but carries the RIGHT marker.
            _claim(
                "claim-a",
                "Model X is outperformed by Model Y on DTU",
                "Model X is outperformed by Model Y on the DTU benchmark.",
                "data/research/paper_b.md",
            ),
            # id "claim-z" sorts LAST but carries the LEFT marker.
            _claim(
                "claim-z",
                "Model X outperforms Model Y on DTU",
                "Model X outperforms Model Y on the DTU benchmark.",
                "data/research/paper_a.md",
            ),
        ]
    )
    pairs = detect_contradicting_pairs(graph)
    assert len(pairs) == 1
    left, right = pairs[0]
    # Roles assigned by MARKER, not id sort: left = outperforms claim.
    assert left.id == "claim-z"
    assert right.id == "claim-a"


def test_reverse_order_pair_resolves():
    """The reverse-id-order pair resolves into a correct resolved_by edge."""
    graph = ResearchGraph(
        nodes=[
            _claim(
                "claim-a",
                "Model X is outperformed by Model Y on DTU",
                "Model X is outperformed by Model Y on the DTU benchmark.",
                "data/research/paper_b.md",
            ),
            _claim(
                "claim-z",
                "Model X outperforms Model Y on DTU",
                "Model X outperforms Model Y on the DTU benchmark.",
                "data/research/paper_a.md",
            ),
        ]
    )
    # left=claim-z (outperforms). Winner=left => resolved_by edge from loser.
    client = _ScriptedClient(
        [{"winner_id": "claim-z", "loser_id": "claim-a", "rationale": "r"}]
    )
    out, conf = run_contradiction_resolution(graph, llm=client, cache_dir="/tmp/rev")
    resolved = [e for e in out.edges if e.type == "resolved_by"]
    assert len(resolved) == 1
    assert resolved[0].source == "claim-a"  # loser
    assert resolved[0].target == "claim-z"  # winner
    assert conf == {"claim-z": "high", "claim-a": "low"}


def test_warm_cache_hits_under_reminted_node_ids(tmp_path):
    """codex MAJOR 1: identical claim content reminted under DIFFERENT node
    ids must hit the warm content-keyed cache (0 LLM calls) and yield the
    same resolved_by orientation."""
    cache = tmp_path / "cc"

    # Cold run with the original ids.
    warm = _ScriptedClient(
        [{"winner_id": "claim-a", "loser_id": "claim-b", "rationale": "r"}]
    )
    run_contradiction_resolution(_contradicting_graph(), llm=warm, cache_dir=cache)
    assert warm.calls == 1

    # Second run: SAME content, DIFFERENT node ids.
    reminted = ResearchGraph(
        nodes=[
            _claim(
                "node-777",
                "Model X outperforms Model Y on DTU",
                "Model X outperforms Model Y on the DTU benchmark.",
                "data/research/paper_a.md",
            ),
            _claim(
                "node-888",
                "Model X is outperformed by Model Y on DTU",
                "Model X is outperformed by Model Y on the DTU benchmark.",
                "data/research/paper_b.md",
            ),
        ]
    )
    cold = _ScriptedClient([])  # would mint None / error if called
    out2, conf2 = run_contradiction_resolution(
        reminted, llm=cold, cache_dir=cache
    )
    assert cold.calls == 0, "reminted ids must still hit the warm cache"
    resolved = [e for e in out2.edges if e.type == "resolved_by"]
    assert len(resolved) == 1
    # Orientation tracks the MARKER roles (outperforms=node-777 won), not ids.
    assert resolved[0].source == "node-888"  # loser (is outperformed by)
    assert resolved[0].target == "node-777"  # winner (outperforms)
    assert conf2 == {"node-777": "high", "node-888": "low"}


# ---------------------------------------------------------------------------
# Recurring-insight reinforcement
# ---------------------------------------------------------------------------


def _insight(node_id: str, name: str, session_id: str) -> ResearchNode:
    return ResearchNode(
        id=node_id,
        name=name,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": session_id},
    )


def test_recurring_insight_across_three_sessions_is_high():
    name = "Always batch the embedding calls to cut latency"
    graph = ResearchGraph(
        nodes=[
            _insight("i1", name, "s1"),
            _insight("i2", name, "s2"),
            _insight("i3", name, "s3"),
        ]
    )
    conf = compute_recurring_confidence(graph, threshold=3)
    assert set(conf.values()) == {"high"}
    assert len(conf) >= 1  # the canonical surviving node reinforced


def test_below_threshold_not_reinforced():
    name = "Batch the embedding calls to cut latency"
    graph = ResearchGraph(
        nodes=[_insight("i1", name, "s1"), _insight("i2", name, "s2")]
    )
    assert compute_recurring_confidence(graph, threshold=3) == {}


def test_same_session_repeats_do_not_count():
    name = "Batch the embedding calls to cut latency"
    graph = ResearchGraph(
        nodes=[
            _insight("i1", name, "s1"),
            _insight("i2", name, "s1"),
            _insight("i3", name, "s1"),
        ]
    )
    # All three share session s1 -> 1 distinct session < threshold.
    assert compute_recurring_confidence(graph, threshold=3) == {}


def test_supersedes_chain_clusters_distinct_sessions():
    graph = ResearchGraph(
        nodes=[
            _insight("i1", "Cache LLM verdicts on disk", "s1"),
            _insight("i2", "Disk cache the LLM verdicts", "s2"),
            _insight("i3", "Persist LLM verdicts to a disk cache", "s3"),
        ],
        edges=[
            ResearchEdge(source="i2", target="i1", type="supersedes"),
            ResearchEdge(source="i3", target="i2", type="supersedes"),
        ],
    )
    conf = compute_recurring_confidence(graph, threshold=3)
    assert "high" in conf.values()


def test_recurring_empty_graph():
    assert compute_recurring_confidence(ResearchGraph()) == {}


# ---------------------------------------------------------------------------
# Temporal override
# ---------------------------------------------------------------------------


def _node(node_id: str, ntype: ResearchNodeType, **meta) -> ResearchNode:
    return ResearchNode(id=node_id, name=node_id, type=ntype, metadata=dict(meta))


def test_infer_confidence_heuristic_unchanged_without_override():
    subj = _node("s", ResearchNodeType.PERFORMANCE_CLAIM)
    obj = _node("o", ResearchNodeType.PAPER)
    assert infer_confidence(subj, obj, "some evidence") == "medium"
    assert infer_confidence(subj, obj, None) == "low"
    subj2 = _node("s2", ResearchNodeType.PAPER)
    assert infer_confidence(subj2, obj, "ev") == "high"
    assert infer_confidence(subj2, obj, None) == "medium"


def test_infer_confidence_honours_node_memory_override():
    # Orchestrator (05-03) stamps node_memory.confidence onto node metadata.
    subj = _node("s", ResearchNodeType.PERFORMANCE_CLAIM, confidence="high")
    obj = _node("o", ResearchNodeType.PAPER)
    # Without evidence the heuristic would say "low"; override wins.
    assert infer_confidence(subj, obj, None) == "high"
