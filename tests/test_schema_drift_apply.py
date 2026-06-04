"""KB-06: apply_schema_drift renames node.type for APPROVED proposals only.

Pure, deterministic, opt-in (Pitfall 4): a proposal renames the type of its
target nodes ONLY when it carries a truthy ``approved`` key. Missing/falsy
approval, empty proposals, and unknown target types are all no-ops that
return a byte-identical graph.

Deterministic: no wall-clock, no LLM. (The compile-time
``TESSERAE_SCHEMA_DRIFT_APPLY`` env gate is tested via project.py in 05-03;
this exercises the transform directly.)
"""

from __future__ import annotations

from tesserae.research_graph import (
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.schema_drift import apply_schema_drift


def _node(node_id: str, ntype: ResearchNodeType) -> ResearchNode:
    return ResearchNode(id=node_id, name=node_id, type=ntype)


def _graph() -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            _node("n1", ResearchNodeType.CONCEPT),
            _node("n2", ResearchNodeType.CONCEPT),
        ],
        edges=[],
    )


def _type_of(graph: ResearchGraph, node_id: str) -> ResearchNodeType:
    return next(n.type for n in graph.nodes if n.id == node_id)


def test_approved_proposal_renames_node_type() -> None:
    graph = _graph()
    proposals = [
        {
            "name": "Method",
            "approved": True,
            "proposed_type": ResearchNodeType.MODEL.value,
            "examples": ["n1"],
        }
    ]
    out = apply_schema_drift(graph, proposals)
    assert _type_of(out, "n1") == ResearchNodeType.MODEL
    # Untargeted node unchanged.
    assert _type_of(out, "n2") == ResearchNodeType.CONCEPT


def test_unapproved_proposal_is_a_no_op() -> None:
    graph = _graph()
    proposals = [
        {
            "name": "Method",
            # No "approved" key -> NOT applied.
            "proposed_type": ResearchNodeType.MODEL.value,
            "examples": ["n1"],
        }
    ]
    out = apply_schema_drift(graph, proposals)
    assert _type_of(out, "n1") == ResearchNodeType.CONCEPT


def test_empty_proposals_returns_graph_unchanged() -> None:
    graph = _graph()
    out = apply_schema_drift(graph, [])
    # Byte-identical no-op: same json serialization.
    assert out.to_json() == graph.to_json()


def test_unknown_type_is_skipped() -> None:
    graph = _graph()
    proposals = [
        {
            "name": "NotARealType",
            "approved": True,
            "proposed_type": "TotallyUnknownType",
            "examples": ["n1"],
        }
    ]
    out = apply_schema_drift(graph, proposals)
    # Unknown enum -> skipped, graph unchanged.
    assert _type_of(out, "n1") == ResearchNodeType.CONCEPT


def test_node_ids_key_also_resolves_targets() -> None:
    graph = _graph()
    proposals = [
        {
            "name": "Method",
            "approved": True,
            "proposed_type": ResearchNodeType.MODEL.value,
            "node_ids": ["n2"],
        }
    ]
    out = apply_schema_drift(graph, proposals)
    assert _type_of(out, "n2") == ResearchNodeType.MODEL
    assert _type_of(out, "n1") == ResearchNodeType.CONCEPT
