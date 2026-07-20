"""Tests for the reusable, audit-logged ``agent_view.drill_down`` core (§6.4).

Exercises the four statuses (alive / changed / absorbed / gone) against a
distilled fixture and asserts the sidecar audit ledger is written. The core is
the extraction target the MCP ``drill_down`` tool now delegates to; those MCP
tests live in ``tests/test_agent_view.py`` and must stay green independently.
The summarizer is the deterministic stub from the distill tests — no LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.agent_distill import (
    DistillStateStore,
    _node_content_hash,
    _state_db_path,
    distill_agent,
)
from tesserae.agent_view import DRILL_DOWN_AUDIT_SCOPE, drill_down

from tests.test_agent_distill import AGENT, StubSummarizer, _base_graph


def _distilled_project(tmp_path: Path):
    """A project whose L0 fixture graph has been distilled for ``AGENT``."""
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    graph = _base_graph()
    distill_agent(graph, AGENT, project_root=project, summarizer=StubSummarizer())
    return project, graph


def test_alive_status_and_node_payload(tmp_path: Path) -> None:
    project, graph = _distilled_project(tmp_path)

    result = drill_down(project, graph, "SessionInsight:f3", agent=AGENT)

    assert result["status"] == "alive"
    assert result["agent"] == AGENT
    assert result["audited"] is True
    assert result["node"]["id"] == "SessionInsight:f3"
    assert result["node"]["name"] == "Graphql resolver timeout root cause"
    assert result["node"]["type"] == "SessionInsight"
    # content_hash echoes the live node hash, not the (absent) caller hash.
    node = next(n for n in graph.nodes if n.id == "SessionInsight:f3")
    assert result["node"]["content_hash"] == _node_content_hash(node)
    assert "absorbed_by" not in result


def test_changed_status_when_content_hash_stale(tmp_path: Path) -> None:
    project, graph = _distilled_project(tmp_path)

    result = drill_down(
        project, graph, "SessionInsight:f3", content_hash="stale-hash", agent=AGENT
    )

    assert result["status"] == "changed"
    # A matching hash is NOT changed — it is alive.
    live_hash = result["node"]["content_hash"]
    again = drill_down(project, graph, "SessionInsight:f3", content_hash=live_hash, agent=AGENT)
    assert again["status"] == "alive"


def test_absorbed_status_points_at_the_distillate(tmp_path: Path) -> None:
    project, graph = _distilled_project(tmp_path)

    result = drill_down(project, graph, "SessionInsight:old1", agent=AGENT)

    assert result["status"] == "absorbed"
    assert result["absorbed_by"].startswith("DistilledNote:")
    # Absorbed nodes still carry their live L0 payload.
    assert result["node"]["id"] == "SessionInsight:old1"


def test_gone_status_when_node_missing(tmp_path: Path) -> None:
    project, graph = _distilled_project(tmp_path)

    result = drill_down(project, graph, "SessionInsight:nope", agent=AGENT)

    assert result["status"] == "gone"
    assert "node" not in result
    assert result["audited"] is True


def test_absorption_requires_the_agent_argument(tmp_path: Path) -> None:
    """Without ``agent`` there is no L1 to consult — an absorbed raw reads alive."""
    project, graph = _distilled_project(tmp_path)

    result = drill_down(project, graph, "SessionInsight:old1")

    assert result["status"] == "alive"
    assert result["agent"] is None
    assert "absorbed_by" not in result


def test_missing_node_id_raises(tmp_path: Path) -> None:
    project, graph = _distilled_project(tmp_path)
    with pytest.raises(ValueError, match="node_id"):
        drill_down(project, graph, "", agent=AGENT)


def test_audit_rows_written_for_every_call(tmp_path: Path) -> None:
    project, graph = _distilled_project(tmp_path)

    drill_down(project, graph, "SessionInsight:f3", agent=AGENT)
    drill_down(project, graph, "SessionInsight:old1", agent=AGENT)
    drill_down(project, graph, "SessionInsight:nope", agent=AGENT)

    state = DistillStateStore(_state_db_path(project))
    rows = state.rows(DRILL_DOWN_AUDIT_SCOPE, AGENT)
    assert len(rows) == 3
    statuses = [json.loads(row[3])["status"] for row in rows]
    assert statuses == ["alive", "absorbed", "gone"]
    # Each entry records the queried node id and a wall-clock 'at' stamp — the
    # stamp lives ONLY in the sidecar, never in a graph artifact.
    first = json.loads(rows[0][3])
    assert first["node_id"] == "SessionInsight:f3"
    assert "at" in first


def test_audit_failure_is_logged_not_raised(tmp_path: Path, monkeypatch, caplog) -> None:
    project, graph = _distilled_project(tmp_path)

    def _boom(self, scope, agent_key, value):  # noqa: ANN001
        raise RuntimeError("sidecar locked")

    monkeypatch.setattr(DistillStateStore, "append", _boom)

    with caplog.at_level("WARNING"):
        result = drill_down(project, graph, "SessionInsight:f3", agent=AGENT)

    # The read survives a sidecar failure; the failure surfaces via 'audited'.
    assert result["status"] == "alive"
    assert result["audited"] is False
    assert any("audit log write failed" in rec.message for rec in caplog.records)
