"""graph_map agent scopes — the Descent org tree (§5.1 grammar, §6.2, PR9).

Scope grammar additions: ``org:root`` renders the agent registry tree as
agent cards (``children_count`` = direct reports; descent into a child is
``agent:<child key>``); ``agent:<key>`` renders that agent's distilled L1
Index as note cards via the READ-ONLY :func:`tesserae.agent_view.resolve_agent_view`
reuse. CRITICAL invariant under test: **sealed L0** — no raw L0 node ever
becomes a card and no L0 content (session titles, foreign findings) leaks
into the response; the only escalation is each note card's ``drill`` block,
whose ``member_refs`` feed the existing audited ``drill_down`` tool.

Fixture pattern follows ``tests/test_agent_view.py``: the shared distill
fixture graph as L0, ``StubSummarizer`` distillates (no LLM anywhere), and a
declared manager registry. Deliberately NO ``hierarchy.json`` sidecar — the
org tree is the registry, not the Louvain dendrogram, so agent scopes must
work without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.agent_distill import agent_artifact_path, distill_agent
from tesserae.agent_identity import AgentRegistry
from tesserae.mcp_server import LLMWikiMCPServer

from tests.test_agent_distill import (
    AGENT,
    OTHER_AGENT,
    StubSummarizer,
    _base_graph,
)

MANAGER = "claude-code:me:manager"


def _project_with_l0(tmp_path: Path):
    """Write the shared fixture graph as the project's L0 graph.json."""
    project = tmp_path / "proj"
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    graph = _base_graph()
    (project / ".tesserae" / "graph.json").write_text(
        graph.to_json(indent=2), encoding="utf-8"
    )
    return project, graph


def _distill(project: Path, graph, agent: str) -> None:
    distill_agent(graph, agent, project_root=project, summarizer=StubSummarizer())


def _declare_manager(project: Path) -> None:
    registry = AgentRegistry.for_project(project)
    data = registry.load()
    agents = data.setdefault("agents", {})
    agents[MANAGER] = {"label": "Manager", "parent": "org:root", "aliases": [], "match": []}
    agents[AGENT] = {"label": "Reviewer", "parent": MANAGER, "aliases": [], "match": []}
    agents[OTHER_AGENT] = {"label": "Codex", "parent": MANAGER, "aliases": [], "match": []}
    registry.save(data)


def _call(project: Path, **kwargs) -> dict:
    return LLMWikiMCPServer().call_tool(
        "graph_map",
        {"graph_path": str(project / ".tesserae" / "graph.json"), **kwargs},
    )


def _artifact_note_count(project: Path, agent: str) -> int:
    payload = json.loads(agent_artifact_path(project, agent).read_text(encoding="utf-8"))
    return sum(1 for node in payload["nodes"] if node["type"] == "DistilledNote")


# --------------------------------------------------------------------------- org:root


def test_org_root_renders_registry_tree(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _declare_manager(project)
    _distill(project, graph, AGENT)
    _distill(project, graph, OTHER_AGENT)

    result = _call(project, scope="org:root")
    header = result["header"]
    assert header["scope"] == "org:root"
    assert header["kind"] == "org"
    assert header["parent_scope"] is None  # org:root is the org tree's root
    assert header["agent_count"] == 3  # manager + two declared/observed workers

    cards = result["cards"]
    assert [c["scope_id"] for c in cards] == [f"agent:{MANAGER}"]
    card = cards[0]
    assert card["kind"] == "agent"
    assert card["title"] == "Manager"
    assert card["children_count"] == 2  # direct reports (spec §6.2)
    assert card["size"] == 3  # agents in the subtree, manager included
    assert card["parent_scope"] == "org:root"
    assert card["quality"] == "structural"
    assert card["stale"] is False
    expected_notes = _artifact_note_count(project, AGENT) + _artifact_note_count(
        project, OTHER_AGENT
    )
    assert card["leaf_member_count"] == expected_notes


def test_org_root_without_registry_lists_observed_agents(tmp_path: Path) -> None:
    """Zero registry config: every observed L0 Agent reports to org:root, and
    the tree renders even before anything is distilled (agent cards are
    registry/artifact structure, never resolved views)."""
    project, _graph = _project_with_l0(tmp_path)
    cards = _call(project, scope="org:root")["cards"]
    assert [c["scope_id"] for c in cards] == [f"agent:{AGENT}", f"agent:{OTHER_AGENT}"]
    for card in cards:
        assert card["kind"] == "agent"
        assert card["children_count"] == 0
        assert card["leaf_member_count"] == 0  # nothing distilled yet
        assert card["parent_scope"] == "org:root"
        assert "missing" in card["summary"]


# --------------------------------------------------------------------------- agent:<key>


def test_worker_scope_renders_l1_index_cards(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _declare_manager(project)
    _distill(project, graph, AGENT)

    result = _call(project, scope=f"agent:{AGENT}")
    header = result["header"]
    assert header["scope"] == f"agent:{AGENT}"
    assert header["kind"] == "agent"
    assert header["agent"] == AGENT
    assert header["mode"] == "worker"
    assert header["title"] == "Reviewer"
    assert header["parent_scope"] == f"agent:{MANAGER}"  # ascend to the manager

    cards = result["cards"]
    assert cards, "the distilled index must yield note cards"
    assert header["note_count"] == len(cards)
    for card in cards:
        assert card["kind"] == "note"
        assert card["parent_scope"] == f"agent:{AGENT}"
        assert card["children_count"] == 0
        assert card["size"] == card["leaf_member_count"] == len(card["drill"]["member_refs"])
        assert len(card["summary"]) <= 160
        # The escalation pointer: drill_down's exact arguments (§6.2).
        drill = card["drill"]
        assert drill["tool"] == "drill_down"
        assert drill["agent"] == AGENT
        for ref in drill["member_refs"]:
            assert ref["node_id"] and ref["content_hash"]

    # The L1 Index mixes llm-distilled runbooks with structural digest notes
    # (activity/index) — quality stays visibly per-note (§9 risk 8).
    runbooks = [c for c in cards if c["tags"] == ["runbook"]]
    assert runbooks, "StubSummarizer runbook notes must surface as cards"
    for card in runbooks:
        assert card["quality"] == "llm"
        assert card["drill"]["member_refs"]
    assert all(
        c["quality"] == "structural" for c in cards if c["tags"] != ["runbook"]
    )


def test_worker_scope_seals_l0(tmp_path: Path) -> None:
    """Sealed L0: every card is a distillate from the L1 artifact — no raw
    L0 node ever becomes a card, and out-of-scope L0 content (the foreign
    agent's session and finding) never reaches the response. Session titles
    the agent's own activity digest DISTILLED are allowed: that text lives in
    the artifact, not in an L0 read at map time."""
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)

    result = _call(project, scope=f"agent:{AGENT}")
    cards = result["cards"]
    assert {c["kind"] for c in cards} == {"note"}
    l0_ids = {n.id for n in graph.nodes}
    assert not l0_ids & {c["scope_id"] for c in cards}
    serialized = json.dumps(result, ensure_ascii=False)
    for leaked in ("other agent work", "Foreign agent finding"):
        assert leaked not in serialized


def test_manager_scope_has_child_agent_cards_then_distillate_index(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _declare_manager(project)
    _distill(project, graph, AGENT)
    _distill(project, graph, OTHER_AGENT)

    result = _call(project, scope=f"agent:{MANAGER}")
    header = result["header"]
    assert header["mode"] == "manager"
    assert header["parent_scope"] == "org:root"
    assert header["direct_reports"] == 2

    cards = result["cards"]
    # Direct-report agent cards first (org navigation), then the federated
    # distilled index (what the manager can see: distillate-only).
    assert [c["scope_id"] for c in cards[:2]] == [
        f"agent:{AGENT}",
        f"agent:{OTHER_AGENT}",
    ]
    for card in cards[:2]:
        assert card["kind"] == "agent"
        assert card["parent_scope"] == f"agent:{MANAGER}"
    note_cards = cards[2:]
    assert note_cards and all(c["kind"] == "note" for c in note_cards)
    # A federated note card ascends to its OWNING agent, not the manager.
    assert any(c["parent_scope"] == f"agent:{AGENT}" for c in note_cards)
    for card in note_cards:
        assert card["drill"]["agent"] in {AGENT, OTHER_AGENT}


# --------------------------------------------------------------------------- failure modes


def test_unknown_agent_key_fails_loud(tmp_path: Path) -> None:
    project, _graph = _project_with_l0(tmp_path)
    with pytest.raises(ValueError, match="unknown agent scope"):
        _call(project, scope="agent:claude-code:me:nonexistent")


def test_undistilled_worker_scope_fails_with_remedy(tmp_path: Path) -> None:
    project, _graph = _project_with_l0(tmp_path)
    with pytest.raises(ValueError, match=r"tesserae distill --agent"):
        _call(project, scope=f"agent:{AGENT}")


def test_agent_scopes_need_no_hierarchy_sidecar(tmp_path: Path) -> None:
    """The org tree is the registry, not the Louvain dendrogram: agent scopes
    resolve without hierarchy.json while community scopes still demand it."""
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    assert _call(project, scope=f"agent:{AGENT}")["cards"]
    with pytest.raises(ValueError, match="tesserae compile"):
        _call(project)  # the community root still requires the sidecar


# --------------------------------------------------------------------------- budget + determinism


def test_agent_scope_pagination_is_deterministic(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _declare_manager(project)
    _distill(project, graph, AGENT)
    _distill(project, graph, OTHER_AGENT)
    first = _call(project, scope=f"agent:{MANAGER}", budget_chars=2000)
    second = _call(project, scope=f"agent:{MANAGER}", budget_chars=2000)
    assert first == second
    uncapped = _call(project, scope=f"agent:{MANAGER}", budget_chars=0)
    assert "continuation" not in uncapped
    assert len(uncapped["cards"]) == uncapped["header"]["total_cards"]


def test_agent_scopes_do_not_bump_node_memory(tmp_path: Path) -> None:
    """Org/agent cards are registry structure and distillate ids — not L0
    graph nodes. The demand signal for community pre-warming stays clean;
    the audited drill_down escalation is where raw reads are recorded."""
    from tesserae.memory.store import read_memory

    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    _call(project, scope="org:root")
    _call(project, scope=f"agent:{AGENT}")
    db = project / ".tesserae" / "sqlite.db"
    assert not db.exists() or not read_memory(db)


# --------------------------------------------------------------------------- drill escalation round-trip


def test_note_card_drill_pointer_feeds_drill_down(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)

    server = LLMWikiMCPServer()
    graph_path = str(project / ".tesserae" / "graph.json")
    cards = server.call_tool(
        "graph_map", {"graph_path": graph_path, "scope": f"agent:{AGENT}"}
    )["cards"]
    drill = cards[0]["drill"]
    ref = drill["member_refs"][0]
    result = server.call_tool(
        "drill_down",
        {
            "graph_path": graph_path,
            "node_id": ref["node_id"],
            "content_hash": ref["content_hash"],
            "agent": drill["agent"],
        },
    )
    assert result["status"] in {"alive", "absorbed"}


# --------------------------------------------------------------------------- tool surface


def test_description_teaches_agent_scope_grammar() -> None:
    tools = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}
    description = tools["graph_map"]["description"]
    assert "agent:<key>" in description
    assert "org:root" in description
    assert "drill_down" in description
