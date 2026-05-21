"""Tests for the A-MEM-style decay scoring and the supersede edge pass.

Covers three angles:

1. ``compute_decay_score`` ranks fresh > old (and a small access bump
   nudges scores upward).
2. ``run_supersede_pass`` mints a ``supersedes`` edge between a fresh
   finding and its near-duplicate when the LLM agrees.
3. The MCP ``fresh_insights`` tool excludes superseded findings AND
   returns them in decay-score-descending order.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional, Union

import pytest

from tesserae.memory.decay import compute_decay_score
from tesserae.memory.supersede import (
    SUPERSEDE_EDGE,
    jaccard,
    run_supersede_pass,
    supersede_pass_enabled,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _insight(
    *,
    id: str,
    body: str,
    first_seen_at: str,
    access_count: int = 0,
) -> ResearchNode:
    return ResearchNode(
        id=f"SessionInsight:{id}",
        name=body,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={
            "session_id": "sess-1",
            "first_seen_at": first_seen_at,
            "last_accessed_at": first_seen_at,
            "access_count": access_count,
        },
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def three_insights(now: datetime) -> List[ResearchNode]:
    """Three session insights: fresh, old, near-duplicate-of-fresh."""
    fresh_iso = now.isoformat()
    old_iso = (now - timedelta(days=60)).isoformat()
    near_dup_iso = (now - timedelta(hours=1)).isoformat()
    return [
        _insight(
            id="fresh",
            body="Atomic writes need a PID plus random tmp suffix",
            first_seen_at=fresh_iso,
        ),
        _insight(
            id="old",
            body="Use yaml frontmatter for vault snapshots",
            first_seen_at=old_iso,
        ),
        _insight(
            id="dup",
            body="Atomic writes need PID plus random suffix for tmp",
            first_seen_at=near_dup_iso,
        ),
    ]


class _ScriptedClient:
    """LLMJsonClient stub that returns scripted responses in order."""

    def __init__(self, responses: List[Optional[Union[dict, list]]]):
        self._responses = list(responses)
        self.calls: int = 0

    def complete_json(self, **kwargs: Any) -> Optional[Union[dict, list]]:
        self.calls += 1
        if not self._responses:
            return None
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# 1. compute_decay_score
# ---------------------------------------------------------------------------


def test_decay_score_ranks_fresh_above_old(now: datetime, three_insights):
    fresh, old, dup = three_insights
    s_fresh = compute_decay_score(fresh, now)
    s_old = compute_decay_score(old, now)
    s_dup = compute_decay_score(dup, now)

    assert 0.0 <= s_old < 0.1, "60-day-old finding should be heavily decayed"
    assert s_fresh > s_old
    assert s_dup > s_old
    # Fresh and "1 hour old" are within rounding of 1.0.
    assert pytest.approx(s_fresh, abs=1e-3) == 1.0
    assert s_dup > 0.99


def test_decay_score_access_bump_clamped(now: datetime):
    base = _insight(
        id="base",
        body="X",
        first_seen_at=(now - timedelta(days=14)).isoformat(),
    )
    base_score = compute_decay_score(base, now)
    # 14 days = exactly one half-life → ~0.5.
    assert pytest.approx(base_score, abs=1e-3) == 0.5

    bumped = _insight(
        id="bumped",
        body="X",
        first_seen_at=(now - timedelta(days=14)).isoformat(),
        access_count=10,
    )
    bumped_score = compute_decay_score(bumped, now)
    # 0.5 + 0.1*10 = 1.5 → clamped to 1.0.
    assert bumped_score == 1.0


def test_decay_score_missing_metadata_returns_one(now: datetime):
    bare = ResearchNode(
        id="SessionInsight:bare",
        name="No metadata",
        type=ResearchNodeType.SESSION_INSIGHT,
    )
    assert compute_decay_score(bare, now) == 1.0


# ---------------------------------------------------------------------------
# 2. Similarity + supersede pass
# ---------------------------------------------------------------------------


def test_jaccard_token_set_similarity():
    a = "Atomic writes need a PID plus random tmp suffix"
    b = "Atomic writes need PID plus random suffix for tmp"
    c = "Use yaml frontmatter for vault snapshots"
    assert jaccard(a, b) > 0.55, "near-duplicates should clear the gate"
    assert jaccard(a, c) < 0.2, "unrelated insights should fall well below"


def test_supersede_pass_mints_edge_for_near_duplicate(
    tmp_path: Path, three_insights
):
    graph = ResearchGraph(nodes=list(three_insights), edges=[])
    cache_dir = tmp_path / "supersede_cache"

    # The "fresh" insight obsoletes the "dup" one (newer wording supersedes
    # the older near-duplicate). Returned by the LLM mock.
    fresh, _, dup = three_insights
    # The pass calls _ask_llm with the (lo, hi) pair where the smaller
    # id sorts first. SessionInsight:dup < SessionInsight:fresh, so
    # `a` = dup, `b` = fresh. To say "fresh obsoletes dup" we return
    # "b_obsoletes_a".
    assert dup.id < fresh.id
    client = _ScriptedClient([
        {"verdict": "b_obsoletes_a", "rationale": "Same idea, sharper wording."}
    ])

    out = run_supersede_pass(graph, json_client=client, cache_dir=cache_dir)
    assert client.calls == 1, "exactly one candidate pair should reach the LLM"

    supersede_edges = [e for e in out.edges if e.type == SUPERSEDE_EDGE]
    assert len(supersede_edges) == 1
    edge = supersede_edges[0]
    assert edge.source == fresh.id, "newer finding should be the edge source"
    assert edge.target == dup.id, "older finding should be the edge target"
    assert edge.metadata.get("kind") == "SessionInsight"
    assert edge.evidence == "Same idea, sharper wording."

    # Cache file was written, so a second pass with no new LLM responses
    # still produces the same outcome (verdict comes from disk).
    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1
    client_two = _ScriptedClient([])
    graph_two = ResearchGraph(nodes=list(three_insights), edges=[])
    run_supersede_pass(graph_two, json_client=client_two, cache_dir=cache_dir)
    assert client_two.calls == 0, "cached verdict must skip the LLM"
    assert [e.type for e in graph_two.edges].count(SUPERSEDE_EDGE) == 1


def test_supersede_pass_no_client_is_no_op(tmp_path: Path, three_insights):
    graph = ResearchGraph(nodes=list(three_insights), edges=[])
    out = run_supersede_pass(
        graph, json_client=None, cache_dir=tmp_path / "cache"
    )
    assert out.edges == [], "no LLM client → no edges minted"


def test_supersede_pass_distinct_verdict_skips_edge(
    tmp_path: Path, three_insights
):
    graph = ResearchGraph(nodes=list(three_insights), edges=[])
    client = _ScriptedClient([{"verdict": "distinct", "rationale": "diff."}])
    out = run_supersede_pass(
        graph, json_client=client, cache_dir=tmp_path / "cache"
    )
    assert client.calls == 1
    assert [e for e in out.edges if e.type == SUPERSEDE_EDGE] == []


def test_supersede_env_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TESSERAE_SUPERSEDE_PASS", raising=False)
    assert not supersede_pass_enabled()
    monkeypatch.setenv("TESSERAE_SUPERSEDE_PASS", "true")
    assert supersede_pass_enabled()
    monkeypatch.setenv("TESSERAE_SUPERSEDE_PASS", "0")
    assert not supersede_pass_enabled()


# ---------------------------------------------------------------------------
# 3. fresh_insights MCP tool
# ---------------------------------------------------------------------------


def test_fresh_insights_excludes_superseded(tmp_path: Path, three_insights):
    from tesserae.mcp_server import LLMWikiMCPServer

    # Hand-mint the supersede edge so this test doesn't depend on the
    # LLM pass. fresh > dup canonical orientation.
    fresh, old, dup = three_insights
    graph = ResearchGraph(
        nodes=list(three_insights),
        edges=[
            ResearchEdge(
                source=fresh.id,
                target=dup.id,
                type="supersedes",
                metadata={"kind": "SessionInsight"},
            )
        ],
    )

    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph.to_json(), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    payload = server.call_tool("fresh_insights", {"limit": 10})
    bodies = [f["body"] for f in payload["findings"]]

    # The duplicate is the target of a supersedes edge — filtered out.
    assert dup.name not in bodies
    # Fresh and old remain; fresh ranks first by decay score.
    assert bodies[0] == fresh.name
    assert old.name in bodies
    # Decay scores are descending.
    scores = [f["decay_score"] for f in payload["findings"]]
    assert scores == sorted(scores, reverse=True)


def test_structural_decisions_inherit_session_timestamps(now: datetime):
    """Structural SessionDecisions must NOT score as freshly minted.

    Regression for codex P2 on PR #6: the structural extractor used to
    leave ``first_seen_at`` / ``last_accessed_at`` unset, so
    ``compute_decay_score`` fell back to 1.0 and old decisions from
    30-day-old sessions crowded out fresh LLM-extracted findings.
    """
    from tesserae.harness_sessions import HarnessSession
    from tesserae.session_graph_path_index import DocPathIndex
    from tesserae.session_graph_structural import extract_structural

    started = (now - timedelta(days=30)).isoformat()
    project_root = "/tmp/decay-fixture-project"
    session = HarnessSession(
        id="sess-old",
        harness="claude",
        agent_label="claude",
        slug="sess-old",
        project_name="decay-fixture",
        project_root=project_root,
        started_at=started,
        ended_at=started,
        title="old session",
        decisions=("Adopt the 14-day half-life",),
    )

    graph = extract_structural(
        sessions=[session],
        path_index=DocPathIndex(project_root=Path(project_root)),
        project_root=project_root,
    )

    decisions = [
        n for n in graph.nodes if n.type == ResearchNodeType.SESSION_DECISION
    ]
    assert len(decisions) == 1
    meta = decisions[0].metadata or {}
    assert meta.get("first_seen_at") == started
    assert meta.get("last_accessed_at") == started

    score = compute_decay_score(decisions[0], now)
    # 30 days at a 14-day half-life ≈ 0.226 — explicitly NOT 1.0.
    assert score < 0.5
    assert score > 0.1


def test_fresh_insights_ranks_structural_decision_by_age(
    tmp_path: Path, now: datetime
):
    """End-to-end: fresh insight > 30-day-old structural decision > stale insight.

    Goes through ``extract_structural`` to verify the timestamp-stamping
    fix flows into the MCP ``fresh_insights`` ranking.
    """
    from tesserae.harness_sessions import HarnessSession
    from tesserae.mcp_server import LLMWikiMCPServer
    from tesserae.session_graph_path_index import DocPathIndex
    from tesserae.session_graph_structural import extract_structural

    started = (now - timedelta(days=30)).isoformat()
    project_root = "/tmp/decay-fresh-ranking"
    session = HarnessSession(
        id="sess-structural",
        harness="claude",
        agent_label="claude",
        slug="sess-structural",
        project_name="decay-fresh",
        project_root=project_root,
        started_at=started,
        ended_at=started,
        title="structural session",
        decisions=("Use PID+random tmp suffix for atomic writes",),
    )

    structural_graph = extract_structural(
        sessions=[session],
        path_index=DocPathIndex(project_root=Path(project_root)),
        project_root=project_root,
    )

    fresh_insight = _insight(
        id="fresh-llm",
        body="Wrap session-graph cache writes in flock",
        first_seen_at=now.isoformat(),
    )
    stale_insight = _insight(
        id="stale-llm",
        body="Old guidance about manifest writes",
        first_seen_at=(now - timedelta(days=60)).isoformat(),
    )

    nodes = list(structural_graph.nodes) + [fresh_insight, stale_insight]
    graph = ResearchGraph(nodes=nodes, edges=list(structural_graph.edges))

    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph.to_json(), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    payload = server.call_tool("fresh_insights", {"limit": 3})
    findings = payload["findings"]
    bodies = [f["body"] for f in findings]
    scores = [f["decay_score"] for f in findings]

    # Ranking: fresh insight first, structural decision middle, stale last.
    assert bodies[0] == fresh_insight.name
    assert bodies[1] == "Use PID+random tmp suffix for atomic writes"
    assert bodies[2] == stale_insight.name

    # The structural decision must NOT be ranked as 1.0/fresh.
    structural_score = next(
        f["decay_score"] for f in findings
        if f["kind"] == "SessionDecision"
    )
    assert structural_score < 0.5
    assert structural_score > 0.1

    # Sanity: scores strictly descending.
    assert scores == sorted(scores, reverse=True)


def test_fresh_insights_kind_filter(tmp_path: Path, three_insights):
    from tesserae.mcp_server import LLMWikiMCPServer

    # Add a non-insight finding so the kind filter has something to skip.
    decision = ResearchNode(
        id="SessionDecision:d1",
        name="Adopt half-life of 14 days",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "sess-1"},
    )
    graph = ResearchGraph(nodes=[*three_insights, decision], edges=[])
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph.to_json(), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    payload = server.call_tool("fresh_insights", {"kind": "decision"})
    assert [f["kind"] for f in payload["findings"]] == ["SessionDecision"]
    assert payload["findings"][0]["body"] == decision.name
