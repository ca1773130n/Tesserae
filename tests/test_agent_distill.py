"""Tests for the Phase-2 per-agent distill pass (2026-07-19 layered-agent-kg §5-§7).

The summarizer is ALWAYS a deterministic stub — no live LLM calls anywhere
(the byte-parity tests depend on it). Coverage per the Phase-2 work package:
closure determinism, clustering + the mandated tie-at-cutoff fixture,
lineage-key recursion stability, double-run byte equality (warm AND cold
cache; write-if-changed leaves mtime untouched), watermark skip + ``full``,
forget-ledger rows, lint-probe acceptance of minted metadata, and the L1
size lint. The WP-B section exercises the real :class:`LLMSummarizer` over a
stubbed ``LLMJsonClient`` — map-reduce chunking, fold, fallback caching +
``--retry-fallbacks``, negative-cache backoff, the circuit breaker, the
citation whitelist, and the provider-call budget. Distinct from
``tests/test_distillation.py`` (the Runbook/Gotcha pass over the compiled
project graph), which must stay untouched.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable, List, Optional

import pytest

from tesserae.agent_distill import (
    DistillError,
    DistillOptions,
    DistillRequest,
    DistillSizeError,
    DistillStateStore,
    LLMSummarizer,
    SummarizerTransportError,
    _raw_roots_for,
    _scope_for_agent,
    agent_artifact_path,
    build_llm_summarizer,
    compute_lineage_key,
    distill_agent,
    distill_all,
    distill_cache_dir,
    set_agent_distill_test_client,
)
from tesserae.llm_chunking import pack_blocks
from tesserae.lint import WikiLinter
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    graph_from_payload,
    stable_id,
)

AGENT = "claude-code:me:reviewer"
AGENT_ID = stable_id("Agent", f"agent:{AGENT}")
OTHER_AGENT = "codex:you:default"
OTHER_AGENT_ID = stable_id("Agent", f"agent:{OTHER_AGENT}")


# --------------------------------------------------------------------------- fixture helpers


def _session(sid: str, started: str, ended: str, title: str) -> ResearchNode:
    metadata = {"session_id": sid, "agent_label": "Claude Code"}
    if started:
        metadata["started_at"] = started
    if ended:
        metadata["ended_at"] = ended
    return ResearchNode(
        id=f"Session:{sid}", name=title, type=ResearchNodeType.SESSION, metadata=metadata
    )


def _finding(
    fid: str,
    name: str,
    session: ResearchNode,
    first_seen: str = "",
    kind: ResearchNodeType = ResearchNodeType.SESSION_INSIGHT,
) -> ResearchNode:
    metadata = {"session_id": session.metadata["session_id"], "content_hash": f"ch-{fid}"}
    if first_seen:
        metadata["first_seen_at"] = first_seen
    return ResearchNode(id=fid, name=name, type=kind, metadata=metadata)


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


def _base_graph() -> ResearchGraph:
    """Two sessions, a near-dup cluster, a singleton, an anchored Concept,
    an old absorbable pair, and a second agent that must stay out of scope."""
    s1 = _session("s1", "2026-06-20T10:00:00Z", "2026-06-20T11:00:00Z", "release work")
    s2 = _session("s2", "2026-07-01T09:00:00Z", "2026-07-01T10:00:00Z", "graphql work")
    s9 = _session("s9", "2026-07-02T09:00:00Z", "2026-07-02T10:00:00Z", "other agent work")

    f1 = _finding("SessionInsight:f1", "Release flow needs staging deploy verification", s1, "2026-06-20T10:00:00Z")
    f2 = _finding("SessionInsight:f2", "Release flow needs staging deploy verification step", s2, "2026-07-01T09:00:00Z")
    f3 = _finding("SessionInsight:f3", "Graphql resolver timeout root cause", s2, "2026-07-01T09:00:00Z")
    old1 = _finding("SessionInsight:old1", "Legacy rollback script juggling dance", s1, "2026-01-01T00:00:00Z")
    old2 = _finding("SessionInsight:old2", "Legacy rollback script juggling dances", s1, "2026-01-02T00:00:00Z")
    f9 = _finding("SessionInsight:f9", "Foreign agent finding", s9, "2026-07-02T09:00:00Z")

    concept = ResearchNode(
        id="Concept:staging-deploy:abc123",
        name="Staging Deploy",
        type=ResearchNodeType.CONCEPT,
    )

    nodes = [
        _agent_node(AGENT, AGENT_ID),
        _agent_node(OTHER_AGENT, OTHER_AGENT_ID),
        s1, s2, s9, f1, f2, f3, old1, old2, f9, concept,
    ]
    edges = [
        ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by"),
        ResearchEdge(source=s2.id, target=AGENT_ID, type="performed_by"),
        ResearchEdge(source=s9.id, target=OTHER_AGENT_ID, type="performed_by"),
        ResearchEdge(source=f1.id, target=s1.id, type="derived_from_session"),
        ResearchEdge(source=f2.id, target=s2.id, type="derived_from_session"),
        ResearchEdge(source=f3.id, target=s2.id, type="derived_from_session"),
        ResearchEdge(source=old1.id, target=s1.id, type="derived_from_session"),
        ResearchEdge(source=old2.id, target=s1.id, type="derived_from_session"),
        ResearchEdge(source=f9.id, target=s9.id, type="derived_from_session"),
        ResearchEdge(source=f1.id, target=concept.id, type="references"),
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


def _run(
    tmp_path: Path,
    graph: ResearchGraph,
    summarizer=None,
    options: Optional[DistillOptions] = None,
    agent: str = AGENT,
):
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return (
        distill_agent(
            graph,
            agent,
            project_root=project,
            summarizer=summarizer,
            options=options,
        ),
        project,
    )


def _artifact_payload(project: Path, agent: str = AGENT) -> dict:
    return json.loads(agent_artifact_path(project, agent).read_text(encoding="utf-8"))


def _notes_by_kind(payload: dict, kind: str) -> list:
    return [
        node
        for node in payload["nodes"]
        if node["type"] == "DistilledNote" and node["metadata"].get("kind") == kind
    ]


# --------------------------------------------------------------------------- scope closure (§5.1)


def test_scope_closure_is_deterministic_and_agent_scoped() -> None:
    graph = _base_graph()
    shuffled = ResearchGraph(
        nodes=list(reversed(graph.nodes)), edges=list(reversed(graph.edges))
    )

    sessions_a, findings_a, extras_a = _scope_for_agent(graph, AGENT)
    sessions_b, findings_b, extras_b = _scope_for_agent(shuffled, AGENT)

    assert [n.id for n in sessions_a] == [n.id for n in sessions_b] == ["Session:s1", "Session:s2"]
    assert [n.id for n in findings_a] == [n.id for n in findings_b]
    assert [n.id for n in extras_a] == [n.id for n in extras_b]
    # The other agent's session finding never enters scope.
    scope_ids = {n.id for n in [*sessions_a, *findings_a, *extras_a]}
    assert "SessionInsight:f9" not in scope_ids
    assert "Session:s9" not in scope_ids
    # The Concept is reachable within 2 hops via the allowlisted `references`.
    assert "Concept:staging-deploy:abc123" in {n.id for n in extras_a}


# --------------------------------------------------------------------------- lineage (§4)


def test_lineage_key_is_recursion_stable() -> None:
    flat = compute_lineage_key({"raw1": ["raw1"], "raw2": ["raw2"], "raw3": ["raw3"]})
    via_note = compute_lineage_key({"note1": ["raw1", "raw2"], "raw3": ["raw3"]})
    assert flat == via_note

    note = ResearchNode(
        id="DistilledNote:x",
        name="X",
        type=ResearchNodeType.DISTILLED_NOTE,
        metadata={"member_refs": [{"node_id": "raw1", "content_hash": "a"}, {"node_id": "raw2", "content_hash": "b"}]},
    )
    raw3 = ResearchNode(id="raw3", name="Y", type=ResearchNodeType.SESSION_INSIGHT)
    recursive = compute_lineage_key(
        {node.id: _raw_roots_for(node) for node in [note, raw3]}
    )
    assert recursive == flat
    # Order independence.
    assert flat == compute_lineage_key({"raw3": ["raw3"], "raw2": ["raw2"], "raw1": ["raw1"]})


# --------------------------------------------------------------------------- byte idempotence (§7.2)


def test_double_run_is_byte_idempotent_and_write_if_changed(tmp_path: Path) -> None:
    graph = _base_graph()
    summarizer = StubSummarizer()
    result1, project = _run(tmp_path, graph, summarizer)
    assert result1.status == "written"

    artifact = agent_artifact_path(project, AGENT)
    first_bytes = artifact.read_bytes()
    first_mtime = artifact.stat().st_mtime_ns

    # Second run bypasses the watermark but must not rewrite identical bytes.
    result2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=summarizer,
        options=DistillOptions(full=True),
    )
    assert result2.status == "unchanged"
    assert artifact.read_bytes() == first_bytes
    assert artifact.stat().st_mtime_ns == first_mtime

    # The artifact is a loadable, ordinary graph.json.
    loaded = graph_from_payload(json.loads(first_bytes))
    assert any(n.type is ResearchNodeType.DISTILLED_NOTE for n in loaded.nodes)


def test_cold_cache_parity_with_deterministic_stub(tmp_path: Path) -> None:
    graph = _base_graph()
    result1, project = _run(tmp_path, graph, StubSummarizer())
    assert result1.status == "written"
    first_bytes = agent_artifact_path(project, AGENT).read_bytes()

    shutil.rmtree(distill_cache_dir(project))
    result2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=StubSummarizer(),
        options=DistillOptions(full=True),
    )
    assert result2.status == "unchanged"
    assert agent_artifact_path(project, AGENT).read_bytes() == first_bytes


def test_no_wall_clock_leaks_into_artifact(tmp_path: Path) -> None:
    _result, project = _run(tmp_path, _base_graph(), StubSummarizer())
    payload = _artifact_payload(project)
    stamps = {
        node["metadata"].get("distilled_through")
        for node in payload["nodes"]
        if node["type"] in {"DistilledNote", "ExpertiseProfile"}
    }
    # Corpus clock = max session timestamp, never "now".
    assert stamps == {"2026-07-01T10:00:00Z"}


# --------------------------------------------------------------------------- clustering + summaries


def test_cluster_distillate_and_anchor_minting(tmp_path: Path) -> None:
    result, project = _run(tmp_path, _base_graph(), StubSummarizer())
    payload = _artifact_payload(project)

    runbooks = _notes_by_kind(payload, "runbook")
    assert len(runbooks) == 2  # f1+f2 cluster and old1+old2 cluster
    by_members = {
        tuple(sorted(ref["node_id"] for ref in note["metadata"]["member_refs"])): note
        for note in runbooks
    }
    fresh = by_members[("SessionInsight:f1", "SessionInsight:f2")]
    assert fresh["metadata"]["distill_quality"] == "llm"
    assert fresh["metadata"]["member_count"] == 2
    assert fresh["metadata"]["first_seen_at"] == "2026-06-20T10:00:00Z"
    assert result.cluster_count == 2

    # Anchor copied verbatim with its original L0 id + derived_from edge.
    anchor_ids = {n["id"] for n in payload["nodes"] if n["type"] == "Concept"}
    assert anchor_ids == {"Concept:staging-deploy:abc123"}
    assert any(
        e["source"] == fresh["id"] and e["target"] == "Concept:staging-deploy:abc123"
        and e["type"] == "derived_from"
        for e in payload["edges"]
    )


def test_remainder_tie_at_cutoff_uses_id_tiebreak(tmp_path: Path) -> None:
    # Three singleton findings, all confidence 0.0 — a guaranteed tie at the
    # K=2 cutoff. The mandated (-confidence, node_id) tiebreak admits the two
    # smallest ids; the third demotes to the Index note (never invisible).
    s1 = _session("s1", "2026-07-01T09:00:00Z", "2026-07-01T10:00:00Z", "one")
    findings = [
        _finding("SessionInsight:aa", "Alpha topic entirely unique", s1, "2026-07-01T09:00:00Z"),
        _finding("SessionInsight:bb", "Beta subject wholly distinct", s1, "2026-07-01T09:00:00Z"),
        _finding("SessionInsight:cc", "Gamma matter fully separate", s1, "2026-07-01T09:00:00Z"),
    ]
    nodes = [_agent_node(AGENT, AGENT_ID), s1, *findings]
    edges = [ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by")]
    edges += [
        ResearchEdge(source=f.id, target=s1.id, type="derived_from_session")
        for f in findings
    ]
    graph = ResearchGraph(nodes=nodes, edges=edges)

    result, project = _run(
        tmp_path, graph, None, DistillOptions(remainder_top_k=2)
    )
    assert result.status == "written"
    payload = _artifact_payload(project)
    remainder_ids = sorted(
        n["id"] for n in payload["nodes"] if n["type"] == "SessionInsight"
    )
    assert remainder_ids == ["SessionInsight:aa", "SessionInsight:bb"]
    index_note = _notes_by_kind(payload, "index")[0]
    assert [ref["node_id"] for ref in index_note["metadata"]["member_refs"]] == [
        "SessionInsight:cc"
    ]
    assert index_note["metadata"]["distill_quality"] == "structural"


# --------------------------------------------------------------------------- watermark (§7.2)


def test_new_supersedes_edge_defeats_watermark_and_cluster_memo(tmp_path: Path) -> None:
    # Scope-node (id, content_hash) pairs unchanged, but a NEW edge between
    # two in-scope findings changes what a fresh run renders: the watermark
    # must not skip (edges are part of input_hash) and the cluster-assignment
    # memo must not resurrect the stale pre-edge partition (edges are part of
    # the memo key).
    graph = _base_graph()
    summarizer = StubSummarizer()
    result1, project = _run(tmp_path, graph, summarizer)
    assert result1.status == "written"

    graph2 = ResearchGraph(
        nodes=list(graph.nodes),
        edges=[
            *graph.edges,
            ResearchEdge(
                source="SessionInsight:f3", target="SessionInsight:f1", type="supersedes"
            ),
        ],
    )
    result2 = distill_agent(
        graph2, AGENT, project_root=project, summarizer=summarizer
    )
    assert result2.status == "written"  # neither skipped-watermark nor unchanged
    merged = [
        note
        for note in _notes_by_kind(_artifact_payload(project), "runbook")
        if {ref["node_id"] for ref in note["metadata"]["member_refs"]}
        >= {"SessionInsight:f1", "SessionInsight:f2", "SessionInsight:f3"}
    ]
    assert merged  # the memoized split would have kept f3 a singleton


def test_watermark_skip_and_full_override(tmp_path: Path) -> None:
    graph = _base_graph()
    summarizer = StubSummarizer()
    result1, project = _run(tmp_path, graph, summarizer)
    assert result1.status == "written"
    calls_after_first = len(summarizer.calls)
    assert calls_after_first > 0

    result2 = distill_agent(graph, AGENT, project_root=project, summarizer=summarizer)
    assert result2.status == "skipped-watermark"
    assert len(summarizer.calls) == calls_after_first  # zero LLM work

    result3 = distill_agent(
        graph, AGENT, project_root=project, summarizer=summarizer,
        options=DistillOptions(full=True),
    )
    assert result3.status == "unchanged"  # cache hits, no new bytes
    assert len(summarizer.calls) == calls_after_first


# --------------------------------------------------------------------------- forgetting (§6)


def test_absorption_and_forget_ledger_rows(tmp_path: Path) -> None:
    graph = _base_graph()
    result, project = _run(tmp_path, graph, StubSummarizer())

    # The old near-dup pair decays below 0.2 at the corpus clock and is
    # absorbed by its llm-quality distillate — recorded, never deleted.
    assert result.absorbed_count == 2
    payload = _artifact_payload(project)
    absorbed = {
        ref["node_id"]
        for note in _notes_by_kind(payload, "runbook")
        for ref in note["metadata"]["absorbed_refs"]
    }
    assert absorbed == {"SessionInsight:old1", "SessionInsight:old2"}
    # Absorbed members are not full remainder nodes in the artifact.
    artifact_ids = {n["id"] for n in payload["nodes"]}
    assert "SessionInsight:old1" not in artifact_ids

    state = DistillStateStore(project / ".tesserae" / "sqlite.db")
    rows = state.rows(DistillStateStore.SCOPE_FORGET_LEDGER, AGENT)
    assert len(rows) == 1
    entry = json.loads(rows[0][3])
    assert sorted(entry["absorbed"]) == ["SessionInsight:old1", "SessionInsight:old2"]
    assert entry["distilled_through"] == "2026-07-01T10:00:00Z"
    assert set(entry["promoted"]) == {
        "SessionInsight:f1", "SessionInsight:f2", "SessionInsight:f3"
    }
    assert entry["demoted"] == []


def test_fallback_never_absorbs(tmp_path: Path) -> None:
    graph = _base_graph()
    result, project = _run(tmp_path, graph, StubSummarizer(lambda req: None))
    assert result.absorbed_count == 0
    payload = _artifact_payload(project)
    for note in _notes_by_kind(payload, "runbook") + _notes_by_kind(payload, "gotcha") + _notes_by_kind(payload, "note"):
        assert note["metadata"]["distill_quality"] == "fallback"
        assert note["metadata"]["absorbed_refs"] == []


def test_hysteresis_keeps_prior_remainder_then_demotes(tmp_path: Path) -> None:
    # Enter the remainder at decay >= 0.3, demote only below 0.15 (§6.2),
    # evaluated against the prior committed artifact as a declared input.
    s1 = _session("s1", "2026-06-01T00:00:00Z", "2026-06-01T01:00:00Z", "one")
    f1 = _finding("SessionInsight:hys", "Rare failure mode observation", s1, "2026-06-01T00:00:00Z")
    graph = ResearchGraph(
        nodes=[_agent_node(AGENT, AGENT_ID), s1, f1],
        edges=[
            ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by"),
            ResearchEdge(source=f1.id, target=s1.id, type="derived_from_session"),
        ],
    )
    project = tmp_path / "proj"
    project.mkdir()

    def _run_at(as_of: str):
        return distill_agent(
            graph, AGENT, project_root=project,
            options=DistillOptions(as_of=as_of, full=True),
        )

    # Fresh (decay ~0.82): enters the remainder.
    _run_at("2026-06-05T00:00:00Z")
    ids = {n["id"] for n in _artifact_payload(project)["nodes"]}
    assert "SessionInsight:hys" in ids

    # Aged to ~0.18 — below the 0.3 entry bar but above the 0.15 exit bar:
    # hysteresis keeps a prior-remainder node in place (no rank-51 churn).
    _run_at("2026-07-06T00:00:00Z")
    ids = {n["id"] for n in _artifact_payload(project)["nodes"]}
    assert "SessionInsight:hys" in ids

    # Aged below 0.15: demoted to the Index note — never invisible, and the
    # forget ledger records the demotion.
    _run_at("2026-08-01T00:00:00Z")
    payload = _artifact_payload(project)
    assert "SessionInsight:hys" not in {n["id"] for n in payload["nodes"]}
    index_note = _notes_by_kind(payload, "index")[0]
    assert [ref["node_id"] for ref in index_note["metadata"]["member_refs"]] == [
        "SessionInsight:hys"
    ]
    state = DistillStateStore(project / ".tesserae" / "sqlite.db")
    last_entry = json.loads(state.rows(DistillStateStore.SCOPE_FORGET_LEDGER, AGENT)[-1][3])
    assert last_entry["demoted"] == ["SessionInsight:hys"]


# --------------------------------------------------------------------------- LLM cache (§5.3)


def test_fallback_verdict_is_cached_until_retry_fallbacks(tmp_path: Path) -> None:
    graph = _base_graph()
    failing = StubSummarizer(lambda req: None)
    result1, project = _run(tmp_path, graph, failing)
    assert result1.llm_failed > 0
    assert result1.llm_fallbacks > 0

    # A recovered provider must NOT flip bytes: the fallback verdict is cached.
    recovered = StubSummarizer()
    result2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=recovered,
        options=DistillOptions(full=True),
    )
    assert result2.status == "unchanged"
    assert recovered.calls == []
    assert result2.llm_cache_hits > 0

    # --retry-fallbacks re-attempts and upgrades the notes to llm quality.
    result3 = distill_agent(
        graph, AGENT, project_root=project, summarizer=recovered,
        options=DistillOptions(full=True, retry_fallbacks=True),
    )
    assert result3.status == "written"
    assert len(recovered.calls) > 0
    payload = _artifact_payload(project)
    qualities = {
        note["metadata"]["distill_quality"] for note in _notes_by_kind(payload, "runbook")
    }
    assert qualities == {"llm"}


def test_citation_whitelist_and_faithfulness_rejection(tmp_path: Path) -> None:
    graph = _base_graph()

    fabricating = StubSummarizer(
        lambda req: {
            "kind": "runbook",
            "title": "Fabricated",
            "body": "Steps.",
            "citations": ["SessionInsight:not-a-member"],
        }
    )
    result1, project1 = _run(tmp_path, graph, fabricating)
    assert result1.llm_rejected == result1.cluster_count
    payload = _artifact_payload(project1)
    assert {
        n["metadata"]["distill_quality"] for n in _notes_by_kind(payload, "runbook")
    } == {"fallback"}

    unfaithful = StubSummarizer(
        lambda req: {
            "kind": "runbook",
            "title": "Unfaithful",
            "body": "Upgrade to `totally_invented_symbol` v99.99 first.",
            "citations": [req.members[0][0]],
        }
    )
    project2 = tmp_path / "proj2"
    project2.mkdir()
    result2 = distill_agent(
        graph, AGENT, project_root=project2, summarizer=unfaithful
    )
    assert result2.llm_rejected == result2.cluster_count


# --------------------------------------------------------------------------- corpus clock (§7.1)


def test_missing_timestamps_hard_fail_and_as_of_override(tmp_path: Path) -> None:
    s1 = _session("s1", "", "", "undated work")
    f1 = _finding("SessionInsight:f1", "Something unique happened", s1)
    graph = ResearchGraph(
        nodes=[_agent_node(AGENT, AGENT_ID), s1, f1],
        edges=[
            ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by"),
            ResearchEdge(source=f1.id, target=s1.id, type="derived_from_session"),
        ],
    )
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(DistillError, match="as_of"):
        distill_agent(graph, AGENT, project_root=project)

    result = distill_agent(
        graph, AGENT, project_root=project,
        options=DistillOptions(as_of="2026-07-10T00:00:00Z"),
    )
    assert result.status == "written"
    assert result.distilled_through == "2026-07-10T00:00:00Z"


def test_corpus_clock_orders_instants_not_strings(tmp_path: Path) -> None:
    # '...T10:00:00Z' > '...T10:00:00.500+00:00' lexicographically ('Z' >
    # '.'), but the offset-spelled session ends 500ms LATER — the corpus
    # clock and the Activity-note recency order must follow the instant.
    s1 = _session("s1", "2026-07-01T09:00:00Z", "2026-07-01T10:00:00Z", "zulu spelling")
    s2 = _session(
        "s2", "2026-07-01T09:30:00+00:00", "2026-07-01T10:00:00.500+00:00", "offset spelling"
    )
    f1 = _finding("SessionInsight:f1", "Something notable happened", s1, "2026-07-01T09:00:00Z")
    graph = ResearchGraph(
        nodes=[_agent_node(AGENT, AGENT_ID), s1, s2, f1],
        edges=[
            ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by"),
            ResearchEdge(source=s2.id, target=AGENT_ID, type="performed_by"),
            ResearchEdge(source=f1.id, target=s1.id, type="derived_from_session"),
        ],
    )
    result, project = _run(tmp_path, graph, None)
    assert result.status == "written"
    assert result.distilled_through == "2026-07-01T10:00:00.500+00:00"
    activity = _notes_by_kind(_artifact_payload(project), "activity")[0]
    lines = [line for line in activity["description"].splitlines() if line.startswith("- ")]
    assert lines[0].startswith("- 2026-07-01T10:00:00.500+00:00")


# --------------------------------------------------------------------------- lint probe (§7.2)


def test_minted_metadata_passes_the_phase1_lint_probe(tmp_path: Path) -> None:
    _result, project = _run(tmp_path, _base_graph(), StubSummarizer())
    payload = _artifact_payload(project)

    # Feed the artifact through the closed-allowlist probe as if it were a
    # project graph — every minted key must come from the §4 schemas.
    probe_project = tmp_path / "probe"
    (probe_project / ".tesserae").mkdir(parents=True)
    (probe_project / ".tesserae" / "graph.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    report = WikiLinter(probe_project).run()
    agent_findings = [f for f in report.findings if f.code == "AGENT_METADATA_KEY"]
    assert agent_findings == []


# --------------------------------------------------------------------------- size bound (§2)


def test_size_lint_fails_loud_when_over_budget(tmp_path: Path) -> None:
    graph = _base_graph()
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(DistillSizeError, match="one-read bound"):
        distill_agent(
            graph, AGENT, project_root=project, summarizer=StubSummarizer(),
            options=DistillOptions(artifact_char_budget=700),
        )
    # Fail-loud means no artifact was written.
    assert not agent_artifact_path(project, AGENT).exists()


def _bulk_graph() -> ResearchGraph:
    """One session + 40 unrelated singleton findings (index-pressure fixture)."""
    s1 = _session("s1", "2026-07-01T09:00:00Z", "2026-07-01T10:00:00Z", "bulk")
    findings = [
        _finding(
            f"SessionInsight:z{index:03d}",
            f"Distinct matter number{index} entirely standalone item{index}",
            s1,
            f"2026-06-{(index % 28) + 1:02d}T00:00:00Z",
        )
        for index in range(40)
    ]
    nodes = [_agent_node(AGENT, AGENT_ID), s1, *findings]
    edges = [ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by")]
    edges += [
        ResearchEdge(source=f.id, target=s1.id, type="derived_from_session")
        for f in findings
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_index_truncation_rolls_oldest_into_count_line(tmp_path: Path) -> None:
    # Many singleton findings + a small budget forces the Index note to shed
    # its oldest entries into the deterministic backlog count line.
    result, project = _run(
        tmp_path, _bulk_graph(), None,
        DistillOptions(remainder_top_k=2, artifact_char_budget=9000),
    )
    assert result.status == "written"
    assert result.artifact_chars <= 9000
    index_note = _notes_by_kind(_artifact_payload(project), "index")[0]
    assert "older undistilled findings" in index_note["description"]
    assert index_note["metadata"]["member_count"] < 38


def test_artifact_bytes_ignore_the_chunk_chars_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The §2 one-read bound is ARTIFACT_CHAR_BUDGET, a constant — the
    # TESSERAE_LLM_CHUNK_CHARS env var steers LLM chunk packing only and is
    # NOT a §7.2 declared input, so it must never move artifact bytes.
    from tesserae.agent_distill import ARTIFACT_CHAR_BUDGET

    assert ARTIFACT_CHAR_BUDGET == 48_000
    graph = _bulk_graph()
    monkeypatch.delenv("TESSERAE_LLM_CHUNK_CHARS", raising=False)
    result1, project1 = _run(tmp_path, graph, None)
    baseline = agent_artifact_path(project1, AGENT).read_bytes()
    assert result1.artifact_chars > 4000  # the env value below would bite

    monkeypatch.setenv("TESSERAE_LLM_CHUNK_CHARS", "4000")
    project2 = tmp_path / "envproj"
    project2.mkdir()
    result2 = distill_agent(graph, AGENT, project_root=project2)
    assert result2.status == "written"
    assert agent_artifact_path(project2, AGENT).read_bytes() == baseline


# --------------------------------------------------------------------------- enablement + dry run


def test_agent_distill_enabled_resolution() -> None:
    from tesserae.agent_distill import agent_distill_enabled

    assert agent_distill_enabled(env={}) is False
    assert agent_distill_enabled(env={"TESSERAE_AGENT_DISTILL": "1"}) is True
    assert agent_distill_enabled(env={"TESSERAE_AGENT_DISTILL": "false"}) is False
    assert agent_distill_enabled({"agent_distill": {"enabled": True}}, env={}) is True
    # The Runbook/Gotcha env var must NOT bleed into this pass.
    assert agent_distill_enabled(env={"TESSERAE_RUNBOOK_DISTILLATION": "1"}) is False


def test_no_backend_run_counts_fallbacks_not_estimates(tmp_path: Path) -> None:
    # §5.3 provider-health reporting: a REAL run without any backend is a
    # fallback run, not an estimate — estimated_llm_calls belongs to dry-run.
    result, project = _run(tmp_path, _base_graph(), None)
    assert result.status == "written"
    assert result.llm_fallbacks == result.cluster_count == 2
    assert result.estimated_llm_calls == 0
    assert result.llm_calls == 0
    payload = _artifact_payload(project)
    assert {
        note["metadata"]["distill_quality"] for note in _notes_by_kind(payload, "runbook")
    } == {"fallback"}


def test_dry_run_estimates_without_writing(tmp_path: Path) -> None:
    graph = _base_graph()
    summarizer = StubSummarizer()
    result, project = _run(
        tmp_path, graph, summarizer, DistillOptions(dry_run=True)
    )
    assert result.status == "dry-run"
    assert result.estimated_llm_calls == result.cluster_count == 2
    assert summarizer.calls == []
    assert not agent_artifact_path(project, AGENT).exists()
    state = DistillStateStore(project / ".tesserae" / "sqlite.db")
    assert state.rows(DistillStateStore.SCOPE_FORGET_LEDGER, AGENT) == []


# --------------------------------------------------------------------------- distill_all


def test_distill_all_covers_observed_agents(tmp_path: Path) -> None:
    graph = _base_graph()
    project = tmp_path / "proj"
    project.mkdir()
    results = distill_all(
        graph, project_root=project, summarizer=StubSummarizer()
    )
    assert [r.agent_key for r in results] == [AGENT, OTHER_AGENT]
    assert {r.status for r in results} == {"written"}
    assert agent_artifact_path(project, OTHER_AGENT).is_file()


# --------------------------------------------------------------------------- LLM layer (WP-B, §5.3/§7)


_VALID_IDS_RE = re.compile(r"(?m)^Valid citation ids: (.*)$")


def _prompt_ids(user: str) -> List[str]:
    """Citation whitelist the prompt itself declares (the stub's ground truth)."""
    match = _VALID_IDS_RE.search(user)
    if not match:
        return []
    return [part.strip() for part in match.group(1).split(",") if part.strip()]


class StubJsonClient:
    """Deterministic ``LLMJsonClient`` stub — no live LLM anywhere.

    The default behavior answers every call with a valid note citing exactly
    the whitelist the prompt declares, so outputs are pure functions of the
    prompts and the byte-parity tests hold. A custom ``fn`` scripts failures
    (return ``None``) or misbehavior (fabricated citations, unfaithful
    bodies).
    """

    def __init__(self, fn: Optional[Callable[[dict], Optional[dict]]] = None) -> None:
        self.calls: List[dict] = []
        self._fn = fn if fn is not None else self._default

    @staticmethod
    def _default(call: dict) -> Optional[dict]:
        ids = _prompt_ids(call["user"])
        return {
            "kind": "runbook",
            "title": f"Distilled around {ids[0] if ids else 'nothing'}",
            "body": "Consolidated guidance drawn from the cited members.",
            "citations": ids,
        }

    def complete_json(
        self, *, system, user, schema_name, cache_key=None, max_retries=2
    ):
        call = {
            "system": system,
            "user": user,
            "schema_name": schema_name,
            "cache_key": cache_key,
        }
        self.calls.append(call)
        return self._fn(call)

    def complete_text(self, *, system, user, max_retries=2):
        return None


def _near_dup_graph(base: str, count: int, suffixes: List[str]) -> ResearchGraph:
    """One session + ``count`` near-dup findings forming a single cluster."""
    s1 = _session("s1", "2026-07-01T09:00:00Z", "2026-07-01T10:00:00Z", "work")
    nodes = [_agent_node(AGENT, AGENT_ID), s1]
    edges = [ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by")]
    for suffix in suffixes[:count]:
        finding = _finding(
            f"SessionInsight:{base}-{suffix}",
            f"{base.capitalize()} cache eviction policy tuning {suffix}",
            s1,
            "2026-06-28T00:00:00Z",
        )
        nodes.append(finding)
        edges.append(
            ResearchEdge(source=finding.id, target=s1.id, type="derived_from_session")
        )
    return ResearchGraph(nodes=nodes, edges=edges)


def test_llm_summarizer_end_to_end_with_warm_and_cold_cache_parity(tmp_path: Path) -> None:
    graph = _base_graph()
    client1 = StubJsonClient()
    result1, project = _run(tmp_path, graph, LLMSummarizer(client1))
    assert result1.status == "written"
    assert len(client1.calls) == 2  # one single-chunk map call per cluster
    first_bytes = agent_artifact_path(project, AGENT).read_bytes()

    payload = _artifact_payload(project)
    runbooks = _notes_by_kind(payload, "runbook")
    assert runbooks and all(
        note["metadata"]["distill_quality"] == "llm" for note in runbooks
    )
    for note in runbooks:
        member_ids = {ref["node_id"] for ref in note["metadata"]["member_refs"]}
        assert f"Distilled around {sorted(member_ids)[0]}" == note["name"]

    # Warm cache: identical bytes, zero wire calls.
    client2 = StubJsonClient()
    result2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(client2),
        options=DistillOptions(full=True),
    )
    assert result2.status == "unchanged"
    assert client2.calls == []
    assert result2.llm_cache_hits == 2

    # Cold cache (§7.2): cache dir deleted; the prior committed artifact is a
    # declared input, so the exact-same clusters replay verbatim from it —
    # byte-identical with ZERO wire calls (cache state never picks bytes).
    shutil.rmtree(distill_cache_dir(project))
    client3 = StubJsonClient()
    result3 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(client3),
        options=DistillOptions(full=True),
    )
    assert result3.status == "unchanged"
    assert client3.calls == []
    assert agent_artifact_path(project, AGENT).read_bytes() == first_bytes

    # Fully cold (fresh-clone-without-artifact): the deterministic stub must
    # regenerate a byte-identical artifact via real wire calls.
    agent_artifact_path(project, AGENT).unlink()
    client4 = StubJsonClient()
    result4 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(client4),
        options=DistillOptions(full=True),
    )
    assert result4.status == "written"
    assert len(client4.calls) == 2
    assert agent_artifact_path(project, AGENT).read_bytes() == first_bytes


def test_llm_summarizer_map_reduce_over_multiple_chunks() -> None:
    members = tuple(
        (f"SessionInsight:m{index}", f"Wholly distinct subject {index}", "")
        for index in range(3)
    )
    blocks = [f"[{member_id}] {name}" for member_id, name, _desc in members]
    chunks = tuple(pack_blocks(blocks, 60))
    assert len(chunks) == 3  # the fixture must actually exercise map+reduce

    client = StubJsonClient()
    summarizer = LLMSummarizer(client)
    request = DistillRequest(
        agent_key=AGENT,
        lineage_key="ab" * 32,
        kind_hint="runbook",
        members=members,
        chunks=chunks,
    )
    output = summarizer(request)

    assert [call["schema_name"] for call in client.calls] == [
        "agent_distill_map", "agent_distill_map", "agent_distill_map",
        "agent_distill_reduce",
    ]
    assert summarizer.provider_calls == 4
    # Map calls see a chunk-scoped whitelist; the reduce sees all members.
    assert _prompt_ids(client.calls[0]["user"]) == ["SessionInsight:m0"]
    assert _prompt_ids(client.calls[3]["user"]) == [m[0] for m in members]
    assert sorted(output["citations"]) == [m[0] for m in members]

    # A dead backend surfaces as the transport error the breaker counts.
    with pytest.raises(SummarizerTransportError):
        LLMSummarizer(StubJsonClient(lambda call: None))(request)


def test_llm_client_failure_is_cached_and_retry_fallbacks_upgrades(tmp_path: Path) -> None:
    graph = _base_graph()
    failing = StubJsonClient(lambda call: None)
    result1, project = _run(tmp_path, graph, LLMSummarizer(failing))
    assert result1.status == "written"
    assert result1.llm_failed == 2
    assert result1.llm_fallbacks == 2
    assert not result1.llm_aborted  # 2 consecutive failures < breaker limit 3
    payload = _artifact_payload(project)
    assert {
        note["metadata"]["distill_quality"] for note in _notes_by_kind(payload, "runbook")
    } == {"fallback"}

    # Recovered provider, no --retry-fallbacks: the cached fallback verdict
    # holds — bytes must not flip, zero wire calls (§5.3).
    healthy = StubJsonClient()
    result2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(healthy),
        options=DistillOptions(full=True),
    )
    assert result2.status == "unchanged"
    assert healthy.calls == []
    assert result2.llm_cache_hits == 2

    # --retry-fallbacks re-attempts through the real wire path and upgrades.
    result3 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(healthy),
        options=DistillOptions(full=True, retry_fallbacks=True),
    )
    assert result3.status == "written"
    assert len(healthy.calls) == 2
    payload = _artifact_payload(project)
    assert {
        note["metadata"]["distill_quality"] for note in _notes_by_kind(payload, "runbook")
    } == {"llm"}


def test_negative_cache_backoff_blocks_reattempt_after_cache_loss(tmp_path: Path) -> None:
    graph = _base_graph()
    failing = StubJsonClient(lambda call: None)
    result1, project = _run(tmp_path, graph, LLMSummarizer(failing))
    assert result1.llm_failed == 2

    # Losing the verdict cache (GC / fresh clone) must not stampede a backend
    # that just failed: the negative cache blocks until the backoff expires.
    shutil.rmtree(distill_cache_dir(project))
    healthy = StubJsonClient()
    result2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(healthy),
        options=DistillOptions(full=True),
    )
    assert result2.status == "unchanged"  # deterministic fallback = same bytes
    assert healthy.calls == []
    assert result2.llm_calls == 0
    assert result2.llm_fallbacks == 2

    # The next executed run passes the retry watermark and goes to the wire.
    result3 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(healthy),
        options=DistillOptions(full=True),
    )
    assert result3.status == "written"
    assert len(healthy.calls) == 2
    payload = _artifact_payload(project)
    assert {
        note["metadata"]["distill_quality"] for note in _notes_by_kind(payload, "runbook")
    } == {"llm"}


def test_backoff_resets_when_members_digest_changes(tmp_path: Path) -> None:
    # A negative-cache entry records the digest it failed on; a changed
    # members_digest is NEW input and must re-attempt immediately (a fresh
    # sidecar would), instead of the stale backoff deciding artifact bytes.
    graph = _near_dup_graph("echo", 2, ["alpha", "bravo"])
    failing = StubJsonClient(lambda call: None)
    result1, project = _run(tmp_path, graph, LLMSummarizer(failing))
    assert result1.llm_failed == 1

    nodes2 = []
    for node in graph.nodes:
        if node.id == "SessionInsight:echo-alpha":
            node = ResearchNode(
                id=node.id,
                name=node.name,
                type=node.type,
                description="Root-cause detail added",
                metadata={**(node.metadata or {}), "content_hash": "ch-echo-alpha-v2"},
            )
        nodes2.append(node)
    graph2 = ResearchGraph(nodes=nodes2, edges=list(graph.edges))

    healthy = StubJsonClient()
    result2 = distill_agent(
        graph2, AGENT, project_root=project, summarizer=LLMSummarizer(healthy)
    )
    assert result2.status == "written"
    assert len(healthy.calls) == 1  # backoff ignored: the digest changed
    assert {
        note["metadata"]["distill_quality"]
        for note in _notes_by_kind(_artifact_payload(project), "runbook")
    } == {"llm"}


def test_circuit_breaker_is_shared_across_distill_all(tmp_path: Path) -> None:
    # §5.3: "3 consecutive transport failures aborts the LLM stage for the
    # RUN" — one invocation over N agents shares one breaker; a dead provider
    # costs at most 3 attempts for the whole sweep, not 3 per agent.
    graphs = [
        _near_dup_graph(base, 2, suffixes)
        for base, suffixes in (
            ("alpha", ["uno", "dos"]),
            ("bravo", ["tres", "cuatro"]),
            ("carbon", ["cinco", "seis"]),
            ("dune", ["siete", "ocho"]),
        )
    ]
    s8 = _session("s8", "2026-07-03T09:00:00Z", "2026-07-03T10:00:00Z", "other work")
    o1 = _finding(
        "SessionInsight:oscar-nine", "Oscar deployment lockfile pruning nine", s8,
        "2026-07-02T00:00:00Z",
    )
    o2 = _finding(
        "SessionInsight:oscar-ten", "Oscar deployment lockfile pruning ten", s8,
        "2026-07-02T00:00:00Z",
    )
    merged_nodes = {node.id: node for graph in graphs for node in graph.nodes}
    merged_edges = {
        (edge.source, edge.type, edge.target): edge
        for graph in graphs
        for edge in graph.edges
    }
    for node in [_agent_node(OTHER_AGENT, OTHER_AGENT_ID), s8, o1, o2]:
        merged_nodes[node.id] = node
    for edge in [
        ResearchEdge(source=s8.id, target=OTHER_AGENT_ID, type="performed_by"),
        ResearchEdge(source=o1.id, target=s8.id, type="derived_from_session"),
        ResearchEdge(source=o2.id, target=s8.id, type="derived_from_session"),
    ]:
        merged_edges[(edge.source, edge.type, edge.target)] = edge
    graph = ResearchGraph(
        nodes=list(merged_nodes.values()), edges=list(merged_edges.values())
    )

    project = tmp_path / "proj"
    project.mkdir()
    dead = StubJsonClient(lambda call: None)
    results = distill_all(graph, project_root=project, summarizer=LLMSummarizer(dead))
    assert [r.agent_key for r in results] == [AGENT, OTHER_AGENT]
    assert len(dead.calls) == 3  # tripped during the first agent, stays tripped
    assert results[0].llm_aborted is True
    assert results[1].llm_aborted is True
    assert results[1].llm_calls == 0
    assert results[1].llm_fallbacks == 1  # its cluster degraded without a wire call


def test_circuit_breaker_aborts_llm_stage_without_poisoning_cache(tmp_path: Path) -> None:
    # Four two-member clusters. Suffixes are globally unique so cross-pair
    # Jaccard stays at 4/8 = 0.5 < 0.55 while within-pair is 5/7 ≈ 0.71.
    graphs = [
        _near_dup_graph(base, 2, suffixes)
        for base, suffixes in (
            ("alpha", ["uno", "dos"]),
            ("bravo", ["tres", "cuatro"]),
            ("carbon", ["cinco", "seis"]),
            ("dune", ["siete", "ocho"]),
        )
    ]
    merged_nodes = {node.id: node for graph in graphs for node in graph.nodes}
    merged_edges = {
        (edge.source, edge.type, edge.target): edge
        for graph in graphs
        for edge in graph.edges
    }
    graph = ResearchGraph(
        nodes=list(merged_nodes.values()), edges=list(merged_edges.values())
    )

    dead = StubJsonClient(lambda call: None)
    result, project = _run(tmp_path, graph, LLMSummarizer(dead))
    assert result.cluster_count == 4
    assert result.llm_aborted is True
    assert len(dead.calls) == 3  # breaker trips after 3 consecutive failures
    assert result.llm_failed == 3
    assert result.llm_fallbacks == 4  # 3 failed + 1 aborted-without-a-call
    payload = _artifact_payload(project)
    assert len(_notes_by_kind(payload, "runbook")) == 4

    # Only the 3 real attempts cached fallback verdicts; the aborted cluster
    # was NOT poisoned — a later healthy run pays exactly one wire call.
    healthy = StubJsonClient()
    result2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(healthy),
        options=DistillOptions(full=True),
    )
    assert result2.status == "written"
    assert len(healthy.calls) == 1
    assert result2.llm_cache_hits == 3
    qualities = [
        note["metadata"]["distill_quality"]
        for note in _notes_by_kind(_artifact_payload(project), "runbook")
    ]
    assert sorted(qualities) == ["fallback", "fallback", "fallback", "llm"]


def test_fold_request_goes_through_the_fold_wire_path(tmp_path: Path) -> None:
    suffixes = ["alpha", "bravo", "golf", "hotel", "india"]
    graph4 = _near_dup_graph("delta", 4, suffixes)
    client1 = StubJsonClient()
    result1, project = _run(tmp_path, graph4, LLMSummarizer(client1))
    assert result1.status == "written"
    assert result1.llm_folds == 0
    assert len(client1.calls) == 1

    # Growing the cluster 4 → 5 (<30%, no merge) takes the cheap fold call.
    graph5 = _near_dup_graph("delta", 5, suffixes)
    client2 = StubJsonClient()
    result2 = distill_agent(
        graph5, AGENT, project_root=project, summarizer=LLMSummarizer(client2)
    )
    assert result2.status == "written"
    assert result2.llm_folds == 1
    assert [call["schema_name"] for call in client2.calls] == ["agent_distill_fold"]
    fold_user = client2.calls[0]["user"]
    assert "Existing note:" in fold_user and "New findings:" in fold_user
    note = _notes_by_kind(_artifact_payload(project), "runbook")[0]
    assert note["metadata"]["member_count"] == 5
    assert note["metadata"]["distill_quality"] == "llm"

    # The folded output is cached like any other value — replays are stable.
    client3 = StubJsonClient()
    result3 = distill_agent(
        graph5, AGENT, project_root=project, summarizer=LLMSummarizer(client3),
        options=DistillOptions(full=True),
    )
    assert result3.status == "unchanged"
    assert client3.calls == []

    # Cold-after-fold (§7.2): with the cache gone the fold precondition
    # (strict growth) no longer holds, so a naive rerun would full-distill
    # and mint DIFFERENT text — the prior artifact must replay verbatim
    # instead, keeping folded steady-state clusters byte-stable.
    shutil.rmtree(distill_cache_dir(project))
    client4 = StubJsonClient()
    result4 = distill_agent(
        graph5, AGENT, project_root=project, summarizer=LLMSummarizer(client4),
        options=DistillOptions(full=True),
    )
    assert result4.status == "unchanged"
    assert client4.calls == []


def test_llm_citation_whitelist_and_faithfulness_reject_through_client(tmp_path: Path) -> None:
    graph = _base_graph()

    fabricating = StubJsonClient(
        lambda call: {
            "kind": "runbook",
            "title": "Fabricated",
            "body": "Steps.",
            "citations": ["SessionInsight:not-a-member"],
        }
    )
    result1, project1 = _run(tmp_path, graph, LLMSummarizer(fabricating))
    assert result1.llm_rejected == result1.cluster_count == 2
    assert {
        note["metadata"]["distill_quality"]
        for note in _notes_by_kind(_artifact_payload(project1), "runbook")
    } == {"fallback"}

    unfaithful = StubJsonClient(
        lambda call: {
            "kind": "runbook",
            "title": "Unfaithful",
            "body": "Upgrade `totally_invented_symbol` to v99.99 first.",
            "citations": _prompt_ids(call["user"])[:1],
        }
    )
    project2 = tmp_path / "proj2"
    project2.mkdir()
    result2 = distill_agent(
        graph, AGENT, project_root=project2, summarizer=LLMSummarizer(unfaithful)
    )
    assert result2.llm_rejected == result2.cluster_count == 2


def test_max_llm_calls_budget_and_capped_runs_converge(tmp_path: Path) -> None:
    graph = _base_graph()
    client1 = StubJsonClient()
    result1, project = _run(
        tmp_path, graph, LLMSummarizer(client1), DistillOptions(max_llm_calls=1)
    )
    assert result1.status == "written"
    assert result1.llm_calls == 1
    assert len(client1.calls) == 1
    assert result1.llm_fallbacks == 1  # the capped cluster, NOT verdict-cached
    qualities = sorted(
        note["metadata"]["distill_quality"]
        for note in _notes_by_kind(_artifact_payload(project), "runbook")
    )
    assert qualities == ["fallback", "llm"]

    # The cache makes capped runs converge over invocations (§5.3).
    client2 = StubJsonClient()
    result2 = distill_agent(
        graph, AGENT, project_root=project, summarizer=LLMSummarizer(client2),
        options=DistillOptions(full=True, max_llm_calls=1),
    )
    assert result2.status == "written"
    assert len(client2.calls) == 1
    assert result2.llm_cache_hits == 1
    assert {
        note["metadata"]["distill_quality"]
        for note in _notes_by_kind(_artifact_payload(project), "runbook")
    } == {"llm"}


def test_llm_dry_run_estimates_planned_calls_and_writes_nothing(tmp_path: Path) -> None:
    graph = _base_graph()
    client = StubJsonClient()
    result, project = _run(
        tmp_path, graph, LLMSummarizer(client), DistillOptions(dry_run=True)
    )
    assert result.status == "dry-run"
    assert client.calls == []
    assert result.estimated_llm_calls == 2  # two single-chunk clusters
    assert not agent_artifact_path(project, AGENT).exists()
    assert not distill_cache_dir(project).exists()


def test_build_llm_summarizer_resolution() -> None:
    stub = StubJsonClient()
    set_agent_distill_test_client(stub)
    try:
        built = build_llm_summarizer()
        assert isinstance(built, LLMSummarizer)
        assert built.client is stub

        explicit = StubJsonClient()
        assert build_llm_summarizer(explicit).client is explicit
    finally:
        set_agent_distill_test_client(None)
