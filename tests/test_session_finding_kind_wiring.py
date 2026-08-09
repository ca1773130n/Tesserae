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


# ---------------------------------------------------------------------------
# The PUBLISHED contract, not the internal table
#
# The tests above assert the Python dicts a caller never sees. An MCP client is
# schema-driven: it reads ``inputSchema`` and can only send what the enum
# allows. A kind the server maps but the schema omits is unreachable through
# the published API — dead on arrival, and silently so, which is the same
# defect (a kind wired into six tables and missing from the seventh) that this
# file exists to catch. Same for the ask planner: its catalog string IS the
# prompt, so a kind absent there is a kind the planner is instructed never to
# emit, and ``_FINDING_TYPES`` mapping it changes nothing.
# ---------------------------------------------------------------------------


def _tool_schema(name: str) -> dict:
    from tesserae.mcp_server import LLMWikiMCPServer

    return next(t for t in LLMWikiMCPServer().list_tools() if t["name"] == name)


def test_find_session_findings_advertises_every_kind_it_accepts():
    tool = _tool_schema("find_session_findings")
    enum = set(tool["inputSchema"]["properties"]["kinds"]["items"]["enum"])
    missing = sorted(set(ALLOWED_FINDING_KINDS) - enum)
    assert not missing, f"kinds no schema-driven client can request: {missing}"


def test_fresh_insights_advertises_every_kind_it_accepts():
    tool = _tool_schema("fresh_insights")
    enum = set(tool["inputSchema"]["properties"]["kind"]["enum"])
    missing = sorted(set(ALLOWED_FINDING_KINDS) - enum)
    assert not missing, f"kinds no schema-driven client can request: {missing}"


def test_the_advertised_kinds_are_exactly_the_accepted_ones():
    """Both directions. An enum that offers a kind the server does not map is
    as broken as one that hides a kind it does — the client's call just comes
    back empty with no error."""
    for name, prop, holder in (
        ("find_session_findings", "kinds", "items"),
        ("fresh_insights", "kind", None),
    ):
        schema = _tool_schema(name)["inputSchema"]["properties"][prop]
        enum = set((schema[holder] if holder else schema)["enum"])
        assert enum == set(ALLOWED_FINDING_KINDS), name


def test_every_kind_is_named_in_the_tool_descriptions_an_agent_reads():
    """The description is what an agent reads to decide whether the tool can
    answer its question. A tool whose prose lists six kinds will not be reached
    for the seventh, whatever the enum says."""
    for name in ("find_session_findings", "fresh_insights"):
        description = _tool_schema(name)["description"]
        missing = [k for k in ALLOWED_FINDING_KINDS if k not in description]
        assert not missing, f"{name} description omits {missing}"


def test_the_ask_planner_may_emit_every_kind():
    """``_CATALOG`` is interpolated straight into the planner's system prompt,
    so its ``kind`` union is the set of kinds the planner is ALLOWED to ask
    for. ``_FINDING_TYPES`` mapping a kind the planner is told not to emit is
    an executor branch that never runs."""
    from tesserae.ask_planner import _CATALOG

    (sig,) = [s for name, s, _d in _CATALOG if name == "session_findings"]
    missing = [k for k in ALLOWED_FINDING_KINDS if k not in sig]
    assert not missing, f"kinds the planner cannot plan: {missing} (sig={sig})"


def test_the_planner_system_prompt_carries_every_kind():
    """Through the real prompt, not the catalog it is built from."""
    from tesserae.ask_planner import _PLANNER_SYSTEM

    missing = [k for k in ALLOWED_FINDING_KINDS if k not in _PLANNER_SYSTEM]
    assert not missing, f"kinds absent from the planner prompt: {missing}"


def test_the_extractor_prompt_describes_every_kind_it_allows():
    """The last hand-maintained list: a kind in ``ALLOWED_FINDING_KINDS`` that
    the prompt never names is a kind the model is never told to emit, so it is
    allowed and unreachable — the same shape as the schema gap above."""
    from tesserae.session_graph_llm import _PROMPT_SYSTEM

    missing = [k for k in ALLOWED_FINDING_KINDS if f'"{k}"' not in _PROMPT_SYSTEM]
    assert not missing, f"kinds the extractor prompt never describes: {missing}"


def test_every_finding_node_type_has_a_vault_callout():
    """The last per-type table that cannot be derived — a callout carries a
    human label and an Obsidian callout name, neither of which follows from the
    type. A missing entry does not raise; the finding just renders as an
    untyped blockquote in the vault."""
    from tesserae.markdown_projection import _CALLOUT_BY_NODE_TYPE
    from tesserae.research_graph import SESSION_FINDING_TYPES

    missing = sorted(
        t.value for t in SESSION_FINDING_TYPES if t not in _CALLOUT_BY_NODE_TYPE
    )
    assert not missing, f"finding types with no vault callout: {missing}"


def test_the_taxonomy_has_exactly_one_source_of_truth():
    """Every derived table is the SAME object or the same content as
    ``research_graph.SESSION_FINDING_KIND_TO_TYPE``. This is what makes the
    tests above unable to drift back into agreeing-with-a-copy."""
    from tesserae.ask_planner import _FINDING_TYPES
    from tesserae.mcp_server import LLMWikiMCPServer
    from tesserae.research_graph import (
        SESSION_FINDING_KIND_TO_TYPE,
        SESSION_FINDING_TYPE_VALUES,
    )
    from tesserae.session_graph import _KIND_TO_NODE_TYPE

    canonical = {k: t.value for k, t in SESSION_FINDING_KIND_TO_TYPE.items()}
    assert _KIND_TO_NODE_TYPE == SESSION_FINDING_KIND_TO_TYPE
    assert _FINDING_TYPES == canonical
    assert LLMWikiMCPServer._KIND_TO_TYPE == canonical
    assert set(LLMWikiMCPServer._SESSION_FINDING_TYPES) == set(
        SESSION_FINDING_TYPE_VALUES
    )
    assert tuple(ALLOWED_FINDING_KINDS) == tuple(SESSION_FINDING_KIND_TO_TYPE)
