"""Tests for Phase 4 — recursion + forgetting hardening (layered-agent-kg §6.2/§6.3/§8.2/§8.3).

Covers the manager pass (L2' materialization: lineage dedup, Jaccard grouping,
verbatim carry, arbitration-only LLM), the recursive corpus clock with the
mandated ``time.sleep`` parity test, lineage-set watermarks (child re-distill
without content change → zero manager work), ``distill_all`` leaves-first
ordering, the §8.2 memory-pressure refresh trigger, and the two new lint
surfaces (forget ledger, undistilled backlog). No LLM calls anywhere.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tesserae.agent_distill import (
    DistillError,
    DistillOptions,
    agent_artifact_path,
    distill_agent,
    distill_all,
    maybe_distill_on_refresh,
)
from tesserae.agent_identity import AgentRegistry
from tesserae.lint import WikiLinter
from tesserae.research_graph import ResearchGraph, stable_id

from tests.test_agent_distill import (
    AGENT,
    OTHER_AGENT,
    StubSummarizer,
    _base_graph,
)

MANAGER = "claude-code:me:manager"
CHILD_A = "claude-code:me:deployer"
CHILD_B = "codex:me:deployer"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    return project


def _declare(project: Path, parents: dict) -> AgentRegistry:
    registry = AgentRegistry.for_project(project)
    data = registry.load()
    agents = data.setdefault("agents", {})
    for key, parent in parents.items():
        agents[key] = {"label": key, "parent": parent, "aliases": [], "match": []}
    registry.save(data)
    return registry


def _handcrafted_note(
    agent: str, title: str, body: str, member_ids: list, *, kind: str = "runbook"
) -> dict:
    refs = [{"node_id": mid, "content_hash": f"ch-{mid}"} for mid in sorted(member_ids)]
    import hashlib

    lineage = hashlib.sha256("\n".join(sorted(member_ids)).encode()).hexdigest()
    return {
        "id": stable_id("DistilledNote", f"distilled:{agent}:{lineage[:16]}"),
        "name": title,
        "type": "DistilledNote",
        "aliases": [],
        "description": body,
        "source_path": None,
        "metadata": {
            "agent": agent,
            "kind": kind,
            "lineage_key": lineage,
            "content_hash": hashlib.sha256(body.encode()).hexdigest()[:24],
            "member_count": len(refs),
            "member_refs": refs,
            "absorbed_refs": [],
            "distill_quality": "llm",
            "distilled_through": "2026-07-01T10:00:00Z",
        },
    }


def _write_child_artifact(project: Path, agent: str, notes: list) -> None:
    path = agent_artifact_path(project, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"nodes": notes, "edges": []}, indent=2) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- manager pass (§8.3)


def _distilled_manager_setup(tmp_path: Path):
    """Two real children distilled from the shared fixture, MANAGER above them."""
    project = _project(tmp_path)
    graph = _base_graph()
    _declare(project, {MANAGER: "org:root", AGENT: MANAGER, OTHER_AGENT: MANAGER})
    for child in (AGENT, OTHER_AGENT):
        distill_agent(graph, child, project_root=project, summarizer=StubSummarizer())
    return project, graph


def test_manager_carries_child_distillates_verbatim(tmp_path: Path) -> None:
    project, graph = _distilled_manager_setup(tmp_path)
    result = distill_agent(graph, MANAGER, project_root=project, summarizer=StubSummarizer())
    assert result.status == "written"
    # Recursive corpus clock: max over children's distilled_through
    # (OTHER_AGENT's session ends 07-02 — the freshest child watermark wins).
    assert result.distilled_through == "2026-07-02T10:00:00Z"

    payload = json.loads(agent_artifact_path(project, MANAGER).read_text(encoding="utf-8"))
    notes = [
        n for n in payload["nodes"]
        if n["type"] == "DistilledNote" and n["metadata"].get("kind") == "runbook"
    ]
    assert notes, "child runbook was not carried"
    child_payload = json.loads(agent_artifact_path(project, AGENT).read_text(encoding="utf-8"))
    child_note = next(
        n for n in child_payload["nodes"]
        if n["type"] == "DistilledNote" and n["metadata"].get("kind") == "runbook"
    )
    carried = notes[0]
    # Step 3: body/title verbatim — no paraphrase-of-paraphrase.
    assert carried["description"] == child_note["description"]
    assert carried["name"] == child_note["name"]
    assert carried["metadata"]["agent"] == MANAGER
    # §6.4: refs flattened to the same L0 roots.
    assert carried["metadata"]["member_refs"] == child_note["metadata"]["member_refs"]
    # Org chart: children report to the manager inside the artifact.
    manager_id = stable_id("Agent", f"agent:{MANAGER}")
    reports = [
        e for e in payload["edges"] if e["type"] == "reports_to" and e["target"] == manager_id
    ]
    assert len(reports) == 2


def test_manager_watermark_zero_work_on_unchanged_children(tmp_path: Path) -> None:
    project, graph = _distilled_manager_setup(tmp_path)
    first = distill_agent(graph, MANAGER, project_root=project, summarizer=StubSummarizer())
    assert first.status == "written"
    again = distill_agent(graph, MANAGER, project_root=project, summarizer=StubSummarizer())
    assert again.status == "skipped-watermark"
    # A child re-distill that changes no constituent content_hash (§8.3 step
    # 6): the child pass itself watermark-skips, and the manager stays skipped.
    child = distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())
    assert child.status == "skipped-watermark"
    still = distill_agent(graph, MANAGER, project_root=project, summarizer=StubSummarizer())
    assert still.status == "skipped-watermark"


def test_manager_pass_is_wall_clock_independent(tmp_path: Path) -> None:
    """The mandated time.sleep parity test (§12 Phase 4)."""
    project, graph = _distilled_manager_setup(tmp_path)
    options = DistillOptions(full=True)
    distill_agent(graph, MANAGER, project_root=project, summarizer=StubSummarizer(), options=options)
    first_bytes = agent_artifact_path(project, MANAGER).read_bytes()
    time.sleep(0.05)
    second = distill_agent(
        graph, MANAGER, project_root=project, summarizer=StubSummarizer(), options=options
    )
    assert second.status == "unchanged"
    assert agent_artifact_path(project, MANAGER).read_bytes() == first_bytes


def test_manager_missing_child_artifact_fails_loud(tmp_path: Path) -> None:
    project = _project(tmp_path)
    graph = _base_graph()
    _declare(project, {MANAGER: "org:root", AGENT: MANAGER, OTHER_AGENT: MANAGER})
    distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())
    with pytest.raises(DistillError, match=f"tesserae distill --agent {OTHER_AGENT}"):
        distill_agent(graph, MANAGER, project_root=project, summarizer=StubSummarizer())


def test_manager_groups_by_ref_overlap_and_arbitrates_conflicts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _declare(project, {MANAGER: "org:root", CHILD_A: MANAGER, CHILD_B: MANAGER})
    _write_child_artifact(
        project,
        CHILD_A,
        [_handcrafted_note(CHILD_A, "Retry backoff for flaky deploys",
                           "Use retry backoff for flaky deploys.", ["L0:x", "L0:y"])],
    )
    _write_child_artifact(
        project,
        CHILD_B,
        [_handcrafted_note(CHILD_B, "Retry backoff considered harmful",
                           "Never use retry backoff for flaky deploys.", ["L0:x", "L0:y", "L0:z"])],
    )
    result = distill_agent(
        ResearchGraph(), MANAGER, project_root=project, summarizer=StubSummarizer()
    )
    assert result.status == "written"
    payload = json.loads(agent_artifact_path(project, MANAGER).read_text(encoding="utf-8"))
    notes = [n for n in payload["nodes"] if n["type"] == "DistilledNote"]
    by_kind = {}
    for note in notes:
        by_kind.setdefault(note["metadata"].get("kind"), []).append(note)
    # Jaccard 2/3 ≥ 0.5 → ONE group → one carried runbook (rep = higher
    # member_count, i.e. CHILD_B's) whose refs are the union of both.
    carried = by_kind["runbook"]
    assert len(carried) == 1
    assert carried[0]["description"] == "Never use retry backoff for flaky deploys."
    assert [r["node_id"] for r in carried[0]["metadata"]["member_refs"]] == [
        "L0:x", "L0:y", "L0:z"
    ]
    # Cross-agent conflict (one-sided negation, shared topic) → arbitration
    # note citing both sides — the only prose minted at manager level.
    arbitration = by_kind.get("arbitration")
    assert arbitration and len(arbitration) == 1
    assert arbitration[0]["metadata"]["distill_quality"] == "llm"
    assert [r["node_id"] for r in arbitration[0]["metadata"]["member_refs"]] == [
        "L0:x", "L0:y", "L0:z"
    ]


def test_distill_all_runs_leaves_before_managers(tmp_path: Path) -> None:
    project = _project(tmp_path)
    graph = _base_graph()
    _declare(project, {MANAGER: "org:root", AGENT: MANAGER, OTHER_AGENT: MANAGER})
    results = distill_all(graph, project_root=project, summarizer=StubSummarizer())
    keys = [r.agent_key for r in results]
    assert keys == [AGENT, OTHER_AGENT, MANAGER]
    manager_result = results[-1]
    # Children were distilled in the same sweep, so the manager pass found
    # fresh artifacts and materialized L2' instead of failing loud.
    assert manager_result.status == "written"


# --------------------------------------------------------------------------- refresh trigger (§8.2)


def _pressured_graph():
    """The base fixture with one finding inflated past the pressure floor.

    The §8.2 threshold is half the chunk budget — 24k chars at the 48k
    default — so one long transcript-derived description (~28k chars) is
    exactly the real-world shape that creates memory pressure.
    """
    import dataclasses

    graph = _base_graph()
    nodes = [
        dataclasses.replace(node, description="Root-cause detail. " * 1500)  # ~28k chars
        if node.id == "SessionInsight:f3"
        else node
        for node in graph.nodes
    ]
    return ResearchGraph(nodes=nodes, edges=graph.edges)


def test_refresh_trigger_is_gated_and_pressure_driven(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    monkeypatch.delenv("TESSERAE_LLM_CHUNK_CHARS", raising=False)

    # Gate off → skipped wholesale, no artifacts.
    out = maybe_distill_on_refresh(project, _pressured_graph(), env={})
    assert "skipped" in out
    assert not agent_artifact_path(project, AGENT).is_file()

    # Gate on but no memory pressure (small corpus, default budget) → skip.
    out = maybe_distill_on_refresh(
        project, _base_graph(), env={"TESSERAE_AGENT_DISTILL": "1"}, summarizer=StubSummarizer()
    )
    assert out["distilled"] == []
    assert not agent_artifact_path(project, AGENT).is_file()

    # Pressure: the inflated finding pushes AGENT's undistilled slice past
    # half the chunk budget → consolidation fires for that agent only.
    out = maybe_distill_on_refresh(
        project, _pressured_graph(), env={"TESSERAE_AGENT_DISTILL": "1"}, summarizer=StubSummarizer()
    )
    assert AGENT in out["distilled"]
    assert OTHER_AGENT in out["skipped"]
    assert agent_artifact_path(project, AGENT).is_file()


def test_refresh_trigger_rerolls_managers_after_child_writes(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    monkeypatch.delenv("TESSERAE_LLM_CHUNK_CHARS", raising=False)
    graph = _pressured_graph()
    _declare(project, {MANAGER: "org:root", AGENT: MANAGER, OTHER_AGENT: MANAGER})
    # OTHER_AGENT is under no pressure and never distills, so the manager
    # re-roll must fail loud on the missing child artifact — recorded as a
    # failure, never a silently thinner rollup, and never a dead refresh.
    out = maybe_distill_on_refresh(
        project, graph, env={"TESSERAE_AGENT_DISTILL": "1"}, summarizer=StubSummarizer()
    )
    assert AGENT in out["distilled"]
    assert MANAGER in out["failed"]

    # Once the quiet child is distilled too, the next refresh rolls the
    # manager up.
    distill_agent(graph, OTHER_AGENT, project_root=project, summarizer=StubSummarizer())
    out = maybe_distill_on_refresh(
        project, graph, env={"TESSERAE_AGENT_DISTILL": "1"}, summarizer=StubSummarizer()
    )
    assert MANAGER in out["distilled"]
    assert agent_artifact_path(project, MANAGER).is_file()


# --------------------------------------------------------------------------- lint surfaces (§6.2/§6.3)


def test_lint_surfaces_forget_ledger_and_backlog(tmp_path: Path) -> None:
    project = _project(tmp_path)
    graph = _base_graph()
    # AGENT distills (absorbing the old pair → ledger warning); OTHER_AGENT
    # never distills (its whole scope is backlog).
    distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())
    (project / ".tesserae" / "graph.json").write_text(
        graph.to_json(indent=2), encoding="utf-8"
    )
    report = WikiLinter(project).run()
    by_code = {}
    for finding in report.findings:
        by_code.setdefault(finding.code, []).append(finding)

    ledger = by_code.get("AGENT_FORGET_LEDGER") or []
    assert any(AGENT in f.message and "absorbed 2" in f.message for f in ledger)

    backlog = by_code.get("AGENT_UNDISTILLED_BACKLOG") or []
    assert any(OTHER_AGENT in f.message for f in backlog)
