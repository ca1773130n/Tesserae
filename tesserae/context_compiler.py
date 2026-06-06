"""On-demand context compilation — Pillar 3 (CTX-01).

``compile_context`` is a *pure* function that turns a free-form query (or a set
of explicit seeds) into a tailored, fully-cited, agent-ready markdown bundle by
COMPOSING the existing retrieval stack. No new ranking algorithm lives here.

Pipeline
--------
1. **Seed resolution** — explicit seeds (kept iff they exist in the graph)
   first, then :func:`tesserae.retrieval.hybrid.hybrid_search` results, deduped
   with stable order.
2. **PPR expansion** — :func:`tesserae.retrieval.ppr.personalized_pagerank`
   ranks the k-hop neighbourhood. If PPR returns nothing (disconnected seeds),
   we fall back to the seed order so the bundle is never empty.
3. **Budget-bound selection** — walk the PPR order, including each node's cited
   body until the next body would overflow ``budget`` (``budget <= 0`` = uncapped).
4. **Cited markdown assembly** — one section per selected node + a trailing
   ``## Citations`` block. The no-LLM body embeds NO wall-clock timestamp, so it
   is byte-identical for the same ``(graph, query, seeds, depth, budget)``.
5. **Optional LLM synthesis** — only when ``synthesize=True`` AND an
   ``ANTHROPIC_API_KEY`` is present; otherwise the deterministic assembly stands.

The returned :class:`ContextBundle` is in-memory only — nothing is written under
``.tesserae/`` (the bundle is an on-demand projection, not part of compile, and
must not perturb byte-idempotence).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from .research_graph import ResearchGraph, ResearchNode
from .retrieval.hybrid import hybrid_search
from .retrieval.ppr import personalized_pagerank
from .wiki_projector import kind_for_node
from .wiki_store import WikiPageStore

__all__ = ["compile_context", "ContextBundle", "ContextCitation"]


@dataclass(frozen=True)
class ContextCitation:
    """A single resolvable citation in a :class:`ContextBundle`."""

    node_id: str
    node_name: str
    source_path: Optional[str]
    wiki_kind: Optional[str]


@dataclass(frozen=True)
class ContextBundle:
    """The compiled, cited context document plus its provenance metadata."""

    query: str
    seeds_used: List[str] = field(default_factory=list)
    ranked_nodes: List[str] = field(default_factory=list)
    selected_nodes: List[str] = field(default_factory=list)
    citations: List[ContextCitation] = field(default_factory=list)
    body: str = ""
    synthesized: bool = False
    char_budget_used: int = 0
    char_budget_total: int = 0


def _fetch_body(node: ResearchNode, store: Optional[WikiPageStore]) -> str:
    """Return the best available body text for ``node``, degrading gracefully.

    Prefer the projected wiki page body (when a ``store`` and a public wiki kind
    exist); fall back to the node description, then a minimal stub. Filesystem
    errors are swallowed (PITFALL 2 — degrade, never raise).
    """
    if store is not None:
        try:
            kind = kind_for_node(node)
            if kind:
                slug = store.slug_for(node.name)
                path = store.path_for(kind, slug)
                if path.exists():
                    return store.read_page(path).body
        except (FileNotFoundError, OSError):
            pass
    return node.description or f"_{node.type.value} node: {node.name}_"


_TRUNCATION_MARKER = "\n…[truncated]"


def _truncate_to_budget(body: str, budget: int) -> str:
    """Truncate ``body`` to ``<= budget`` chars at a word/newline boundary.

    Appends :data:`_TRUNCATION_MARKER` so the cut is visible to the agent, and
    guarantees the returned string (marker included) never exceeds ``budget``.
    Deterministic — no wall-clock — so byte-idempotence holds. When ``budget`` is
    too small to fit even the marker, fall back to a hard char slice (the marker
    would itself overflow), preserving the ``<= budget`` invariant.
    """
    if len(body) <= budget:
        return body
    keep = budget - len(_TRUNCATION_MARKER)
    if keep <= 0:
        # No room for the marker — hard slice to honour the budget.
        return body[:budget]
    head = body[:keep]
    # Prefer the last newline, then the last space, to land on a clean boundary.
    cut = max(head.rfind("\n"), head.rfind(" "))
    if cut > 0:
        head = head[:cut]
    return head + _TRUNCATION_MARKER


def _neighborhood_within_depth(
    graph: ResearchGraph, seed_ids: Sequence[str], depth: int
) -> Set[str]:
    """Return the set of node ids reachable from any seed in ``<= depth`` hops.

    BFS over the UNDIRECTED edge set (each edge traversable both ways, matching
    ``personalized_pagerank``'s default ``directed=False``). ``depth <= 0``
    collapses to just the seeds themselves. The returned set always contains the
    valid seeds so PPR seeded on them never runs over an empty subgraph.
    """
    adjacency: Dict[str, Set[str]] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)

    reachable: Set[str] = set(seed_ids)
    frontier: deque[tuple] = deque((sid, 0) for sid in seed_ids)
    while frontier:
        node_id, dist = frontier.popleft()
        if dist >= depth:
            continue
        for neighbor in adjacency.get(node_id, ()):  # noqa: SIM118
            if neighbor not in reachable:
                reachable.add(neighbor)
                frontier.append((neighbor, dist + 1))
    return reachable


def compile_context(
    graph: ResearchGraph,
    project_root: Optional[str] = None,
    query: str = "",
    seeds: Optional[Sequence[str]] = None,
    depth: int = 2,
    budget: int = 32_000,
    synthesize: bool = False,
    backend=None,
) -> ContextBundle:
    """Compile a tailored, cited context bundle for ``query`` / ``seeds``.

    See the module docstring for the full pipeline. Pure function: returns a
    :class:`ContextBundle` and writes nothing to disk.
    """
    node_index = {n.id: n for n in graph.nodes}

    # --- Step 1: seed resolution (explicit first, then hybrid, deduped) ------
    seed_ids: List[str] = []
    seen = set()
    if seeds:
        for sid in seeds:
            if sid in node_index and sid not in seen:
                seed_ids.append(sid)
                seen.add(sid)
    if query and query.strip():
        result = hybrid_search(
            graph, query, top_k=max(1, depth) * 5, backend=backend
        )
        for scored in result.scored:
            nid = scored.node.id
            if nid not in seen:
                seed_ids.append(nid)
                seen.add(nid)

    # Empty query + no valid seeds -> empty-but-valid bundle.
    if not seed_ids:
        return ContextBundle(
            query=query,
            seeds_used=[],
            ranked_nodes=[],
            selected_nodes=[],
            citations=[],
            body=f"# Context: {query}\n\n---\n## Citations\n",
            synthesized=False,
            char_budget_used=0,
            char_budget_total=budget,
        )

    # --- Step 2: PPR expansion, bounded to the depth-hop neighbourhood -------
    # ``depth`` must bound hop-distance, not just scale ``top_k``: PPR runs over
    # the FULL connected component, so without this filter a depth=1 request can
    # surface nodes only reachable in 2+ hops. We precompute the seed
    # neighbourhood up to ``depth`` hops (BFS over the undirected edge set) and
    # keep only PPR results that fall inside it.
    #
    # The depth filter must run BEFORE the ``top_k`` cap, not after — otherwise
    # out-of-depth high-PPR nodes consume the window and valid in-depth nodes get
    # dropped. We request the FULL PPR ranking (``top_k = node count``), filter to
    # the in-depth set, THEN cap, so the cap is filled from in-depth nodes only.
    in_neighborhood = _neighborhood_within_depth(graph, seed_ids, max(0, depth))
    cap = max(1, depth) * 10
    full_ranked = personalized_pagerank(
        graph, seed_ids, alpha=0.15, top_k=max(1, len(graph.nodes))
    )
    ranked = [
        (nid, score) for nid, score in full_ranked if nid in in_neighborhood
    ][:cap]
    if not ranked:  # PITFALL 1: disconnected seeds -> fall back to seed order.
        ranked = [(sid, 0.0) for sid in seed_ids]
    ranked_nodes = [nid for nid, _ in ranked]

    # --- Step 3: budget-bound selection (deterministic, PPR order) ----------
    store: Optional[WikiPageStore] = None
    if project_root is not None:
        store = WikiPageStore(Path(project_root) / ".tesserae" / "wiki")

    selected: List[tuple] = []  # (node, body)
    chars_used = 0
    for node_id, _score in ranked:
        node = node_index.get(node_id)
        if node is None:
            continue
        body = _fetch_body(node, store)
        if budget > 0 and chars_used + len(body) > budget:
            # A valid query must never yield an empty bundle just because the
            # first ranked body overflows the budget: always include the FIRST
            # selectable node, truncating its body to fit. Subsequent overflows
            # stop the walk as before.
            if not selected:
                truncated = _truncate_to_budget(body, budget) if budget > 0 else body
                selected.append((node, truncated))
                chars_used += len(truncated)
            break
        selected.append((node, body))
        chars_used += len(body)

    # --- Step 4: assemble cited markdown ------------------------------------
    sections: List[str] = [f"# Context: {query}\n"]
    citations: List[ContextCitation] = []
    for i, (node, body) in enumerate(selected, 1):
        anchor = f"node-{i}"
        sections.append(f"\n## [{node.name}][{anchor}]\n\n{body}\n")
        citations.append(
            ContextCitation(
                node_id=node.id,
                node_name=node.name,
                source_path=node.source_path,
                wiki_kind=kind_for_node(node),
            )
        )
    sections.append("\n---\n## Citations\n")
    for i, c in enumerate(citations, 1):
        target = c.source_path or c.node_id
        sections.append(
            f"[node-{i}]: {target}  <!-- node_id={c.node_id} -->\n"
        )
    body_text = "".join(sections)

    # --- Step 5: optional, gated LLM synthesis ------------------------------
    # PITFALL 4 — degrade, NEVER raise. ANY missing SDK / missing key / API
    # failure falls back to the deterministic ``body_text`` assembled above. The
    # module docstring promises graceful fallback, so synthesis is purely
    # additive: when it works we prepend the synthesized body; otherwise the
    # deterministic bundle stands unchanged.
    synthesized = False
    if synthesize:
        try:
            from .llm_synthesis import LlmSynthesisRequest, LlmSynthesizer

            req = LlmSynthesisRequest(
                # ``topic`` is the VALID synthesis kind for a narrative context
                # summary over a set of related nodes (see _VALID_KINDS).
                kind="topic",
                title=query or "Context Summary",
                inputs=[
                    {
                        "id": c.node_id,
                        "name": c.node_name,
                        "description": node.description,
                    }
                    for (node, _b), c in zip(selected, citations)
                ],
            )
            resp = LlmSynthesizer(max_tokens=1200).synthesize(req)
            if resp:
                body_text = resp.body + "\n\n---\n" + body_text
                synthesized = True
        except Exception:
            # Missing anthropic SDK, missing API key, network/API error — keep
            # the deterministic assembly. Synthesis is best-effort only.
            synthesized = False

    return ContextBundle(
        query=query,
        seeds_used=seed_ids,
        ranked_nodes=ranked_nodes,
        selected_nodes=[n.id for n, _ in selected],
        citations=citations,
        body=body_text,
        synthesized=synthesized,
        char_budget_used=chars_used,
        char_budget_total=budget,
    )
