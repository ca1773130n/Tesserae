"""Tests for the Phase-5 per-agent extraction-guidance stream (2026-07-19
layered-agent-kg §12).

The stream lives beside the project's ``extraction-guidance.md`` as a per-agent
sibling and refines it (project-then-agent concatenation). Threaded into the
distill pass it forks the cluster cache (positive + negative envelopes) and the
per-agent watermark via ``guidance_digest`` so a guidance edit re-distills ONLY
the agent whose combined text changed, leaving every other agent's cache hits
intact. The summarizer is ALWAYS a deterministic stub — no live LLM anywhere
(byte-parity depends on it).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, List, Optional

import pytest

from tesserae.agent_distill import (
    DistillOptions,
    DistillRequest,
    agent_artifact_path,
    distill_agent,
    distill_cache_dir,
)
from tesserae.extraction_guidance import (
    agent_guidance_path,
    project_guidance_path,
    resolve_agent_guidance,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)

AGENT = "claude-code:me:reviewer"
AGENT_ID = stable_id("Agent", f"agent:{AGENT}")
OTHER = "codex:you:builder"
OTHER_ID = stable_id("Agent", f"agent:{OTHER}")


# --------------------------------------------------------------------------- fixtures


def _session(sid: str, started: str, ended: str, title: str) -> ResearchNode:
    return ResearchNode(
        id=f"Session:{sid}",
        name=title,
        type=ResearchNodeType.SESSION,
        metadata={
            "session_id": sid,
            "agent_label": "Claude Code",
            "started_at": started,
            "ended_at": ended,
        },
    )


def _finding(fid: str, name: str, session: ResearchNode, first_seen: str) -> ResearchNode:
    return ResearchNode(
        id=fid,
        name=name,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={
            "session_id": session.metadata["session_id"],
            "content_hash": f"ch-{fid}",
            "first_seen_at": first_seen,
        },
    )


def _agent_node(agent_key: str, node_id: str) -> ResearchNode:
    harness, account, role = agent_key.split(":")
    return ResearchNode(
        id=node_id,
        name=agent_key,
        type=ResearchNodeType.AGENT,
        metadata={
            "agent_key": agent_key,
            "harness": harness,
            "account": account,
            "role": role,
            "label": agent_key,
        },
    )


def _two_agent_graph() -> ResearchGraph:
    """Two agents, each owning a two-finding near-dup cluster (LLM-eligible),
    with disjoint sessions so their lineage keys — and cache files — never
    overlap."""
    sa = _session("sa", "2026-06-20T10:00:00Z", "2026-06-20T11:00:00Z", "reviewer work")
    sb = _session("sb", "2026-07-01T09:00:00Z", "2026-07-01T10:00:00Z", "builder work")

    fa1 = _finding("SessionInsight:fa1", "Release flow needs staging deploy verification", sa, "2026-06-20T10:00:00Z")
    fa2 = _finding("SessionInsight:fa2", "Release flow needs staging deploy verification pass", sa, "2026-06-20T10:30:00Z")
    fb1 = _finding("SessionInsight:fb1", "Graphql resolver caching avoids timeout", sb, "2026-07-01T09:00:00Z")
    fb2 = _finding("SessionInsight:fb2", "Graphql resolver caching avoids timeouts", sb, "2026-07-01T09:30:00Z")

    nodes = [
        _agent_node(AGENT, AGENT_ID),
        _agent_node(OTHER, OTHER_ID),
        sa, sb, fa1, fa2, fb1, fb2,
    ]
    edges = [
        ResearchEdge(source=sa.id, target=AGENT_ID, type="performed_by"),
        ResearchEdge(source=sb.id, target=OTHER_ID, type="performed_by"),
        ResearchEdge(source=fa1.id, target=sa.id, type="derived_from_session"),
        ResearchEdge(source=fa2.id, target=sa.id, type="derived_from_session"),
        ResearchEdge(source=fb1.id, target=sb.id, type="derived_from_session"),
        ResearchEdge(source=fb2.id, target=sb.id, type="derived_from_session"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


class StubSummarizer:
    """Deterministic injected summarizer with a call log (no LLM anywhere)."""

    def __init__(self, fn: Optional[Callable[[DistillRequest], Optional[dict]]] = None) -> None:
        self.calls: List[DistillRequest] = []
        self._fn = fn if fn is not None else self._default

    @staticmethod
    def _default(request: DistillRequest) -> dict:
        ids = [member[0] for member in request.members]
        names = "; ".join(member[1] for member in request.members)
        return {
            "kind": "runbook",
            "title": f"Runbook over {len(ids)} findings",
            "body": f"Steps distilled from: {names}",
            "citations": ids,
        }

    def __call__(self, request: DistillRequest) -> Optional[dict]:
        self.calls.append(request)
        return self._fn(request)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    return project


def _write_guidance(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_guidance_digests(project: Path) -> List[str]:
    digests: List[str] = []
    for path in sorted(distill_cache_dir(project).rglob("*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        gd = entry.get("guidance_digest")
        if gd:
            digests.append(str(gd))
    return digests


# --------------------------------------------------------------------------- resolution


def test_resolve_agent_guidance_combines_project_then_agent(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_guidance(project_guidance_path(project), "PROJECT: prefer runbooks")
    _write_guidance(agent_guidance_path(project, AGENT), "AGENT: cite the rollback step")

    combined = resolve_agent_guidance(project, AGENT)

    # Project stream first, agent stream appended (refines, never replaces).
    assert combined == "PROJECT: prefer runbooks\n\nAGENT: cite the rollback step"


def test_resolve_agent_guidance_empty_when_no_files(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert resolve_agent_guidance(project, AGENT) == ""


def test_resolve_agent_guidance_agent_only(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_guidance(agent_guidance_path(project, AGENT), "AGENT: solo stream")
    assert resolve_agent_guidance(project, AGENT) == "AGENT: solo stream"


def test_agent_and_project_streams_reach_the_summarizer(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_guidance(project_guidance_path(project), "PROJECT: be terse")
    _write_guidance(agent_guidance_path(project, AGENT), "AGENT: name the deploy target")
    summ = StubSummarizer()

    distill_agent(_two_agent_graph(), AGENT, project_root=project, summarizer=summ)

    assert summ.calls, "the near-dup cluster must reach the summarizer"
    seen = summ.calls[0].guidance
    assert "PROJECT: be terse" in seen
    assert "AGENT: name the deploy target" in seen


# --------------------------------------------------------------------------- cache fork


def test_cluster_cache_envelope_records_combined_guidance_digest(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_guidance(project_guidance_path(project), "PROJECT: prefer runbooks")
    _write_guidance(agent_guidance_path(project, AGENT), "AGENT: cite rollback")

    distill_agent(_two_agent_graph(), AGENT, project_root=project, summarizer=StubSummarizer())

    expected = _sha(resolve_agent_guidance(project, AGENT))
    digests = _cache_guidance_digests(project)
    assert digests, "the LLM cluster must have written a cache envelope"
    assert set(digests) == {expected}


def test_guidance_edit_forks_cache_for_that_agent_only(tmp_path: Path) -> None:
    graph = _two_agent_graph()
    project = _project(tmp_path)
    _write_guidance(agent_guidance_path(project, AGENT), "AGENT-A guidance v1")
    _write_guidance(agent_guidance_path(project, OTHER), "AGENT-B guidance")

    # Cold: both agents pay the summarizer once.
    a1 = distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())
    b1 = distill_agent(graph, OTHER, project_root=project, summarizer=StubSummarizer())
    assert a1.llm_calls > 0 and b1.llm_calls > 0

    # B, guidance unchanged, warms straight off its cache (no summarizer spend).
    b_warm = distill_agent(
        graph, OTHER, project_root=project, summarizer=StubSummarizer(),
        options=DistillOptions(full=True),
    )
    assert b_warm.llm_calls == 0 and b_warm.llm_cache_hits > 0

    # Edit ONLY agent A's stream.
    _write_guidance(agent_guidance_path(project, AGENT), "AGENT-A guidance v2 — much stricter")

    # A's guidance_digest changed → its cluster cache forks → it re-summarizes.
    # (recheck bypasses the cold-parity artifact replay so the fork is observable.)
    a2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=StubSummarizer(),
        options=DistillOptions(recheck=True),
    )
    assert a2.llm_calls > 0, "A's guidance edit must fork its cluster cache"

    # B is untouched by A's edit: still a pure cache hit, zero summarizer spend.
    b_after = distill_agent(
        graph, OTHER, project_root=project, summarizer=StubSummarizer(),
        options=DistillOptions(full=True),
    )
    assert b_after.llm_calls == 0 and b_after.llm_cache_hits > 0


def test_guidance_edit_busts_the_watermark(tmp_path: Path) -> None:
    graph = _two_agent_graph()
    project = _project(tmp_path)
    _write_guidance(agent_guidance_path(project, AGENT), "guidance v1")
    distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())

    # Same guidance → the watermark skips the whole pass.
    skipped = distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())
    assert skipped.status == "skipped-watermark"

    # Editing the stream folds a new digest into the watermark → NOT skipped.
    _write_guidance(agent_guidance_path(project, AGENT), "guidance v2")
    reran = distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())
    assert reran.status != "skipped-watermark"


# --------------------------------------------------------------------------- idempotence


def test_unchanged_guidance_is_byte_idempotent(tmp_path: Path) -> None:
    graph = _two_agent_graph()
    project = _project(tmp_path)
    _write_guidance(agent_guidance_path(project, AGENT), "AGENT: stable guidance")

    distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())
    first = agent_artifact_path(project, AGENT).read_bytes()

    # A forced re-run over the SAME guidance reproduces the artifact byte-for-byte.
    distill_agent(
        graph, AGENT, project_root=project, summarizer=StubSummarizer(),
        options=DistillOptions(full=True),
    )
    assert agent_artifact_path(project, AGENT).read_bytes() == first


def test_no_guidance_leaves_options_and_bytes_unchanged(tmp_path: Path) -> None:
    """Absent streams must not perturb the pre-Phase-5 artifact bytes."""
    graph = _two_agent_graph()
    project_a = _project(tmp_path / "a")
    project_b = _project(tmp_path / "b")

    distill_agent(graph, AGENT, project_root=project_a, summarizer=StubSummarizer())
    distill_agent(graph, AGENT, project_root=project_b, summarizer=StubSummarizer())

    # Two guidance-free projects distill the same agent to identical bytes.
    assert (
        agent_artifact_path(project_a, AGENT).read_bytes()
        == agent_artifact_path(project_b, AGENT).read_bytes()
    )


# --------------------------------------------------------------------------- negative cache


def test_guidance_fork_reopens_the_negative_cache(tmp_path: Path) -> None:
    """A fallback recorded under old guidance must not suppress a retry under a
    new stream — the negative-cache/backoff key forks on guidance_digest too."""
    graph = _two_agent_graph()
    project = _project(tmp_path)
    _write_guidance(agent_guidance_path(project, AGENT), "guidance v1")

    # A dead provider records a fallback verdict + a backoff window under v1.
    failing = StubSummarizer(lambda req: None)
    r1 = distill_agent(graph, AGENT, project_root=project, summarizer=failing)
    assert r1.llm_failed > 0 and r1.llm_fallbacks > 0

    # New guidance within the still-open backoff window: the fork must reopen
    # the retry path instead of replaying the cached fallback.
    _write_guidance(agent_guidance_path(project, AGENT), "guidance v2 — retry me")
    recovered = StubSummarizer()
    r2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=recovered,
        options=DistillOptions(full=True),
    )
    assert recovered.calls, "guidance fork must reopen the backed-off cluster"
    payload = json.loads(agent_artifact_path(project, AGENT).read_text(encoding="utf-8"))
    qualities = {
        node["metadata"]["distill_quality"]
        for node in payload["nodes"]
        if node["type"] == "DistilledNote" and node["metadata"].get("kind") == "runbook"
    }
    assert qualities == {"llm"}
