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

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

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

    # --- Step 2: PPR expansion (with seed-order fallback) --------------------
    ranked = personalized_pagerank(
        graph, seed_ids, alpha=0.15, top_k=max(1, depth) * 10
    )
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
    synthesized = False
    if synthesize:
        if not os.environ.get("ANTHROPIC_API_KEY"):  # PITFALL 4
            raise ValueError("synthesize=true requires ANTHROPIC_API_KEY")
        from .llm_synthesis import LlmSynthesisClient, LlmSynthesisRequest

        req = LlmSynthesisRequest(
            kind="ContextSummary",
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
        resp = LlmSynthesisClient(max_tokens=1200).synthesize(req)
        if resp:
            body_text = resp.body + "\n\n---\n" + body_text
            synthesized = True

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
