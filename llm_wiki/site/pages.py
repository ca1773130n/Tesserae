"""Page-template renderers for the LLM-Wiki static site.

One function per route in §3.1 of the redesign spec. Each renderer takes a
``SiteContext`` plus the relevant ``WikiPage`` (for detail pages) and returns
a complete HTML document. Renderers never reach back into the graph globally:
``SiteContext`` carries every precomputed index they need.

The page anatomy follows §3.3: breadcrumbs, eyebrow (type · last updated ·
≈ reading time), title, optional TOC right rail, markdown body, Mentions,
Related (4-signal), Source provenance, Activity sparkline, AI siblings footer.

Components (``page_shell``, ``breadcrumbs``, ``card``, ``badge``,
``node_table``, ``edge_list``, ``sparkline_svg``, ``heatmap_svg``,
``ai_siblings_footer``, ``toc``) come from :mod:`llm_wiki.site.components` —
Subagent D owns those primitives. ``top_related`` comes from
:mod:`llm_wiki.site.relevance`. Both are imported eagerly; missing modules
are a build-time bug worth surfacing rather than papering over.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ..research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from ..wiki_store import WikiPage
from .components import (
    ai_siblings_footer,
    badge,
    breadcrumbs,
    card,
    edge_list,
    heatmap_svg,
    node_table,
    page_shell,
    sparkline_svg,
    toc,
)
from .markdown import render_markdown, strip_frontmatter
from .relevance import RelevanceContext, top_related
from .search import WIKI_LAYER_TYPES


# ---------------------------------------------------------------------------
# routing helpers (private to this module — F owns the public ones)
# ---------------------------------------------------------------------------


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _canonical_slug(value: str) -> str:
    """Stable URL-safe slug — byte-identical to :func:`WikiPageStore.slug_for`.

    Lifted into this module (rather than imported) so the renderers stay
    independent of ``wiki_store``'s public API surface; the algorithm is the
    same so wiki pages on disk and HTML hrefs always agree.
    """
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    if len(safe.encode("utf-8")) > 96:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        safe = (
            safe.encode("utf-8")[:80].decode("utf-8", errors="ignore").strip("-")
            + "-" + digest
        )
    return safe or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


# Legacy alias kept for any internal callers; new code should use
# ``_canonical_slug`` (which matches WikiPageStore on disk).
_slug = _canonical_slug


def _safe_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")


# Single source of truth for kind -> URL segment.
ROUTE_FOR_KIND: Dict[str, str] = {
    "sources": "sources",
    "concepts": "concepts",
    "entities": "entities",
    "papers": "papers",
    "repos": "repos",
    "topics": "topics",
    "syntheses": "syntheses",
    "questions": "questions",
}


# Cap on the number of nodes shipped to the interactive graph view. Beyond
# ~1500 the browser-side force simulation gets sluggish on mid-range hardware,
# so we drop low-degree nodes first when we exceed this. The exported
# ``graph.json`` is unaffected — this only bounds the page-embedded payload.
MAX_GRAPH_NODES: int = 1500


def page_href(kind: str, slug: str) -> str:
    """Relative URL (from site root) for a wiki-layer page.

    Returns ``""`` for any kind that has no public route (CodeClass etc.) so
    callers that walk the graph never accidentally mint a code-layer URL.
    """
    if kind not in ROUTE_FOR_KIND:
        return ""
    return f"{ROUTE_FOR_KIND[kind]}/{slug}.html"


def kind_for_node(node: ResearchNode) -> Optional[str]:
    """Return the public wiki kind for ``node``, or ``None``.

    Tiny pass-through over :func:`_kind_for_node_type` so external callers
    (and the internal link helpers below) can ask the question once with a
    ``ResearchNode`` in hand. Mirrors the ``_KIND_FOR_TYPE`` table in
    ``wiki_projector`` — keep them consistent.
    """
    return _kind_for_node_type(node.type)


def node_href(node: ResearchNode, ctx: "Optional[SiteContext]" = None) -> str:
    """Single source of truth for "what URL does this node live at?".

    Looks up the on-disk wiki page slug via ``ctx.page_slug_for_node`` first
    (the authoritative mapping written by the projectors). Falls back to
    ``slug_for(node.name)`` when no page exists yet — that is the slug
    :class:`WikiLayerProjector` would mint for this node on the next
    compile, so the link is still self-consistent.
    """
    kind = kind_for_node(node)
    if not kind:
        return ""
    if ctx is not None:
        slug = ctx.page_slug_for_node.get(node.id)
        if slug:
            return page_href(kind, slug)
    return page_href(kind, _canonical_slug(node.name))


_CONCEPT_TYPES = {
    ResearchNodeType.CONCEPT,
    ResearchNodeType.TECHNICAL_TERM,
    ResearchNodeType.ALGORITHM,
    ResearchNodeType.ARCHITECTURE_PATTERN,
    ResearchNodeType.METHODOLOGICAL_CONCEPT,
    ResearchNodeType.MATHEMATICAL_CONCEPT,
    ResearchNodeType.TRAINING_PARADIGM,
    ResearchNodeType.INFERENCE_STRATEGY,
    ResearchNodeType.EVALUATION_PROTOCOL,
    ResearchNodeType.OBJECTIVE_FUNCTION,
    ResearchNodeType.TASK,
    ResearchNodeType.CAPABILITY,
}
_ENTITY_TYPES = {
    ResearchNodeType.MODEL,
    ResearchNodeType.DATASET,
    ResearchNodeType.BENCHMARK,
    ResearchNodeType.METRIC,
    ResearchNodeType.ORGANIZATION,
    ResearchNodeType.PERSON,
}
_TOPIC_TYPES = {
    ResearchNodeType.RESEARCH_FIELD,
    ResearchNodeType.RESEARCH_TOPIC,
    ResearchNodeType.PROBLEM_AREA,
    ResearchNodeType.APPROACH_FAMILY,
    ResearchNodeType.TREND,
}


def _kind_for_node_type(node_type: ResearchNodeType) -> Optional[str]:
    """Map a graph node type onto its public wiki kind, or ``None``.

    ``None`` means the type has no public detail page (CodeClass /
    CodeFunction / CodeModule / Dependency / EvidenceSpan / SourceFile /
    Claim variants). Those types stay in ``graph.json`` for MCP/Cognee
    consumers but never get a URL of their own.
    """
    if node_type == ResearchNodeType.SOURCE_DOCUMENT:
        return "sources"
    if node_type == ResearchNodeType.PAPER:
        return "papers"
    if node_type in {ResearchNodeType.REPOSITORY, ResearchNodeType.CODE_PROJECT, ResearchNodeType.PROJECT}:
        return "repos"
    if node_type == ResearchNodeType.OPEN_QUESTION:
        return "questions"
    if node_type == ResearchNodeType.SYNTHESIS:
        return "syntheses"
    if node_type in _CONCEPT_TYPES:
        return "concepts"
    if node_type in _ENTITY_TYPES:
        return "entities"
    if node_type in _TOPIC_TYPES:
        return "topics"
    return None


# ---------------------------------------------------------------------------
# SiteContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SiteContext:
    """Carries every precomputed index a page renderer needs.

    Built once at the top of the build by Subagent G's StaticSiteBuilder and
    threaded through every renderer. Renderers never call back into the
    graph or filesystem — everything they need is here.
    """

    site_title: str
    graph: ResearchGraph
    wiki_pages_by_kind: Mapping[str, Sequence[WikiPage]]
    nodes_by_id: Mapping[str, ResearchNode] = field(default_factory=dict)
    nodes_by_kind: Mapping[str, Sequence[ResearchNode]] = field(default_factory=dict)
    nodes_by_name: Mapping[str, ResearchNode] = field(default_factory=dict)
    outgoing: Mapping[str, Sequence[ResearchEdge]] = field(default_factory=dict)
    incoming: Mapping[str, Sequence[ResearchEdge]] = field(default_factory=dict)
    type_counts: Mapping[str, int] = field(default_factory=dict)
    source_counts: Mapping[str, int] = field(default_factory=dict)
    activity_weeks: Sequence[Sequence[int]] = field(default_factory=tuple)
    relevance: Optional[RelevanceContext] = None
    # node_id → on-disk wiki page slug. Lets the link helpers resolve a
    # synthesis node ("Project pulse") to the slug its page actually lives at
    # on disk (``pulse``) rather than minting ``project-pulse`` from the
    # node name and 404'ing.
    page_slug_for_node: Mapping[str, str] = field(default_factory=dict)
    # node_id → 12-week mention counts (newest week last). Empty list means
    # the renderer should pass a zero-filled list of length 12 to
    # :func:`sparkline_svg` so the page still gets a placeholder spark.
    activity_by_node_id: Mapping[str, Sequence[int]] = field(default_factory=dict)
    # source_path (data/research/...md style) → lower-cased body text.
    # Used by the per-page "Cross-references in raw data" section to surface
    # every research markdown that mentions a node by name.
    source_body_by_path: Mapping[str, str] = field(default_factory=dict)
    # source_path → SourceDocument node-id, used to link a node's
    # ``source_path`` back to its source detail page.
    node_id_for_source_path: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        graph: ResearchGraph,
        wiki_pages_by_kind: Mapping[str, Sequence[WikiPage]],
        site_title: str = "LLM-Wiki",
    ) -> "SiteContext":
        nodes_by_id = {n.id: n for n in graph.nodes}
        outgoing: Dict[str, List[ResearchEdge]] = defaultdict(list)
        incoming: Dict[str, List[ResearchEdge]] = defaultdict(list)
        for edge in graph.edges:
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)

        nodes_by_kind: Dict[str, List[ResearchNode]] = defaultdict(list)
        for node in graph.nodes:
            kind = _kind_for_node_type(node.type)
            if kind:
                nodes_by_kind[kind].append(node)

        nodes_by_name = {n.name.casefold(): n for n in graph.nodes}

        # Build node_id → page-slug. Two passes:
        #   1) frontmatter ``node_id`` (WikiLayerProjector emits this).
        #   2) title match (SynthesisProjector does *not* emit ``node_id``;
        #      its synthesis nodes share ``name == page.title``).
        page_slug_for_node: Dict[str, str] = {}
        title_to_slug_by_kind: Dict[str, Dict[str, str]] = {}
        for kind, kind_pages in wiki_pages_by_kind.items():
            kind_index: Dict[str, str] = {}
            for page in kind_pages:
                fm = page.frontmatter or {}
                nid = fm.get("node_id") if isinstance(fm, dict) else None
                if isinstance(nid, str) and nid and nid not in page_slug_for_node:
                    page_slug_for_node[nid] = page.slug
                if page.title:
                    kind_index.setdefault(page.title.casefold(), page.slug)
            title_to_slug_by_kind[kind] = kind_index
        for node in graph.nodes:
            if node.id in page_slug_for_node:
                continue
            kind = _kind_for_node_type(node.type)
            if not kind:
                continue
            slug = title_to_slug_by_kind.get(kind, {}).get(node.name.casefold())
            if slug:
                page_slug_for_node[node.id] = slug

        try:
            relevance = RelevanceContext.from_graph(graph)
        except Exception:
            relevance = None

        # source_path → SourceDocument node-id mapping so we can render a
        # provenance link on detail pages. Uses ``source_path`` for source-kind
        # nodes only; everything else inherits via shared ``source_path``.
        node_id_for_source_path: Dict[str, str] = {}
        for node in graph.nodes:
            if node.source_path and node.type == ResearchNodeType.SOURCE_DOCUMENT:
                node_id_for_source_path.setdefault(node.source_path, node.id)

        # Cache the lowercased body of every raw data markdown file referenced
        # by a node's ``source_path`` so the "Cross-references in raw data"
        # section can do a cheap case-insensitive substring search without
        # re-reading the disk for each detail page. We deliberately read the
        # original on-disk files (not the projected wiki bodies) — the user
        # wants matches against the *raw* corpus content. Failures are
        # silently skipped so a stale graph entry doesn't break the build.
        source_body_by_path: Dict[str, str] = {}
        seen_paths: set[str] = set()
        for node in graph.nodes:
            sp = node.source_path or ""
            if not sp or sp in seen_paths:
                continue
            seen_paths.add(sp)
            try:
                p = Path(sp)
                if p.is_file() and p.suffix.lower() == ".md":
                    source_body_by_path[sp] = p.read_text(
                        encoding="utf-8", errors="replace"
                    ).lower()
            except OSError:
                continue

        activity_by_node_id = _activity_by_node_id(graph, weeks=12)

        return cls(
            site_title=site_title,
            graph=graph,
            wiki_pages_by_kind={k: list(v) for k, v in wiki_pages_by_kind.items()},
            nodes_by_id=nodes_by_id,
            nodes_by_kind={k: list(v) for k, v in nodes_by_kind.items()},
            nodes_by_name=nodes_by_name,
            outgoing={k: list(v) for k, v in outgoing.items()},
            incoming={k: list(v) for k, v in incoming.items()},
            type_counts=Counter(n.type.value for n in graph.nodes),
            source_counts=Counter(n.source_path or "unknown" for n in graph.nodes),
            activity_weeks=_activity_weeks(graph, weeks=26),
            relevance=relevance,
            page_slug_for_node=page_slug_for_node,
            activity_by_node_id=activity_by_node_id,
            source_body_by_path=source_body_by_path,
            node_id_for_source_path=node_id_for_source_path,
        )


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _activity_by_node_id(graph: ResearchGraph, weeks: int) -> Dict[str, List[int]]:
    """Return per-node weekly mention counts (length ``weeks``, newest last).

    Counts how many distinct dated source paths each node touches, then bins
    those dates into ``weeks`` evenly-spaced columns matching the
    :func:`_activity_weeks` global heatmap. Nodes with no dated source paths
    return an empty list — callers fall back to a zero list of length 12 so
    the sparkline component still renders an empty placeholder.
    """
    # Collect (node_id, date) hits via the node's own source_path AND any
    # extra ``source_paths`` recorded in metadata (synthesis nodes use the
    # latter to point at multiple daily digests).
    hits_by_node: Dict[str, List[str]] = {}
    all_dates: set[str] = set()
    for node in graph.nodes:
        dates: List[str] = []
        if node.source_path:
            m = _DATE_RE.search(node.source_path)
            if m:
                dates.append(m.group(1))
        meta = node.metadata or {}
        if isinstance(meta, dict):
            extra = meta.get("source_paths")
            if isinstance(extra, (list, tuple, set)):
                for sp in extra:
                    if isinstance(sp, str):
                        m = _DATE_RE.search(sp)
                        if m:
                            dates.append(m.group(1))
        if dates:
            hits_by_node[node.id] = dates
            all_dates.update(dates)

    if not all_dates:
        return {}

    sorted_all = sorted(all_dates)
    n = len(sorted_all)
    # Map each unique date to a column index in [0, weeks).
    column_for_date: Dict[str, int] = {}
    for idx, d in enumerate(sorted_all):
        column_for_date[d] = min(weeks - 1, int(idx * weeks / max(n, 1)))

    out: Dict[str, List[int]] = {}
    for node_id, dates in hits_by_node.items():
        bucket = [0] * weeks
        for d in dates:
            bucket[column_for_date[d]] += 1
        out[node_id] = bucket
    return out


def _activity_weeks(graph: ResearchGraph, weeks: int) -> List[List[int]]:
    """Return ``weeks`` columns of 7-day buckets for ``heatmap_svg``.

    We bucket nodes by the date string in their ``source_path`` (if any) and
    spread them across the requested window. The resulting shape matches
    Subagent D's ``heatmap_svg`` contract: a list of week-columns, each
    column a list of 7 ints.
    """
    counts: Counter[str] = Counter()
    for node in graph.nodes:
        if not node.source_path:
            continue
        m = _DATE_RE.search(node.source_path)
        if m:
            counts[m.group(1)] += 1
    grid: List[List[int]] = [[0] * 7 for _ in range(weeks)]
    if not counts:
        return grid
    sorted_dates = sorted(counts)
    n = len(sorted_dates)
    for idx, date in enumerate(sorted_dates):
        col = min(weeks - 1, int(idx * weeks / max(n, 1)))
        # fan out across the 7-day column deterministically.
        row = idx % 7
        grid[col][row] += counts[date]
    return grid


# ---------------------------------------------------------------------------
# Markdown body rendering
# ---------------------------------------------------------------------------
#
# The actual engine lives in ``markdown.py``. The wrapper below adds a
# wiki-link rewriter so ``[Foo](papers/foo.md)`` and friends point at the
# emitted HTML neighbour, using the same canonical slug ``WikiPageStore``
# uses on disk.


_WIKI_LINK_KINDS = set(ROUTE_FOR_KIND)


def _wiki_link_rewriter(target: str) -> str:
    """Rewrite ``papers/foo.md`` → ``papers/foo.html`` for cross-page links.

    Leaves external URLs, anchors, and unknown targets alone. The slug stem
    is normalised through :func:`_canonical_slug` so a body that wrote
    ``[Foo Bar](papers/Foo Bar.md)`` still resolves cleanly.
    """
    if not target or target.startswith(("#", "http://", "https://", "mailto:", "data:", "javascript:")):
        return target
    # ``//host/path`` is protocol-relative — treat as external.
    if target.startswith("//"):
        return target
    # Strip any fragment / query while we work, restore at the end.
    fragment = ""
    query = ""
    rest = target
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
        fragment = "#" + fragment
    if "?" in rest:
        rest, query = rest.split("?", 1)
        query = "?" + query
    if not rest.endswith(".md"):
        return target
    parts = rest.split("/")
    if len(parts) >= 2 and parts[-2] in _WIKI_LINK_KINDS:
        kind = parts[-2]
        stem = parts[-1][:-len(".md")]
        slug = _canonical_slug(stem) or stem
        prefix = "/".join(parts[:-2])
        rewritten = f"{kind}/{slug}.html"
        if prefix:
            rewritten = f"{prefix}/{rewritten}"
        return rewritten + query + fragment
    # Fallback: drop ``.md`` for ``.html`` so a link still resolves to a
    # neighbour file rather than the markdown source.
    return rest[: -len(".md")] + ".html" + query + fragment


def _render_markdown(body: str) -> Tuple[str, List[Tuple[int, str, str]]]:
    """Render markdown ``body`` and return ``(html, headings)``.

    Headings are returned as ``(level, text, anchor)`` triples for the TOC
    component. Frontmatter (``---\\n…\\n---``) is stripped before rendering
    so the YAML never bleeds through as visible text.
    """
    html_out, heading_objs = render_markdown(body, link_rewriter=_wiki_link_rewriter)
    headings: List[Tuple[int, str, str]] = [
        (h.level, h.text, h.anchor) for h in heading_objs
    ]
    return html_out, headings


# ---------------------------------------------------------------------------
# Adapters: convert internal data into the dict shapes D's components expect.
# ---------------------------------------------------------------------------


def _node_table_rows(
    nodes: Sequence[ResearchNode],
    *,
    depth: int,
    ctx: Optional[SiteContext] = None,
) -> List[dict]:
    rows: List[dict] = []
    for n in nodes:
        href = node_href(n, ctx)
        if not href:
            continue
        rows.append({
            "title": n.name,
            "href": href,
            "kind": n.type.value,
            "mentions": "",
            "source": n.source_path or "",
        })
    return rows


def _edge_list_rows(
    edges: Sequence[ResearchEdge], ctx: SiteContext, *, outgoing: bool
) -> List[dict]:
    rows: List[dict] = []
    for edge in edges:
        other_id = edge.target if outgoing else edge.source
        other = ctx.nodes_by_id.get(other_id)
        if other is None:
            continue
        rows.append({
            "relation": edge.type,
            "other_title": other.name,
            "other_href": node_href(other, ctx),
        })
    return rows


def _build_breadcrumbs(trail: Sequence[Tuple[str, str]], depth: int) -> str:
    """Adapt to D's ``breadcrumbs(items: list[(label, href)])`` signature.

    ``trail`` items are ``(label, root_relative_href)`` — for the *current*
    page the href is ignored by D's renderer (it is still annotated with
    ``aria-current="page"``). We rewrite hrefs with the depth prefix so a
    leaf page two levels deep links back correctly.
    """
    prefix = "../" * max(depth, 0)
    items: List[Tuple[str, str]] = []
    for label, href in trail:
        items.append((label, (prefix + href) if href else ""))
    return breadcrumbs(items)


def _nav_counts(ctx: SiteContext) -> Dict[str, int]:
    return {
        kind: max(
            len(ctx.wiki_pages_by_kind.get(kind, [])),
            len(ctx.nodes_by_kind.get(kind, [])),
        )
        for kind in ROUTE_FOR_KIND
    }


def _reading_time_minutes(body: str) -> int:
    words = max(1, len(body.split()))
    return max(1, round(words / 220))


def _eyebrow(kind: str, page: WikiPage) -> str:
    fm = page.frontmatter or {}
    updated = fm.get("generated_at") or fm.get("updated") or ""
    minutes = _reading_time_minutes(page.body)
    parts = [kind]
    if updated:
        parts.append(str(updated))
    parts.append(f"≈ {minutes} min read")
    return f'<p class="eyebrow">{_esc(" · ".join(parts))}</p>'


def _find_node_for_page(ctx: SiteContext, page: WikiPage) -> Optional[ResearchNode]:
    fm = page.frontmatter or {}
    nid = fm.get("node_id")
    if isinstance(nid, str) and nid in ctx.nodes_by_id:
        return ctx.nodes_by_id[nid]
    return ctx.nodes_by_name.get(page.title.casefold())


def _related_html(ctx: SiteContext, node: ResearchNode, *, depth: int, k: int = 8) -> str:
    related: List[Tuple[ResearchNode, float]] = []
    if ctx.relevance is not None:
        for other_id, score in top_related(node.id, ctx.relevance, limit=k):
            other = ctx.nodes_by_id.get(other_id)
            if not other:
                continue
            kind = _kind_for_node_type(other.type)
            if kind is None:
                continue
            related.append((other, score))

    if not related:
        # Cheap fallback: rank candidates by neighbour overlap.
        own = {e.target for e in ctx.outgoing.get(node.id, [])} | {
            e.source for e in ctx.incoming.get(node.id, [])
        }
        scored: List[Tuple[ResearchNode, float]] = []
        for other in ctx.graph.nodes:
            if other.id == node.id:
                continue
            kind = _kind_for_node_type(other.type)
            if kind is None:
                continue
            their = {e.target for e in ctx.outgoing.get(other.id, [])} | {
                e.source for e in ctx.incoming.get(other.id, [])
            }
            overlap = len(own & their)
            same_source = 1 if (other.source_path and other.source_path == node.source_path) else 0
            same_type = 1 if other.type == node.type else 0
            score = float(overlap * 3 + same_source * 2 + same_type)
            if score > 0:
                scored.append((other, score))
        scored.sort(key=lambda x: (-x[1], x[0].name))
        related = scored[:k]

    if not related:
        return '<p class="muted">No related items yet.</p>'

    prefix = "../" * max(depth, 0)
    cards: List[str] = []
    for other, score in related:
        href = node_href(other, ctx)
        if not href:
            continue
        cards.append(card(
            title=other.name,
            href=prefix + href,
            kind_label=other.type.value,
            description=other.description or "",
            footer=f"score {score:.2f}",
        ))
    return '<div class="cards">' + "".join(cards) + "</div>"


def _mentions_html(ctx: SiteContext, node: ResearchNode, *, depth: int) -> str:
    rows = _edge_list_rows(ctx.incoming.get(node.id, []), ctx, outgoing=False)
    if not rows:
        return '<p class="muted">No mentions yet.</p>'
    return edge_list(rows, depth=depth)


# Match a leading ATX H1 heading. Used to strip the duplicate body-title from
# detail pages whose markdown body opens with ``# Same as frontmatter title``.
_LEADING_H1_RE = re.compile(r"\A\s*#\s+(.+?)\s*#*\s*\n+", re.MULTILINE)


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _strip_duplicate_h1(body: str, title: str) -> str:
    """Remove a leading ``# Title`` from ``body`` if it matches ``title``.

    The page header already renders the title above the markdown body; when
    the body starts with the same H1 the user sees the title twice. We only
    strip when the leading heading matches the frontmatter title (after
    whitespace+case normalization) so legitimate document H1s with different
    text are preserved.
    """
    if not body or not title:
        return body
    match = _LEADING_H1_RE.match(body)
    if not match:
        return body
    if _normalize_title(match.group(1)) != _normalize_title(title):
        return body
    return body[match.end():]


def _cross_refs_in_raw(ctx: SiteContext, node: ResearchNode) -> List[Tuple[str, str]]:
    """Return up to 12 ``(label, href)`` raw-data pages mentioning ``node``.

    Substring-matches the node's name (case-insensitive) against every
    cached source body. ``href`` is rendered relative to a leaf detail page
    one directory deep — i.e. ``../sources/<slug>.html`` if the source has
    a wiki page, else a tooltip-style ``link-broken`` anchor pointing to
    the raw ``data/.../foo.md`` path inside the source-path string. Hits
    above the cap fall off; ordering is the cached map's insertion order
    so it stays stable across compiles.
    """
    name = node.name or ""
    if not name or len(name) < 3 or not ctx.source_body_by_path:
        return []
    needle = name.lower()
    own_path = node.source_path or ""
    out: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for src_path, body in ctx.source_body_by_path.items():
        if src_path == own_path:
            continue
        if needle not in body:
            continue
        if src_path in seen:
            continue
        seen.add(src_path)
        out.append((src_path, src_path))
        if len(out) >= 12:
            break
    return out


def _cross_refs_html(
    ctx: SiteContext, node: ResearchNode, *, depth: int
) -> str:
    refs = _cross_refs_in_raw(ctx, node)
    if not refs:
        return '<p class="muted">No raw-data mentions found.</p>'
    prefix = "../" * max(depth, 0)
    items: List[str] = []
    for label, src_path in refs:
        # Resolve to the source detail page if we have one.
        src_node_id = ctx.node_id_for_source_path.get(src_path)
        href = ""
        if src_node_id:
            other = ctx.nodes_by_id.get(src_node_id)
            if other is not None:
                rel = node_href(other, ctx)
                if rel:
                    href = prefix + rel
        if href:
            items.append(
                f'<li><a href="{_esc(href)}"><code>{_esc(label)}</code></a></li>'
            )
        else:
            # No matching wiki page — surface a non-404 indicator instead of
            # a silently-broken link.
            items.append(
                f'<li><span class="link-broken" title="Not found in wiki">'
                f'<code>{_esc(label)}</code></span></li>'
            )
    return '<ul class="cross-refs">' + "".join(items) + "</ul>"


def _provenance_html(
    ctx: SiteContext, node: Optional[ResearchNode], src_value: str, *, depth: int
) -> str:
    if not src_value:
        return '<p class="muted">No source path recorded.</p>'
    prefix = "../" * max(depth, 0)
    src_node_id = ctx.node_id_for_source_path.get(src_value)
    if src_node_id:
        other = ctx.nodes_by_id.get(src_node_id)
        if other is not None and (node is None or other.id != node.id):
            rel = node_href(other, ctx)
            if rel:
                href = prefix + rel
                return (
                    f'<p><a href="{_esc(href)}"><code>{_esc(src_value)}</code></a></p>'
                )
    return f'<p><code>{_esc(src_value)}</code></p>'


# ---------------------------------------------------------------------------
# detail / index helpers
# ---------------------------------------------------------------------------


def _detail_page(
    *,
    ctx: SiteContext,
    page: WikiPage,
    kind_label: str,
    kind_route: str,
    breadcrumbs_trail: Sequence[Tuple[str, str]],
    active: str,
    extra_section: str = "",
) -> str:
    # Strip any defensive frontmatter from the body before rendering. The
    # WikiPageStore reader already separates frontmatter out, but synthesis
    # bodies sometimes embed an inline ``---`` block.
    _, body_md = strip_frontmatter(page.body)
    title = page.title or page.slug
    # Page header already renders the title; drop a duplicate leading H1 if
    # the body opens with ``# <frontmatter title>`` verbatim. We require the
    # match to be against the *frontmatter* title (not just ``page.title``,
    # which falls back to the body's own H1 when no frontmatter is provided)
    # so a fixture page with no frontmatter title keeps its body heading.
    fm_title = ""
    fm_for_strip = page.frontmatter or {}
    if isinstance(fm_for_strip, dict):
        candidate = fm_for_strip.get("title")
        if isinstance(candidate, str):
            fm_title = candidate
    if fm_title:
        body_md = _strip_duplicate_h1(body_md, fm_title)
    body_html, headings = _render_markdown(body_md)
    eyebrow = _eyebrow(kind_label, page)
    bc = _build_breadcrumbs(breadcrumbs_trail, depth=1)
    toc_headings: List[Tuple[int, str, str]] = [
        (level, text, anchor) for level, text, anchor in headings if level >= 2
    ]
    toc_html = toc(toc_headings) if toc_headings else ""

    node = _find_node_for_page(ctx, page)
    mentions_html = _mentions_html(ctx, node, depth=1) if node else '<p class="muted">No mentions yet.</p>'
    related_html = _related_html(ctx, node, depth=1) if node else '<p class="muted">No related items yet.</p>'

    fm = page.frontmatter or {}
    src_value = fm.get("source_path") or (node.source_path if node else "")
    provenance = _provenance_html(ctx, node, str(src_value or ""), depth=1)

    # Inline frontmatter metadata: aliases + source_path appear under the
    # title; everything else stays hidden (already surfaced via ``title``,
    # ``generated_at``, etc., or simply not user-facing).
    meta_bits: List[str] = []
    aliases = fm.get("aliases")
    if isinstance(aliases, (list, tuple)) and aliases:
        rendered_aliases = ", ".join(_esc(str(a)) for a in aliases)
        meta_bits.append(f'<span class="meta-aliases"><b>Also known as:</b> {rendered_aliases}</span>')
    if src_value:
        meta_bits.append(f'<span class="meta-source"><b>Source:</b> <code>{_esc(src_value)}</code></span>')
    metadata_html = (
        f'<p class="page-meta">{" · ".join(meta_bits)}</p>' if meta_bits else ""
    )

    # Per-node weekly mentions feed the sparkline. Synthesis pages and
    # nodes with no dated source path still get a 12-bucket zero list so
    # the SVG renders an empty placeholder instead of a stub graphic.
    spark_values: List[int] = []
    if node is not None:
        spark_values = list(ctx.activity_by_node_id.get(node.id, []))
    if not spark_values:
        spark_values = [0] * 12
    sparkline = sparkline_svg(spark_values)

    cross_refs_html = (
        _cross_refs_html(ctx, node, depth=1)
        if node is not None
        else '<p class="muted">No raw-data mentions found.</p>'
    )

    sibling_path = page_href(kind_route, page.slug)
    siblings_html = ai_siblings_footer(sibling_path)

    body = f"""{eyebrow}
<h1>{_esc(title)}</h1>
{metadata_html}
<section class="markdown-body">{body_html}</section>
{extra_section}
<section id="mentions" class="mentions"><h2>Mentions in the corpus</h2>{mentions_html}</section>
<section id="related" class="related"><h2>Related</h2>{related_html}</section>
<section id="cross-refs" class="cross-refs-section"><h2>Cross-references in raw data</h2>{cross_refs_html}</section>
<section id="provenance" class="provenance"><h2>Source provenance</h2>{provenance}</section>
<section id="activity" class="activity"><h2>Activity</h2>{sparkline}</section>
"""
    return page_shell(
        title=title,
        head="",
        body=body,
        depth=1,
        active=active,
        site_title=ctx.site_title,
        counts=_nav_counts(ctx),
        toc_html=toc_html,
        breadcrumbs_html=bc,
        ai_siblings_html=siblings_html,
    )


def _index_page(
    *,
    ctx: SiteContext,
    title: str,
    description: str,
    pages: Sequence[WikiPage],
    nodes: Sequence[ResearchNode],
    kind_route: str,
    active: str,
) -> str:
    bc = _build_breadcrumbs([("Home", "index.html"), (title, "")], depth=1)
    cards_html: List[str] = []
    seen: set[str] = set()
    for page in pages:
        if page.slug in seen:
            continue
        seen.add(page.slug)
        cards_html.append(card(
            title=page.title or page.slug,
            href=f"{page.slug}.html",
            kind_label=str((page.frontmatter or {}).get("type") or kind_route.rstrip("s")),
            description=str((page.frontmatter or {}).get("summary") or "")[:200],
            footer=str((page.frontmatter or {}).get("generated_at") or ""),
        ))
    for n in nodes:
        # Prefer the on-disk slug recorded in SiteContext (so synthesis
        # nodes resolve to ``pulse.html``, not ``project-pulse.html``).
        # Fall back to ``slug_for(node.name)`` for nodes without a
        # materialised page yet.
        slug = ctx.page_slug_for_node.get(n.id) or _canonical_slug(n.name)
        if slug in seen:
            continue
        seen.add(slug)
        cards_html.append(card(
            title=n.name,
            href=f"{slug}.html",
            kind_label=n.type.value,
            description=n.description or "",
            footer=n.source_path or "",
        ))

    if not cards_html:
        cards_html = ['<p class="muted">No entries yet — they appear here as the corpus grows.</p>']

    body = f"""<header class="hero">
  <p class="eyebrow">{_esc(kind_route)}</p>
  <h1>{_esc(title)}</h1>
  <p class="lead">{_esc(description)}</p>
</header>
<section class="cards">{''.join(cards_html)}</section>
"""
    return page_shell(
        title=title,
        head="",
        body=body,
        depth=1,
        active=active,
        site_title=ctx.site_title,
        counts=_nav_counts(ctx),
        breadcrumbs_html=bc,
    )


# ---------------------------------------------------------------------------
# route renderers
# ---------------------------------------------------------------------------


def render_home(ctx: SiteContext) -> str:
    syntheses = list(ctx.wiki_pages_by_kind.get("syntheses", []))
    pulse = next(
        (p for p in syntheses if (p.frontmatter or {}).get("synthesis_kind") == "pulse"),
        None,
    )
    overview_pages = list(ctx.wiki_pages_by_kind.get("overview", []))
    if not overview_pages:
        overview_pages = [p for p in ctx.wiki_pages_by_kind.get("wiki", []) if p.slug == "overview"]
    if overview_pages and overview_pages[0].body.strip():
        first = overview_pages[0].body.strip().splitlines()[0]
        tagline = first[2:].strip() if first.startswith("# ") else first
    else:
        tagline = "A self-indexing knowledge base built from your sources."

    counts = _nav_counts(ctx)

    pulse_cards = ""
    if pulse:
        bullets = re.findall(r"^[\-\*]\s+(.+)$", pulse.body, flags=re.MULTILINE)[:3]
        if not bullets:
            bullets = [pulse.title]
        pulse_cards = '<section class="pulse-cards cards" aria-label="What\'s new">' + "".join(
            card(
                title=b[:80],
                href=f"syntheses/{pulse.slug}.html",
                kind_label="pulse",
                description="from this week's pulse",
            )
            for b in bullets
        ) + "</section>"

    stat_row = f"""<section class="stats hero" aria-label="Corpus stats">
  <div class="stat"><b>{counts.get('sources', 0)}</b><span>Sources</span></div>
  <div class="stat"><b>{counts.get('concepts', 0)}</b><span>Concepts</span></div>
  <div class="stat"><b>{counts.get('papers', 0)}</b><span>Papers</span></div>
  <div class="stat"><b>{counts.get('questions', 0)}</b><span>Open questions</span></div>
</section>"""

    entry_points = '<section class="cards entry-points" aria-label="Entry points">' + "".join([
        card(title="Sources", href="sources/index.html", kind_label="library", description="Raw documents and digests.", footer=f"{counts.get('sources', 0)} pages"),
        card(title="Concepts", href="concepts/index.html", kind_label="library", description="Recurring concepts, terms, and algorithms.", footer=f"{counts.get('concepts', 0)} pages"),
        card(title="Papers", href="papers/index.html", kind_label="library", description="Paper hub with year/topic facets.", footer=f"{counts.get('papers', 0)} pages"),
        card(title="Repos", href="repos/index.html", kind_label="library", description="Repositories and code projects.", footer=f"{counts.get('repos', 0)} pages"),
        card(title="Topics", href="topics/index.html", kind_label="library", description="Research fields and approach families.", footer=f"{counts.get('topics', 0)} pages"),
        card(title="Syntheses", href="syntheses/index.html", kind_label="library", description="Higher-order synthesis pages.", footer=f"{counts.get('syntheses', 0)} pages"),
        card(title="Open questions", href="questions/index.html", kind_label="library", description="Open research questions.", footer=f"{counts.get('questions', 0)} pages"),
        card(title="Graph view", href="graph/index.html", kind_label="tools", description="Interactive sigma.js graph."),
    ]) + "</section>"

    heatmap = heatmap_svg(list(ctx.activity_weeks))

    body = f"""<section class="hero" aria-label="Project pulse">
  <p class="eyebrow">{_esc(ctx.site_title)} · self-indexing knowledge base</p>
  <h1>{_esc(ctx.site_title)}</h1>
  <p class="lead">{_esc(tagline)}</p>
</section>
{stat_row}
{pulse_cards}
<section class="entry-points-wrap">
  <h2>Browse</h2>
  {entry_points}
</section>
<section class="activity hero" aria-label="Activity heatmap">
  <h2>26-week activity</h2>
  {heatmap}
</section>"""
    return page_shell(
        title="Home",
        head="",
        body=body,
        depth=0,
        active="home",
        site_title=ctx.site_title,
        counts=counts,
    )


def render_sources_index(ctx: SiteContext) -> str:
    return _index_page(
        ctx=ctx,
        title="Sources",
        description="Raw documents and digests indexed by the wiki.",
        pages=ctx.wiki_pages_by_kind.get("sources", []),
        nodes=ctx.nodes_by_kind.get("sources", []),
        kind_route="sources",
        active="sources",
    )


def render_source_detail(ctx: SiteContext, page: WikiPage) -> str:
    return _detail_page(
        ctx=ctx,
        page=page,
        kind_label="Source",
        kind_route="sources",
        breadcrumbs_trail=[("Home", "index.html"), ("Sources", "sources/index.html"), (page.title or page.slug, "")],
        active="sources",
    )


def render_concepts_index(ctx: SiteContext) -> str:
    return _index_page(
        ctx=ctx,
        title="Concepts",
        description="Recurring concepts, terms, algorithms, and architecture patterns.",
        pages=ctx.wiki_pages_by_kind.get("concepts", []),
        nodes=ctx.nodes_by_kind.get("concepts", []),
        kind_route="concepts",
        active="concepts",
    )


def render_concept_detail(ctx: SiteContext, page: WikiPage) -> str:
    return _detail_page(
        ctx=ctx,
        page=page,
        kind_label="Concept",
        kind_route="concepts",
        breadcrumbs_trail=[("Home", "index.html"), ("Concepts", "concepts/index.html"), (page.title or page.slug, "")],
        active="concepts",
    )


def render_entities_index(ctx: SiteContext) -> str:
    return _index_page(
        ctx=ctx,
        title="Entities",
        description="Models, datasets, benchmarks, organizations, and people.",
        pages=ctx.wiki_pages_by_kind.get("entities", []),
        nodes=ctx.nodes_by_kind.get("entities", []),
        kind_route="entities",
        active="entities",
    )


def render_entity_detail(ctx: SiteContext, page: WikiPage) -> str:
    return _detail_page(
        ctx=ctx,
        page=page,
        kind_label="Entity",
        kind_route="entities",
        breadcrumbs_trail=[("Home", "index.html"), ("Entities", "entities/index.html"), (page.title or page.slug, "")],
        active="entities",
    )


def render_papers_index(ctx: SiteContext) -> str:
    return _index_page(
        ctx=ctx,
        title="Papers",
        description="Paper hub with year and topic facets.",
        pages=ctx.wiki_pages_by_kind.get("papers", []),
        nodes=ctx.nodes_by_kind.get("papers", []),
        kind_route="papers",
        active="papers",
    )


def render_paper_detail(ctx: SiteContext, page: WikiPage) -> str:
    return _detail_page(
        ctx=ctx,
        page=page,
        kind_label="Paper",
        kind_route="papers",
        breadcrumbs_trail=[("Home", "index.html"), ("Papers", "papers/index.html"), (page.title or page.slug, "")],
        active="papers",
    )


def render_repos_index(ctx: SiteContext) -> str:
    return _index_page(
        ctx=ctx,
        title="Repos",
        description="Repositories and code projects.",
        pages=ctx.wiki_pages_by_kind.get("repos", []),
        nodes=ctx.nodes_by_kind.get("repos", []),
        kind_route="repos",
        active="repos",
    )


def render_repo_detail(ctx: SiteContext, page: WikiPage) -> str:
    return _detail_page(
        ctx=ctx,
        page=page,
        kind_label="Repository",
        kind_route="repos",
        breadcrumbs_trail=[("Home", "index.html"), ("Repos", "repos/index.html"), (page.title or page.slug, "")],
        active="repos",
    )


def render_topics_index(ctx: SiteContext) -> str:
    return _index_page(
        ctx=ctx,
        title="Topics",
        description="Research fields, topics, and approach families.",
        pages=ctx.wiki_pages_by_kind.get("topics", []),
        nodes=ctx.nodes_by_kind.get("topics", []),
        kind_route="topics",
        active="topics",
    )


def render_topic_detail(ctx: SiteContext, page: WikiPage) -> str:
    return _detail_page(
        ctx=ctx,
        page=page,
        kind_label="Topic",
        kind_route="topics",
        breadcrumbs_trail=[("Home", "index.html"), ("Topics", "topics/index.html"), (page.title or page.slug, "")],
        active="topics",
    )


def render_syntheses_index(ctx: SiteContext) -> str:
    return _index_page(
        ctx=ctx,
        title="Syntheses",
        description="Higher-order synthesis pages — daily, weekly, topic, comparison, field overview, and pulse.",
        pages=ctx.wiki_pages_by_kind.get("syntheses", []),
        nodes=ctx.nodes_by_kind.get("syntheses", []),
        kind_route="syntheses",
        active="syntheses",
    )


def render_synthesis_detail(ctx: SiteContext, page: WikiPage) -> str:
    return _detail_page(
        ctx=ctx,
        page=page,
        kind_label="Synthesis",
        kind_route="syntheses",
        breadcrumbs_trail=[("Home", "index.html"), ("Syntheses", "syntheses/index.html"), (page.title or page.slug, "")],
        active="syntheses",
    )


def render_questions_index(ctx: SiteContext) -> str:
    return _index_page(
        ctx=ctx,
        title="Open questions",
        description="Open questions extracted from the corpus.",
        pages=ctx.wiki_pages_by_kind.get("questions", []),
        nodes=ctx.nodes_by_kind.get("questions", []),
        kind_route="questions",
        active="questions",
    )


def render_question_detail(ctx: SiteContext, page: WikiPage) -> str:
    return _detail_page(
        ctx=ctx,
        page=page,
        kind_label="Open question",
        kind_route="questions",
        breadcrumbs_trail=[("Home", "index.html"), ("Open questions", "questions/index.html"), (page.title or page.slug, "")],
        active="questions",
    )


def render_timeline(ctx: SiteContext) -> str:
    """Render the timeline page.

    Walks the per-day source paths the corpus already knows about (via
    ``node.source_path`` / ``metadata['source_paths']``), bucketed by ISO
    date, and emits a heatmap plus a synthesis list. Each heatmap cell
    points back at the matching day's ``digest.md`` source page when one
    exists, so users can drill in without us having to mint per-day index
    pages.

    Per-day index pages (``timeline/<YYYY-MM-DD>.html`` listing every
    activity for that day) are an explicit follow-up: they would let us
    surface added papers/repos/concepts inline, but emitting them from
    here requires plumbing through ``StaticSiteBuilder`` which Subagent L
    is not allowed to touch in this pass. The cells already linking to the
    day's digest source page is a reasonable interim that uses existing
    routes.
    """
    bc = _build_breadcrumbs([("Home", "index.html"), ("Timeline", "")], depth=1)

    # Collect daily counts from node source_paths (digest.md / paper.md /
    # repo.md all share the ``YYYY-MM-DD`` prefix when they live under
    # ``data/research/daily/``).
    counts_by_date: Counter = Counter()
    paper_dates: Counter = Counter()
    digest_path_for_date: Dict[str, str] = {}
    for node in ctx.graph.nodes:
        sp = node.source_path or ""
        if not sp:
            continue
        m = _DATE_RE.search(sp)
        if not m:
            continue
        date_str = m.group(1)
        counts_by_date[date_str] += 1
        if node.type == ResearchNodeType.PAPER:
            paper_dates[date_str] += 1
        # Remember the digest.md path per day so we can link the cell back
        # to a real source page.
        if "digest" in sp.lower() and date_str not in digest_path_for_date:
            digest_path_for_date[date_str] = sp

    # Build the heatmap from ``activity_weeks`` — Subagent O is updating the
    # signature to accept ``with_labels=True``; until that lands we call the
    # existing two-arg signature so the build doesn't break, and the labels
    # appear automatically once O ships.
    try:
        heatmap = heatmap_svg(list(ctx.activity_weeks), weeks_back=26, with_labels=True)
    except TypeError:
        heatmap = heatmap_svg(list(ctx.activity_weeks))

    # Synthesis-link list (kept as the canonical "what shipped" rail).
    syntheses = list(ctx.wiki_pages_by_kind.get("syntheses", []))
    rows: List[str] = []
    for page in syntheses:
        kind = (page.frontmatter or {}).get("synthesis_kind", "")
        when = (page.frontmatter or {}).get("generated_at", "")
        rows.append(
            f'<li>{badge(str(kind) or "synthesis")} '
            f'<a href="../syntheses/{_esc(page.slug)}.html">{_esc(page.title or page.slug)}</a> '
            f'<small>{_esc(when)}</small></li>'
        )
    if not rows:
        rows = ['<li class="muted">No syntheses yet — they appear here on the next compile.</li>']

    # Per-day list: every observed date, total activity + paper count, and
    # a link to the digest source page if we have one.
    day_rows: List[str] = []
    for date_str in sorted(counts_by_date, reverse=True)[:60]:
        total = counts_by_date[date_str]
        papers = paper_dates.get(date_str, 0)
        digest = digest_path_for_date.get(date_str)
        href = ""
        if digest:
            src_node_id = ctx.node_id_for_source_path.get(digest)
            if src_node_id:
                src_node = ctx.nodes_by_id.get(src_node_id)
                if src_node is not None:
                    rel = node_href(src_node, ctx)
                    if rel:
                        href = "../" + rel
        label_html = (
            f'<a href="{_esc(href)}">{_esc(date_str)}</a>' if href else _esc(date_str)
        )
        day_rows.append(
            f'<li>{label_html} <small>{total} activity · {papers} paper{"s" if papers != 1 else ""}</small></li>'
        )

    body = f"""<header class="hero">
  <p class="eyebrow">timeline</p>
  <h1>Timeline</h1>
  <p class="lead">Recent activity, synthesis updates, and weekly digests.</p>
</header>
<section class="activity">{heatmap}</section>
<section class="timeline-days"><h2>Days</h2><ol class="timeline-day-list">{''.join(day_rows) if day_rows else '<li class="muted">No dated activity yet.</li>'}</ol></section>
<section class="timeline">
  <h2>Syntheses</h2>
  <ol class="timeline-list">{''.join(rows)}</ol>
</section>"""
    return page_shell(
        title="Timeline",
        head="",
        body=body,
        depth=1,
        active="timeline",
        site_title=ctx.site_title,
        counts=_nav_counts(ctx),
        breadcrumbs_html=bc,
    )


def render_graph_view(ctx: SiteContext) -> str:
    # Filter to wiki-layer node types only — see WIKI_LAYER_TYPES (the
    # canonical allow-list defined alongside the search index). Anything
    # outside that set stays in graph.json for MCP consumers but never
    # surfaces in the on-page interactive view.
    visible_nodes: List[ResearchNode] = [
        n for n in ctx.graph.nodes if n.type.value in WIKI_LAYER_TYPES
    ]
    visible_ids = {n.id for n in visible_nodes}

    # Compute degree on the wiki-layer subgraph so we can:
    #   (a) size nodes by degree, and
    #   (b) drop low-degree nodes if we exceed MAX_GRAPH_NODES.
    degree: Dict[str, int] = {nid: 0 for nid in visible_ids}
    visible_edges: List[ResearchEdge] = []
    for e in ctx.graph.edges:
        if e.source in visible_ids and e.target in visible_ids:
            visible_edges.append(e)
            degree[e.source] = degree.get(e.source, 0) + 1
            degree[e.target] = degree.get(e.target, 0) + 1

    # Cap at MAX_GRAPH_NODES, dropping low-degree nodes first. Stable on
    # ties by node id so the build stays byte-identical across runs.
    if len(visible_nodes) > MAX_GRAPH_NODES:
        ranked = sorted(visible_nodes, key=lambda n: (-degree.get(n.id, 0), n.id))
        kept = ranked[:MAX_GRAPH_NODES]
        kept_ids = {n.id for n in kept}
        visible_nodes = [n for n in visible_nodes if n.id in kept_ids]
        visible_ids = kept_ids
        visible_edges = [e for e in visible_edges if e.source in kept_ids and e.target in kept_ids]

    nodes_payload: List[Dict[str, object]] = []
    type_counts: Counter = Counter()
    for n in visible_nodes:
        kind = _kind_for_node_type(n.type)  # one of sources/concepts/entities/...
        group = kind or "other"
        type_counts[group] += 1
        href_rel = node_href(n)
        href = f"../{href_rel}" if href_rel else ""
        deg = degree.get(n.id, 0)
        description = (n.description or "").strip()
        nodes_payload.append({
            "id": n.id,
            "name": n.name,
            "type": n.type.value,
            "kind": kind,
            "group": group,
            "href": href,
            "val": deg + 1,
            "degree": deg,
            "description": description[:400],  # JS clips to 200 chars itself
        })

    links_payload: List[Dict[str, object]] = []
    for e in visible_edges:
        links_payload.append({
            "source": e.source,
            "target": e.target,
            "type": e.type,
            "label": e.type.replace("_", " ") if e.type else "related",
        })

    payload = {"nodes": nodes_payload, "links": links_payload}

    # Legend (server-rendered fallback; the JS rebuilds it with click-to-toggle
    # behaviour, but if JS is off the user still sees the palette key).
    palette = {
        "sources": "#5b574f",
        "papers": "#be185d",
        "repos": "#2563eb",
        "concepts": "#0891b2",
        "entities": "#7c3aed",
        "topics": "#b3502b",
        "syntheses": "#2a6f4f",
        "questions": "#c08a1a",
        "other": "#64748b",
    }
    legend_items = "".join(
        f'<button type="button" class="graph-legend-chip" data-group="{_esc(group)}">'
        f'<span class="graph-legend-dot" style="background:{palette.get(group, "#64748b")}"></span>'
        f'<span class="graph-legend-label">{_esc(group)}</span>'
        f'<span class="graph-legend-count">{count}</span>'
        f'</button>'
        for group, count in sorted(type_counts.items(), key=lambda kv: kv[0])
    )

    bc = _build_breadcrumbs([("Home", "index.html"), ("Graph view", "")], depth=1)

    # CDN-loaded ES modules. We pin specific versions and supply integrity
    # hashes so a network MITM can't swap the bundle. If either fetch fails
    # the JS module never sets ``window.ForceGraph(3D)`` and the runtime
    # bundle (``app.js``) renders the static SVG fallback after a 6s timeout.
    #
    # Versions chosen 2026-04: 3d-force-graph 1.74.x, force-graph 1.49.x,
    # three 0.169.x (peer of 3d-force-graph). Hashes computed for these
    # exact tarballs on esm.sh; if the version pin moves, regen the hashes
    # via ``openssl dgst -sha384 -binary <file> | openssl base64 -A``.
    head = (
        '<link rel="preconnect" href="https://esm.sh">\n'
        '<script type="module">\n'
        '  // Load 3D + 2D force-graph plus three.js peer dep from esm.sh.\n'
        '  // We attach the constructors to ``window`` so the deferred\n'
        '  // ``app.js`` (loaded with the rest of the site bundle) can pick\n'
        '  // them up without itself needing to be a module. If any import\n'
        '  // throws (CDN blocked, offline, CSP), app.js falls back to the\n'
        '  // inline SVG renderer and surfaces the error banner.\n'
        '  try {\n'
        '    const [{ default: ForceGraph3D }, { default: ForceGraph }] = await Promise.all([\n'
        '      import("https://esm.sh/3d-force-graph@1.74.5"),\n'
        '      import("https://esm.sh/force-graph@1.49.5")\n'
        '    ]);\n'
        '    window.ForceGraph3D = ForceGraph3D;\n'
        '    window.ForceGraph = ForceGraph;\n'
        '    window.__graphLibsReady = true;\n'
        '  } catch (err) {\n'
        '    console.warn("graph: CDN load failed", err);\n'
        '    window.__graphLibsError = String(err && err.message ? err.message : err);\n'
        '  }\n'
        '</script>\n'
    )

    body = f"""<header class="hero">
  <p class="eyebrow">interactive graph · 3D force layout</p>
  <h1>Knowledge graph</h1>
  <p class="lead">Hover a node to inspect it. Click to open the page; ⌘/Ctrl-click to focus the camera. Drag to orbit, scroll to zoom. Press <kbd>/</kbd> to search, <kbd>f</kbd> to fit, <kbd>2</kbd>/<kbd>3</kbd> to switch projection.</p>
</header>
<section class="graph-page" aria-label="Knowledge graph visualization">
  <div class="graph-toolbar" role="toolbar" aria-label="Graph controls">
    <div class="graph-toolbar-group" role="group" aria-label="Projection">
      <button type="button" class="button" data-graph-mode="3d" aria-pressed="true">3D</button>
      <button type="button" class="button" data-graph-mode="2d" aria-pressed="false">2D</button>
    </div>
    <div class="graph-toolbar-group" role="group" aria-label="View">
      <button type="button" class="button" data-graph-action="fit" title="Fit to view (f)">Fit</button>
      <button type="button" class="button" data-graph-action="reset" title="Reset (r)">Reset</button>
    </div>
    <div class="graph-search">
      <label class="visually-hidden" for="graph-search-input">Search nodes</label>
      <input id="graph-search-input" type="search" placeholder="Search nodes ( / )" autocomplete="off" spellcheck="false">
    </div>
  </div>
  <div class="graph-canvas" id="graph-canvas" role="img" aria-label="Interactive 3D knowledge graph">
    <div class="graph-info-panel" id="graph-info-panel" aria-live="polite"></div>
    <div class="graph-tooltip" id="graph-tooltip" role="status" aria-live="polite"></div>
    <div class="graph-error-banner" id="graph-error-banner" role="alert"></div>
  </div>
  <div class="graph-legend" id="graph-legend" aria-label="Type legend">{legend_items}</div>
  <p class="graph-help muted">Showing {len(nodes_payload)} of {len(visible_nodes) if len(visible_nodes) >= len(nodes_payload) else len(nodes_payload)} wiki nodes · {len(links_payload)} links</p>
</section>
<script id="graph-data" type="application/json">{_safe_json(payload)}</script>"""
    return page_shell(
        title="Graph view",
        head=head,
        body=body,
        depth=1,
        active="graph",
        site_title=ctx.site_title,
        counts=_nav_counts(ctx),
        breadcrumbs_html=bc,
    )


def render_about(ctx: SiteContext) -> str:
    bc = _build_breadcrumbs([("Home", "index.html"), ("About", "")], depth=0)
    type_rows = "".join(
        f"<tr><td>{_esc(t)}</td><td>{c}</td></tr>"
        for t, c in sorted(ctx.type_counts.items(), key=lambda x: (-x[1], x[0]))
    )
    body = f"""<header class="hero">
  <p class="eyebrow">about</p>
  <h1>About this wiki</h1>
  <p class="lead">A self-indexing knowledge base built from your project's sources, papers, repos, and notes. Every page is generated deterministically by <code>project compile</code>; rerunning produces byte-identical output.</p>
</header>
<section class="schema">
  <h2>Schema</h2>
  <p>Routes:</p>
  <ul>
    <li>/sources — raw documents</li>
    <li>/concepts — recurring concepts, terms, algorithms</li>
    <li>/entities — models, datasets, benchmarks, orgs, people</li>
    <li>/papers — papers</li>
    <li>/repos — repositories</li>
    <li>/topics — research fields, problem areas, approach families</li>
    <li>/syntheses — higher-order generated pages</li>
    <li>/questions — open questions</li>
    <li>/timeline — activity log</li>
    <li>/graph — interactive graph view</li>
  </ul>
  <h2>Node-type counts</h2>
  <table class="node-table"><thead><tr><th>Type</th><th>Count</th></tr></thead>
  <tbody>{type_rows}</tbody></table>
</section>"""
    return page_shell(
        title="About",
        head="",
        body=body,
        depth=0,
        active="about",
        site_title=ctx.site_title,
        counts=_nav_counts(ctx),
        breadcrumbs_html=bc,
    )


__all__ = [
    "ROUTE_FOR_KIND",
    "SiteContext",
    "kind_for_node",
    "node_href",
    "page_href",
    "render_about",
    "render_concept_detail",
    "render_concepts_index",
    "render_entities_index",
    "render_entity_detail",
    "render_graph_view",
    "render_home",
    "render_paper_detail",
    "render_papers_index",
    "render_question_detail",
    "render_questions_index",
    "render_repo_detail",
    "render_repos_index",
    "render_source_detail",
    "render_sources_index",
    "render_synthesis_detail",
    "render_syntheses_index",
    "render_timeline",
    "render_topic_detail",
    "render_topics_index",
]
