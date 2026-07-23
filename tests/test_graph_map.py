"""graph_map — the budgeted Descent entry point (§5.1, structural-only PR5).

Synthetic multi-level fixture: a hand-written ``.tesserae/hierarchy.json``
(the loader trusts the PR4 sidecar, so tests need not depend on Louvain's
partitioning of a crafted graph) over a small graph with two coarsest
communities:

* ``A`` (4 members) — has an in-graph COMMUNITY_SUMMARY node, so its card
  reuses that title/description/tags with ``quality="llm"``.
* ``B`` (6 members) — no summary node → deterministic structural card.

Levels (finest → coarsest) exercise pass-through skipping: ``B`` appears
byte-identically at the middle level (descent must skip it), and ``A1``
appears identically at the finest level (descent must fall through to
member-node cards). ``b6`` belongs to ``B`` but to no finest-level child —
the loose-member node-card path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tesserae.community_summaries import community_id
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

A_MEMBERS = ["Concept:a1", "Concept:a2", "Concept:a3", "Concept:a4"]
B_MEMBERS = ["Concept:b1", "Concept:b2", "Concept:b3", "Concept:b4", "Concept:b5", "Concept:b6"]
A1_MEMBERS = ["Concept:a1", "Concept:a2"]
A2_MEMBERS = ["Concept:a3", "Concept:a4"]
B1_MEMBERS = ["Concept:b1", "Concept:b2", "Concept:b3"]
B2_MEMBERS = ["Concept:b4", "Concept:b5"]

CID_A = community_id(A_MEMBERS)
CID_B = community_id(B_MEMBERS)
CID_A1 = community_id(A1_MEMBERS)
CID_A2 = community_id(A2_MEMBERS)
CID_B1 = community_id(B1_MEMBERS)
CID_B2 = community_id(B2_MEMBERS)

_LONG_DESC = "B-one is the hub of the beta cluster and its description repeats. " * 6


def _fixture_graph() -> ResearchGraph:
    def _concept(nid: str) -> ResearchNode:
        if nid == "Concept:b1":
            # b1 is the top-degree member of B → the structural card's title anchor.
            return ResearchNode(
                id=nid, name="Beta Hub", type=ResearchNodeType.CONCEPT, description=_LONG_DESC
            )
        return ResearchNode(
            id=nid,
            name=f"Node {nid.split(':')[1].upper()}",
            type=ResearchNodeType.CONCEPT,
            description=f"description of {nid}",
        )

    nodes = [_concept(nid) for nid in A_MEMBERS + B_MEMBERS]
    nodes.append(
        ResearchNode(
            id=CID_A,
            name="Alpha Systems",
            type=ResearchNodeType.COMMUNITY_SUMMARY,
            description="LLM-written summary of the alpha community.",
            metadata={
                "member_ids": list(A_MEMBERS),
                "member_count": len(A_MEMBERS),
                "tags": ["alpha", "systems", "graph", "kg", "llm"],
            },
        )
    )
    edges = [
        ResearchEdge(source="Concept:a1", target="Concept:a2", type="shares_concept_with"),
        ResearchEdge(source="Concept:a3", target="Concept:a4", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b2", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b3", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b4", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b5", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b6", type="shares_concept_with"),
        ResearchEdge(source="Concept:b2", target="Concept:b3", type="shares_concept_with"),
    ]
    edges.extend(
        ResearchEdge(source=CID_A, target=mid, type="summarizes", metadata={"community_id": CID_A})
        for mid in A_MEMBERS
    )
    return ResearchGraph(nodes=nodes, edges=edges)


def _hierarchy_payload() -> dict:
    return {
        "schema_version": 1,
        "levels": [
            # finest: A1/A2 (A1 identical to middle level), B1/B2 (b6 loose)
            {CID_A1: A1_MEMBERS, CID_A2: A2_MEMBERS, CID_B1: B1_MEMBERS, CID_B2: B2_MEMBERS},
            # middle: A splits, B passes through byte-identically
            {CID_A1: A1_MEMBERS, CID_A2: A2_MEMBERS, CID_B: B_MEMBERS},
            # coarsest: the root card set
            {CID_A: A_MEMBERS, CID_B: B_MEMBERS},
        ],
        "hubs": ["Concept:b1"],
    }


@pytest.fixture()
def project(tmp_path: Path) -> dict:
    root = tmp_path / "proj"
    tess = root / ".tesserae"
    tess.mkdir(parents=True)
    graph_path = tess / "graph.json"
    graph_path.write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    (tess / "hierarchy.json").write_text(
        json.dumps(_hierarchy_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"root": root, "graph_path": graph_path, "server": LLMWikiMCPServer()}


def _call(project: dict, **kwargs) -> dict:
    return project["server"].call_tool(
        "graph_map", {"graph_path": str(project["graph_path"]), **kwargs}
    )


# ---------------------------------------------------------------------------
# Root card set
# ---------------------------------------------------------------------------


def test_root_header_carries_counts_and_hub_names(project) -> None:
    result = _call(project)
    header = result["header"]
    assert header["scope"] is None
    assert header["node_count"] == 11  # 10 concepts + 1 CommunitySummary
    assert header["edge_count"] == 12
    assert header["levels"] == 3
    assert header["community_count"] == 2
    assert header["hubs"] == ["Beta Hub"]
    assert header["total_cards"] == 2


def test_root_cards_sorted_by_size_then_cid(project) -> None:
    cards = _call(project)["cards"]
    assert [c["scope_id"] for c in cards] == [CID_B, CID_A]  # B (6) before A (4)
    for card in cards:
        assert card["kind"] == "community"
        assert card["parent_scope"] is None
        assert card["stale"] is False


def test_root_card_reuses_in_graph_llm_summary(project) -> None:
    card = next(c for c in _call(project)["cards"] if c["scope_id"] == CID_A)
    assert card["quality"] == "llm"
    assert card["title"] == "Alpha Systems"
    assert card["summary"] == "LLM-written summary of the alpha community."
    assert card["tags"] == ["alpha", "systems", "graph", "kg", "llm"]
    assert card["size"] == 4
    assert card["leaf_member_count"] == 4
    assert card["children_count"] == 2  # A1 + A2 at the middle level


def test_root_card_structural_fallback(project) -> None:
    card = next(c for c in _call(project)["cards"] if c["scope_id"] == CID_B)
    assert card["quality"] == "structural"
    assert card["title"] == "Beta Hub"  # top-degree member's name
    assert "6 members" in card["summary"]
    assert "Concept" in card["summary"]  # type histogram
    assert len(card["summary"]) <= 160
    assert card["tags"] == ["concept"]
    assert card["size"] == 6
    assert card["leaf_member_count"] == 6
    assert card["children_count"] == 3  # B1 + B2 + loose b6


# ---------------------------------------------------------------------------
# Descent
# ---------------------------------------------------------------------------


def test_descend_skips_passthrough_level(project) -> None:
    """B is byte-identical at the middle level — descent lands on the finest
    split (B1, B2) plus the loose member b6 as a node card."""
    result = _call(project, scope=CID_B)
    header = result["header"]
    assert header["scope"] == CID_B
    assert header["level"] == 2  # coarsest occurrence (0 = finest)
    assert header["parent_scope"] is None
    assert header["leaf_member_count"] == 6
    cards = result["cards"]
    assert [c["scope_id"] for c in cards] == [CID_B1, CID_B2, "Concept:b6"]
    assert [c["kind"] for c in cards] == ["community", "community", "node"]
    for card in cards:
        assert card["parent_scope"] == CID_B


def test_finest_level_yields_member_node_cards(project) -> None:
    cards = _call(project, scope=CID_B1)["cards"]
    assert [c["scope_id"] for c in cards] == B1_MEMBERS  # sorted node ids
    for card in cards:
        assert card["kind"] == "node"
        assert card["size"] == 1
        assert card["children_count"] == 0
        assert card["leaf_member_count"] == 1
        assert card["parent_scope"] == CID_B1
        assert card["quality"] == "structural"
        assert len(card["summary"]) <= 160
    hub_card = cards[0]
    assert hub_card["title"] == "Beta Hub"
    assert hub_card["summary"].endswith("…[truncated]")


def test_passthrough_to_finest_yields_node_cards(project) -> None:
    """A1 repeats identically at the finest level — descent falls through to
    its member nodes instead of returning a self-referential card."""
    cards = _call(project, scope=CID_A1)["cards"]
    assert [c["scope_id"] for c in cards] == A1_MEMBERS
    assert all(c["kind"] == "node" for c in cards)


def test_parent_scope_is_ascend(project) -> None:
    b1_card = next(c for c in _call(project, scope=CID_B)["cards"] if c["scope_id"] == CID_B1)
    assert b1_card["parent_scope"] == CID_B
    header = _call(project, scope=CID_B1)["header"]
    assert header["parent_scope"] == CID_B
    # And the ascend call itself works.
    up = _call(project, scope=b1_card["parent_scope"])
    assert up["header"]["scope"] == CID_B


def test_middle_level_scope_parent_is_coarsest(project) -> None:
    header = _call(project, scope=CID_A1)["header"]
    assert header["parent_scope"] == CID_A


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_unknown_scope_fails_loud(project) -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        _call(project, scope="CommunitySummary:doesnotexist00")


def test_missing_sidecar_fails_with_remedy(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    tess = root / ".tesserae"
    tess.mkdir(parents=True)
    graph_path = tess / "graph.json"
    graph_path.write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    server = LLMWikiMCPServer()
    with pytest.raises(ValueError, match="tesserae compile"):
        server.call_tool("graph_map", {"graph_path": str(graph_path)})


# ---------------------------------------------------------------------------
# Budget + cursor pagination
# ---------------------------------------------------------------------------


def test_budget_zero_is_uncapped(project) -> None:
    result = _call(project, scope=CID_B, budget_chars=0)
    assert len(result["cards"]) == 3
    assert "continuation" not in result


def test_cursor_pagination_covers_all_cards(project) -> None:
    seen: list = []
    cursor = 0
    for _ in range(10):  # bounded walk — must terminate well before this
        result = _call(project, scope=CID_B, budget_chars=1400, cursor=cursor)
        assert result["cards"], "each page must admit at least one card"
        seen.extend(c["scope_id"] for c in result["cards"])
        continuation = result.get("continuation")
        if continuation is None:
            break
        match = re.fullmatch(r"\+(\d+) more, cursor=(\d+)", continuation)
        assert match, continuation
        cursor = int(match.group(2))
        assert cursor == len(seen)  # absolute cursor, resumes past kept cards
    else:
        pytest.fail("pagination never terminated")
    assert seen == [CID_B1, CID_B2, "Concept:b6"]


def test_pagination_is_deterministic(project) -> None:
    first = _call(project, scope=CID_B, budget_chars=1400)
    second = _call(project, scope=CID_B, budget_chars=1400)
    assert first == second


# ---------------------------------------------------------------------------
# Memory (LRU) access bumps
# ---------------------------------------------------------------------------


def test_returned_ids_bump_node_memory(project) -> None:
    from tesserae.memory.store import read_memory

    _call(project, scope=CID_B1)
    rows = read_memory(project["root"] / ".tesserae" / "sqlite.db")
    for mid in B1_MEMBERS:
        assert rows[mid].access_count >= 1


def test_root_call_bumps_community_ids(project) -> None:
    from tesserae.memory.store import read_memory

    _call(project)
    rows = read_memory(project["root"] / ".tesserae" / "sqlite.db")
    assert CID_A in rows and CID_B in rows


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_graph_map_is_listed_with_scope_grammar(project) -> None:
    tools = {t["name"]: t for t in project["server"].list_tools()}
    tool = tools["graph_map"]
    assert "scope" in tool["inputSchema"]["properties"]
    assert "cursor" in tool["inputSchema"]["properties"]
    assert "budget_chars" in tool["inputSchema"]["properties"]
    # The description alone must teach the scope grammar (spec risk #7).
    assert "scope_id" in tool["description"]
    assert "parent_scope" in tool["description"]
    assert "cursor" in tool["description"]
