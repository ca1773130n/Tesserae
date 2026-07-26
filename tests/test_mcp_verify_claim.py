"""verify_claim: deterministic, exact-match triple verification.

Measured motivation: of 15,284 edges in the real project graph, 73% are
structural "X appeared near Y" membership edges and only 8% carry reasoning.
Every existing read surface answers with a RANKED LIST, which an evaluator
will happily agree with. ``verify_claim`` answers with a verdict + one
deciding edge + a citation, or it refuses. These tests pin the refusals as
hard as the successes.
"""

import json


from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.verify import verify_claim


def _node(node_id, name, node_type, **kw):
    return ResearchNode(id=node_id, name=name, type=node_type, **kw)


def _base_graph(source_path=None):
    """Paper --supports_claim--> Claim, Claim --evidenced_by--> EvidenceSpan."""
    paper = _node("Paper:p1", "Attention Is All You Need", ResearchNodeType.PAPER)
    claim = _node(
        "Claim:c1",
        "Transformers outperform recurrent models",
        ResearchNodeType.CLAIM,
        source_path=source_path,
    )
    span = _node(
        "EvidenceSpan:e1",
        "span-1",
        ResearchNodeType.EVIDENCE_SPAN,
        description="Transformers outperform recurrent models on WMT14.",
        source_path=source_path,
    )
    other = _node("Claim:c2", "Recurrence is required", ResearchNodeType.CLAIM)
    return ResearchGraph(
        nodes=[paper, claim, span, other],
        edges=[
            ResearchEdge(
                source=paper.id,
                target=claim.id,
                type="supports_claim",
                evidence="Section 4 reports the BLEU improvement.",
            ),
            ResearchEdge(source=claim.id, target=span.id, type="evidenced_by"),
        ],
    )


def _write_project(tmp_path, graph):
    tesserae_dir = tmp_path / ".tesserae"
    tesserae_dir.mkdir(parents=True, exist_ok=True)
    graph_path = tesserae_dir / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return graph_path


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def test_verify_supported_cites_edge_and_span():
    result = verify_claim(
        _base_graph(),
        subject="Paper:p1",
        predicate="supports_claim",
        obj="Claim:c1",
    )
    assert result["verdict"] == "SUPPORTED"
    assert result["reason"] == "triple_present"
    assert result["triple"] == {
        "subject_id": "Paper:p1",
        "predicate": "supports_claim",
        "object_id": "Claim:c1",
    }
    assert result["citation"]["edge"] == {
        "source": "Paper:p1",
        "type": "supports_claim",
        "target": "Claim:c1",
    }
    assert result["citation"]["edge_evidence"].startswith("Section 4")
    assert result["citation"]["evidence_span"]["node_id"] == "EvidenceSpan:e1"
    assert "WMT14" in result["citation"]["evidence_span"]["text"]


def test_verify_resolves_by_exact_name_and_alias():
    graph = _base_graph()
    graph = ResearchGraph(
        nodes=[
            n
            if n.id != "Claim:c1"
            else ResearchNode(
                id=n.id,
                name=n.name,
                type=n.type,
                aliases=["transformer superiority"],
                description=n.description,
            )
            for n in graph.nodes
        ],
        edges=list(graph.edges),
    )
    by_name = verify_claim(
        graph,
        subject="attention is all you need",
        predicate="supports_claim",
        obj="Transformers Outperform Recurrent Models",
    )
    by_alias = verify_claim(
        graph,
        subject="Paper:p1",
        predicate="supports_claim",
        obj="TRANSFORMER SUPERIORITY",
    )
    assert by_name["verdict"] == "SUPPORTED"
    assert by_alias["verdict"] == "SUPPORTED"


def test_verify_absent_triple_is_not_found():
    result = verify_claim(
        _base_graph(),
        subject="Paper:p1",
        predicate="supports_claim",
        obj="Claim:c2",
    )
    assert result["verdict"] == "NOT_FOUND"
    assert result["reason"] == "triple_absent"
    # Anti-search regression guard: a verifier must never degrade into a
    # ranked list an evaluator can rubber-stamp.
    assert set(result) == {"verdict", "reason", "triple", "citation", "provenance"}
    for banned in ("nodes", "results", "matches", "facts", "candidates", "score"):
        assert banned not in result


def test_verify_superseded_object_is_contradicted():
    winner = _node(
        "SessionInsight:winner", "Current insight", ResearchNodeType.SESSION_INSIGHT
    )
    loser = _node(
        "SessionInsight:loser", "Stale insight", ResearchNodeType.SESSION_INSIGHT
    )
    session = _node("Session:s1", "session one", ResearchNodeType.SESSION)
    graph = ResearchGraph(
        nodes=[winner, loser, session],
        edges=[
            ResearchEdge(source=winner.id, target=loser.id, type="supersedes"),
            ResearchEdge(
                source=loser.id,
                target=session.id,
                type="derived_from_session",
                evidence="loser came from session one",
            ),
        ],
    )
    result = verify_claim(
        graph,
        subject=loser.id,
        predicate="derived_from_session",
        obj=session.id,
    )
    assert result["verdict"] == "CONTRADICTED"
    assert result["reason"] == "superseded"
    assert result["citation"]["edge"] == {
        "source": winner.id,
        "type": "supersedes",
        "target": loser.id,
    }


def test_verify_contradicts_claim_edge_wins():
    """Dead on today's graph (contradicts_claim == 0) — pinned so it stays live."""
    a = _node("Claim:a", "Claim A", ResearchNodeType.CLAIM)
    b = _node("Claim:b", "Claim B", ResearchNodeType.CLAIM)
    p = _node("Paper:p", "Some Paper", ResearchNodeType.PAPER)
    graph = ResearchGraph(
        nodes=[a, b, p],
        edges=[
            ResearchEdge(
                source=a.id,
                target=b.id,
                type="contradicts_claim",
                evidence="A refutes B",
            )
        ],
    )
    result = verify_claim(
        graph, subject=p.id, predicate="supports_claim", obj=b.id
    )
    assert result["verdict"] == "CONTRADICTED"
    assert result["reason"] == "contradicted_claim"
    assert result["citation"]["edge"]["type"] == "contradicts_claim"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_verify_ambiguous_name_refuses():
    """15 of 5,144 names in the real graph collide after casefolding.

    ``mcp_server._find_node`` builds ``{name.casefold(): node}`` and silently
    keeps the LAST one. A verifier must refuse instead.
    """
    one = _node("Concept:one", "Attention", ResearchNodeType.CONCEPT)
    two = _node("Technique:two", "attention", ResearchNodeType.TECHNICAL_TERM)
    target = _node("Claim:t", "Some claim", ResearchNodeType.CLAIM)
    graph = ResearchGraph(nodes=[one, two, target], edges=[])
    result = verify_claim(
        graph, subject="Attention", predicate="supports_claim", obj=target.id
    )
    assert result["verdict"] == "NOT_FOUND"
    assert result["reason"] == "ambiguous_subject"
    assert result["matched_nodes"] == sorted([one.id, two.id])


def test_verify_unknown_predicate_lists_observed():
    graph = _base_graph()
    result = verify_claim(
        graph, subject="Paper:p1", predicate="used_by", obj="Claim:c1"
    )
    assert result["verdict"] == "NOT_FOUND"
    assert result["reason"] == "predicate_not_in_ontology"
    assert result["observed_predicates"] == ["supports_claim"]


def test_verify_unresolved_subject_is_distinct_from_absent_triple():
    graph = _base_graph()
    result = verify_claim(
        graph, subject="No Such Node", predicate="supports_claim", obj="Claim:c1"
    )
    assert result["reason"] == "subject_unresolved"
    assert result["triple"]["subject_id"] is None


# ---------------------------------------------------------------------------
# NL convenience
# ---------------------------------------------------------------------------


def test_verify_nl_claim_with_unique_match_resolves():
    graph = _base_graph()
    result = verify_claim(
        graph,
        claim=(
            "Attention Is All You Need supports_claim "
            "Transformers outperform recurrent models"
        ),
    )
    assert result["verdict"] == "SUPPORTED"
    assert result["triple"]["subject_id"] == "Paper:p1"
    assert result["triple"]["object_id"] == "Claim:c1"


def test_verify_nl_claim_without_unique_match_is_not_found():
    graph = _base_graph()
    result = verify_claim(
        graph,
        claim=(
            "Attention Is All You Need supports_claim "
            "Transformers outperform recurrent models and Recurrence is required"
        ),
    )
    assert result["verdict"] == "NOT_FOUND"
    assert result["reason"] == "nl_not_resolvable"
    assert result["triple"] == {
        "subject_id": None,
        "predicate": None,
        "object_id": None,
    }


# ---------------------------------------------------------------------------
# Provenance / re-grounding
# ---------------------------------------------------------------------------


def test_verify_provenance_class_document_span_vs_model_assertion(tmp_path):
    doc = tmp_path / "paper.md"
    doc.write_text(
        "# Notes\n\nTransformers outperform recurrent models on WMT14.\n",
        encoding="utf-8",
    )
    graph = _base_graph(source_path=str(doc))
    result = verify_claim(
        graph,
        subject="Paper:p1",
        predicate="supports_claim",
        obj="Claim:c1",
        project_root=tmp_path,
    )
    assert result["provenance"]["class"] == "document_span"
    assert result["provenance"]["regrounded"] is True

    model_claim = _node(
        "Claim:m",
        "A model said so",
        ResearchNodeType.CLAIM,
        metadata={
            "extractor": "session-llm",
            "confidence": 0.4,
            "confidence_rationale": "weak signal",
        },
    )
    paper = _node("Paper:p1", "Attention Is All You Need", ResearchNodeType.PAPER)
    model_graph = ResearchGraph(
        nodes=[paper, model_claim],
        edges=[
            ResearchEdge(
                source=paper.id, target=model_claim.id, type="supports_claim"
            )
        ],
    )
    model_result = verify_claim(
        model_graph,
        subject=paper.id,
        predicate="supports_claim",
        obj=model_claim.id,
        project_root=tmp_path,
    )
    assert model_result["verdict"] == "SUPPORTED"
    assert model_result["provenance"]["class"] == "model_assertion"
    assert model_result["provenance"]["extractor"] == "session-llm"
    assert model_result["provenance"]["confidence"] == 0.4
    assert model_result["provenance"]["regrounded"] is None


def test_verify_regrounding_miss_does_not_flip_verdict(tmp_path):
    doc = tmp_path / "paper.md"
    doc.write_text("# Notes\n\nSomething else entirely.\n", encoding="utf-8")
    graph = _base_graph(source_path=str(doc))
    result = verify_claim(
        graph,
        subject="Paper:p1",
        predicate="supports_claim",
        obj="Claim:c1",
        project_root=tmp_path,
    )
    assert result["verdict"] == "SUPPORTED"
    assert result["provenance"]["regrounded"] is False


def test_verify_regrounding_missing_file_is_null(tmp_path):
    graph = _base_graph(source_path=str(tmp_path / "gone.md"))
    result = verify_claim(
        graph,
        subject="Paper:p1",
        predicate="supports_claim",
        obj="Claim:c1",
        project_root=tmp_path,
    )
    assert result["provenance"]["regrounded"] is None


def test_verify_regrounding_can_be_switched_off(tmp_path):
    doc = tmp_path / "paper.md"
    doc.write_text("Transformers outperform recurrent models on WMT14.\n", "utf-8")
    graph = _base_graph(source_path=str(doc))
    off = verify_claim(
        graph,
        subject="Paper:p1",
        predicate="supports_claim",
        obj="Claim:c1",
        project_root=tmp_path,
        reground=False,
    )
    assert off["provenance"]["regrounded"] is None


def test_verify_regrounding_refuses_paths_outside_the_project_root(tmp_path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text(
        "Transformers outperform recurrent models on WMT14.\n", encoding="utf-8"
    )
    root = tmp_path / "proj"
    root.mkdir()
    graph = _base_graph(source_path=str(outside))
    result = verify_claim(
        graph,
        subject="Paper:p1",
        predicate="supports_claim",
        obj="Claim:c1",
        project_root=root,
    )
    assert result["provenance"]["regrounded"] is None


# ---------------------------------------------------------------------------
# Determinism + MCP surface
# ---------------------------------------------------------------------------


def test_verify_is_byte_deterministic(tmp_path):
    doc = tmp_path / "paper.md"
    doc.write_text("Transformers outperform recurrent models on WMT14.\n", "utf-8")
    graph = _base_graph(source_path=str(doc))
    kwargs = dict(
        subject="Paper:p1",
        predicate="supports_claim",
        obj="Claim:c1",
        project_root=tmp_path,
    )
    first = json.dumps(verify_claim(graph, **kwargs), sort_keys=True)
    second = json.dumps(verify_claim(graph, **kwargs), sort_keys=True)
    assert first == second


def test_verify_claim_listed_in_list_tools():
    tools = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}
    assert "verify_claim" in tools
    schema = tools["verify_claim"]["inputSchema"]["properties"]
    assert {"subject", "predicate", "object", "claim", "reground"} <= set(schema)
    description = tools["verify_claim"]["description"]
    assert "NOT_FOUND" in description
    assert "not a refutation" in description.lower()


def test_verify_claim_dispatches_through_call_tool(tmp_path):
    doc = tmp_path / "paper.md"
    doc.write_text("Transformers outperform recurrent models on WMT14.\n", "utf-8")
    graph_path = _write_project(tmp_path, _base_graph(source_path=str(doc)))
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    result = server.call_tool(
        "verify_claim",
        {"subject": "Paper:p1", "predicate": "supports_claim", "object": "Claim:c1"},
    )
    assert result["verdict"] == "SUPPORTED"
    assert result["provenance"]["regrounded"] is True
