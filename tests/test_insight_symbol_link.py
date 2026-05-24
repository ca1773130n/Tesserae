"""Tests for the SessionFinding ↔ CodeSymbol linker (feature H).

Covers the four-case acceptance matrix from the feature spec plus the
missing-code-graph degradation path and the ``find_code_symbol_mentions``
MCP tool's two-stage resolver.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tesserae.memory.insight_symbol_link import (
    DISCUSSES_EDGE,
    build_symbol_index,
    find_symbol_mentions,
    insight_symbol_link_enabled,
    run_insight_symbol_link_pass,
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


def _insight(node_id: str, body: str) -> ResearchNode:
    return ResearchNode(
        id=f"SessionInsight:{node_id}",
        name=body,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "sess-1"},
    )


def _decision(node_id: str, body: str) -> ResearchNode:
    return ResearchNode(
        id=f"SessionDecision:{node_id}",
        name=body,
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "sess-1"},
    )


def _code_graph_payload() -> Dict[str, Any]:
    """Tiny code graph fixture.

    Shape: ``File`` -> ``Module(demo)`` ; ``Module`` contains class ``A``
    (with method ``A.foo``) and top-level function ``bar``.
    """
    return {
        "nodes": [
            {"id": "CodeFile:demo.py", "name": "demo.py", "type": "CodeFile"},
            {"id": "CodeModule:demo", "name": "demo", "type": "CodeModule"},
            {
                "id": "CodeClass:demo.A",
                "name": "A",
                "type": "CodeClass",
                "source_path": "demo.py",
                "metadata": {"line": 1},
            },
            {
                "id": "CodeMethod:demo.A.foo",
                "name": "A.foo",
                "type": "CodeMethod",
                "source_path": "demo.py",
                "metadata": {"line": 2, "parent_class": "A"},
            },
            {
                "id": "CodeFunction:demo.bar",
                "name": "bar",
                "type": "CodeFunction",
                "source_path": "demo.py",
                "metadata": {"line": 8},
            },
        ],
        "edges": [
            {"source": "CodeModule:demo", "type": "contains",
             "target": "CodeClass:demo.A"},
            {"source": "CodeClass:demo.A", "type": "contains",
             "target": "CodeMethod:demo.A.foo"},
            {"source": "CodeModule:demo", "type": "contains",
             "target": "CodeFunction:demo.bar"},
        ],
    }


@pytest.fixture
def code_graph_path(tmp_path: Path) -> Path:
    path = tmp_path / "code-graph.json"
    path.write_text(json.dumps(_code_graph_payload()), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Acceptance matrix
# ---------------------------------------------------------------------------


def _discusses_edges_from(graph: ResearchGraph, source_id: str) -> List[ResearchEdge]:
    return [
        e for e in graph.edges
        if e.type == DISCUSSES_EDGE and e.source == source_id
    ]


def test_backtick_identifier_links_to_code_function(code_graph_path: Path):
    """Case 1: ``Refactored `bar` to handle empty input`` links to ``bar``."""
    insight = _insight("c1", "Refactored `bar` to handle empty input")
    graph = ResearchGraph(nodes=[insight], edges=[])

    out = run_insight_symbol_link_pass(graph, code_graph_path=code_graph_path)
    edges = _discusses_edges_from(out, insight.id)

    targets = {e.target for e in edges}
    assert targets == {"CodeFunction:demo.bar"}
    # Edge carries the extractor + symbol_type provenance.
    edge = edges[0]
    assert edge.metadata.get("extractor") == "memory.insight_symbol_link"
    assert edge.metadata.get("symbol_type") == "CodeFunction"
    assert edge.metadata.get("symbol_name") == "bar"


def test_dotted_path_and_backtick_link_to_multiple_symbols(code_graph_path: Path):
    """Case 2: ``A.foo`` (dotted) AND ``bar`` (bare) both resolve."""
    decision = _decision(
        "c2", "Decision: use A.foo over the global bar"
    )
    graph = ResearchGraph(nodes=[decision], edges=[])

    out = run_insight_symbol_link_pass(graph, code_graph_path=code_graph_path)
    edges = _discusses_edges_from(out, decision.id)
    targets = {e.target for e in edges}

    # ``A.foo`` resolves to the CodeMethod; ``bar`` resolves to the
    # CodeFunction. The bare ``A`` token is also a CodeClass and is a
    # legitimate match — the linker fans out to all candidates.
    assert "CodeMethod:demo.A.foo" in targets
    assert "CodeFunction:demo.bar" in targets


def test_stopwords_and_dict_chatter_are_skipped(code_graph_path: Path):
    """Case 3: ``len`` / ``dict`` are blocked even when backticked."""
    insight = _insight(
        "c3", "Generic note about `len` and dict comprehensions"
    )
    graph = ResearchGraph(nodes=[insight], edges=[])

    out = run_insight_symbol_link_pass(graph, code_graph_path=code_graph_path)
    edges = _discusses_edges_from(out, insight.id)

    assert edges == []


def test_existing_discusses_edge_is_not_duplicated(code_graph_path: Path):
    """Case 4: idempotency — re-running doesn't double up edges."""
    insight = _insight("c4", "`bar` was renamed")
    prior_edge = ResearchEdge(
        source=insight.id,
        target="CodeFunction:demo.bar",
        type=DISCUSSES_EDGE,
        metadata={"extractor": "memory.insight_symbol_link"},
    )
    graph = ResearchGraph(nodes=[insight], edges=[prior_edge])

    out = run_insight_symbol_link_pass(graph, code_graph_path=code_graph_path)
    edges = _discusses_edges_from(out, insight.id)

    # Exactly the one edge we started with.
    assert len(edges) == 1
    assert edges[0] is prior_edge


def test_missing_code_graph_is_a_no_op(tmp_path: Path):
    """Pass returns input graph unchanged when code-graph.json is missing."""
    insight = _insight("c5", "`bar` is everywhere")
    graph = ResearchGraph(nodes=[insight], edges=[])

    missing = tmp_path / "does-not-exist.json"
    assert not missing.exists()

    out = run_insight_symbol_link_pass(graph, code_graph_path=missing)
    assert out is graph
    assert out.edges == []


# ---------------------------------------------------------------------------
# Same-name-multiple-files fan-out
# ---------------------------------------------------------------------------


def test_ambiguous_same_name_symbol_fans_out_to_all_candidates(tmp_path: Path):
    """v0.2.0 invariant: same display name across files yields all matches."""
    payload = {
        "nodes": [
            {"id": "CodeFunction:pkg_a.helper", "name": "helper",
             "type": "CodeFunction", "source_path": "pkg_a/util.py"},
            {"id": "CodeFunction:pkg_b.helper", "name": "helper",
             "type": "CodeFunction", "source_path": "pkg_b/util.py"},
        ],
        "edges": [],
    }
    code_graph_path = tmp_path / "code-graph.json"
    code_graph_path.write_text(json.dumps(payload), encoding="utf-8")

    insight = _insight("c6", "Updated `helper` in both packages")
    graph = ResearchGraph(nodes=[insight], edges=[])

    out = run_insight_symbol_link_pass(graph, code_graph_path=code_graph_path)
    targets = {e.target for e in _discusses_edges_from(out, insight.id)}

    assert targets == {
        "CodeFunction:pkg_a.helper",
        "CodeFunction:pkg_b.helper",
    }


# ---------------------------------------------------------------------------
# Helper sanity
# ---------------------------------------------------------------------------


def test_env_flag_default_on_and_falsy_opt_out(monkeypatch: pytest.MonkeyPatch):
    # Default-on: unset env → enabled.
    monkeypatch.delenv("TESSERAE_INSIGHT_SYMBOL_LINK", raising=False)
    assert insight_symbol_link_enabled()
    # Explicit truthy spellings → enabled.
    monkeypatch.setenv("TESSERAE_INSIGHT_SYMBOL_LINK", "true")
    assert insight_symbol_link_enabled()
    monkeypatch.setenv("TESSERAE_INSIGHT_SYMBOL_LINK", "1")
    assert insight_symbol_link_enabled()
    # Explicit opt-out spellings → disabled.
    for falsy in ("0", "false", "no", "off"):
        monkeypatch.setenv("TESSERAE_INSIGHT_SYMBOL_LINK", falsy)
        assert not insight_symbol_link_enabled(), f"{falsy!r} should disable"
    # Empty / whitespace → default (enabled).
    monkeypatch.setenv("TESSERAE_INSIGHT_SYMBOL_LINK", "")
    assert insight_symbol_link_enabled()
    monkeypatch.setenv("TESSERAE_INSIGHT_SYMBOL_LINK", "   ")
    assert insight_symbol_link_enabled()
    # Garbage values → default (enabled) — conservative: only explicit
    # opt-out spellings disable.
    monkeypatch.setenv("TESSERAE_INSIGHT_SYMBOL_LINK", "maybe")
    assert insight_symbol_link_enabled()


def test_build_symbol_index_keys_method_by_bare_and_qualified_name():
    index = build_symbol_index(_code_graph_payload()["nodes"])
    # Method indexed under both ``A.foo`` (qualified) and ``foo`` (bare).
    assert "A.foo" in index
    assert "foo" in index
    # CodeFile / CodeModule are filtered out (not linkable).
    assert "demo.py" not in index
    assert "demo" not in index


def test_find_symbol_mentions_pure_function():
    """Sanity check on the pure helper used by the MCP tool's live scan."""
    index = build_symbol_index(_code_graph_payload()["nodes"])
    insight = _insight("c7", "`bar` and `A.foo`")
    matches = find_symbol_mentions(insight, index)
    names = {m["name"] for m in matches}
    assert names == {"bar", "A.foo"}


def test_skips_non_finding_nodes(code_graph_path: Path):
    """Findings only — Papers / Concepts / etc. are ignored even with hits."""
    paper = ResearchNode(
        id="Paper:demo",
        name="A great paper about `bar`",
        type=ResearchNodeType.PAPER,
    )
    graph = ResearchGraph(nodes=[paper], edges=[])
    out = run_insight_symbol_link_pass(graph, code_graph_path=code_graph_path)
    assert _discusses_edges_from(out, paper.id) == []


# ---------------------------------------------------------------------------
# CodeGraph-adapter expanded types (codex PR #10 P2 fix)
# ---------------------------------------------------------------------------


def _codegraph_payload() -> Dict[str, Any]:
    """Realistic code-graph slice covering the new CodeGraph-derived
    node types so we exercise the expanded LINKABLE_CODE_SYMBOL_TYPES.
    """
    return {
        "nodes": [
            # Rust trait + struct
            {"id": "CodeTrait:Cacheable", "name": "Cacheable", "type": "CodeTrait", "metadata": {}},
            {"id": "CodeStruct:Buffer", "name": "Buffer", "type": "CodeStruct", "metadata": {}},
            # TS interface + type alias
            {"id": "CodeInterface:HttpClient", "name": "HttpClient", "type": "CodeInterface", "metadata": {}},
            {"id": "CodeTypeAlias:UserId", "name": "UserId", "type": "CodeTypeAlias", "metadata": {}},
            # Enum + EnumMember (dotted form)
            {"id": "CodeEnum:Color", "name": "Color", "type": "CodeEnum", "metadata": {}},
            {"id": "CodeEnumMember:Color.Red", "name": "Color.Red", "type": "CodeEnumMember", "metadata": {}},
            # Class with field (dotted)
            {"id": "CodeClass:Account", "name": "Account", "type": "CodeClass", "metadata": {}},
            {"id": "CodeField:Account.balance", "name": "Account.balance", "type": "CodeField", "metadata": {}},
            # Framework route + component
            {"id": "CodeRoute:LoginRoute", "name": "LoginRoute", "type": "CodeRoute", "metadata": {}},
            {"id": "CodeComponent:UserCard", "name": "UserCard", "type": "CodeComponent", "metadata": {}},
            # Generic fallback
            {"id": "CodeSymbol:RetryPolicy", "name": "RetryPolicy", "type": "CodeSymbol", "metadata": {}},
            # NOT linkable: file/module/parameter (excluded by spec)
            {"id": "CodeFile:lib.rs", "name": "lib.rs", "type": "CodeFile", "metadata": {}},
            {"id": "CodeModule:lib", "name": "lib", "type": "CodeModule", "metadata": {}},
            {"id": "CodeParameter:request", "name": "request", "type": "CodeParameter", "metadata": {}},
        ],
        "edges": [],
    }


def test_expanded_linkable_types_indexed():
    """All new CodeGraph-derived symbol types are indexed; File/Module/Parameter are not."""
    index = build_symbol_index(_codegraph_payload()["nodes"])
    # New types — must be present
    for expected in [
        "Cacheable", "Buffer", "HttpClient", "UserId", "Color",
        "Color.Red", "Account", "Account.balance", "LoginRoute",
        "UserCard", "RetryPolicy",
    ]:
        assert expected in index, f"expected {expected!r} in symbol index"
    # Excluded types — must NOT be present
    for excluded in ["lib.rs", "lib", "request"]:
        assert excluded not in index, f"{excluded!r} should not be linkable"


def test_dotted_tail_indexed_for_field_and_enum_member():
    """CodeField + CodeEnumMember (like CodeMethod) get a bare-tail alias."""
    index = build_symbol_index(_codegraph_payload()["nodes"])
    # Dotted forms
    assert "Account.balance" in index
    assert "Color.Red" in index
    # Bare tails (added because Field/EnumMember are in _DOTTED_TAIL_TYPES)
    assert "balance" in index
    assert "Red" in index
    # Bare tail resolves back to the dotted node
    assert any(n["id"] == "CodeField:Account.balance" for n in index["balance"])
    assert any(n["id"] == "CodeEnumMember:Color.Red" for n in index["Red"])


def test_pass_links_findings_to_new_codegraph_types(tmp_path: Path):
    """End-to-end: a finding mentioning new types via backticks gets discusses edges."""
    code_graph_path = tmp_path / "code-graph.json"
    code_graph_path.write_text(json.dumps(_codegraph_payload()), encoding="utf-8")
    findings = [
        _insight("c1", "Refactored `Cacheable` to use the new `HttpClient`"),
        _decision("c2", "Use `Color.Red` instead of the legacy `RetryPolicy`"),
        _insight("c3", "The `LoginRoute` should delegate to `UserCard`"),
    ]
    graph = ResearchGraph(nodes=list(findings), edges=[])
    out = run_insight_symbol_link_pass(graph, code_graph_path=code_graph_path)
    discusses = [e for e in out.edges if e.type == DISCUSSES_EDGE]
    targets = {(e.source, e.target) for e in discusses}
    expected_pairs = {
        (findings[0].id, "CodeTrait:Cacheable"),
        (findings[0].id, "CodeInterface:HttpClient"),
        (findings[1].id, "CodeEnumMember:Color.Red"),
        (findings[1].id, "CodeSymbol:RetryPolicy"),
        (findings[2].id, "CodeRoute:LoginRoute"),
        (findings[2].id, "CodeComponent:UserCard"),
    }
    missing = expected_pairs - targets
    assert not missing, f"missing discusses edges: {missing}"


def test_pass_does_not_link_to_files_modules_or_parameters(tmp_path: Path):
    """An insight that backticks the file/module/parameter name produces no edge."""
    code_graph_path = tmp_path / "code-graph.json"
    code_graph_path.write_text(json.dumps(_codegraph_payload()), encoding="utf-8")
    insight = _insight("c4", "The bug is in `lib.rs` module `lib`, parameter `request`")
    graph = ResearchGraph(nodes=[insight], edges=[])
    out = run_insight_symbol_link_pass(graph, code_graph_path=code_graph_path)
    discusses = [e for e in out.edges if e.type == DISCUSSES_EDGE and e.source == insight.id]
    assert discusses == [], (
        "CodeFile / CodeModule / CodeParameter must not be linked — got: "
        f"{[(e.target) for e in discusses]}"
    )
