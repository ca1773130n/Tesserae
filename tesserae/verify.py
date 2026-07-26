"""Deterministic claim verification over a compiled :class:`ResearchGraph`.

``verify_claim`` answers exactly one question — *does this graph structurally
license this triple, and what is the single deciding edge?* — with **no LLM, no
fuzzy matching, no ranked list and no natural language**. It is deliberately NOT
a search tool.

v1 shipped one verdict patched per hole and died three times in review, each
time to a verdict reached through a path that was not the triple under test:
a citation borrowed from the object node, a contradiction matched by target
alone, a refutation read out of a whole-graph ``superseded`` set, and an
``SUPPORTED`` for ``"It is not true that Alpha supports_claim Beta"``. Patching
narrowed one path at a time and left the shape intact. v2 removes the shape.

Three structural invariants, from which the verdict falls out:

**1. NO NL SURFACE.** There is no ``claim=`` parameter and no phrase parser.
Endpoints resolve by exact node id, then unique casefolded name, then unique
casefolded alias, and nothing else. The negation family ("it is not true
that…", "does not…", any hedge, any language) is not mis-parsed — it is
unrepresentable, because nothing here reads prose.

**2. LOCALITY.** ``verdict = f(E_{s,o}, spans reachable from those edges)``
where ``E_{s,o}`` is the set of edges whose endpoint set is exactly
``{subject_id, object_id}``. No global scan, no target-only match, no
endpoint-only match. "An unrelated edge flipped my triple" is not a narrower
predicate away — the offending edges are simply not in the input set.

**3. MONOTONE PROVENANCE.** The provenance class is a lattice MINIMUM over
every signal on the deciding chain, ordered
``agent_assertion < model_assertion < structural < document_span``. An agent
marker on the deciding edge, on either endpoint, or on the cited span pins the
result to ``agent_assertion``. A write can therefore only ever WEAKEN a class;
the classifier can be wrong about *which* document backs a claim, but it cannot
be wrong in the direction that matters.

``supersedes`` produces NO verdict, ever. It is a node-lifecycle relation
asserted about an endpoint, not about the triple: "R supersedes Q" says nothing
about whether "P supports_claim Q" held, only that Q's currency changed. v1
conflated the two and returned ``CONTRADICTED`` for 86 of 15,284 structurally
true edges verifying themselves. Here it lives in ``advisory.superseded_endpoints``
— the one deliberately non-local field in the payload, and one that no verdict
may read.

The verdict set is TOTAL and every gap is a REFUSAL, so no default leaks
confidence: an edge with no evidence is ``PRESENT_UNEVIDENCED``, not
``SUPPORTED``; an unevidenced contradiction is ``DISPUTED_UNEVIDENCED``, not
``CONTRADICTED``; both polarities present is ``CONFLICTING``, not adjudicated;
nothing found is ``ABSENT``, not refuted; endpoints that do not resolve exactly
are ``NOT_RESOLVABLE``, not guessed.

A SUPPORTED verdict is not automatically an informative one. When the deciding
edge is itself the ``evidenced_by`` edge, the span it cites is that edge's own
endpoint, so the citation confirms the edge by construction — true, and
uninformative. ``citation.evidence_span.is_edge_endpoint`` marks exactly those
(827 of 2,088 SUPPORTED verdicts on the real 15,284-edge graph); the remaining
1,261 cite a third node that a document backs independently of the edge's own
shape. The key exists only when ``evidence_span`` does, so there is no tri-state
boolean, and like ``advisory`` it is report-only — no verdict may read it.

The one impurity is *re-grounding*: the cited span is checked against the bytes
of the source file on disk. It is part of the SUPPORTED gate, NOT advisory
colour — see ``_Chain.document_backed``. A span the file provably does not
contain (198 of 2,088 SUPPORTED verdicts on the real 5,197-node graph, where the
extractor had stitched a fragment across headings) is disproven evidence, and
disproven evidence may not read SUPPORTED; ``regrounded is False`` therefore
demotes the verdict to ``PRESENT_UNEVIDENCED``. ``None`` (``reground=False``, or
no readable ``source_path``) never demotes anything: disprove, don't assume.

So the verdict is a pure function of the graph bytes *for a fixed
``reground``*, and re-grounding is the ONE axis on which the same graph can
answer differently — because that axis is reading a file the graph does not own.
It can only ever move a verdict DOWN, in the same direction as the provenance
lattice, and only on evidence read off disk and found missing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

__all__ = ["verify_claim", "REGROUND_BYTE_CAP", "VERDICTS", "PROVENANCE_CLASSES"]


# Same cap the ``raw_source`` MCP tool uses — one small read, never enough to
# blow up an agent's context window.
REGROUND_BYTE_CAP = 16 * 1024

_WS = re.compile(r"\s+")

# Total and disjoint. Presence and evidence are SEPARATE axes so neither is ever
# inferred from the other, and no member of this set means "false".
VERDICTS = (
    "SUPPORTED",
    "PRESENT_UNEVIDENCED",
    "CONTRADICTED",
    "DISPUTED_UNEVIDENCED",
    "CONFLICTING",
    "ABSENT",
    "NOT_RESOLVABLE",
)

# Lattice, weakest first. The class of a verdict is the MINIMUM over every
# signal on the deciding chain, which is what makes an agent write unable to
# raise anything. ``execution_verified`` is reserved and never emitted — see the
# ponytail note at the bottom of this module.
PROVENANCE_CLASSES = (
    "agent_assertion",
    "model_assertion",
    "structural",
    "document_span",
)
_CLASS_RANK = {name: rank for rank, name in enumerate(PROVENANCE_CLASSES)}

# ``contradicts_claim`` is the polar opposite of exactly one predicate. Against
# a structural predicate ("Delta derived_from Beta") it asserts nothing, so it
# may not produce a verdict there.
_REFUTABLE_PREDICATES = frozenset({"supports_claim"})

# Stamped by ``agent_write._graph_from_record`` on every node AND every edge it
# mints. The EDGE stamp is the load-bearing one: cross-type dedup
# (``research_graph._merge_cross_type_duplicates``) drops a loser node's
# metadata wholesale while redirecting its edges onto the survivor, so the
# node-level marker is erased for exactly the writes that align onto curated
# nodes — the case this module exists to catch. Edge metadata survives every
# dedup pass verbatim, and ``agent_write.validate_write``'s edge whitelist
# accepts only source/target/type/evidence, so a payload can neither forge nor
# clear it.
_AGENT_WRITE_MARKER = "agent_write_id"


def _norm_ws(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


# ---------------------------------------------------------------------------
# Endpoint resolution — exact only
# ---------------------------------------------------------------------------


class _Index:
    """Exact-match indices over the graph. Collisions are kept, not dropped.

    ``mcp_server._find_node`` builds ``{name.casefold(): node}``, so when two
    nodes share a casefolded display name the last one silently wins. A verifier
    must not inherit that: a colliding name is ``ambiguous``, which is a
    refusal, not a guess. 15 such names exist in the tesserae graph; all remain
    reachable by id.
    """

    def __init__(self, graph: ResearchGraph) -> None:
        self.by_id: Dict[str, ResearchNode] = {n.id: n for n in graph.nodes}
        self.by_name: Dict[str, List[ResearchNode]] = {}
        self.by_alias: Dict[str, List[ResearchNode]] = {}
        for node in graph.nodes:
            self.by_name.setdefault(node.name.casefold(), []).append(node)
            for alias in node.aliases:
                self.by_alias.setdefault(str(alias).casefold(), []).append(node)

    def resolve(self, token: str) -> Tuple[Optional[ResearchNode], str, List[str]]:
        """Return ``(node, status, candidate_ids)``.

        ``status`` is one of ``resolved`` / ``unresolved`` / ``ambiguous``.
        Resolution order is id → casefolded name → casefolded alias, and
        nothing else.
        """
        token = str(token or "").strip()
        if not token:
            return None, "unresolved", []
        node = self.by_id.get(token)
        if node is not None:
            return node, "resolved", [node.id]
        for bucket in (self.by_name, self.by_alias):
            hits = bucket.get(token.casefold())
            if not hits:
                continue
            unique = {n.id: n for n in hits}
            if len(unique) == 1:
                return next(iter(unique.values())), "resolved", sorted(unique)
            return None, "ambiguous", sorted(unique)
        return None, "unresolved", []


# ---------------------------------------------------------------------------
# Span linkage — the edge's OWN span, or nothing
# ---------------------------------------------------------------------------


def _matching_spans(graph: ResearchGraph, edge: Any) -> List[ResearchNode]:
    """EvidenceSpans **this edge** was co-minted with, sorted by id.

    Edges carry no ``evidenced_by`` of their own, so the only honest link is the
    one the extractor actually creates: every span site in ``research_graph``
    co-mints the sibling pair

        add_edge(paper, "supports_claim", claim, evidence=sentence)
        add_edge(claim, "evidenced_by", span,   evidence=sentence)

    with ``span.description == sentence``. Whitespace-identical text between the
    edge's own evidence and a span reachable by ``evidenced_by`` from one of
    that edge's own endpoints is therefore evidence that this edge came from
    that span. Anything looser ("the object node has some span") cites a
    document that never made the assertion under test.
    """
    needle = _norm_ws(edge.evidence or "")
    if not needle:
        return []
    by_id = {n.id: n for n in graph.nodes}
    reachable = sorted(
        {
            e.target
            for e in graph.edges
            if e.type == "evidenced_by" and e.source in (edge.source, edge.target)
        }
    )
    out: List[ResearchNode] = []
    for sid in reachable:
        node = by_id.get(sid)
        if node is None or node.type != ResearchNodeType.EVIDENCE_SPAN:
            continue
        if _norm_ws(node.description) == needle:
            out.append(node)
    return out


def _reground(
    span_text: str, source_path: Optional[str], project_root: Optional[Path]
) -> Optional[bool]:
    """Is the span text still present in the file it was minted from?

    ``None`` means *unknown* — no path, no project root, path outside the root,
    the file is gone, or the span could lie past the read cap. Only a file we
    actually read in full may answer ``False``. The null-vs-false distinction is
    what keeps this honest: 4 of 200 sampled spans in the real graph point at
    paths that no longer exist, and reporting those as "failed to reground"
    would be a lie about the evidence rather than about the reader.
    """
    needle = _norm_ws(span_text)
    if not needle or not source_path or project_root is None:
        return None
    # Same confinement contract as ``mcp_server.raw_source``: never read outside
    # the resolved project root.
    root = Path(project_root).resolve()
    rel = Path(source_path)
    if rel.is_absolute():
        try:
            rel = rel.resolve().relative_to(root)
        except ValueError:
            return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.exists() or not target.is_file():
        return None
    try:
        raw = target.read_bytes()
    except OSError:
        return None
    body = raw[:REGROUND_BYTE_CAP].decode("utf-8", errors="ignore")
    if needle in _norm_ws(body):
        return True
    # Truncated read: absence inside the prefix proves nothing.
    return None if len(raw) > REGROUND_BYTE_CAP else False


# ---------------------------------------------------------------------------
# The deciding chain: one edge, its spans, its provenance
# ---------------------------------------------------------------------------


def _agent_marker(meta: Any) -> bool:
    """Did an agent write the surface this metadata belongs to?

    Read on the deciding EDGE, on BOTH endpoints and on the cited span, because
    an agent write can leave its marker on any of them and the class must be
    pinned by the weakest signal anywhere on the chain.
    """
    return bool(isinstance(meta, dict) and meta.get(_AGENT_WRITE_MARKER))


def _model_marker(meta: Any) -> Optional[str]:
    """Did a model assert the EDGE itself? (``memory.contrast`` stamps this.)

    Deliberately read on the edge only. Endpoint and span nodes are stamped with
    ``extractor`` by the ordinary extraction pass — that is how nearly every
    document-derived node in the graph is minted, so reading it there would
    collapse ``document_span`` to ``model_assertion`` for the whole corpus and
    say nothing. What this gate is for is the narrow, real case: a model minting
    a *link* between two nodes. A model asserting a link is not evidence.
    """
    if not isinstance(meta, dict):
        return None
    return str(meta["extractor"]) if meta.get("extractor") else None


class _Chain:
    """Everything the verdict may read about ONE edge. Nothing else exists.

    Construction is the whole locality invariant: the only graph reads are the
    edge itself, its two endpoint nodes, and the spans reachable by
    ``evidenced_by`` from those two endpoints.
    """

    def __init__(
        self,
        graph: ResearchGraph,
        edge: Any,
        *,
        project_root: Optional[Path],
        reground: bool,
    ) -> None:
        by_id = {n.id: n for n in graph.nodes}
        self.edge = edge
        self.endpoints = [by_id.get(edge.source), by_id.get(edge.target)]
        spans = _matching_spans(graph, edge)
        # Two spans with identical text are a citation-swap waiting to happen:
        # picking the lowest id is a ranking decision dressed as determinism.
        # Refuse instead. 0 occurrences on either real corpus today — this
        # closes the hole before corpus growth opens it.
        self.ambiguous_span = len(spans) > 1
        self.span: Optional[ResearchNode] = (
            spans[0] if len(spans) == 1 and spans[0].source_path else None
        )
        self.no_evidence = not _norm_ws(edge.evidence or "")

        # Lattice minimum. The base is the strongest thing the structure could
        # justify; every marker on the chain can only pull it down.
        base = "document_span" if self.span is not None else "structural"
        signals = [base]

        # Which surface carried the agent marker — reported, never adjudicated.
        self.agent_marker_on: Optional[str] = None
        self.agent_meta: Dict[str, Any] = {}
        # ENDPOINT MARKERS ARE NOT READ — they were the one remaining non-local
        # read, and they violated this module's own LOCALITY invariant. An
        # endpoint being agent-touched says nothing about whether THIS edge was
        # agent-minted, so reading it turned one benign agent write into a
        # self-DoS: on the real graph a single write against a curated Paper
        # node degraded all 13 edges touching it, flipping SUPPORTED ->
        # PRESENT_UNEVIDENCED for facts no agent ever asserted. Writing to the
        # graph is the product's primary use case; it must not poison the
        # verifier. The edge and the cited span are the deciding chain, and they
        # are what carry an unforgeable marker (agent_write.py stamps the edge).
        for where, meta in (
            ("edge", edge.metadata),
            ("span", self.span.metadata if self.span is not None else None),
        ):
            if _agent_marker(meta):
                signals.append("agent_assertion")
                if self.agent_marker_on is None:
                    self.agent_marker_on = where
                    self.agent_meta = dict(meta or {})

        self.extractor: Optional[str] = _model_marker(edge.metadata)
        if self.extractor:
            signals.append("model_assertion")

        self.cls = min(signals, key=lambda name: _CLASS_RANK[name])

        self.regrounded: Optional[bool] = None
        if reground and self.span is not None:
            self.regrounded = _reground(
                self.span.description, self.span.source_path, project_root
            )

    @property
    def document_backed(self) -> bool:
        """The ONLY gate to a confident verdict.

        Re-grounding is part of the gate, not advisory colour. The tool tells its
        caller that SUPPORTED means "its own evidence is a verbatim document
        span"; on the real 5,197-node graph 198 of 2,088 SUPPORTED verdicts
        (9.5%, across 56 distinct files) cited a span that is provably NOT in the
        file — the extractor had stitched a fragment across headings. Class alone
        cannot see that, because class is computed from metadata and the span
        text is only checked against disk by ``_reground``.

        ``regrounded is False`` means we READ the file and the text was absent —
        a definite refutation of the promise, so it must not be SUPPORTED.
        ``None`` (re-grounding disabled, or no readable source_path) is left
        alone: refusing on "not checked" as well would make ``reground=False``
        incapable of ever returning SUPPORTED, forcing disk I/O into every
        verdict for no gain in honesty. Disprove, don't assume — the 198 are
        disproven, the 87 are merely unchecked.
        """
        return self.cls == "document_span" and self.regrounded is not False

    def weakness(self) -> str:
        """Machine slug naming why this chain is not document-backed."""
        if self.cls == "agent_assertion":
            return "agent_assertion"
        if self.cls == "model_assertion":
            return "model_assertion"
        if self.no_evidence:
            return "no_edge_evidence"
        if self.ambiguous_span:
            return "ambiguous_span"
        return "evidence_not_span_backed"

    def citation(self, *, subject_id: str) -> Dict[str, Any]:
        return {
            "edge": {
                "source": self.edge.source,
                "type": self.edge.type,
                "target": self.edge.target,
            },
            # Direction matters for ``contradicts_claim``, which refutes
            # symmetrically but is asserted in one direction.
            "direction": (
                "subject_to_object" if self.edge.source == subject_id else "object_to_subject"
            ),
            "edge_evidence": self.edge.evidence,
            "evidence_span": (
                None
                if self.span is None
                else {
                    "node_id": self.span.id,
                    "text": self.span.description,
                    "source_path": self.span.source_path,
                    # WHY: when the deciding edge IS the ``evidenced_by`` edge,
                    # its own target is trivially in ``_matching_spans``' output
                    # and its text is trivially identical to the edge evidence —
                    # the extractor co-mints both from one sentence. The verdict
                    # stays true ("C evidenced_by S" really is licensed by
                    # reading S) but the citation is circular, and 827 of the
                    # 2,088 SUPPORTED verdicts on the real 15,284-edge graph are
                    # this shape. Both operands were already in the payload;
                    # naming the comparison stops every consumer reinventing it
                    # as ``node_id == edge.target``, which is WRONG — a span can
                    # be the edge's SOURCE (729 spans source 974
                    # part_of/discussed_in edges today). Report-only: nothing
                    # reads this key, so it can never reach ``verdict``.
                    "is_edge_endpoint": self.span.id
                    in (self.edge.source, self.edge.target),
                }
            ),
        }

    def provenance(self) -> Dict[str, Any]:
        return {
            "class": self.cls,
            "edge": {
                "source": self.edge.source,
                "type": self.edge.type,
                "target": self.edge.target,
            },
            "source_path": None if self.span is None else self.span.source_path,
            "span_id": None if self.span is None else self.span.id,
            "agent_marker_on": self.agent_marker_on,
            "agent_write_id": (
                str(self.agent_meta.get(_AGENT_WRITE_MARKER))
                if self.agent_meta.get(_AGENT_WRITE_MARKER)
                else None
            ),
            "agent_key": (
                str(self.agent_meta.get("agent_key"))
                if self.agent_meta.get("agent_key")
                else None
            ),
            # The agent's claimed outside anchor, VERBATIM and UNVERIFIED:
            # ``agent_write.validate_write`` only tests it for non-emptiness, so
            # it is a claim about evidence, not evidence.
            "unverified_anchor": dict(
                self.agent_meta.get("agent_write_provenance") or {}
            )
            or None,
            "extractor": self.extractor,
            "regrounded": self.regrounded,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_claim(
    graph: ResearchGraph,
    *,
    subject: str,
    predicate: str,
    obj: str,
    reground: bool = True,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verify one triple against ``graph``.

    The VERDICT is a pure function of the graph bytes: no LLM, no embedding, no
    fuzzy match, no ranking, no I/O on the decision path. Same bytes in, same
    verdict out, whatever order the nodes and edges arrive in.

    Returns a fixed-key object::

        {verdict, reason, triple, citation, provenance, advisory}

    ``verdict`` ∈ :data:`VERDICTS`. ``reason`` is a machine slug, never prose.
    Only ``SUPPORTED`` and ``CONTRADICTED`` are confident; ``ABSENT`` and
    ``PRESENT_UNEVIDENCED`` are NOT refutations.

    ``citation.evidence_span.is_edge_endpoint`` is ``True`` when the cited span
    IS one of the deciding edge's own endpoints — the verdict holds, but reading
    the span only re-reads the edge. ``True`` means *uninformative*, never
    *false*. Callers wanting the citations a separate document backs want
    ``verdict == "SUPPORTED" and not citation["evidence_span"]["is_edge_endpoint"]``.

    There is deliberately no ``claim=`` parameter. Passing natural language is a
    ``TypeError``, not a guess.
    """
    index = _Index(graph)

    subject_node, s_status, s_ids = index.resolve(subject)
    if s_status != "resolved":
        return _refusal(
            "ambiguous_subject" if s_status == "ambiguous" else "subject_unresolved",
            subject_id=None,
            predicate=predicate,
            object_id=None,
            matched_nodes=s_ids,
        )
    object_node, o_status, o_ids = index.resolve(obj)
    if o_status != "resolved":
        return _refusal(
            "ambiguous_object" if o_status == "ambiguous" else "object_unresolved",
            subject_id=subject_node.id,
            predicate=predicate,
            object_id=None,
            matched_nodes=o_ids,
        )

    assert subject_node is not None and object_node is not None
    s_id, o_id = subject_node.id, object_node.id
    if s_id == o_id:
        # A self-loop has no pair-local edge set distinct from its endpoints, so
        # every invariant below degenerates. Refuse rather than degrade.
        # 1 self-loop exists in the real tesserae graph.
        return _refusal(
            "self_referential",
            subject_id=s_id,
            predicate=predicate,
            object_id=o_id,
            matched_nodes=[s_id],
        )

    # LOCALITY. Every edge the verdict may read, and no other. Both round-1 and
    # round-3 holes (contradiction matched by target alone; supersedes read out
    # of a whole-graph loser set) are non-local reads — they cannot be expressed
    # against this set.
    pair_edges = [e for e in graph.edges if {e.source, e.target} == {s_id, o_id}]
    observed = sorted({e.type for e in pair_edges})
    superseded = sorted(
        {e.target for e in pair_edges if e.type == "supersedes"}
        | {e.source for e in pair_edges if e.type == "resolved_by"}
    )

    if predicate not in ALLOWED_EDGE_TYPES:
        # Both endpoints resolved, so the edge types actually present between
        # them are a GRAPH FACT we can hand back — and we still refuse. No
        # synonym resolution, ever.
        return _refusal(
            "predicate_not_in_ontology",
            subject_id=s_id,
            predicate=predicate,
            object_id=o_id,
            observed_predicates=observed,
            superseded_endpoints=superseded,
        )

    triple = {"subject_id": s_id, "predicate": predicate, "object_id": o_id}
    advisory = {
        # The ONE deliberately non-local field. It is advisory because
        # ``supersedes`` is a node-lifecycle relation asserted about an
        # endpoint, not a statement about this triple: "R supersedes Q" says Q's
        # currency changed, not that "P supports_claim Q" was false. No verdict
        # branch reads it.
        "superseded_endpoints": superseded,
        "pair_edge_types": observed,
        "matched_nodes": None,
        "observed_predicates": None,
    }

    # STRONGEST match, not first match. ``load_graph`` does not dedup and the MCP
    # tool accepts an arbitrary graph_path, so two edges can differ only in
    # evidence — and picking whichever came first made the verdict depend on file
    # order: the same triple returned SUPPORTED or PRESENT_UNEVIDENCED depending
    # on how the JSON happened to be written. Prefer an evidenced edge, then sort
    # by (evidence, id-ish repr) so the choice is total and content-derived.
    # ponytail: sorts a handful of parallel edges, not the graph. There are zero
    # duplicates on today's real graphs; this is a correctness floor, not a
    # hot path.
    def _pick(matches):
        ordered = sorted(matches, key=lambda e: (not bool(e.evidence), e.evidence or "", repr(e)))
        return ordered[0] if ordered else None

    deciding = _pick(
        [e for e in pair_edges if e.source == s_id and e.type == predicate and e.target == o_id]
    )
    # Symmetric in effect, asserted in one direction; the direction is reported
    # in the citation. Only ``supports_claim`` has a polar opposite.
    counter = (
        _pick([e for e in pair_edges if e.type == "contradicts_claim"])
        if predicate in _REFUTABLE_PREDICATES
        else None
    )

    d_chain = (
        None
        if deciding is None
        else _Chain(graph, deciding, project_root=project_root, reground=reground)
    )
    c_chain = (
        None
        if counter is None
        else _Chain(graph, counter, project_root=project_root, reground=reground)
    )

    # CONFLICTING is reserved for a genuine standoff: BOTH polarities
    # document-backed. Only then does the tool decline to adjudicate.
    #
    # Requiring it of both sides is what stops an unevidenced assertion from
    # cancelling an evidenced one — in EITHER direction. Before this, any
    # counter-edge produced CONFLICTING, so one agent write with empty evidence
    # downgraded a document-grounded CONTRADICTED to a non-verdict. Symmetrically,
    # an unevidenced positive must not blunt a document-backed refutation. When
    # exactly one side is document-backed, that side wins and the other is
    # reported in `advisory`, where unevidenced disagreement belongs.
    if (
        d_chain is not None
        and c_chain is not None
        and d_chain.document_backed
        and c_chain.document_backed
    ):
        return _payload(
            "CONFLICTING", "both_polarities_asserted", triple, d_chain, advisory, s_id
        )
    if (
        c_chain is not None
        and c_chain.document_backed
        and (d_chain is None or not d_chain.document_backed)
    ):
        return _payload(
            "CONTRADICTED", "contradicted_by_document_span", triple, c_chain, advisory, s_id
        )
    if d_chain is not None:
        if d_chain.document_backed:
            return _payload(
                "SUPPORTED",
                "edge_evidenced_by_document_span",
                triple,
                d_chain,
                advisory,
                s_id,
            )
        return _payload(
            "PRESENT_UNEVIDENCED", d_chain.weakness(), triple, d_chain, advisory, s_id
        )
    if c_chain is not None:
        if c_chain.document_backed:
            return _payload(
                "CONTRADICTED",
                "contradicted_by_document_span",
                triple,
                c_chain,
                advisory,
                s_id,
            )
        # An unevidenced counter-edge is a dispute, not a refutation.
        return _payload(
            "DISPUTED_UNEVIDENCED", c_chain.weakness(), triple, c_chain, advisory, s_id
        )
    # ABSENT means "this graph does not assert this triple", full stop. It is
    # not a refutation and must never be read as one.
    return _payload("ABSENT", "triple_absent", triple, None, advisory, s_id)


def _payload(
    verdict: str,
    reason: str,
    triple: Dict[str, Any],
    chain: Optional[_Chain],
    advisory: Dict[str, Any],
    subject_id: str,
) -> Dict[str, Any]:
    return {
        "verdict": verdict,
        "reason": reason,
        "triple": triple,
        "citation": None if chain is None else chain.citation(subject_id=subject_id),
        "provenance": None if chain is None else chain.provenance(),
        "advisory": advisory,
    }


def _refusal(
    reason: str,
    *,
    subject_id: Optional[str],
    predicate: Optional[str],
    object_id: Optional[str],
    matched_nodes: Optional[Sequence[str]] = None,
    observed_predicates: Optional[Sequence[str]] = None,
    superseded_endpoints: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "verdict": "NOT_RESOLVABLE",
        "reason": reason,
        "triple": {
            "subject_id": subject_id,
            "predicate": predicate,
            "object_id": object_id,
        },
        "citation": None,
        "provenance": None,
        "advisory": {
            "superseded_endpoints": sorted(superseded_endpoints or []),
            "pair_edge_types": sorted(observed_predicates or []),
            "matched_nodes": sorted(matched_nodes) if matched_nodes else None,
            "observed_predicates": (
                sorted(observed_predicates) if observed_predicates is not None else None
            ),
        },
    }


# ponytail: span linkage is whitespace-normalised text identity between
# ``edge.evidence`` and an ``EvidenceSpan.description``. Ceiling: any extractor
# change that rewrites, truncates or re-cases edge evidence silently converts
# SUPPORTED into PRESENT_UNEVIDENCED with no error — the direction is safe, the
# silence is not. The golden-number test over the real graph
# (``test_real_graph_distribution_locked``) is the only tripwire. Upgrade path:
# have ``research_graph._add_evidence`` stamp the span id into the edge's own
# metadata at mint time, then read that id here and keep text identity as the
# fallback. That is a research_graph change, not a verify change.

# ponytail: ``execution_verified`` provenance is reserved in the vocabulary and
# never emitted, because the data does not exist yet. The Claude/Codex session
# importers mint turns from ``tool_use`` INPUTS only
# (``harness_sessions._claude_turns``); ``tool_result`` is parsed solely to map
# subagent ids and never becomes a turn, so no exit code survives ingest. Event
# nodes carry ``{actor, action, tool}`` but no exit code, and the Event pass is
# gated on ``distillation_enabled(cfg)``. Ceiling: this tool can say "a document
# says so", never "this ran and passed". Upgrade path, in order — (1) capture
# ``tool_result`` text + exit code as a turn, (2) put ``exit_code`` on
# ``Event.metadata``, (3) promote a finding here when one of its
# ``derived_from`` Events carries a zero exit code. That is an INGEST change,
# not a verify change.
