"""Deterministic claim verification over a compiled :class:`ResearchGraph`.

``verify_claim`` answers exactly one question — *does the graph assert this
triple, and what is the single deciding edge?* — with **no LLM, no fuzzy
matching and no ranked list**. It is deliberately NOT a search tool.

Why LLM-free is the design and not an optimisation:

* A verifier that hallucinates is worth less than no verifier. The failure
  mode this codebase must not ship is handing an evaluator ranked prose and
  watching it agree with everything.
* Reproducibility. Two agents verifying the same claim against the same
  ``graph.json`` bytes must not disagree, or "agents checking agents" degrades
  into organised nonsense.
* Entity resolution is where graph products die: at 85% per-hop accuracy a
  5-hop chain is 44% trustworthy. Exact-match-only endpoint resolution is the
  only policy that does not compound that error, and ``NOT_FOUND`` is a
  correct answer — never a refutation.

The one impurity is *re-grounding*: the cited evidence span is checked against
the bytes of the source file on disk, i.e. against evidence that originates
OUTSIDE the graph that produced the claim. Because that file can change
independently of the graph, re-grounding may only set
``provenance.regrounded`` — it can never change ``verdict``.

Everything a verdict reports is scoped to **the deciding edge**, never to its
endpoints. An earlier version anchored the citation and the provenance class on
the *object node's* evidence span, which let an agent write one fabricated node
plus one ``supports_claim`` edge into a curated claim and collect
``SUPPORTED`` + ``class: document_span`` + ``regrounded: true`` citing a real
document that says nothing about the fabricated claim. A span belongs to an
edge only when the extractor co-minted the two from the same sentence
(``research_graph._add_evidence`` puts that sentence on both), so that identity
is the only link this module will accept.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .graph_filters import superseded_ids
from .research_graph import (
    ALLOWED_EDGE_TYPES,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

__all__ = ["verify_claim", "REGROUND_BYTE_CAP"]


# Same cap the ``raw_source`` MCP tool uses — one small read, never enough to
# blow up an agent's context window.
REGROUND_BYTE_CAP = 16 * 1024

_WS = re.compile(r"\s+")

# ``contradicts_claim`` is the polar opposite of exactly one predicate. Against
# a structural predicate ("Delta derived_from Beta") it asserts nothing, so it
# may not produce a verdict there.
_REFUTABLE_PREDICATES = frozenset({"supports_claim"})

# Stamped on every node minted by ``agent_write._graph_from_record`` AFTER the
# agent's own metadata dict, so an agent cannot clear or forge it downward.
_AGENT_WRITE_MARKER = "agent_write_id"


def _norm_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Endpoint resolution — exact only
# ---------------------------------------------------------------------------


class _Index:
    """Exact-match indices over the graph. Collisions are kept, not dropped.

    ``mcp_server._find_node`` builds ``{name.casefold(): node}``, so when two
    nodes share a casefolded display name the last one silently wins. A
    verifier must not inherit that: a colliding name is ``ambiguous``, which is
    a refusal, not a guess.
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
# Provenance
# ---------------------------------------------------------------------------


def _evidence_spans(graph: ResearchGraph, node_id: str) -> List[str]:
    """Span node ids reachable from ``node_id`` via ``evidenced_by``, sorted."""
    return sorted(
        e.target
        for e in graph.edges
        if e.type == "evidenced_by" and e.source == node_id
    )


def _reground(
    span_text: str, source_path: Optional[str], project_root: Optional[Path]
) -> Optional[bool]:
    """Is the span text still present in the file it was minted from?

    ``None`` means *unknown* — no path, no project root, path outside the root,
    the file is gone, or the span could lie past the read cap. Only a file we
    actually read in full may answer ``False``. The null-vs-false distinction
    is what keeps this honest: 4 of 200 sampled spans in the real graph point
    at paths that no longer exist, and reporting those as "failed to reground"
    would be a lie about the evidence rather than about the reader.
    """
    needle = _norm_ws(span_text)
    if not needle or not source_path or project_root is None:
        return None
    # Same confinement contract as ``mcp_server.raw_source``: never read
    # outside the resolved project root.
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


def _edge_provenance(
    graph: ResearchGraph,
    edge: Any,
    project_root: Optional[Path],
    reground: bool,
) -> Optional[Dict[str, Any]]:
    """Classify who asserted **the deciding edge**, in refusal order.

    ``agent_assertion`` → ``model_assertion`` (edge stamped by an LLM pass) →
    ``document_span`` → ``model_assertion`` (endpoint stamped by an extractor)
    → ``structural``. The order is what makes the class non-forgeable: an agent
    may write any ``metadata`` it likes onto its nodes, but the agent check runs
    first and wins, so no agent write can present itself as a document.

    ``execution_verified`` is deliberately RESERVED and never emitted — see the
    ponytail note at the bottom of this module.
    """
    if edge is None:
        return None
    by_id = {n.id: n for n in graph.nodes}
    base: Dict[str, Any] = {
        "edge": {"source": edge.source, "type": edge.type, "target": edge.target},
        "source_path": None,
        "regrounded": None,
    }

    agent_node = next(
        (
            by_id[nid]
            for nid in (edge.source, edge.target)
            if nid in by_id and by_id[nid].metadata.get(_AGENT_WRITE_MARKER)
        ),
        None,
    )
    if agent_node is not None:
        return {
            **base,
            "class": "agent_assertion",
            "agent_key": str(agent_node.metadata.get("agent_key") or ""),
            "agent_write_id": str(agent_node.metadata.get(_AGENT_WRITE_MARKER)),
            # The agent's claimed outside anchor, VERBATIM and UNVERIFIED:
            # ``agent_write.validate_write`` only tests it for non-emptiness,
            # so it is a claim about evidence, not evidence.
            "unverified_anchor": dict(
                agent_node.metadata.get("agent_write_provenance") or {}
            ),
        }

    # An edge a model minted is a model assertion even if its rationale happens
    # to match a span: ``memory.contrast`` stamps ``extractor`` on the EDGE, and
    # reading only node metadata is how LLM verdicts reached ``verdict``
    # labelled ``structural``.
    edge_extractor = (edge.metadata or {}).get("extractor")
    if edge_extractor:
        return {**base, "class": "model_assertion", "extractor": str(edge_extractor)}

    span = _edge_span(graph, edge)
    if span is not None and span.source_path:
        return {
            **base,
            "class": "document_span",
            "source_path": span.source_path,
            "span_id": span.id,
            "regrounded": (
                _reground(span.description, span.source_path, project_root)
                if reground
                else None
            ),
        }

    for nid in (edge.target, edge.source):
        node = by_id.get(nid)
        extractor = node.metadata.get("extractor") if node is not None else None
        if extractor:
            return {
                **base,
                "class": "model_assertion",
                "source_path": node.source_path,
                "extractor": str(extractor),
                # Passed through untouched — a model's self-reported confidence
                # must never influence the verdict.
                "confidence": node.metadata.get("confidence"),
                "confidence_rationale": node.metadata.get("confidence_rationale"),
                "node_id": node.id,
            }
    return {**base, "class": "structural"}


# ---------------------------------------------------------------------------
# NL convenience path
# ---------------------------------------------------------------------------


def _resolve_nl_claim(
    graph: ResearchGraph, claim: str
) -> Optional[Tuple[str, str, str]]:
    """Verbatim-only NL resolution. Returns ``None`` unless it is unambiguous.

    Requires exactly one ALLOWED_EDGE_TYPES token in the string, exactly one
    node name/alias occurring verbatim to its left, and exactly one to its
    right. Any other count refuses — this path never guesses.
    """
    lowered = str(claim or "").casefold()
    predicates = sorted(p for p in ALLOWED_EDGE_TYPES if p in lowered)
    # A shorter predicate can be a substring of a longer one ("references" vs
    # "discussed_in" do not collide, but "contains" ⊂ nothing today); keep only
    # maximal matches so a single token is not counted twice.
    predicates = [
        p for p in predicates if not any(p != q and p in q for q in predicates)
    ]
    if len(predicates) != 1:
        return None
    predicate = predicates[0]
    cut = lowered.index(predicate)
    left, right = lowered[:cut], lowered[cut + len(predicate) :]

    def _hits(fragment: str) -> List[str]:
        found: Dict[str, None] = {}
        for node in graph.nodes:
            labels = [node.name] + [str(a) for a in node.aliases]
            if any(len(lbl) >= 2 and lbl.casefold() in fragment for lbl in labels):
                found[node.id] = None
        return sorted(found)

    subjects, objects = _hits(left), _hits(right)
    if len(subjects) != 1 or len(objects) != 1:
        return None
    return subjects[0], predicate, objects[0]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_claim(
    graph: ResearchGraph,
    *,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    obj: Optional[str] = None,
    claim: Optional[str] = None,
    reground: bool = True,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verify one triple against ``graph``. Pure function of its inputs.

    Returns a fixed-key object::

        {verdict, reason, triple{subject_id,predicate,object_id}, citation, provenance}

    ``verdict`` ∈ ``SUPPORTED`` / ``CONTRADICTED`` / ``NOT_FOUND``. ``reason``
    is a machine slug, never prose. ``NOT_FOUND`` means *the graph does not
    assert this* — it is NOT a refutation.
    """
    index = _Index(graph)

    if not (subject and predicate and obj):
        resolved = _resolve_nl_claim(graph, claim or "") if claim else None
        if resolved is None:
            return _not_found(
                "nl_not_resolvable" if claim else "subject_unresolved",
                subject_id=None,
                predicate=predicate,
                object_id=None,
            )
        subject, predicate, obj = resolved

    subject_node, s_status, s_ids = index.resolve(subject)
    if s_status != "resolved":
        return _not_found(
            "ambiguous_subject" if s_status == "ambiguous" else "subject_unresolved",
            subject_id=None,
            predicate=predicate,
            object_id=None,
            matched_nodes=s_ids or None,
        )
    object_node, o_status, o_ids = index.resolve(obj)
    if o_status != "resolved":
        return _not_found(
            "ambiguous_object" if o_status == "ambiguous" else "object_unresolved",
            subject_id=subject_node.id,
            predicate=predicate,
            object_id=None,
            matched_nodes=o_ids or None,
        )

    assert subject_node is not None and object_node is not None
    if predicate not in ALLOWED_EDGE_TYPES:
        # Both endpoints resolved, so the edge types actually present between
        # them are a GRAPH FACT we can hand back — not a search result.
        observed = sorted(
            {
                e.type
                for e in graph.edges
                if {e.source, e.target} == {subject_node.id, object_node.id}
            }
        )
        return _not_found(
            "predicate_not_in_ontology",
            subject_id=subject_node.id,
            predicate=predicate,
            object_id=object_node.id,
            observed_predicates=observed,
        )

    triple = {
        "subject_id": subject_node.id,
        "predicate": predicate,
        "object_id": object_node.id,
    }
    deciding = next(
        (
            e
            for e in graph.edges
            if e.source == subject_node.id
            and e.type == predicate
            and e.target == object_node.id
        ),
        None,
    )

    if deciding is not None:
        losers = superseded_ids(graph)
        loser = next(
            (nid for nid in (subject_node.id, object_node.id) if nid in losers), None
        )
        if loser is not None:
            winner_edge = _winning_edge(graph, loser)
            return {
                "verdict": "CONTRADICTED",
                "reason": "superseded",
                "triple": triple,
                "citation": _citation(graph, winner_edge),
                "provenance": _edge_provenance(
                    graph, winner_edge, project_root, reground
                ),
            }
        return {
            "verdict": "SUPPORTED",
            "reason": "triple_present",
            "triple": triple,
            "citation": _citation(graph, deciding),
            "provenance": _edge_provenance(graph, deciding, project_root, reground),
        }

    # A ``contradicts_claim`` edge refutes THIS triple only when it is ABOUT
    # this triple. Matching by target alone turned "somebody, somewhere,
    # disputes the object" into "your triple is refuted": one unrelated edge
    # flipped uninvolved triples from NOT_FOUND to CONTRADICTED, i.e. absence
    # rendered as refutation — the exact inversion this module exists to
    # prevent, on the default path, from any ``graph_write`` caller.
    contradiction = (
        next(
            (
                e
                for e in graph.edges
                if e.type == "contradicts_claim"
                and e.source == subject_node.id
                and e.target == object_node.id
            ),
            None,
        )
        if predicate in _REFUTABLE_PREDICATES
        else None
    )
    if contradiction is not None:
        return {
            "verdict": "CONTRADICTED",
            "reason": "contradicted_claim",
            "triple": triple,
            "citation": _citation(graph, contradiction),
            "provenance": _edge_provenance(
                graph, contradiction, project_root, reground
            ),
        }
    return _not_found(
        "triple_absent",
        subject_id=subject_node.id,
        predicate=predicate,
        object_id=object_node.id,
    )


def _winning_edge(graph: ResearchGraph, loser_id: str):
    """The ``supersedes`` / ``resolved_by`` edge that demoted ``loser_id``."""
    for edge in graph.edges:
        if edge.type == "supersedes" and edge.target == loser_id:
            return edge
        if edge.type == "resolved_by" and edge.source == loser_id:
            return edge
    return None


def _edge_span(graph: ResearchGraph, edge: Any) -> Optional[ResearchNode]:
    """The EvidenceSpan **this edge** was minted from, or ``None``.

    Edges carry no ``evidenced_by`` of their own, so the only honest link is the
    one the extractor actually creates: every span site in
    ``research_graph`` co-mints the sibling pair

        add_edge(paper, "supports_claim", claim, evidence=sentence)
        add_edge(claim, "evidenced_by", span,   evidence=sentence)

    with ``span.description == sentence``. Text identity between the edge's own
    evidence and a span reachable from one of its endpoints is therefore
    *evidence that this edge came from that span* — and anything looser (e.g.
    "the object node has some span") cites a document that never made the
    assertion under test.
    """
    needle = _norm_ws(edge.evidence or "")
    if not needle:
        return None
    by_id = {n.id: n for n in graph.nodes}
    span_ids = sorted(
        set(_evidence_spans(graph, edge.source)) | set(_evidence_spans(graph, edge.target))
    )
    # Lowest span id wins when several match — determinism, not ranking.
    for sid in span_ids:
        node = by_id.get(sid)
        if node is None or node.type != ResearchNodeType.EVIDENCE_SPAN:
            continue
        if _norm_ws(node.description) == needle:
            return node
    return None


def _citation(graph: ResearchGraph, edge: Any) -> Optional[Dict[str, Any]]:
    if edge is None:
        return None
    span = _edge_span(graph, edge)
    return {
        "edge": {"source": edge.source, "type": edge.type, "target": edge.target},
        "edge_evidence": edge.evidence,
        "evidence_span": (
            None
            if span is None
            else {
                "node_id": span.id,
                "text": span.description,
                "source_path": span.source_path,
            }
        ),
    }


def _not_found(
    reason: str,
    *,
    subject_id: Optional[str],
    predicate: Optional[str],
    object_id: Optional[str],
    matched_nodes: Optional[Sequence[str]] = None,
    observed_predicates: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "verdict": "NOT_FOUND",
        "reason": reason,
        "triple": {
            "subject_id": subject_id,
            "predicate": predicate,
            "object_id": object_id,
        },
        "citation": None,
        "provenance": None,
    }
    if matched_nodes:
        payload["matched_nodes"] = sorted(matched_nodes)
    if observed_predicates is not None:
        payload["observed_predicates"] = sorted(observed_predicates)
    return payload


# ponytail: ``agent_assertion`` is detected from the ``agent_write_id`` marker
# that ``agent_write._graph_from_record`` stamps on every node it mints. That
# marker is reliable in one direction only. ``agent_write.align_overlay`` drops
# a redirected node wholesale (marker included) and remaps its edges onto the
# curated node they aligned with, so an agent write whose endpoints BOTH align
# onto existing curated nodes leaves no node-level trace and is reported here as
# whatever its endpoints are — ``document_span`` if, and only if, the agent
# supplied the verbatim text of an existing span as its edge evidence. Ceiling:
# this module can say "no agent wrote this edge" only for edges with at least
# one unaligned endpoint. Upgrade path: stamp ``agent_write_id`` on the EDGE
# metadata in ``agent_write._graph_from_record`` (edges survive alignment with
# ``metadata=dict(edge.metadata)``, so the marker would too) and read it here
# before the endpoint check. That is an agent_write change, not a verify change.

# ponytail: ``execution_verified`` provenance is reserved in the vocabulary and
# never emitted, because the data does not exist yet. The Claude/Codex session
# importers mint turns from ``tool_use`` INPUTS only
# (``harness_sessions._claude_turns``); ``tool_result`` is parsed solely to map
# subagent ids and never becomes a turn, so no exit code survives ingest. Event
# nodes carry ``{actor, action, tool}`` but no exit code, and the Event pass is
# gated on ``distillation_enabled(cfg)``. Ceiling: this tool can say "a document
# says so", never "this was executed and passed". Upgrade path, in order —
# (1) capture ``tool_result`` text + exit code as a turn, (2) put ``exit_code``
# on ``Event.metadata``, (3) promote a finding here when one of its
# ``derived_from`` Events carries a zero exit code. That is an INGEST change,
# not a verify change.
