"""Agentic research mode — dzhng/deep-research-style plan/search/reflect/synthesize.

A breadth/depth-bounded loop that decomposes a user query into sub-questions,
searches the compiled Tesserae graph (and optionally the web) for evidence per
sub-question, asks the LLM to reflect (1 finding + optional follow-up
sub-questions + optional hypotheses), and finally writes a cited synthesis
report.

The loop mints typed nodes/edges as it goes so subsequent compiles can recover
the research thread:

* :data:`ResearchNodeType.OPEN_QUESTION` per sub-question (root + follow-ups);
  follow-ups carry ``metadata.parent_question_id`` and an outgoing
  ``derived_from`` edge to the parent question.
* :data:`ResearchNodeType.SESSION_HYPOTHESIS` per LLM-minted hypothesis, with
  ``references`` edges to every evidence node id cited.
* :data:`ResearchNodeType.SOURCE_DOCUMENT` per web result (when a
  :class:`WebFetcher` is provided).

The whole loop is dependency-injected via ``LLMJsonClient``, ``SearchBackend``
(which wraps ``LLMWikiMCPServer.search_nodes`` / ``node_context``), and an
optional ``WebFetcher`` protocol so tests can drive deterministic behaviour
without touching the network.

Reference: https://github.com/dzhng/deep-research (lead → subagent → writer)
and the GPT-Researcher / open_deep_research patterns codified in the
v0.3.0 research notes.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from .llm_json import LLMJsonClient
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)


_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pluggable backends — kept as Protocols so tests can mock them without
# instantiating the real MCP server / network stack.
# ---------------------------------------------------------------------------


class SearchBackend(Protocol):
    """Graph-search facade. Mirrors the subset of ``LLMWikiMCPServer`` we use.

    Returning the raw MCP shape (``{"nodes": [...]}``) keeps the prod adapter a
    one-line passthrough and tests an obvious dict-literal.
    """

    def search_nodes(self, query: str, *, limit: int = 5) -> List[Dict[str, object]]:
        ...


class WebFetcher(Protocol):
    """Optional web backend. Implementations return ``(title, url, snippet)``.

    A return of ``[]`` is treated as "no web evidence" — the loop falls back
    to graph-only and logs at WARNING. Implementations MUST NOT raise on
    transient failure; they should log + return ``[]``.
    """

    def search(self, query: str, *, limit: int = 5) -> List[Tuple[str, str, str]]:
        ...


@dataclass
class GraphSearchBackend:
    """Adapter wrapping :class:`LLMWikiMCPServer` so we can call its in-process
    Python methods directly — no MCP JSON-RPC transport, no subprocess.
    """

    server: "object"  # LLMWikiMCPServer — typed as object to avoid hard import.
    graph: ResearchGraph

    def search_nodes(self, query: str, *, limit: int = 5) -> List[Dict[str, object]]:
        result = self.server.search_nodes(  # type: ignore[attr-defined]
            self.graph, query=query, limit=limit
        )
        nodes = result.get("nodes") if isinstance(result, dict) else None
        return list(nodes) if isinstance(nodes, list) else []


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EvidenceRef:
    """One piece of evidence backing a finding.

    ``node_id`` is preferred (graph hit); ``url`` is set for web hits and
    surfaces as the cited identifier in the report.
    """

    node_id: Optional[str] = None
    name: str = ""
    url: Optional[str] = None
    snippet: str = ""

    @property
    def cite_id(self) -> str:
        return self.node_id or self.url or self.name


@dataclass
class SubQuestion:
    question: str
    parent_id: Optional[str] = None  # node id of the parent question, if any
    depth: int = 0
    evidence: List[EvidenceRef] = field(default_factory=list)
    finding: str = ""
    hypotheses: List[str] = field(default_factory=list)
    node_id: Optional[str] = None  # filled after minting


@dataclass
class ResearchReport:
    report_path: Path
    report_text: str
    questions: int
    hypotheses: int
    sources: int
    edges: int


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


_PLAN_SYSTEM = (
    "You are the lead planner of an agentic research loop. "
    "Decompose the user's query into focused sub-questions that, taken together, "
    "would let a careful analyst answer the original query. "
    "Return STRICT JSON of the form {\"subqueries\": [\"q1\", \"q2\", ...]} with "
    "no commentary."
)

_REFLECT_SYSTEM = (
    "You are a research subagent. Given a sub-question and a list of "
    "evidence snippets (each tagged with a citation id), write one paragraph "
    "stating what the evidence does and does not show, suggest follow-up "
    "sub-questions that would resolve remaining uncertainty, and propose "
    "research hypotheses worth recording. Cite evidence by the provided id. "
    "Return STRICT JSON: {\"finding\": str, \"followups\": [str], "
    "\"hypotheses\": [{\"text\": str, \"evidence_ids\": [str]}]}."
)

_SYNTHESIZE_SYSTEM = (
    "You are the writer of an agentic research report. Given the original "
    "query plus a list of (sub-question, finding, evidence-ids) tuples, "
    "produce a ~500-word markdown report. Cite evidence inline using the "
    "provided ids in square brackets, e.g. [Paper:abc:123]. "
    "Return STRICT JSON: {\"report\": str}."
)


def _build_plan_user(query: str, breadth: int) -> str:
    return (
        f"Original query: {query}\n\n"
        f"Generate exactly {breadth} sub-questions. Each should be specific, "
        f"answerable, and non-overlapping."
    )


def _build_reflect_user(sub: SubQuestion, breadth: int, allow_followups: bool) -> str:
    lines = [
        f"Sub-question: {sub.question}",
        "",
        "Evidence:",
    ]
    if not sub.evidence:
        lines.append("(no evidence found)")
    for ref in sub.evidence:
        snippet = ref.snippet or ref.name
        # WHY: keep snippets short — long evidence lists blow the context.
        snippet = snippet.replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        lines.append(f"- [{ref.cite_id}] {ref.name}: {snippet}")
    lines.append("")
    if allow_followups:
        lines.append(f"Propose up to {breadth} follow-up sub-questions.")
    else:
        lines.append("Do NOT propose follow-up sub-questions (depth budget exhausted).")
    return "\n".join(lines)


def _build_synthesize_user(query: str, subs: Sequence[SubQuestion]) -> str:
    lines = [f"Original query: {query}", "", "Findings:"]
    for sub in subs:
        cites = ", ".join(ref.cite_id for ref in sub.evidence) or "(none)"
        finding = sub.finding or "(no finding)"
        lines.append(f"- Q: {sub.question}")
        lines.append(f"  Finding: {finding}")
        lines.append(f"  Evidence ids: {cites}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Atomic disk write — mirrors batch._write_manifest / schema_drift._atomic_write.
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = (
        f".{os.getpid()}."
        + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        + ".tmp"
    )
    tmp = path.with_name(path.name + suffix)
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _slugify(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:limit] or "query").rstrip("-")


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------


@dataclass
class ResearchSession:
    """End-to-end agentic-research loop, with all backends injected."""

    query: str
    llm: LLMJsonClient
    search: SearchBackend
    output_dir: Path
    breadth: int = 3
    depth: int = 2
    max_iters: int = 6
    top_k_evidence: int = 5
    web: Optional[WebFetcher] = None

    # Filled by run().
    subquestions: List[SubQuestion] = field(default_factory=list)
    builder: ResearchGraphBuilder = field(default_factory=ResearchGraphBuilder)
    source_nodes: List[ResearchNode] = field(default_factory=list)
    hypothesis_nodes: List[ResearchNode] = field(default_factory=list)
    iters_used: int = 0

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def plan(self) -> List[SubQuestion]:
        """Decompose the root query into ``breadth`` sub-questions."""
        payload = self.llm.complete_json(
            system=_PLAN_SYSTEM,
            user=_build_plan_user(self.query, self.breadth),
            schema_name="research-mode-plan-v1",
            cache_key=f"research-mode:plan:{_slugify(self.query, 64)}",
        )
        items = _coerce_subqueries(payload, self.breadth)
        if not items:
            # WHY: a planner failure is recoverable — fall back to a single
            # sub-question that re-runs the loop on the raw query so the
            # caller still gets a graph + report.
            _LOG.warning("research-mode: plan returned no subqueries; using raw query as sole subquery")
            items = [self.query]
        subs = [SubQuestion(question=q, depth=0) for q in items[: self.breadth]]
        self.subquestions.extend(subs)
        return subs

    def search_for(self, sub: SubQuestion) -> List[EvidenceRef]:
        """Gather top-K graph (and optionally web) evidence for one sub-question."""
        evidence: List[EvidenceRef] = []
        try:
            hits = self.search.search_nodes(sub.question, limit=self.top_k_evidence)
        except Exception as exc:  # pragma: no cover — backend should be robust
            _LOG.warning("research-mode: graph search failed for %r: %s", sub.question, exc)
            hits = []
        for hit in hits:
            node_id = str(hit.get("id") or "")
            if not node_id:
                continue
            evidence.append(
                EvidenceRef(
                    node_id=node_id,
                    name=str(hit.get("name") or node_id),
                    snippet=str(hit.get("description") or ""),
                )
            )
        if self.web is not None:
            web_hits: List[Tuple[str, str, str]] = []
            try:
                web_hits = self.web.search(sub.question, limit=self.top_k_evidence)
            except Exception as exc:
                _LOG.warning("research-mode: web search failed for %r: %s", sub.question, exc)
                web_hits = []
            for title, url, snippet in web_hits:
                src_node = self.builder.add_node(
                    title or url,
                    ResearchNodeType.SOURCE_DOCUMENT,
                    description=snippet,
                    source_path=url,
                    metadata={"discovered_in": "research-mode", "url": url},
                    id_seed=url,
                )
                self.source_nodes.append(src_node)
                evidence.append(
                    EvidenceRef(node_id=src_node.id, name=src_node.name, url=url, snippet=snippet)
                )
        sub.evidence = evidence
        return evidence

    def reflect(self, sub: SubQuestion, *, allow_followups: bool) -> List[SubQuestion]:
        """Ask the LLM for a finding + follow-ups + hypotheses for this sub-question."""
        payload = self.llm.complete_json(
            system=_REFLECT_SYSTEM,
            user=_build_reflect_user(sub, self.breadth, allow_followups),
            schema_name="research-mode-reflect-v1",
            cache_key=f"research-mode:reflect:{_slugify(sub.question, 64)}",
        )
        finding, followups, hypotheses = _coerce_reflection(payload, self.breadth)
        sub.finding = finding
        sub.hypotheses = [h["text"] for h in hypotheses]
        # Mint Hypothesis nodes here so the references edges can be wired
        # while we still have the sub.evidence list in hand.
        for hyp in hypotheses:
            hyp_node = self.builder.add_node(
                "Hypothesis: " + _truncate(hyp["text"], 96),
                ResearchNodeType.SESSION_HYPOTHESIS,
                description=hyp["text"],
                metadata={
                    "discovered_in": "research-mode",
                    "parent_question_id": sub.node_id,
                    "query": self.query,
                },
            )
            self.hypothesis_nodes.append(hyp_node)
            valid_evidence_ids = {ref.node_id for ref in sub.evidence if ref.node_id}
            for eid in hyp.get("evidence_ids", []):
                if eid not in valid_evidence_ids:
                    continue
                # WHY: we don't have a ResearchNode handle for graph-hit
                # evidence (they aren't in self.builder); fall back to a
                # synthetic stub that carries the same node id so add_edge
                # records the right (source, target) pair. The builder
                # never re-adds these because they aren't in self._nodes.
                target_node = _synthetic_node_for_id(eid)
                self.builder.add_edge(hyp_node, "references", target_node)
        if not allow_followups:
            return []
        new_subs: List[SubQuestion] = []
        for q in followups[: self.breadth]:
            new_subs.append(SubQuestion(question=q, parent_id=sub.node_id, depth=sub.depth + 1))
        return new_subs

    def synthesize(self) -> str:
        """Final report writer."""
        payload = self.llm.complete_json(
            system=_SYNTHESIZE_SYSTEM,
            user=_build_synthesize_user(self.query, self.subquestions),
            schema_name="research-mode-synthesize-v1",
            cache_key=f"research-mode:synth:{_slugify(self.query, 64)}",
        )
        if isinstance(payload, dict) and isinstance(payload.get("report"), str):
            return payload["report"]
        # Fallback: assemble a deterministic stub from findings so the file is
        # never empty when the synthesizer 500s.
        _LOG.warning("research-mode: synthesize returned no usable report; falling back to deterministic stub")
        return _fallback_report(self.query, self.subquestions)

    # ------------------------------------------------------------------
    # Top-level orchestrator
    # ------------------------------------------------------------------

    def run(self) -> ResearchReport:
        # 1) plan
        roots = self.plan()
        for sub in roots:
            self._mint_question_node(sub)
        # 2-3) BFS search + reflect with depth + iteration budget
        frontier: List[SubQuestion] = list(roots)
        next_frontier: List[SubQuestion] = []
        depth = 0
        while frontier and self.iters_used < self.max_iters:
            for sub in frontier:
                if self.iters_used >= self.max_iters:
                    break
                self.iters_used += 1
                self.search_for(sub)
                allow = depth < self.depth
                children = self.reflect(sub, allow_followups=allow)
                for child in children:
                    self._mint_question_node(child)
                    self.subquestions.append(child)
                    next_frontier.append(child)
            frontier, next_frontier = next_frontier, []
            depth += 1
        # 4) synthesize
        report_text = self.synthesize()
        slug = _slugify(self.query)
        report_path = self.output_dir / f"{slug}.md"
        _atomic_write(report_path, report_text.rstrip() + "\n")
        graph = self.builder.build()
        edge_count = len(graph.edges)
        return ResearchReport(
            report_path=report_path,
            report_text=report_text,
            questions=sum(1 for n in graph.nodes if n.type == ResearchNodeType.OPEN_QUESTION),
            hypotheses=len(self.hypothesis_nodes),
            sources=len(self.source_nodes),
            edges=edge_count,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mint_question_node(self, sub: SubQuestion) -> ResearchNode:
        node = self.builder.add_node(
            "Question: " + _truncate(sub.question, 96),
            ResearchNodeType.OPEN_QUESTION,
            description=sub.question,
            metadata={
                "discovered_in": "research-mode",
                "parent_question_id": sub.parent_id,
                "depth": sub.depth,
                "query": self.query,
            },
        )
        sub.node_id = node.id
        if sub.parent_id:
            parent_stub = _synthetic_node_for_id(sub.parent_id)
            self.builder.add_edge(node, "derived_from", parent_stub)
        return node


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _synthetic_node_for_id(node_id: str) -> ResearchNode:
    """Build a ResearchNode handle for an id that lives in another graph.

    add_edge only reads the id off the node objects we pass in, so we can
    wire references / derived_from edges to externally-resolvable ids
    without forcing those nodes into our local builder. The type assignment
    is irrelevant — the edge model only stores the string id.
    """
    return ResearchNode(id=node_id, name=node_id, type=ResearchNodeType.OPEN_QUESTION)


def _coerce_subqueries(payload: object, breadth: int) -> List[str]:
    if isinstance(payload, dict):
        candidate = payload.get("subqueries") or payload.get("subquestions") or payload.get("questions")
        if isinstance(candidate, list):
            return [str(q).strip() for q in candidate if isinstance(q, (str, int))]
    if isinstance(payload, list):
        return [str(q).strip() for q in payload if isinstance(q, (str, int))]
    return []


def _coerce_reflection(payload: object, breadth: int) -> Tuple[str, List[str], List[Dict[str, object]]]:
    if not isinstance(payload, dict):
        return "", [], []
    finding = str(payload.get("finding") or "").strip()
    raw_followups = payload.get("followups") or payload.get("follow_ups") or []
    followups: List[str] = []
    if isinstance(raw_followups, list):
        followups = [str(q).strip() for q in raw_followups if isinstance(q, (str, int)) and str(q).strip()]
    raw_hypotheses = payload.get("hypotheses") or []
    hypotheses: List[Dict[str, object]] = []
    if isinstance(raw_hypotheses, list):
        for entry in raw_hypotheses:
            if isinstance(entry, str):
                hypotheses.append({"text": entry, "evidence_ids": []})
            elif isinstance(entry, dict) and entry.get("text"):
                ev = entry.get("evidence_ids") or entry.get("evidence") or []
                if not isinstance(ev, list):
                    ev = []
                hypotheses.append(
                    {"text": str(entry["text"]).strip(), "evidence_ids": [str(e) for e in ev if isinstance(e, (str, int))]}
                )
    return finding, followups[:breadth], hypotheses


def _fallback_report(query: str, subs: Sequence[SubQuestion]) -> str:
    lines = [f"# Research report: {query}", ""]
    for sub in subs:
        lines.append(f"## {sub.question}")
        lines.append(sub.finding or "_(no finding)_")
        if sub.evidence:
            cites = ", ".join(f"[{ref.cite_id}]" for ref in sub.evidence)
            lines.append(f"Evidence: {cites}")
        lines.append("")
    return "\n".join(lines)
