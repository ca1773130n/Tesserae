"""Retraction — an agent saying "this is wrong" without inventing a replacement.

Roadmap step 10. Two mechanisms, tested together because neither is useful
alone: an edge endpoint that may be an EXISTING node id (so a retraction can
reach a session finding that ``NEVER_ALIGNED_TYPES`` deliberately refuses to
fuse by name), and a ``retracts`` edge whose target is suppressed from every
default read while nothing is deleted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.agent_write import (
    NEVER_ALIGNED_TYPES,
    record_agent_write,
    replay_agent_writes,
    resolve_existing_id,
    validate_write,
)
from tesserae.graph_filters import retracted_ids, superseded_ids, suppressed_ids
from tesserae.llm_extractor import GraphJSONValidationError
from tesserae.research_graph import (
    ALLOWED_EDGE_TYPES,
    EXTRACTABLE_EDGE_TYPES,
    RETRACTION_EDGE_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

AGENT = "test-agent"
PROV = {"agent": AGENT, "commit": "abc1234"}


def _finding_graph() -> ResearchGraph:
    """A session finding — exactly the population an agent most often
    discovers is wrong, and exactly the one name-alignment refuses to touch."""
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id="SessionInsight:budget:aaaaaaaaaaaa",
                name="Retrieval budget must be 8k",
                type=ResearchNodeType.SESSION_INSIGHT,
                description="Measured on the 2026-07 corpus.",
                metadata={"session_id": "s1"},
            ),
            ResearchNode(
                id="Concept:budget:bbbbbbbbbbbb",
                name="Retrieval Budgeting",
                type=ResearchNodeType.CONCEPT,
                description="How much context to spend.",
            ),
        ],
        edges=[],
    )


def _retraction_payload(target_id: str) -> dict:
    return {
        "nodes": [
            {
                "name": "The 8k budget number was measured wrong",
                "type": "Claim",
                "description": "The benchmark double-counted cached chunks.",
            }
        ],
        "edges": [
            {
                "source": "The 8k budget number was measured wrong",
                "target": target_id,
                "type": "retracts",
                "evidence": "Re-ran the benchmark at commit abc1234; the "
                "cache was counted twice.",
            }
        ],
        "provenance": PROV,
    }


# --- the vocabulary ---------------------------------------------------------


def test_retracts_is_in_the_ontology_and_is_not_llm_extractable():
    """Producer/agent-owned for a different reason than the causal layer: a
    retraction SILENCES its target, so an extraction pass misreading "we
    retract our earlier claim" could quietly delete curated knowledge."""
    assert RETRACTION_EDGE_TYPES == frozenset({"retracts"})
    assert RETRACTION_EDGE_TYPES <= ALLOWED_EDGE_TYPES
    assert not (RETRACTION_EDGE_TYPES & EXTRACTABLE_EDGE_TYPES)


def test_agents_may_retract_unlike_the_causal_layer():
    """The whole point: an AGENT saying "this is wrong" is the supported
    path, so ``retracts`` must NOT be in the agent deny set."""
    from tesserae.agent_write import DENIED_EDGE_TYPES

    assert not (RETRACTION_EDGE_TYPES & DENIED_EDGE_TYPES)


def test_every_hand_maintained_list_knows_the_retraction_types():
    """One source of truth, enforced where the code cannot derive it."""
    from tesserae.retrieval.views import VIEWS
    from tesserae.temporal import INVALIDATING_PREDICATES

    assert RETRACTION_EDGE_TYPES <= INVALIDATING_PREDICATES
    assert RETRACTION_EDGE_TYPES <= VIEWS["temporal"]


# --- suppression ------------------------------------------------------------


def test_retracted_ids_are_suppressed_but_distinct_from_superseded():
    graph = _finding_graph()
    graph = ResearchGraph(
        nodes=list(graph.nodes),
        edges=[
            ResearchEdge(
                source="Claim:wrong:cccccccccccc",
                target="SessionInsight:budget:aaaaaaaaaaaa",
                type="retracts",
                evidence="benchmark double-counted",
            )
        ],
    )
    target = "SessionInsight:budget:aaaaaaaaaaaa"
    assert retracted_ids(graph) == {target}
    # A retraction is NOT a supersession — nothing replaced it.
    assert superseded_ids(graph) == set()
    assert suppressed_ids(graph) == {target}


def test_nothing_is_deleted_by_a_retraction():
    """Tombstone, not delete — the CHARTER posture. The node and the
    retraction edge both stay readable."""
    graph = _finding_graph()
    graph = ResearchGraph(
        nodes=list(graph.nodes),
        edges=[
            ResearchEdge(
                source="Concept:budget:bbbbbbbbbbbb",
                target="SessionInsight:budget:aaaaaaaaaaaa",
                type="retracts",
                evidence="wrong",
            )
        ],
    )
    assert any(n.id == "SessionInsight:budget:aaaaaaaaaaaa" for n in graph.nodes)
    assert any(e.type == "retracts" for e in graph.edges)


def test_compile_context_suppresses_a_retracted_node(tmp_path):
    from tesserae.context_compiler import compile_context
    from tesserae.retrieval.hybrid import HashEmbeddingBackend

    base = _finding_graph()
    graph = ResearchGraph(
        nodes=list(base.nodes),
        edges=[
            ResearchEdge(
                source="Concept:budget:bbbbbbbbbbbb",
                target="SessionInsight:budget:aaaaaaaaaaaa",
                type="retracts",
                evidence="the benchmark double-counted cached chunks",
            )
        ],
    )
    seeds = ["SessionInsight:budget:aaaaaaaaaaaa", "Concept:budget:bbbbbbbbbbbb"]
    default = compile_context(
        graph, project_root=None, query="", seeds=seeds,
        backend=HashEmbeddingBackend(),
    )
    assert "SessionInsight:budget:aaaaaaaaaaaa" not in default.selected_nodes

    # ...and include_superseded still reaches it: retraction hides, never deletes.
    opted_in = compile_context(
        graph, project_root=None, query="", seeds=seeds,
        backend=HashEmbeddingBackend(), include_superseded=True,
    )
    assert "SessionInsight:budget:aaaaaaaaaaaa" in opted_in.selected_nodes


# --- id endpoints -----------------------------------------------------------


def test_an_edge_endpoint_may_be_an_existing_node_id():
    graph = _finding_graph()
    target = "SessionInsight:budget:aaaaaaaaaaaa"
    validated = validate_write(_retraction_payload(target), AGENT, graph)
    edge = validated.edges[0]
    assert edge["target"] == target
    assert edge["id_endpoints"] == ["target"]


def test_the_id_endpoint_reaches_what_name_alignment_refuses_to_fuse():
    """THE reason id endpoints exist: a SessionInsight is in
    NEVER_ALIGNED_TYPES, so re-declaring it by name forks a copy and the
    agent would retract the fork instead of the finding."""
    graph = _finding_graph()
    assert ResearchNodeType.SESSION_INSIGHT in NEVER_ALIGNED_TYPES
    assert resolve_existing_id(
        graph, "SessionInsight", "Retrieval budget must be 8k"
    ) is None
    validated = validate_write(
        _retraction_payload("SessionInsight:budget:aaaaaaaaaaaa"), AGENT, graph
    )
    assert validated.edges[0]["target"] == "SessionInsight:budget:aaaaaaaaaaaa"


def test_an_unknown_endpoint_is_refused_loudly():
    graph = _finding_graph()
    with pytest.raises(GraphJSONValidationError) as exc:
        validate_write(_retraction_payload("SessionInsight:nope:999999999999"), AGENT, graph)
    assert "neither one of the nodes in this payload nor an existing node id" in str(exc.value)

    # And with NO graph to resolve against, an id endpoint is equally refused —
    # never silently accepted as a dangling reference.
    with pytest.raises(GraphJSONValidationError):
        validate_write(_retraction_payload("SessionInsight:budget:aaaaaaaaaaaa"), AGENT, None)


def test_a_payload_name_wins_over_a_graph_id():
    """An endpoint resolves against the payload FIRST, so a node named like
    an id cannot be hijacked into pointing somewhere else."""
    graph = _finding_graph()
    collide = "SessionInsight:budget:aaaaaaaaaaaa"
    payload = {
        "nodes": [{"name": collide, "type": "Claim", "description": "a decoy"}],
        "edges": [
            {
                "source": collide,
                "target": collide,
                "type": "retracts",
                "evidence": "self reference through the payload name",
            }
        ],
        "provenance": PROV,
    }
    validated = validate_write(payload, AGENT, graph)
    assert "id_endpoints" not in validated.edges[0]


def test_write_ids_of_pre_existing_writes_are_unchanged():
    """``id_endpoints`` is present ONLY when an endpoint is an id, so every
    write recorded before step 10 still hashes to the same write_id."""
    payload = {
        "nodes": [
            {"name": "A", "type": "Concept", "description": ""},
            {"name": "B", "type": "Concept", "description": ""},
        ],
        "edges": [
            {"source": "A", "target": "B", "type": "references", "evidence": "e"}
        ],
        "provenance": PROV,
    }
    with_graph = validate_write(payload, AGENT, _finding_graph())
    without_graph = validate_write(payload, AGENT, None)
    assert with_graph.write_id == without_graph.write_id
    assert "id_endpoints" not in with_graph.edges[0]


def test_replay_emits_the_retraction_edge_against_the_existing_id(tmp_path):
    graph = _finding_graph()
    target = "SessionInsight:budget:aaaaaaaaaaaa"
    path = tmp_path / "agent-writes.jsonl"
    result = record_agent_write(path, _retraction_payload(target), AGENT, graph=graph)
    assert result["write_id"]

    overlay = replay_agent_writes(path)
    retractions = [e for e in overlay.edges if e.type == "retracts"]
    assert len(retractions) == 1
    assert retractions[0].target == target
    # Provenance travels on the edge, as for every agent-written edge.
    assert retractions[0].metadata["agent_write_id"] == result["write_id"]
    assert retractions[0].metadata["agent_key"] == AGENT
    # The retracted node is NOT re-declared by the overlay — the edge points
    # into the main graph, which is the whole point of an id endpoint.
    assert not any(n.id == target for n in overlay.nodes)


def test_replay_is_deterministic_and_survives_a_hand_edited_typo(tmp_path):
    graph = _finding_graph()
    path = tmp_path / "agent-writes.jsonl"
    record_agent_write(
        path, _retraction_payload("SessionInsight:budget:aaaaaaaaaaaa"), AGENT, graph=graph
    )
    first = replay_agent_writes(path)
    second = replay_agent_writes(path)
    assert [e.target for e in first.edges] == [e.target for e in second.edges]

    # A record whose NAME endpoint was hand-edited to something undeclared is
    # still dropped with a warning rather than becoming a dangling id edge.
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    record["write_id"] = "0000000000000000"
    record["edges"][0]["id_endpoints"] = []
    record["edges"][0]["source"] = "a name that was never declared"
    path.write_text(
        path.read_text(encoding="utf-8") + json.dumps(record) + "\n", encoding="utf-8"
    )
    replayed = replay_agent_writes(path)
    assert len([e for e in replayed.edges if e.type == "retracts"]) == 1


def test_retraction_survives_a_compile_and_hides_the_finding(tmp_path):
    """End to end: the retraction lands in graph.json as a producer-owned
    edge and the MCP read surface stops serving the retracted node."""
    from tesserae.mcp_server import LLMWikiMCPServer
    from tesserae.project import ProjectWiki, load_graph_file

    project = tmp_path / "proj"
    project.mkdir()
    (project / "note.md").write_text(
        "# Budgets\n\nRetrieval budget must be 8k for the corpus.\n", encoding="utf-8"
    )
    wiki = ProjectWiki.init(project, name="demo", sources=["note.md"])
    wiki.compile()

    graph = load_graph_file(wiki.paths.graph)
    victim = next(
        n for n in graph.nodes
        if n.type is ResearchNodeType.SOURCE_DOCUMENT
    )

    record_agent_write(
        wiki.paths.agent_writes,
        {
            "nodes": [
                {
                    "name": "The budget note is wrong",
                    "type": "Claim",
                    "description": "Superseded measurement methodology.",
                }
            ],
            "edges": [
                {
                    "source": "The budget note is wrong",
                    "target": victim.id,
                    "type": "retracts",
                    "evidence": "re-measured at commit abc1234",
                }
            ],
            "provenance": PROV,
        },
        AGENT,
        graph=graph,
    )
    wiki.compile(changed_only=True)

    recompiled = load_graph_file(wiki.paths.graph)
    assert any(
        e.type == "retracts" and e.target == victim.id for e in recompiled.edges
    ), "the retraction must survive recompilation like every agent write"
    assert victim.id in retracted_ids(recompiled)

    server = LLMWikiMCPServer(default_graph_path=wiki.paths.graph)
    # Opt-in FIRST: it proves the query actually finds the node, so the
    # default-read assertion below cannot pass vacuously on an empty result.
    with_opt_in = server.call_tool(
        "search_nodes",
        {"query": "Budgets", "limit": 50, "include_superseded": True},
    )
    assert victim.id in {h["id"] for h in with_opt_in["nodes"]}, (
        "retraction hides, never deletes — include_superseded must still reach it"
    )
    hits = server.call_tool("search_nodes", {"query": "Budgets", "limit": 50})
    assert victim.id not in {h["id"] for h in hits["nodes"]}
