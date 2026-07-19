"""CLI + shippability tests for `tesserae distill` (spec §12 Phase 2, WP-C).

Covers the argparse surface (gate, unknown-agent fail-loud, --jobs validation,
mutual exclusion), the run summary output, watermark-skip reporting, dry-run,
and the Phase-2 ship gate: a distilled artifact produced by the CLI is
queryable through the SAME code path the MCP `graph_path` argument uses.

The LLM is always a stub injected through `set_agent_distill_test_client`
(the seam `build_llm_summarizer` resolves before any real backend) — no live
calls anywhere.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, List, Optional

import pytest

from tesserae import cli
from tesserae.agent_distill import (
    agent_artifact_path,
    set_agent_distill_test_client,
)
from tesserae.cli_tree import KNOWN_COMMANDS
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
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


def _finding(fid: str, name: str, session: ResearchNode, first_seen: str = "") -> ResearchNode:
    metadata = {"session_id": session.metadata["session_id"], "content_hash": f"ch-{fid}"}
    if first_seen:
        metadata["first_seen_at"] = first_seen
    return ResearchNode(
        id=fid, name=name, type=ResearchNodeType.SESSION_INSIGHT, metadata=metadata
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


def _base_graph() -> ResearchGraph:
    """Two agents; the primary one has a near-dup cluster + a singleton."""
    s1 = _session("s1", "2026-06-20T10:00:00Z", "2026-06-20T11:00:00Z", "release work")
    s2 = _session("s2", "2026-07-01T09:00:00Z", "2026-07-01T10:00:00Z", "graphql work")
    s9 = _session("s9", "2026-07-02T09:00:00Z", "2026-07-02T10:00:00Z", "other agent work")

    f1 = _finding("SessionInsight:f1", "Release flow needs staging deploy verification", s1, "2026-06-20T10:00:00Z")
    f2 = _finding("SessionInsight:f2", "Release flow needs staging deploy verification step", s2, "2026-07-01T09:00:00Z")
    f3 = _finding("SessionInsight:f3", "Graphql resolver timeout root cause", s2, "2026-07-01T09:00:00Z")
    f9 = _finding("SessionInsight:f9", "Foreign agent finding", s9, "2026-07-02T09:00:00Z")

    concept = ResearchNode(
        id="Concept:staging-deploy:abc123",
        name="Staging Deploy",
        type=ResearchNodeType.CONCEPT,
    )

    nodes = [
        _agent_node(AGENT, AGENT_ID),
        _agent_node(OTHER_AGENT, OTHER_AGENT_ID),
        s1, s2, s9, f1, f2, f3, f9, concept,
    ]
    edges = [
        ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by"),
        ResearchEdge(source=s2.id, target=AGENT_ID, type="performed_by"),
        ResearchEdge(source=s9.id, target=OTHER_AGENT_ID, type="performed_by"),
        ResearchEdge(source=f1.id, target=s1.id, type="derived_from_session"),
        ResearchEdge(source=f2.id, target=s2.id, type="derived_from_session"),
        ResearchEdge(source=f3.id, target=s2.id, type="derived_from_session"),
        ResearchEdge(source=f9.id, target=s9.id, type="derived_from_session"),
        ResearchEdge(source=f1.id, target=concept.id, type="references"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _write_project(tmp_path: Path, graph: Optional[ResearchGraph] = None) -> Path:
    """Initialized project dir with a compiled graph.json (compile byte format)."""
    project = tmp_path / "proj"
    tess = project / ".tesserae"
    tess.mkdir(parents=True, exist_ok=True)
    config = tess / "config.json"
    if not config.exists():
        config.write_text("{}\n", encoding="utf-8")
    if graph is not None:
        (tess / "graph.json").write_text(
            graph.canonicalized().to_json(indent=2) + "\n", encoding="utf-8"
        )
    return project


_VALID_IDS_RE = re.compile(r"(?m)^Valid citation ids: (.*)$")


class StubJsonClient:
    """Deterministic ``LLMJsonClient`` stub (mirrors test_agent_distill's)."""

    def __init__(self, fn: Optional[Callable[[dict], Optional[dict]]] = None) -> None:
        self.calls: List[dict] = []
        self._fn = fn if fn is not None else self._default

    @staticmethod
    def _default(call: dict) -> Optional[dict]:
        match = _VALID_IDS_RE.search(call["user"])
        ids = [p.strip() for p in match.group(1).split(",") if p.strip()] if match else []
        return {
            "kind": "runbook",
            "title": f"Distilled around {ids[0] if ids else 'nothing'}",
            "body": "Consolidated guidance drawn from the cited members.",
            "citations": ids,
        }

    def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
        call = {"system": system, "user": user, "schema_name": schema_name, "cache_key": cache_key}
        self.calls.append(call)
        return self._fn(call)

    def complete_text(self, *, system, user, max_retries=2):
        return None


@pytest.fixture()
def stub_client():
    """Route build_llm_summarizer() through the injection seam — never a real CLI."""
    client = StubJsonClient()
    set_agent_distill_test_client(client)
    yield client
    set_agent_distill_test_client(None)


@pytest.fixture()
def enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TESSERAE_AGENT_DISTILL", "1")


# --------------------------------------------------------------------------- surface


def test_distill_is_a_known_command_with_help() -> None:
    assert "distill" in KNOWN_COMMANDS
    parser = cli._build_distill_parser()
    text = parser.format_help()
    for flag in (
        "--agent", "--all", "--dry-run", "--max-llm-calls", "--jobs",
        "--full", "--retry-fallbacks", "--recheck", "--as-of",
    ):
        assert flag in text
    assert "TESSERAE_AGENT_DISTILL" in text


def test_agent_and_all_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["distill", "--agent", AGENT, "--all", "--project", str(tmp_path)])
    assert excinfo.value.code == 2


def test_jobs_must_be_positive(tmp_path: Path, enabled, capsys) -> None:
    project = _write_project(tmp_path, _base_graph())
    assert cli.main(["distill", "--project", str(project), "--jobs", "0"]) == 2
    assert "--jobs" in capsys.readouterr().err


def test_gate_disabled_exits_1_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.delenv("TESSERAE_AGENT_DISTILL", raising=False)
    project = _write_project(tmp_path, _base_graph())
    assert cli.main(["distill", "--project", str(project)]) == 1
    assert "TESSERAE_AGENT_DISTILL" in capsys.readouterr().err


def test_config_flag_enables_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_client, capsys
) -> None:
    monkeypatch.delenv("TESSERAE_AGENT_DISTILL", raising=False)
    project = _write_project(tmp_path, _base_graph())
    (project / ".tesserae" / "config.json").write_text(
        json.dumps({"agent_distill": {"enabled": True}}) + "\n", encoding="utf-8"
    )
    assert cli.main(["distill", "--project", str(project)]) == 0
    assert agent_artifact_path(project, AGENT).is_file()


def test_missing_graph_exits_2(tmp_path: Path, enabled, capsys) -> None:
    project = _write_project(tmp_path, graph=None)
    assert cli.main(["distill", "--project", str(project)]) == 2
    assert "compile" in capsys.readouterr().err


def test_uninitialized_project_exits_2(tmp_path: Path, enabled, capsys) -> None:
    # No .tesserae/config.json: main()'s central FileNotFoundError catch.
    assert cli.main(["distill", "--project", str(tmp_path / "nope")]) == 2
    assert "init" in capsys.readouterr().err


def test_unknown_agent_fails_loud(tmp_path: Path, enabled, stub_client, capsys) -> None:
    project = _write_project(tmp_path, _base_graph())
    code = cli.main(["distill", "--project", str(project), "--agent", "ghost:me:default"])
    assert code == 1
    err = capsys.readouterr().err
    assert "Unknown agent: ghost:me:default" in err
    assert AGENT in err  # known keys listed for the operator
    assert not agent_artifact_path(project, AGENT).exists()  # nothing ran


# --------------------------------------------------------------------------- runs + summary


def test_distill_all_writes_artifacts_and_prints_summary(
    tmp_path: Path, enabled, stub_client, capsys
) -> None:
    project = _write_project(tmp_path, _base_graph())
    assert cli.main(["distill", "--project", str(project)]) == 0
    out = capsys.readouterr().out

    artifact = agent_artifact_path(project, AGENT)
    other = agent_artifact_path(project, OTHER_AGENT)
    assert artifact.is_file() and other.is_file()

    # Required summary fields: clusters, LLM calls, fallbacks, path + size.
    assert f"{AGENT}  written  clusters=1" in out
    assert "llm_calls=1" in out
    assert "fallbacks=0" in out
    assert str(artifact) in out
    assert re.search(r"\(\d+ chars, ok\)", out)
    assert "Distill pass over 2 agent(s): written=2" in out
    assert stub_client.calls, "the injected stub client must have served the LLM stage"


def test_single_agent_flag_scopes_the_run(
    tmp_path: Path, enabled, stub_client, capsys
) -> None:
    project = _write_project(tmp_path, _base_graph())
    assert cli.main(["distill", "--project", str(project), "--agent", AGENT]) == 0
    out = capsys.readouterr().out
    assert agent_artifact_path(project, AGENT).is_file()
    assert not agent_artifact_path(project, OTHER_AGENT).exists()
    assert "Distill pass over 1 agent(s)" in out


def test_second_run_reports_watermark_skip(
    tmp_path: Path, enabled, stub_client, capsys
) -> None:
    project = _write_project(tmp_path, _base_graph())
    assert cli.main(["distill", "--project", str(project), "--agent", AGENT]) == 0
    capsys.readouterr()
    assert cli.main(["distill", "--project", str(project), "--agent", AGENT]) == 0
    out = capsys.readouterr().out
    assert f"{AGENT}  skipped-watermark" in out
    assert "skipped-watermark=1" in out


def test_dry_run_estimates_and_writes_nothing(
    tmp_path: Path, enabled, stub_client, capsys
) -> None:
    project = _write_project(tmp_path, _base_graph())
    assert cli.main(["distill", "--project", str(project), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "estimated_llm_calls=1" in out
    assert not agent_artifact_path(project, AGENT).exists()
    assert not stub_client.calls  # dry-run never touches the summarizer


def test_no_llm_backend_still_runs_with_fallbacks(
    tmp_path: Path, enabled, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    # No injected client AND no default backend -> summarizer is None ->
    # every cluster takes the visible deterministic-fallback path.
    import tesserae.agent_distill as agent_distill_mod

    monkeypatch.setattr(agent_distill_mod, "build_default_json_client", lambda **kw: None)
    project = _write_project(tmp_path, _base_graph())
    assert cli.main(["distill", "--project", str(project), "--agent", AGENT]) == 0
    out = capsys.readouterr().out
    assert f"{AGENT}  written" in out
    assert "llm_calls=0" in out  # no backend -> zero wire calls, run still ships
    assert "fallbacks=1" in out  # ...and the fallback is COUNTED, not "estimated"
    payload = json.loads(agent_artifact_path(project, AGENT).read_text(encoding="utf-8"))
    qualities = {
        node["metadata"].get("distill_quality")
        for node in payload["nodes"]
        if node["type"] == "DistilledNote"
    }
    assert "fallback" in qualities


# --------------------------------------------------------------------------- ship gate (§12 Phase 2)


def test_distilled_artifact_is_queryable_via_mcp_graph_path(
    tmp_path: Path, enabled, stub_client, capsys
) -> None:
    """Shippability smoke: the CLI-produced L1 loads through the SAME path the
    MCP ``graph_path`` argument uses, and Agent/DistilledNote nodes are
    queryable — 'any agent's L1 is queryable via the existing graph-path
    argument' (spec §12 Phase 2 ship gate)."""
    from tesserae.mcp_server import LLMWikiMCPServer

    project = _write_project(tmp_path, _base_graph())
    assert cli.main(["distill", "--project", str(project), "--agent", AGENT]) == 0
    capsys.readouterr()
    artifact = agent_artifact_path(project, AGENT)

    server = LLMWikiMCPServer(registry_path=tmp_path / "mcp-registry.json")

    # graph_summary routes through _load_requested_graph -> load_graph — the
    # exact graph_path code path every MCP tool shares.
    summary = server.call_tool("graph_summary", {"graph_path": str(artifact)})
    assert summary["node_types"].get("Agent", 0) >= 1
    assert summary["node_types"].get("DistilledNote", 0) >= 1
    assert summary["node_types"].get("ExpertiseProfile", 0) == 1
    assert summary["edge_types"].get("derived_from", 0) >= 1

    # The Agent node is searchable...
    agent_hits = server.call_tool(
        "search_nodes",
        {"graph_path": str(artifact), "query": "reviewer", "types": ["Agent"], "mode": "legacy"},
    )
    assert any(node["name"] == AGENT for node in agent_hits["nodes"])

    # ...and so is the distilled runbook the stub produced.
    note_hits = server.call_tool(
        "search_nodes",
        {
            "graph_path": str(artifact),
            "query": "Distilled",
            "types": ["DistilledNote"],
            "mode": "legacy",
        },
    )
    assert note_hits["nodes"], "distilled note must be retrievable via graph_path"
    note = note_hits["nodes"][0]
    assert note["metadata"]["kind"] == "runbook"
    assert note["metadata"]["member_refs"]
