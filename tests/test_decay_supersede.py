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
    SupersedeJudgement,
    _deterministic_verdict,
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


def test_supersede_warm_cache_hits_under_reminted_node_ids(
    tmp_path: Path, three_insights
):
    """codex MAJOR 1: the supersede warm cache is CONTENT-keyed, so the same
    finding content reminted under DIFFERENT node ids hits the cache with
    ZERO LLM calls and yields the same edge orientation (newer supersedes
    older near-duplicate)."""
    cache_dir = tmp_path / "supersede_cache"
    fresh, _old, dup = three_insights

    # Cold run with the original ids. dup.id < fresh.id, so the pass passes
    # (a=dup, b=fresh); "fresh obsoletes dup" => verdict "b_obsoletes_a".
    assert dup.id < fresh.id
    cold = _ScriptedClient([{"verdict": "b_obsoletes_a", "rationale": "sharper."}])
    graph = ResearchGraph(nodes=list(three_insights), edges=[])
    out = run_supersede_pass(graph, json_client=cold, cache_dir=cache_dir)
    assert cold.calls == 1
    e1 = [e for e in out.edges if e.type == SUPERSEDE_EDGE][0]
    assert (e1.source, e1.target) == (fresh.id, dup.id)

    # Remint: SAME bodies, DIFFERENT ids. Keep the same first_seen_at so the
    # content (name/description) is byte-identical to the cold run.
    re_fresh = ResearchNode(
        id="SessionInsight:zzz-fresh-renamed",
        name=fresh.name,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata=dict(fresh.metadata or {}),
    )
    re_dup = ResearchNode(
        id="SessionInsight:aaa-dup-renamed",
        name=dup.name,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata=dict(dup.metadata or {}),
    )
    warm = _ScriptedClient([])  # would yield None if the LLM were hit
    graph2 = ResearchGraph(nodes=[re_fresh, re_dup], edges=[])
    out2 = run_supersede_pass(graph2, json_client=warm, cache_dir=cache_dir)
    assert warm.calls == 0, "reminted ids must still hit the warm cache"
    edges2 = [e for e in out2.edges if e.type == SUPERSEDE_EDGE]
    assert len(edges2) == 1
    # Orientation tracks CONTENT: the "fresh" body supersedes the "dup" body,
    # regardless of which renamed id sorts first.
    assert edges2[0].source == re_fresh.id
    assert edges2[0].target == re_dup.id


def test_supersede_pass_no_client_mints_deterministic_edges(
    tmp_path: Path, three_insights
):
    """KB-03 default-on: with no LLM client the pass STILL mints a
    deterministic supersedes edge for the near-dup pair (inverted from the
    old no-op assertion)."""
    graph = ResearchGraph(nodes=list(three_insights), edges=[])
    out = run_supersede_pass(
        graph, json_client=None, cache_dir=tmp_path / "cache"
    )
    sup = [e for e in out.edges if e.type == SUPERSEDE_EDGE]
    assert sup, "no client → deterministic supersedes edge minted"
    # No cache file should be written on the deterministic (clientless) path.
    assert not list((tmp_path / "cache").glob("*.json"))


def test_deterministic_verdict_is_pure_and_stable():
    """``_deterministic_verdict`` is a pure function: identical output across
    calls, and the three documented rule branches resolve as specified."""
    def _node(id_: str, name: str, sid: str = "") -> ResearchNode:
        meta = {"session_id": sid} if sid else {}
        return ResearchNode(
            id=id_, name=name, type=ResearchNodeType.SESSION_INSIGHT, metadata=meta
        )

    # Branch 1: both session ids set, sid_b > sid_a → b_obsoletes_a.
    a1 = _node("SessionInsight:a", "Same short name", sid="sess-1")
    b1 = _node("SessionInsight:b", "Same short name", sid="sess-2")
    v1 = _deterministic_verdict(a1, b1)
    v1_again = _deterministic_verdict(a1, b1)
    assert isinstance(v1, SupersedeJudgement)
    assert (v1.verdict, v1.rationale) == (v1_again.verdict, v1_again.rationale)
    assert v1.verdict == "b_obsoletes_a"

    # Branch 2: equal sids, len(b.name) > len(a.name)*1.1 → b_obsoletes_a.
    a2 = _node("SessionInsight:a", "short", sid="s")
    b2 = _node("SessionInsight:b", "a much longer and more specific name", sid="s")
    assert _deterministic_verdict(a2, b2).verdict == "b_obsoletes_a"

    # Branch 3: equal sids, equal-ish names → a_obsoletes_b (stable fallback).
    a3 = _node("SessionInsight:a", "roughly the same length name", sid="s")
    b3 = _node("SessionInsight:b", "roughly the same length namer", sid="s")
    assert _deterministic_verdict(a3, b3).verdict == "a_obsoletes_b"

    # Codex blocker regression: session id is DECISIVE in BOTH directions. When
    # ``a`` is the NEWER session (sid_a > sid_b) but ``b`` has the longer name,
    # the newer finding (a) must obsolete the older (b) — session id must NOT
    # fall through to name length and let the OLDER finding win (which would
    # suppress the current finding under default-on supersede).
    a4 = _node("SessionInsight:a", "short", sid="sess-9")  # newer session
    b4 = _node("SessionInsight:b", "a much longer older-session name", sid="sess-1")
    assert _deterministic_verdict(a4, b4).verdict == "a_obsoletes_b"


def test_supersede_default_path_byte_idempotent(tmp_path: Path, three_insights):
    """Two clientless runs over identical graphs mint an identical sorted set
    of (source, type, target) supersedes tuples — content-derived, byte-idempotent."""
    def _run() -> set:
        graph = ResearchGraph(nodes=list(three_insights), edges=[])
        out = run_supersede_pass(
            graph, json_client=None, cache_dir=tmp_path / "cache"
        )
        return {
            (e.source, e.type, e.target)
            for e in out.edges
            if e.type == SUPERSEDE_EDGE
        }

    first = _run()
    second = _run()
    assert first, "deterministic path must mint at least one edge"
    assert first == second, "supersedes edge set must be byte-identical across runs"


def test_supersede_llm_verdict_overrides_deterministic(
    tmp_path: Path, three_insights
):
    """When a client is present its valid verdict OVERRIDES the deterministic
    fallback. The deterministic rule picks dup->fresh (a_obsoletes_b, a=dup);
    the LLM returns the OPPOSITE (b_obsoletes_a → fresh supersedes dup)."""
    fresh, _old, dup = three_insights
    assert dup.id < fresh.id  # a=dup, b=fresh in _candidate_pairs

    # Sanity: deterministic verdict would be a_obsoletes_b (dup supersedes fresh).
    assert _deterministic_verdict(dup, fresh).verdict == "a_obsoletes_b"

    graph = ResearchGraph(nodes=list(three_insights), edges=[])
    client = _ScriptedClient(
        [{"verdict": "b_obsoletes_a", "rationale": "LLM says fresh wins."}]
    )
    out = run_supersede_pass(
        graph, json_client=client, cache_dir=tmp_path / "cache"
    )
    assert client.calls == 1
    sup = [e for e in out.edges if e.type == SUPERSEDE_EDGE]
    assert len(sup) == 1
    # LLM verdict (fresh supersedes dup) overrides deterministic (dup wins).
    assert sup[0].source == fresh.id
    assert sup[0].target == dup.id


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
    # KB-03: the pass is DEFAULT-ON. Unset env => enabled.
    monkeypatch.delenv("TESSERAE_SUPERSEDE_PASS", raising=False)
    assert supersede_pass_enabled()
    # Explicit truthy keeps it enabled.
    monkeypatch.setenv("TESSERAE_SUPERSEDE_PASS", "true")
    assert supersede_pass_enabled()
    # Opt-OUT: the falsy spellings disable it.
    for falsy in ("0", "false", "no", "off"):
        monkeypatch.setenv("TESSERAE_SUPERSEDE_PASS", falsy)
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
    # Only the DETERMINISTIC decay anchor (``first_seen_at``, derived from the
    # session's own ``started_at``) lives on the graph node. Mutable memory
    # state (``last_accessed_at`` / ``access_count``) is sidecar-only and must
    # NOT be stamped onto node.metadata (Phase-5 byte-idempotence BLOCKER), so
    # decay backdates from ``first_seen_at`` alone here.
    assert meta.get("first_seen_at") == started
    assert "last_accessed_at" not in meta
    assert "access_count" not in meta

    score = compute_decay_score(decisions[0], now)
    # 30 days at a 14-day half-life ≈ 0.226 — explicitly NOT 1.0.
    assert score < 0.5
    assert score > 0.1


def test_fresh_insights_ranks_structural_decision_by_age(tmp_path: Path):
    """End-to-end: fresh insight > 30-day-old structural decision > stale insight.

    Goes through ``extract_structural`` to verify the timestamp-stamping
    fix flows into the MCP ``fresh_insights`` ranking.

    Anchors on REAL ``datetime.now`` — NOT the pinned ``now`` fixture — because
    the MCP ``fresh_insights`` path computes decay against the wall clock with
    no injectable ``now``. With the pinned fixture the "30-day-old" decision
    silently aged in real time until its score crossed the 0.1 floor
    (0.5^(47/14) ≈ 0.099 on 2026-06-07) and the test went red on a calendar
    boundary. Real-now anchoring keeps the decision exactly 30 days old at
    evaluation time (score ≈ 0.226) forever.
    """
    from tesserae.harness_sessions import HarnessSession
    from tesserae.mcp_server import LLMWikiMCPServer
    from tesserae.session_graph_path_index import DocPathIndex
    from tesserae.session_graph_structural import extract_structural

    now = datetime.now(timezone.utc)

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


def test_decay_anchors_on_session_started_at():
    """A session node carries its date in started_at (no first_seen_at). Decay must
    use it, not treat the node as freshly minted (1.0) — the reviewer's guard."""
    from datetime import datetime, timezone

    from tesserae.memory.decay import compute_decay_score

    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    node = {"metadata": {"started_at": "2026-05-18T14:23:04Z"}}  # ~25d old, no first_seen_at
    score = compute_decay_score(node, now)
    assert score < 1.0  # decayed, not brand-new
