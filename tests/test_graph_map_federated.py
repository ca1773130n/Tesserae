"""graph_map federated scopes — ``<alias>::`` / ``<alias>::<cid>`` (§6.3, PR10).

Two sibling projects (``alpha`` / ``beta``) with hand-written hierarchy
sidecars (the loader trusts the PR4 sidecar) registered in a tmp-path
registry. Covers: alias-root card sets and descent served READ-ONLY from the
sibling's compiled bytes, ``alias::`` namespacing (federate_graphs semantics),
content-digest staleness verification, single-graph degradation on a
missing/corrupt registry, exactly one child-graph load per call through the
mtime-keyed LRU, byte-stability under project-order permutation, and the
codified merger ban (no cross-graph read path imports ``batch.merge_graphs``).
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import tesserae
import tesserae.mcp_server as mcp_server_mod
import tesserae.project as project_mod
from tesserae.community_summaries import community_id
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

X_MEMBERS = ["Concept:x1", "Concept:x2", "Concept:x3"]
Y_MEMBERS = ["Concept:y1", "Concept:y2"]
X1_MEMBERS = ["Concept:x1", "Concept:x2"]

CID_X = community_id(X_MEMBERS)
CID_Y = community_id(Y_MEMBERS)
CID_X1 = community_id(X1_MEMBERS)


def _fixture_graph() -> ResearchGraph:
    nodes = [
        ResearchNode(
            id=nid,
            name=f"Node {nid.split(':')[1].upper()}",
            type=ResearchNodeType.CONCEPT,
            description=f"description of {nid}",
        )
        for nid in X_MEMBERS + Y_MEMBERS
    ]
    nodes.append(
        ResearchNode(
            id=CID_X,
            name="X Systems",
            type=ResearchNodeType.COMMUNITY_SUMMARY,
            description="LLM-written summary of the X community.",
            metadata={
                "member_ids": list(X_MEMBERS),
                "member_count": len(X_MEMBERS),
                "tags": ["x", "systems"],
            },
        )
    )
    edges = [
        ResearchEdge(source="Concept:x1", target="Concept:x2", type="shares_concept_with"),
        ResearchEdge(source="Concept:x1", target="Concept:x3", type="shares_concept_with"),
        ResearchEdge(source="Concept:y1", target="Concept:y2", type="shares_concept_with"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _hierarchy_payload() -> dict:
    return {
        "schema_version": 1,
        "levels": [
            # finest: X splits into X1 (+ loose x3); Y passes through.
            {CID_X1: X1_MEMBERS, CID_Y: Y_MEMBERS},
            # coarsest: the alias-root card set.
            {CID_X: X_MEMBERS, CID_Y: Y_MEMBERS},
        ],
        "hubs": ["Concept:x1"],
    }


def _make_project(base: Path, name: str) -> Path:
    root = base / name
    tess = root / ".tesserae"
    tess.mkdir(parents=True)
    (tess / "graph.json").write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    (tess / "hierarchy.json").write_text(
        json.dumps(_hierarchy_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _write_registry(path: Path, projects: list[tuple[str, Path]]) -> Path:
    data = {
        "version": 1,
        "projects": {
            name: {"root": str(root), "graph_path": str(root / ".tesserae" / "graph.json")}
            for name, root in projects
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fresh_fed_cache():
    mcp_server_mod._FED_CHILD_CACHE.clear()
    yield
    mcp_server_mod._FED_CHILD_CACHE.clear()


@pytest.fixture(autouse=True)
def _reset_community_client():
    project_mod.set_community_summaries_test_client(None)
    yield
    project_mod.set_community_summaries_test_client(None)


@pytest.fixture()
def fed(tmp_path: Path) -> dict:
    alpha = _make_project(tmp_path, "alpha")
    beta = _make_project(tmp_path, "beta")
    registry = _write_registry(tmp_path / "registry.json", [("alpha", alpha), ("beta", beta)])
    return {
        "alpha": alpha,
        "beta": beta,
        "registry": registry,
        "server": LLMWikiMCPServer(registry_path=registry),
    }


def _call(fed: dict, **kwargs) -> dict:
    return fed["server"].call_tool("graph_map", kwargs)


def _digests(root: Path) -> dict:
    tess = root / ".tesserae"
    return {
        "graph.json": hashlib.sha256((tess / "graph.json").read_bytes()).hexdigest(),
        "hierarchy.json": hashlib.sha256((tess / "hierarchy.json").read_bytes()).hexdigest(),
    }


def _mutate_sibling_graph(root: Path) -> None:
    """Change graph.json bytes (and size) so digests + mtime signature move."""
    path = root / ".tesserae" / "graph.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("description of Concept:x3", "description of Concept:x3 CHANGED"),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Alias root + descent
# ---------------------------------------------------------------------------


def test_alias_root_maps_sibling_project(fed) -> None:
    result = _call(fed, scope="beta::")
    header = result["header"]
    assert header["scope"] == "beta::"
    assert header["kind"] == "root"
    assert header["project"] == "beta"
    assert header["node_count"] == 6  # 5 concepts + 1 CommunitySummary
    assert header["community_count"] == 2
    assert header["hubs"] == ["Node X1"]
    assert header["stale"] is False
    # GRAPH_REF semantics: content digests of the sibling's bytes ride along.
    assert header["digests"] == _digests(fed["beta"])
    cards = result["cards"]
    assert [c["scope_id"] for c in cards] == [f"beta::{CID_X}", f"beta::{CID_Y}"]
    for card in cards:
        assert card["parent_scope"] == "beta::"  # ascend = the alias root map
        assert card["stale"] is False


def test_alias_root_reuses_sibling_llm_summary(fed) -> None:
    card = next(
        c for c in _call(fed, scope="beta::")["cards"] if c["scope_id"] == f"beta::{CID_X}"
    )
    assert card["quality"] == "llm"
    assert card["title"] == "X Systems"


def test_alias_descent_serves_namespaced_child_cards(fed) -> None:
    result = _call(fed, scope=f"beta::{CID_X}")
    header = result["header"]
    assert header["scope"] == f"beta::{CID_X}"
    assert header["kind"] == "community"
    assert header["project"] == "beta"
    assert header["parent_scope"] == "beta::"
    assert header["stale"] is False
    cards = result["cards"]
    assert [c["scope_id"] for c in cards] == [f"beta::{CID_X1}", "beta::Concept:x3"]
    assert [c["kind"] for c in cards] == ["community", "node"]
    for card in cards:
        assert card["parent_scope"] == f"beta::{CID_X}"
        assert card["stale"] is False


def test_parent_scope_round_trips_to_alias_root(fed) -> None:
    card = _call(fed, scope=f"beta::{CID_X}")["cards"][0]
    up = _call(fed, scope=card["parent_scope"])
    ascended = _call(fed, scope=up["header"]["parent_scope"])
    assert ascended["header"]["scope"] == "beta::"
    assert ascended["header"]["kind"] == "root"


def test_unknown_alias_fails_loud_and_lists_projects(fed) -> None:
    with pytest.raises(ValueError, match="alpha") as excinfo:
        _call(fed, scope="gamma::")
    assert "beta" in str(excinfo.value)
    assert "gamma" in str(excinfo.value)


def test_unknown_scope_within_alias_fails_loud(fed) -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        _call(fed, scope="beta::CommunitySummary:doesnotexist00")


# ---------------------------------------------------------------------------
# Digest verification (stale — recompile)
# ---------------------------------------------------------------------------


def test_digest_mismatch_marks_cards_stale_with_note(fed) -> None:
    _call(fed, scope="beta::")  # records the digests the descent verifies
    _mutate_sibling_graph(fed["beta"])
    result = _call(fed, scope=f"beta::{CID_X}")
    assert result["header"]["stale"] is True
    assert "stale — recompile" in result["header"]["note"]
    assert result["cards"], "the current map is still served, flagged stale"
    for card in result["cards"]:
        assert card["stale"] is True
    # Digests in the response are the CURRENT bytes — never an outdated map.
    assert result["header"]["digests"] == _digests(fed["beta"])


def test_rebuilding_alias_root_clears_staleness(fed) -> None:
    _call(fed, scope="beta::")
    _mutate_sibling_graph(fed["beta"])
    _call(fed, scope="beta::")  # re-record against the new bytes
    result = _call(fed, scope=f"beta::{CID_X}")
    assert result["header"]["stale"] is False
    assert "note" not in result["header"]
    assert all(card["stale"] is False for card in result["cards"])


def test_descent_without_prior_root_is_not_stale(fed) -> None:
    result = _call(fed, scope=f"beta::{CID_X}")
    assert result["header"]["stale"] is False
    assert all(card["stale"] is False for card in result["cards"])


# ---------------------------------------------------------------------------
# Registry degradation — single-graph mode, never a crashed serve
# ---------------------------------------------------------------------------


def test_missing_registry_degrades_to_single_graph_mode(fed, tmp_path: Path) -> None:
    server = LLMWikiMCPServer(registry_path=tmp_path / "does-not-exist.json")
    with pytest.raises(ValueError, match="unknown project alias"):
        server.call_tool("graph_map", {"scope": "beta::"})
    # Non-federated serving is untouched (single-graph mode).
    local = server.call_tool(
        "graph_map", {"graph_path": str(fed["beta"] / ".tesserae" / "graph.json")}
    )
    assert local["header"]["kind"] == "root"


def test_corrupt_registry_degrades_to_single_graph_mode(fed, tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    server = LLMWikiMCPServer(registry_path=corrupt)
    with pytest.raises(ValueError, match="single-graph"):
        server.call_tool("graph_map", {"scope": "beta::"})
    local = server.call_tool(
        "graph_map", {"graph_path": str(fed["beta"] / ".tesserae" / "graph.json")}
    )
    assert local["header"]["kind"] == "root"


# ---------------------------------------------------------------------------
# READ-ONLY child access: one graph load per call, no writes, no LLM
# ---------------------------------------------------------------------------


def test_exactly_one_child_graph_load_per_call(fed, monkeypatch) -> None:
    calls: list = []
    real = mcp_server_mod.load_graph

    def counting(path):
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(mcp_server_mod, "load_graph", counting)
    _call(fed, scope=f"beta::{CID_X}")  # cold: exactly one load, only beta's
    assert calls == [fed["beta"] / ".tesserae" / "graph.json"]
    _call(fed, scope="beta::")  # warm: the mtime-keyed LRU serves it
    _call(fed, scope=f"beta::{CID_X1}")
    assert len(calls) == 1


def test_federated_reads_never_write_the_sibling_project(fed) -> None:
    class _Client:
        calls: list = []

        def complete_json(self, **kwargs):  # noqa: ANN003
            self.calls.append(kwargs)
            return {"title": "T", "description": "D", "tags": ["t"]}

    client = _Client()
    project_mod.set_community_summaries_test_client(client)
    _call(fed, scope="beta::")
    _call(fed, scope=f"beta::{CID_Y}")  # cold structural scope: NO materialization
    assert client.calls == []
    tess = fed["beta"] / ".tesserae"
    assert not (tess / "sqlite.db").exists()  # no node-memory access bumps
    assert not (tess / "community_summaries").exists()  # no summary-cache writes


# ---------------------------------------------------------------------------
# Determinism + merger rule
# ---------------------------------------------------------------------------


def test_output_byte_stable_under_project_order_permutation(fed, tmp_path: Path) -> None:
    reversed_registry = _write_registry(
        tmp_path / "registry_reversed.json", [("beta", fed["beta"]), ("alpha", fed["alpha"])]
    )
    forward = LLMWikiMCPServer(registry_path=fed["registry"])
    backward = LLMWikiMCPServer(registry_path=reversed_registry)
    for scope in ("beta::", f"beta::{CID_X}", "alpha::", f"alpha::{CID_X1}"):
        a = forward.call_tool("graph_map", {"scope": scope})
        mcp_server_mod._FED_CHILD_CACHE.clear()
        b = backward.call_tool("graph_map", {"scope": scope})
        assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)


def test_no_cross_graph_read_path_imports_merge_graphs() -> None:
    """Merger rule, codified (§6.3): cross-graph resolution uses
    federation.federate_graphs namespacing semantics only; the order-dependent
    ``merge_graphs`` (batch/project) is ingest-only and must never enter a
    cross-graph read path."""
    package_root = Path(tesserae.__file__).parent
    read_path_modules = ["mcp_server.py", "hierarchy.py", "federation.py", "agent_view.py"]
    for module in read_path_modules:
        tree = ast.parse((package_root / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not any(alias.name == "merge_graphs" for alias in node.names), (
                    f"{module} imports merge_graphs — cross-graph read paths "
                    f"must use federation.federate_graphs semantics only"
                )
            if isinstance(node, ast.Attribute):
                assert node.attr != "merge_graphs", (
                    f"{module} references .merge_graphs — cross-graph read "
                    f"paths must use federation.federate_graphs semantics only"
                )


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_description_teaches_federated_grammar(fed) -> None:
    tool = {t["name"]: t for t in fed["server"].list_tools()}["graph_map"]
    assert "<alias>::" in tool["description"]
    assert "stale — recompile" in tool["description"]
    assert "<alias>::" in tool["inputSchema"]["properties"]["scope"]["description"]
