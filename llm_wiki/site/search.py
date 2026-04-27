"""Wiki-layer search index.

The search index that the static site ships powers the in-page command palette
and any external agents that read ``search-index.json``. By design it lists
**only the wiki layer** described in §3.1 of the frontend redesign spec:

- sources (``SourceDocument`` / ``Paper`` / ``Repository`` / ``CodeProject``)
- concepts (concept-ish term/algorithm types)
- entities (``Model`` / ``Dataset`` / ``Benchmark`` / ``Metric`` /
  ``Organization`` / ``Person``)
- topics (``ResearchField`` / ``ResearchTopic`` / ``ProblemArea`` /
  ``ApproachFamily`` / ``Trend``)
- syntheses (``Synthesis``)
- questions (``OpenQuestion``)

Code-graph node types (``CodeClass`` / ``CodeFunction`` / ``CodeModule`` /
``Dependency`` / ``SourceFile``) and assertion-layer types (``Claim`` and the
five ``*Claim`` variants, plus ``EvidenceSpan``) are intentionally excluded:
they remain in ``graph.json`` for MCP/Cognee/Graphiti consumers but never get
their own URL or search entry.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Mapping, Sequence

from ..research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from ..wiki_store import WikiPage


# ---------------------------------------------------------------- type filters


# Node types that are allowed to surface as wiki pages / search entries.
WIKI_LAYER_TYPES: frozenset[str] = frozenset(
    {
        # sources
        ResearchNodeType.SOURCE_DOCUMENT.value,
        ResearchNodeType.PAPER.value,
        ResearchNodeType.REPOSITORY.value,
        ResearchNodeType.PROJECT.value,
        ResearchNodeType.CODE_PROJECT.value,
        # concepts
        ResearchNodeType.CONCEPT.value,
        ResearchNodeType.TECHNICAL_TERM.value,
        ResearchNodeType.MATHEMATICAL_CONCEPT.value,
        ResearchNodeType.METHODOLOGICAL_CONCEPT.value,
        ResearchNodeType.ALGORITHM.value,
        ResearchNodeType.OBJECTIVE_FUNCTION.value,
        ResearchNodeType.ARCHITECTURE_PATTERN.value,
        ResearchNodeType.TRAINING_PARADIGM.value,
        ResearchNodeType.INFERENCE_STRATEGY.value,
        ResearchNodeType.EVALUATION_PROTOCOL.value,
        ResearchNodeType.TASK.value,
        ResearchNodeType.CAPABILITY.value,
        # entities
        ResearchNodeType.MODEL.value,
        ResearchNodeType.DATASET.value,
        ResearchNodeType.BENCHMARK.value,
        ResearchNodeType.METRIC.value,
        ResearchNodeType.RESULT.value,
        ResearchNodeType.ORGANIZATION.value,
        ResearchNodeType.PERSON.value,
        # topics
        ResearchNodeType.RESEARCH_FIELD.value,
        ResearchNodeType.RESEARCH_TOPIC.value,
        ResearchNodeType.PROBLEM_AREA.value,
        ResearchNodeType.APPROACH_FAMILY.value,
        ResearchNodeType.TREND.value,
        # syntheses + questions
        ResearchNodeType.SYNTHESIS.value,
        ResearchNodeType.OPEN_QUESTION.value,
    }
)


# Explicit exclusion list (kept as documentation / for tests). These types stay
# in graph.json but never get an HTML route or a search entry.
EXCLUDED_TYPES: frozenset[str] = frozenset(
    {
        ResearchNodeType.CODE_CLASS.value,
        ResearchNodeType.CODE_FUNCTION.value,
        ResearchNodeType.CODE_MODULE.value,
        ResearchNodeType.SOURCE_FILE.value,
        ResearchNodeType.DEPENDENCY.value,
        ResearchNodeType.EVIDENCE_SPAN.value,
        ResearchNodeType.CLAIM.value,
        ResearchNodeType.CONTRIBUTION_CLAIM.value,
        ResearchNodeType.PERFORMANCE_CLAIM.value,
        ResearchNodeType.COMPARISON_CLAIM.value,
        ResearchNodeType.LIMITATION_CLAIM.value,
        ResearchNodeType.CAUSAL_CLAIM.value,
    }
)


# ----------------------------------------------------------------- node → kind


_KIND_BY_TYPE: Dict[str, str] = {
    # sources
    ResearchNodeType.SOURCE_DOCUMENT.value: "sources",
    ResearchNodeType.PAPER.value: "papers",
    ResearchNodeType.REPOSITORY.value: "repos",
    ResearchNodeType.PROJECT.value: "repos",
    ResearchNodeType.CODE_PROJECT.value: "repos",
    # concepts
    ResearchNodeType.CONCEPT.value: "concepts",
    ResearchNodeType.TECHNICAL_TERM.value: "concepts",
    ResearchNodeType.MATHEMATICAL_CONCEPT.value: "concepts",
    ResearchNodeType.METHODOLOGICAL_CONCEPT.value: "concepts",
    ResearchNodeType.ALGORITHM.value: "concepts",
    ResearchNodeType.OBJECTIVE_FUNCTION.value: "concepts",
    ResearchNodeType.ARCHITECTURE_PATTERN.value: "concepts",
    ResearchNodeType.TRAINING_PARADIGM.value: "concepts",
    ResearchNodeType.INFERENCE_STRATEGY.value: "concepts",
    ResearchNodeType.EVALUATION_PROTOCOL.value: "concepts",
    ResearchNodeType.TASK.value: "concepts",
    ResearchNodeType.CAPABILITY.value: "concepts",
    # entities
    ResearchNodeType.MODEL.value: "entities",
    ResearchNodeType.DATASET.value: "entities",
    ResearchNodeType.BENCHMARK.value: "entities",
    ResearchNodeType.METRIC.value: "entities",
    ResearchNodeType.RESULT.value: "entities",
    ResearchNodeType.ORGANIZATION.value: "entities",
    ResearchNodeType.PERSON.value: "entities",
    # topics
    ResearchNodeType.RESEARCH_FIELD.value: "topics",
    ResearchNodeType.RESEARCH_TOPIC.value: "topics",
    ResearchNodeType.PROBLEM_AREA.value: "topics",
    ResearchNodeType.APPROACH_FAMILY.value: "topics",
    ResearchNodeType.TREND.value: "topics",
    # syntheses + questions
    ResearchNodeType.SYNTHESIS.value: "syntheses",
    ResearchNodeType.OPEN_QUESTION.value: "questions",
}


_SUMMARY_LIMIT = 200


def _slug(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe or "node"


def _trim(text: str, limit: int = _SUMMARY_LIMIT) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _first_paragraph(body: str) -> str:
    """Return the first non-heading paragraph in ``body`` (already stripped)."""

    if not body:
        return ""
    paragraphs: List[List[str]] = [[]]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        if stripped.startswith("#"):
            # Skip headings entirely; they're titles, not summaries.
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        paragraphs[-1].append(stripped)
    for para in paragraphs:
        if para:
            return " ".join(para)
    return ""


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def _kind_dir(kind: str) -> str:
    # Already plural-ish from _KIND_BY_TYPE; the index page mounts at ``/<kind>/``.
    return kind


def _node_href(kind: str, slug: str) -> str:
    return f"{_kind_dir(kind)}/{slug}.html"


def _wiki_href(page: WikiPage) -> str:
    return f"{_kind_dir(page.kind)}/{page.slug}.html"


def _node_entry(node: ResearchNode) -> Dict[str, object]:
    kind = _KIND_BY_TYPE[node.type.value]
    title = (node.name or node.id).strip() or node.id
    summary = _trim(node.description or node.name or "")
    return {
        "id": node.id,
        "title": title,
        "kind": kind,
        "href": _node_href(kind, _slug(title)),
        "summary": summary,
        "source_path": node.source_path or "",
    }


def _wiki_entry(page: WikiPage) -> Dict[str, object]:
    fm = page.frontmatter or {}
    title_raw = fm.get("title") if isinstance(fm, dict) else None
    title = ""
    if isinstance(title_raw, str) and title_raw.strip():
        title = title_raw.strip()
    if not title:
        title = _first_h1(page.body) or page.title or page.slug

    summary_raw = ""
    if isinstance(fm, dict):
        candidate = fm.get("summary") or fm.get("description")
        if isinstance(candidate, str):
            summary_raw = candidate
    if not summary_raw:
        summary_raw = _first_paragraph(page.body)
    summary = _trim(summary_raw)

    source_path = ""
    if isinstance(fm, dict):
        sp = fm.get("source_path") or fm.get("source") or ""
        if isinstance(sp, str):
            source_path = sp
    if not source_path:
        source_path = str(page.path) if page.path else ""

    return {
        "id": f"{page.kind}:{page.slug}",
        "title": title,
        "kind": page.kind,
        "href": _wiki_href(page),
        "summary": summary,
        "source_path": source_path,
    }


# --------------------------------------------------------------------- public


def is_wiki_layer(node: ResearchNode) -> bool:
    """Return True iff ``node`` belongs to a wiki-layer type."""

    return node.type.value in WIKI_LAYER_TYPES


def build_search_index(
    graph: ResearchGraph,
    wiki_pages_by_kind: Mapping[str, Sequence[WikiPage]] | None = None,
) -> List[Dict[str, object]]:
    """Build the wiki-layer search index.

    Parameters
    ----------
    graph:
        The validated research graph. Code/claim/evidence nodes are dropped
        before any entry is emitted.
    wiki_pages_by_kind:
        Source-document and synthesis pages prefer their wiki-layer markdown
        rendition (frontmatter title + first paragraph). Pass an empty mapping
        if the wiki layer is not yet materialised.

    Each returned dict has the keys ``id``, ``title``, ``kind``, ``href``,
    ``summary`` (capped at 200 chars), and ``source_path``.
    """

    pages_by_kind: Dict[str, List[WikiPage]] = {}
    if wiki_pages_by_kind:
        for kind, pages in wiki_pages_by_kind.items():
            pages_by_kind[kind] = list(pages)

    entries: List[Dict[str, object]] = []
    seen_hrefs: set[str] = set()

    # 1) Wiki pages first — they own the canonical title/summary for documents
    #    and syntheses (those are the kinds the spec calls out specifically).
    preferred_kinds = ("sources", "syntheses", "topics", "concepts", "entities", "papers", "repos", "questions")
    for kind in preferred_kinds:
        for page in pages_by_kind.get(kind, []):
            entry = _wiki_entry(page)
            if entry["href"] in seen_hrefs:
                continue
            seen_hrefs.add(str(entry["href"]))
            entries.append(entry)

    # 2) Graph nodes — only wiki-layer types, skipping any whose href already
    #    came from a wiki page.
    for node in graph.nodes:
        if not is_wiki_layer(node):
            continue
        entry = _node_entry(node)
        if entry["href"] in seen_hrefs:
            continue
        seen_hrefs.add(str(entry["href"]))
        entries.append(entry)

    entries.sort(key=lambda e: (str(e["kind"]), str(e["title"]).lower()))
    return entries


__all__ = [
    "WIKI_LAYER_TYPES",
    "EXCLUDED_TYPES",
    "build_search_index",
    "is_wiki_layer",
]
