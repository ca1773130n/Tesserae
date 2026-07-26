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
    """An agent re-assertion on the DECIDING CHAIN can only ever WEAKEN the class.

    The chain is the edge and the cited span — not the endpoint nodes. Reading
    endpoint markers was a non-local read that violated this module's own
    LOCALITY invariant and turned writing into a self-DoS: on the real graph one
    benign write against a curated Paper degraded all 13 edges touching it,
    flipping SUPPORTED -> PRESENT_UNEVIDENCED for facts no agent ever asserted.
    Writing to the graph is the product's primary use case, so it must not
    poison the verifier for everyone else. Endpoint coverage is not lost: the
    self-confirmation attack is caught by the EDGE marker, which agent_write
    stamps precisely because dedup drops node metadata (see the test above).
    """
    base = evidenced_graph()
    before = verify_claim(base, subject="P:p", predicate="supports_claim", obj="C:q")
    for surface in ("edge", "span"):
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
# Tautological citations — SUPPORTED that only re-reads its own edge
# ---------------------------------------------------------------------------


def test_self_evidencing_citation_is_flagged():
    """Verifying the ``evidenced_by`` edge ITSELF cites its own target.

    Still SUPPORTED — "C evidenced_by S" really is licensed by reading S — but
    the citation is circular, and the payload must say so.
    """
    out = verify_claim(
        evidenced_graph(), subject="C:q", predicate="evidenced_by", obj="E:s1"
    )
    assert out["verdict"] == "SUPPORTED"
    span = out["citation"]["evidence_span"]
    assert span["node_id"] == out["citation"]["edge"]["target"] == "E:s1"
    assert span["is_edge_endpoint"] is True


def test_document_backed_citation_is_not_flagged():
    """The 60% majority: a third node backs the edge. The flag must not fire."""
    out = verify_claim(
        evidenced_graph(), subject="P:p", predicate="supports_claim", obj="C:q"
    )
    assert out["verdict"] == "SUPPORTED"
    span = out["citation"]["evidence_span"]
    assert span["node_id"] == "E:s1"
    assert span["node_id"] not in out["citation"]["edge"].values()
    assert span["is_edge_endpoint"] is False


def test_flag_reads_both_endpoints_not_just_the_target():
    """A span can be the deciding edge's SOURCE, not only its target.

    The obvious consumer-side reinvention (``node_id == edge.target``) is wrong
    here and this is the branch that catches it. 729 spans are the source of 974
    ``part_of`` / ``discussed_in`` edges on the real graph, so the shape is one
    extractor change away from producing this citation for real.
    """
    graph = ResearchGraph(
        nodes=[
            node("D:doc", "Alpha Doc", ResearchNodeType.PAPER),
            node(
                "E:s1",
                "span-1",
                ResearchNodeType.EVIDENCE_SPAN,
                description=SENTENCE,
                path="docs/alpha.md",
            ),
        ],
        edges=[
            edge("E:s1", "part_of", "D:doc", SENTENCE),
            edge("D:doc", "evidenced_by", "E:s1", SENTENCE),
        ],
    )
    out = verify_claim(graph, subject="E:s1", predicate="part_of", obj="D:doc")
    assert out["verdict"] == "SUPPORTED"
    span = out["citation"]["evidence_span"]
    assert span["node_id"] == out["citation"]["edge"]["source"] == "E:s1"
    assert span["node_id"] != out["citation"]["edge"]["target"]
    assert span["is_edge_endpoint"] is True


def test_evidence_span_keys_are_fixed():
    """Mirror of ``test_payload_keys_are_fixed`` one level down.

    The flag lives INSIDE ``evidence_span`` so it is absent exactly when the
    span is ``None`` — no tri-state boolean for a caller to misread.
    """
    expected = {"node_id", "text", "source_path", "is_edge_endpoint"}
    graph = evidenced_graph()
    for kwargs in (
        {"subject": "P:p", "predicate": "supports_claim", "obj": "C:q"},
        {"subject": "C:q", "predicate": "evidenced_by", "obj": "E:s1"},
    ):
        assert set(verify_claim(graph, **kwargs)["citation"]["evidence_span"]) == expected
    unevidenced = ResearchGraph(
        nodes=graph.nodes,
        edges=[
            edge("P:p", "supports_claim", "C:q", "because I said so")
            if e.type == "supports_claim"
            else e
            for e in graph.edges
        ],
    )
    out = verify_claim(unevidenced, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["citation"]["evidence_span"] is None


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


def test_real_graph_tautology_share_locked():
    """Golden lock on the tautological/informative split of SUPPORTED.

    Companion tripwire to ``test_real_graph_distribution_locked``: that one
    catches SUPPORTED changing COUNT, this one catches it changing KIND. An
    extractor change that starts co-minting spans for a predicate that did not
    have them moves informative -> tautological without moving either total.
    """
    graph = _load_real()
    taut = info = no_span = 0
    for e in graph.edges:
        out = verify_claim(
            graph, subject=e.source, predicate=e.type, obj=e.target, reground=False
        )
        if out["verdict"] != "SUPPORTED":
            continue
        span = out["citation"]["evidence_span"]
        if span is None:
            no_span += 1
            continue
        if span["is_edge_endpoint"]:
            taut += 1
        else:
            info += 1
    # ``document_backed`` requires ``cls == "document_span"``, which requires a
    # span, so SUPPORTED without a citation is unreachable by construction.
    assert no_span == 0
    if len(graph.edges) == 15284:
        # 827 + 1261 == the 2088 SUPPORTED locked above. Corpus-bound, so it
        # skips rather than fails on a recompile — same trade the sibling lock
        # already accepts. Every one of the 827 is an ``evidenced_by`` edge
        # citing its own target; that equivalence is corpus-accidental, which is
        # why the flag compares ids instead of testing the predicate.
        assert (taut, info) == (827, 1261)


def test_agent_write_on_an_endpoint_does_not_poison_unrelated_verdicts():
    """The P3 self-DoS, pinned: touching a NODE must not degrade edges the agent
    never asserted. One benign write against a curated Paper previously flipped
    all 13 edges touching it from SUPPORTED to PRESENT_UNEVIDENCED."""
    base = evidenced_graph()
    before = verify_claim(base, subject="P:p", predicate="supports_claim", obj="C:q")
    assert before["verdict"] == "SUPPORTED"

    stamp = {"agent_write_id": "w1", "agent_key": "prober"}
    poisoned = ResearchGraph(
        nodes=[
            node(n.id, n.name, n.type, description=n.description, path=n.source_path, meta=stamp)
            if n.id == "P:p" else n
            for n in (copy.deepcopy(x) for x in base.nodes)
        ],
        edges=[copy.deepcopy(e) for e in base.edges],
    )
    after = verify_claim(poisoned, subject="P:p", predicate="supports_claim", obj="C:q")
    assert after["verdict"] == "SUPPORTED", "an endpoint write poisoned an unrelated verdict"
    assert after["provenance"]["class"] == "document_span"


def test_unevidenced_counter_edge_cannot_erase_a_document_backed_refutation():
    """P2: an assertion nobody evidenced must not cancel one a document backs."""
    base = evidenced_graph()
    graph = ResearchGraph(
        nodes=base.nodes,
        edges=[e for e in base.edges if e.type != "supports_claim"]
        + [edge("P:p", "contradicts_claim", "C:q", SENTENCE)],
    )
    assert verify_claim(graph, subject="P:p", predicate="supports_claim",
                        obj="C:q")["verdict"] == "CONTRADICTED"

    # An agent adds an unevidenced positive edge. It must NOT downgrade the
    # document-backed refutation to a non-verdict.
    with_agent = ResearchGraph(
        nodes=graph.nodes,
        edges=graph.edges
        + [edge("P:p", "supports_claim", "C:q", "", {"agent_write_id": "w9", "agent_key": "x"})],
    )
    out = verify_claim(with_agent, subject="P:p", predicate="supports_claim", obj="C:q")
    assert out["verdict"] == "CONTRADICTED", f"refutation erased -> {out['verdict']}"


def test_deciding_edge_choice_is_order_independent():
    """P4: duplicate parallel edges must not make the verdict depend on file order."""
    base = evidenced_graph()
    dup = edge("P:p", "supports_claim", "C:q", "")          # same triple, no evidence
    a = ResearchGraph(nodes=base.nodes, edges=list(base.edges) + [dup])
    b = ResearchGraph(nodes=base.nodes, edges=[dup] + list(base.edges))
    va = verify_claim(a, subject="P:p", predicate="supports_claim", obj="C:q")["verdict"]
    vb = verify_claim(b, subject="P:p", predicate="supports_claim", obj="C:q")["verdict"]
    assert va == vb == "SUPPORTED", f"order-dependent: {va} vs {vb}"


# ---------------------------------------------------------------------------
# CLI verb — the non-MCP surface consumers were blocked on.
# ---------------------------------------------------------------------------


def _cli(tmp_path, *argv):
    import subprocess, sys as _sys
    root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [_sys.executable, "-m", "tesserae.cli", "verify-claim", "--project", str(tmp_path), *argv],
        cwd=str(root), capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root), "HOME": str(tmp_path)},
    )


def _seed_cli_project(tmp_path):
    from tesserae.project import ProjectWiki
    ProjectWiki.init(tmp_path, name="vc")
    wiki = ProjectWiki.load(tmp_path)
    payload = evidenced_graph().to_json()
    wiki.paths.graph.write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )
    return wiki


def test_cli_verb_answers_and_exits_zero(tmp_path):
    _seed_cli_project(tmp_path)
    r = _cli(tmp_path, "-s", "P:p", "-p", "supports_claim", "-o", "C:q")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["verdict"] == "SUPPORTED"


def test_cli_verb_exits_2_only_for_not_resolvable(tmp_path):
    """ABSENT is an ANSWER — "this graph does not assert it" — and must not read
    as an error. Only "could not check" gets a non-zero exit."""
    _seed_cli_project(tmp_path)
    absent = _cli(tmp_path, "-s", "P:p", "-p", "contradicts_claim", "-o", "C:q")
    assert absent.returncode == 0
    assert json.loads(absent.stdout)["verdict"] == "ABSENT"

    unknown = _cli(tmp_path, "-s", "no-such-node", "-p", "supports_claim", "-o", "C:q")
    assert unknown.returncode == 2
    assert json.loads(unknown.stdout)["verdict"] == "NOT_RESOLVABLE"


def test_cli_verb_is_registered(tmp_path):
    from tesserae.cli import _NEW_DISPATCH
    from tesserae.cli_tree import KNOWN_COMMANDS

    assert "verify-claim" in _NEW_DISPATCH and "verify-claim" in KNOWN_COMMANDS
