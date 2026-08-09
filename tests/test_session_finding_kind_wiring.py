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
