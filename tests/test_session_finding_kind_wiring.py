"""Every finding kind must be wired end to end, because every gap fails SILENTLY.

A ``kind`` the LLM is allowed to emit passes through eight hand-maintained
tables before it reaches a reader. Not one of them raises when a kind is
missing:

* ``session_graph._KIND_TO_NODE_TYPE`` — an unmapped kind is ``continue``d at
  the mint site, so the finding vanishes with no log and no error;
* ``federation._SEMANTIC_TYPE_VALUES`` — a missing type simply never federates;
* ``wiki_projector`` / ``ask_planner`` / ``mcp_server`` / ``activity_summary``
  — a missing type is quietly excluded from the surface it owns.

So the test is TOTALITY, not one example: assert the wiring holds for *every*
allowed kind. That is what makes it catch the seventh kind after this one, and
what makes it fail today for ``failure`` rather than passing on a technicality.
"""

from __future__ import annotations

from pathlib import Path

from tesserae.research_graph import ResearchNodeType
from tesserae.session_graph import _KIND_TO_NODE_TYPE
from tesserae.session_graph_llm import ALLOWED_FINDING_KINDS


def _node_type_values() -> set:
    return {_KIND_TO_NODE_TYPE[k].value for k in ALLOWED_FINDING_KINDS}


def test_every_allowed_kind_mints_a_node_type():
    missing = [k for k in ALLOWED_FINDING_KINDS if k not in _KIND_TO_NODE_TYPE]
    assert not missing, (
        f"kinds the extractor accepts but the compiler drops silently: {missing}"
    )


def test_failure_is_one_of_the_allowed_kinds():
    assert "failure" in ALLOWED_FINDING_KINDS


def test_a_failure_finding_mints_a_session_failure_node_type():
    assert _KIND_TO_NODE_TYPE["failure"] is ResearchNodeType.SESSION_FAILURE


def test_every_finding_node_type_federates():
    from tesserae.federation import _SEMANTIC_TYPE_VALUES

    missing = sorted(_node_type_values() - set(_SEMANTIC_TYPE_VALUES))
    assert not missing, f"finding types that would never publish: {missing}"


def test_every_finding_node_type_is_a_public_wiki_bucket():
    from tesserae.research_graph import ResearchNode
    from tesserae.wiki_projector import is_session_finding_node

    for value in sorted(_node_type_values()):
        node = ResearchNode(id=f"{value}:x", name="x", type=ResearchNodeType(value))
        assert is_session_finding_node(node), f"{value} is not routed to /sessions/"


def test_every_finding_node_type_is_reachable_from_the_ask_planner():
    from tesserae.ask_planner import _FINDING_TYPES

    missing = sorted(_node_type_values() - set(_FINDING_TYPES.values()))
    assert not missing, f"finding types ask cannot plan retrieval over: {missing}"


def test_every_finding_node_type_is_exposed_over_mcp():
    from tesserae.mcp_server import LLMWikiMCPServer

    missing = sorted(_node_type_values() - set(LLMWikiMCPServer._SESSION_FINDING_TYPES))
    assert not missing, f"finding types find_session_findings cannot return: {missing}"
    missing_kinds = [
        k for k in ALLOWED_FINDING_KINDS if k not in LLMWikiMCPServer._KIND_TO_TYPE
    ]
    assert not missing_kinds, f"kinds the MCP kind filter ignores: {missing_kinds}"


def test_every_finding_node_type_routes_to_the_session_feedback_extractor():
    from tesserae.extraction_feedback import _SESSION_TYPES

    missing = sorted(_node_type_values() - set(_SESSION_TYPES))
    assert not missing, f"finding types misrouted to doc_graph feedback: {missing}"


def test_no_finding_kind_is_offered_to_the_model_as_a_citable_document():
    """The EIGHTH table, and the one the seventh kind was missed in.

    ``_build_doc_id_context`` hands the model a list of node ids it is allowed
    to cite as references. A session finding is not a document, and offering one
    is silent in the worst direction: the model cites a ``SessionFailure`` as
    though a document said so. The exclusion is asserted for every finding type
    rather than for the six that happened to be listed."""
    from tesserae.research_graph import ResearchGraph, ResearchNode
    from tesserae.session_graph import SessionGraphExtractor

    doc_graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id=f"{value}:x{i}",
                name=f"finding {i}",
                type=ResearchNodeType(value),
                description="a finding, not a document",
            )
            for i, value in enumerate(sorted(_node_type_values()))
        ]
        + [
            ResearchNode(
                id="Paper:real",
                name="a real document",
                type=ResearchNodeType.PAPER,
                description="a document",
            )
        ],
        edges=[],
    )
    extractor = SessionGraphExtractor(
        project_root=Path("/tmp/demo"),
        cache_dir=Path("/tmp/demo/.tesserae/session_findings"),
        doc_graph=doc_graph,
        sessions=[],
    )
    offered = {node_id for node_id, _name in extractor._build_doc_id_context()}
    leaked = sorted(i for i in offered if not i.startswith("Paper:"))
    assert not leaked, f"finding types offered to the model as citable docs: {leaked}"
    assert "Paper:real" in offered, "a real document must still be offered"
