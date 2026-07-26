"""Adversarial tests for ``verify_claim`` v2.

Every test here corresponds to a hole that was actually reproduced against v1 or
to a refusal added pre-emptively. The four load-bearing ones are named in the
module docstring of ``tesserae.verify``:

1. an agent-written edge returning SUPPORTED with a document_span citation
2. a ``contradicts_claim`` between unrelated nodes flipping an uninvolved triple
3. ``"It is not true that X supports_claim Y"`` returning SUPPORTED
4. ``supersedes`` on either endpoint returning CONTRADICTED for a true triple
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from tesserae.agent_write import align_overlay, record_agent_write, replay_agent_writes
from tesserae.project import merge_graphs
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.verify import PROVENANCE_CLASSES, verify_claim

_CLASS_RANK = {name: rank for rank, name in enumerate(PROVENANCE_CLASSES)}

REAL_GRAPH = Path(__file__).resolve().parents[1] / ".tesserae" / "graph.json"


# ---------------------------------------------------------------------------
# Fixture helpers — tiny, explicit graphs. No .tesserae is ever read or written.
# ---------------------------------------------------------------------------


def node(nid, name, ntype=ResearchNodeType.CLAIM, *, description="", path=None, meta=None):
    return ResearchNode(
        id=nid,
        name=name,
        type=ntype,
        description=description,
        source_path=path,
        metadata=dict(meta or {}),
    )


def edge(src, etype, tgt, evidence=None, meta=None):
    return ResearchEdge(
        source=src, target=tgt, type=etype, evidence=evidence, metadata=dict(meta or {})
    )


SENTENCE = "Widget throughput doubled on the held-out split."


def evidenced_graph():
    """P -supports_claim-> Q with the co-minted span the extractor produces."""
    return ResearchGraph(
        nodes=[
            node("P:p", "Alpha", ResearchNodeType.PAPER),
            node("C:q", "Beta"),
            node(
                "E:s1",
                "span-1",
                ResearchNodeType.EVIDENCE_SPAN,
                description=SENTENCE,
                path="docs/alpha.md",
            ),
        ],
        edges=[
            edge("P:p", "supports_claim", "C:q", SENTENCE),
            edge("C:q", "evidenced_by", "E:s1", SENTENCE),
        ],
    )


# ---------------------------------------------------------------------------
# INVARIANT 1 — no NL surface
# ---------------------------------------------------------------------------


def test_no_nl_surface():
    """ATTACK 3. v1 read prose and returned SUPPORTED for its own negation."""
    graph = evidenced_graph()
    for phrase in (
        "Alpha supports_claim Beta",
        "It is not true that Alpha supports_claim Beta",
        "Alpha does not supports_claim Beta",
    ):
        with pytest.raises(TypeError):
            verify_claim(graph, claim=phrase)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        verify_claim(graph, subject="Alpha", predicate="supports_claim")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# INVARIANT 2 — locality
# ---------------------------------------------------------------------------


def test_supersedes_on_endpoint_does_not_refute():
    """ATTACK 4. v1 returned CONTRADICTED/superseded for this exact shape."""
    graph = evidenced_graph()
    graph = ResearchGraph(
        nodes=graph.nodes + [node("C:r", "Gamma")],
        edges=graph.edges + [edge("C:r", "supersedes", "C:q", "newer finding")],
    )
    out = verify_claim(graph, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "SUPPORTED"
    assert out["reason"] == "edge_evidenced_by_document_span"
    # supersedes is reported, never adjudicated — and only when it is pair-local.
    assert out["advisory"]["superseded_endpoints"] == []

    pair = verify_claim(graph, subject="C:r", predicate="supersedes", obj="C:q")
    assert pair["advisory"]["superseded_endpoints"] == ["C:q"]


def test_supersedes_triple_is_not_self_contradicting():
    """All 60 real supersedes edges returned CONTRADICTED verifying themselves."""
    graph = evidenced_graph()
    graph = ResearchGraph(
        nodes=graph.nodes + [node("C:r", "Gamma")],
        edges=graph.edges + [edge("C:r", "supersedes", "C:q", "newer finding")],
    )
    out = verify_claim(graph, subject="C:r", predicate="supersedes", obj="C:q")
    assert out["verdict"] == "PRESENT_UNEVIDENCED"
    assert out["verdict"] != "CONTRADICTED"


def test_contradiction_requires_both_endpoints():
    """ATTACK 2. v1 round-1 matched contradicts_claim by TARGET alone."""
    graph = evidenced_graph()
    graph = ResearchGraph(
        nodes=graph.nodes + [node("C:x", "Unrelated")],
        edges=[e for e in graph.edges if e.type != "supports_claim"]
        + [edge("C:x", "contradicts_claim", "C:q", SENTENCE)],
    )
    out = verify_claim(graph, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "ABSENT"
    assert out["reason"] == "triple_absent"


def test_verdict_is_local():
    """Property: edges not incident on BOTH endpoints never move the verdict."""
    base = evidenced_graph()
    baseline = verify_claim(base, subject="P:p", predicate="supports_claim", obj="C:q")
    hostile = [
        edge("C:x", "contradicts_claim", "C:q", SENTENCE),
        edge("C:x", "supersedes", "C:q", SENTENCE),
        edge("C:x", "supersedes", "P:p", SENTENCE),
        edge("C:q", "resolved_by", "C:x", SENTENCE),
    ]
    for injected in hostile:
        graph = ResearchGraph(
            nodes=base.nodes + [node("C:x", "Unrelated")],
            edges=base.edges + [injected],
        )
        out = verify_claim(graph, subject="P:p", predicate="supports_claim", obj="C:q")
        assert out["verdict"] == baseline["verdict"], injected.type
        assert out["reason"] == baseline["reason"], injected.type


# ---------------------------------------------------------------------------
# INVARIANT 3 — monotone provenance / agent writes
# ---------------------------------------------------------------------------


def _agent_forgery_graph(tmp_path: Path):
    """Replay the real agent-write pipeline: overlay -> align -> merge.

    Cross-type dedup fuses the agent's ``Claim: Widget`` into the curated
    ``Paper: Widget`` and drops the loser node's metadata wholesale, so the
    node-level ``agent_write_id`` marker is ERASED. The agent supplies the
    verbatim text of a real span as its edge evidence, so without the edge stamp
    the write is indistinguishable from a curated, document-backed edge.
    """
    curated = ResearchGraph(
        nodes=[
            node("P:widget", "Widget", ResearchNodeType.PAPER),
            node("P:ledger", "Ledger", ResearchNodeType.PAPER),
            node(
                "E:s1",
                "span-1",
                ResearchNodeType.EVIDENCE_SPAN,
                description=SENTENCE,
                path="docs/alpha.md",
            ),
        ],
        edges=[edge("P:ledger", "evidenced_by", "E:s1", SENTENCE)],
    )
    log = tmp_path / "agent_writes.jsonl"
    record_agent_write(
        log,
        {
            "nodes": [
                {"name": "Widget", "type": "Claim", "description": "agent copy"},
                {"name": "Ledger", "type": "Claim"},
            ],
            "edges": [
                {
                    "source": "Widget",
                    "target": "Ledger",
                    "type": "supports_claim",
                    # The agent supplies the VERBATIM text of a real span. Under
                    # v1 this is what bought it a document_span citation.
                    "evidence": SENTENCE,
                }
            ],
            "provenance": {"agent": "prober", "url": "https://example.invalid/x"},
        },
        "prober",
        graph=curated,
    )
    overlay = align_overlay(replay_agent_writes(log), curated)
    return merge_graphs([curated, overlay])


def test_agent_edge_cannot_present_as_document_span(tmp_path):
    """ATTACK 1. v1 returned SUPPORTED / document_span citing a real document."""
    merged = _agent_forgery_graph(tmp_path)
    out = verify_claim(
        merged, subject="P:widget", predicate="supports_claim", obj="P:ledger"
    )
    assert out["verdict"] == "PRESENT_UNEVIDENCED"
    assert out["reason"] == "agent_assertion"
    assert out["provenance"]["class"] == "agent_assertion"
    assert out["provenance"]["agent_marker_on"] == "edge"
    assert out["provenance"]["agent_key"] == "prober"


def test_agent_edge_stamp_survives_merge(tmp_path):
    """The stamp is the only durable, unforgeable agent marker."""
    merged = _agent_forgery_graph(tmp_path)
    stamped = [
        e
        for e in merged.edges
        if e.type == "supports_claim" and e.metadata.get("agent_write_id")
    ]
    assert len(stamped) == 1
    assert stamped[0].metadata["agent_key"] == "prober"
    # ...and the node-level marker really is gone, which is why it is needed.
    assert not [n for n in merged.nodes if n.metadata.get("agent_write_id")]


def test_provenance_is_monotone_under_agent_write():
    """Property: an agent re-assertion can only ever WEAKEN the class."""
    base = evidenced_graph()
    before = verify_claim(base, subject="P:p", predicate="supports_claim", obj="C:q")
    for surface in ("edge", "subject", "object", "span"):
        nodes = [copy.deepcopy(n) for n in base.nodes]
        edges = [copy.deepcopy(e) for e in base.edges]
        stamp = {"agent_write_id": "w1", "agent_key": "prober"}
        if surface == "edge":
            edges = [
                edge(e.source, e.type, e.target, e.evidence, stamp)
                if e.type == "supports_claim"
                else e
                for e in edges
            ]
        else:
            target = {"subject": "P:p", "object": "C:q", "span": "E:s1"}[surface]
            nodes = [
                node(n.id, n.name, n.type, description=n.description, path=n.source_path, meta=stamp)
                if n.id == target
                else n
                for n in nodes
            ]
        out = verify_claim(
            ResearchGraph(nodes=nodes, edges=edges),
            subject="P:p",
            predicate="supports_claim",
            obj="C:q",
        )
        assert _CLASS_RANK[out["provenance"]["class"]] <= _CLASS_RANK[
            before["provenance"]["class"]
        ], surface
        assert out["verdict"] == "PRESENT_UNEVIDENCED", surface


def test_model_asserted_edge_is_not_supported():
    """A model asserting a LINK is not evidence, however its text matches."""
    base = evidenced_graph()
    edges = [
        edge(e.source, e.type, e.target, e.evidence, {"extractor": "contrast-pass"})
        if e.type == "supports_claim"
        else e
        for e in base.edges
    ]
    out = verify_claim(
        ResearchGraph(nodes=base.nodes, edges=edges),
        subject="P:p",
        predicate="supports_claim",
        obj="C:q",
    )
    assert out["verdict"] == "PRESENT_UNEVIDENCED"
    assert out["reason"] == "model_assertion"


# ---------------------------------------------------------------------------
# Evidence axis
# ---------------------------------------------------------------------------


def test_unevidenced_edge_is_not_supported():
    base = evidenced_graph()
    edges = [
        edge("P:p", "supports_claim", "C:q", "because I said so")
        if e.type == "supports_claim"
        else e
        for e in base.edges
    ]
    out = verify_claim(
        ResearchGraph(nodes=base.nodes, edges=edges),
        subject="P:p",
        predicate="supports_claim",
        obj="C:q",
    )
    assert out["verdict"] == "PRESENT_UNEVIDENCED"
    assert out["reason"] == "evidence_not_span_backed"
    assert out["citation"]["evidence_span"] is None


def test_empty_evidence_edge_is_not_supported():
    base = evidenced_graph()
    edges = [
        edge("P:p", "supports_claim", "C:q", "") if e.type == "supports_claim" else e
        for e in base.edges
    ]
    out = verify_claim(
        ResearchGraph(nodes=base.nodes, edges=edges),
        subject="P:p",
        predicate="supports_claim",
        obj="C:q",
    )
    assert out["verdict"] == "PRESENT_UNEVIDENCED"
    assert out["reason"] == "no_edge_evidence"


def test_neighbour_span_never_substituted():
    """The object's real span must not back an edge that says something else."""
    base = evidenced_graph()
    edges = [
        edge("P:p", "supports_claim", "C:q", "totally different rationale")
        if e.type == "supports_claim"
        else e
        for e in base.edges
    ]
    out = verify_claim(
        ResearchGraph(nodes=base.nodes, edges=edges),
        subject="P:p",
        predicate="supports_claim",
        obj="C:q",
    )
    assert out["verdict"] == "PRESENT_UNEVIDENCED"
    assert out["citation"]["evidence_span"] is None
    assert out["provenance"]["source_path"] is None


def test_ambiguous_span_refuses_to_cite():
    """Two spans with identical text: refuse, never pick the lowest id."""
    base = evidenced_graph()
    graph = ResearchGraph(
        nodes=base.nodes
        + [
            node(
                "E:s2",
                "span-2",
                ResearchNodeType.EVIDENCE_SPAN,
                description=SENTENCE,
                path="docs/beta.md",
            )
        ],
        edges=base.edges + [edge("C:q", "evidenced_by", "E:s2", SENTENCE)],
    )
    out = verify_claim(graph, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "PRESENT_UNEVIDENCED"
    assert out["reason"] == "ambiguous_span"
    assert out["citation"]["evidence_span"] is None


# ---------------------------------------------------------------------------
# Contradiction axis
# ---------------------------------------------------------------------------


def test_contradiction_must_itself_be_evidenced():
    graph = ResearchGraph(
        nodes=[node("P:p", "Alpha", ResearchNodeType.PAPER), node("C:q", "Beta")],
        edges=[edge("P:p", "contradicts_claim", "C:q", "hearsay")],
    )
    out = verify_claim(graph, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "DISPUTED_UNEVIDENCED"
    assert out["reason"] == "evidence_not_span_backed"


def test_contradicted_when_counter_edge_is_document_backed():
    graph = ResearchGraph(
        nodes=[
            node("P:p", "Alpha", ResearchNodeType.PAPER),
            node("C:q", "Beta"),
            node(
                "E:s1",
                "span-1",
                ResearchNodeType.EVIDENCE_SPAN,
                description=SENTENCE,
                path="docs/alpha.md",
            ),
        ],
        edges=[
            edge("C:q", "contradicts_claim", "P:p", SENTENCE),
            edge("C:q", "evidenced_by", "E:s1", SENTENCE),
        ],
    )
    out = verify_claim(graph, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "CONTRADICTED"
    # Symmetric in effect, asserted in one direction — the direction is reported.
    assert out["citation"]["direction"] == "object_to_subject"


def test_conflicting_is_not_adjudicated():
    base = evidenced_graph()
    graph = ResearchGraph(
        nodes=base.nodes,
        edges=base.edges + [edge("P:p", "contradicts_claim", "C:q", SENTENCE)],
    )
    out = verify_claim(graph, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "CONFLICTING"
    assert out["reason"] == "both_polarities_asserted"


def test_absent_is_not_refutation():
    graph = ResearchGraph(
        nodes=[node("P:p", "Alpha", ResearchNodeType.PAPER), node("C:q", "Beta")],
        edges=[],
    )
    out = verify_claim(graph, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "ABSENT"
    assert out["reason"] == "triple_absent"
    assert out["citation"] is None and out["provenance"] is None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_self_referential_refused():
    graph = ResearchGraph(nodes=[node("C:q", "Beta")], edges=[])
    out = verify_claim(graph, subject="C:q", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "NOT_RESOLVABLE"
    assert out["reason"] == "self_referential"


def test_ambiguous_name_refuses():
    graph = ResearchGraph(
        nodes=[node("C:a", "Beta"), node("C:b", "beta"), node("P:p", "Alpha")], edges=[]
    )
    out = verify_claim(graph, subject="Alpha", predicate="supports_claim", obj="Beta")
    assert out["verdict"] == "NOT_RESOLVABLE"
    assert out["reason"] == "ambiguous_object"
    assert out["advisory"]["matched_nodes"] == ["C:a", "C:b"]


def test_predicate_not_in_ontology_refuses_but_reports():
    base = evidenced_graph()
    out = verify_claim(base, subject="P:p", predicate="endorses", obj="C:q")
    assert out["verdict"] == "NOT_RESOLVABLE"
    assert out["reason"] == "predicate_not_in_ontology"
    assert out["advisory"]["observed_predicates"] == ["supports_claim"]


def test_unresolved_subject_refuses():
    out = verify_claim(
        evidenced_graph(), subject="Nowhere", predicate="supports_claim", obj="C:q"
    )
    assert out["verdict"] == "NOT_RESOLVABLE"
    assert out["reason"] == "subject_unresolved"


def test_payload_keys_are_fixed():
    expected = {"verdict", "reason", "triple", "citation", "provenance", "advisory"}
    graph = evidenced_graph()
    for kwargs in (
        {"subject": "P:p", "predicate": "supports_claim", "obj": "C:q"},
        {"subject": "P:p", "predicate": "endorses", "obj": "C:q"},
        {"subject": "Nowhere", "predicate": "supports_claim", "obj": "C:q"},
        {"subject": "C:q", "predicate": "supports_claim", "obj": "P:p"},
    ):
        assert set(verify_claim(graph, **kwargs)) == expected


# ---------------------------------------------------------------------------
# Determinism + real-graph regression
# ---------------------------------------------------------------------------


def _load_real():
    """The compiled graph of whatever checkout this test runs in, or skip.

    Read-only, and never a fixture this suite writes. A worktree has no
    ``.tesserae``, so these two tests skip there and gate the primary checkout.
    """
    if not REAL_GRAPH.exists():
        pytest.skip("no compiled .tesserae/graph.json in this checkout")
    from tesserae.mcp_server import load_graph

    return load_graph(REAL_GRAPH)


def test_deterministic_under_shuffled_order():
    graph = _load_real()
    triples = [
        (e.source, e.type, e.target) for e in graph.edges if e.type == "supports_claim"
    ][:300]
    rng = random.Random(11)
    nodes, edges = list(graph.nodes), list(graph.edges)
    rng.shuffle(nodes)
    rng.shuffle(edges)
    shuffled = ResearchGraph(nodes=nodes, edges=edges)
    for s, p, o in triples:
        a = verify_claim(graph, subject=s, predicate=p, obj=o, reground=False)
        b = verify_claim(shuffled, subject=s, predicate=p, obj=o, reground=False)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_real_graph_distribution_locked():
    """Golden numbers. A silent extractor change moving evidence text trips this."""
    graph = _load_real()
    counts = {}
    for e in graph.edges:
        out = verify_claim(
            graph, subject=e.source, predicate=e.type, obj=e.target, reground=False
        )
        counts[out["verdict"]] = counts.get(out["verdict"], 0) + 1
    # The ship criterion: zero confident refutations anywhere in the corpus.
    # v1 returned CONTRADICTED for 86 of these 15,284 structurally true edges
    # (supersedes 60, derived_from_session 25, references 1).
    assert counts.get("CONTRADICTED", 0) == 0
    assert counts.get("SUPPORTED", 0) > 0
    supports = {}
    for e in graph.edges:
        if e.type != "supports_claim":
            continue
        out = verify_claim(
            graph, subject=e.source, predicate=e.type, obj=e.target, reground=False
        )
        supports[out["verdict"]] = supports.get(out["verdict"], 0) + 1
    assert set(supports) <= {"SUPPORTED", "PRESENT_UNEVIDENCED"}
    if len(graph.edges) == 15284:
        # Golden lock for the corpus these numbers were measured on. An
        # extractor change that rewrites or re-cases edge evidence silently
        # converts SUPPORTED into PRESENT_UNEVIDENCED; this is the only
        # tripwire. Moving 818 UPWARD is a regression to investigate, not a win.
        assert supports == {"SUPPORTED": 818, "PRESENT_UNEVIDENCED": 135}
        assert counts == {
            "SUPPORTED": 2088,
            "PRESENT_UNEVIDENCED": 13195,
            "NOT_RESOLVABLE": 1,  # the single self-loop, refused
        }
