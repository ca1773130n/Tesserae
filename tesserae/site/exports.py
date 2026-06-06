"""AI-friendly export artifacts for the static wiki site.

These renderers emit the non-HTML files an Tesserae ships next to the HTML:

- ``llms.txt`` / ``llms-full.txt`` (llmstxt.org convention)
- ``graph.jsonld`` (schema.org JSON-LD; wiki-layer nodes only, ``@graph`` shape)
- ``sitemap.xml`` / ``rss.xml`` / ``robots.txt`` / ``ai-readme.md``
- per-page sibling artifacts (``foo.txt`` and ``foo.json`` next to ``foo.html``)

Everything here is wiki-layer only. Code-graph types (``CodeClass`` etc.) and
assertion-layer types (``Claim`` variants, ``EvidenceSpan``) stay out of these
exports — the wiki layer is the user-facing surface.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple
from xml.sax.saxutils import escape as xml_escape

from ..research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from ..wiki_projector import kind_for_node
from ..wiki_store import WikiPage, _canonical_slug
from .search import is_wiki_layer


# ----------------------------------------------------------------- ExportContext


@dataclass(frozen=True)
class ExportContext:
    """Shared input bundle for every renderer in this module.

    ``Subagent E`` defines an isomorphic ``SiteContext`` for its page
    renderers; the orchestrator (Subagent G) constructs both. Keep the field
    names in lockstep — do not import ``SiteContext`` here.

    ``synthesis_history`` carries the parsed ``.history.jsonl`` ledger entries
    so RSS / sitemap renderers can derive deterministic pubdates without
    consulting the wall clock. Each entry is a mapping with at least
    ``slug``, ``content_hash``, and ``generated_at`` keys.
    """

    site_title: str
    graph: ResearchGraph
    wiki_pages_by_kind: Mapping[str, Sequence[WikiPage]] = field(default_factory=dict)
    routes: Sequence[Tuple[str, Optional[datetime]]] = field(default_factory=tuple)
    synthesis_history: Sequence[Mapping[str, str]] = field(default_factory=tuple)


def slice_export_context_for_topic(ctx: ExportContext, node_ids: Set[str]) -> ExportContext:
    """Return a new :class:`ExportContext` whose ``wiki_pages_by_kind`` is filtered
    to only the pages backed by a node id in ``node_ids`` (a topic's subgraph).

    ``WikiPage`` carries no node id, so we reconstruct a ``(kind, slug) -> node_id``
    map from ``ctx.graph`` using :func:`kind_for_node` (the public wiki kind) and the
    canonical ``_canonical_slug`` from :mod:`tesserae.wiki_store` — the SAME slug
    function ``WikiPageStore`` uses to name pages on disk — then keep only pages
    whose ``(kind, slug)`` maps into ``node_ids``. The export ``_slug`` helper is
    NOT used here: it lacks the 96-byte truncation and the sha1 fallback for
    non-Latin names, so long / non-Latin nodes would mismatch their real page
    slug and get silently dropped from the topic slice. ``ExportContext`` is
    frozen — we rebuild via ``dataclasses.replace``.

    ``render_llms_txt`` / ``render_llms_full_txt`` consume the result unchanged,
    emitting a topic-scoped slice that is a strict subset of the full output.
    """
    by_key: Dict[Tuple[str, str], str] = {}
    for node in ctx.graph.nodes:
        kind = kind_for_node(node)
        if kind is None:
            continue
        by_key[(kind, _canonical_slug(node.name))] = node.id

    filtered: Dict[str, List[WikiPage]] = {}
    for kind, pages in ctx.wiki_pages_by_kind.items():
        kept = [p for p in pages if by_key.get((kind, p.slug)) in node_ids]
        if kept:
            filtered[kind] = kept
    return dataclasses.replace(ctx, wiki_pages_by_kind=filtered)


# --------------------------------------------------------------- helpers


_ORDERED_KINDS: Tuple[str, ...] = (
    "sources",
    "papers",
    "repos",
    "concepts",
    "entities",
    "topics",
    "syntheses",
    "questions",
)


_LLMS_FULL_CAP_BYTES = 5 * 1024 * 1024  # ~5MB
_LLMS_FULL_TRUNCATION_MARKER = (
    "\n\n[TRUNCATED — output exceeded 5MB cap; see graph.jsonld for the full set]\n"
)


def _h1(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def _first_paragraph(body: str) -> str:
    paragraphs: List[List[str]] = [[]]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        if stripped.startswith("#"):
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        paragraphs[-1].append(stripped)
    for para in paragraphs:
        if para:
            return " ".join(para)
    return ""


def _trim(text: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _slug(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe or "node"


def _page_title(page: WikiPage) -> str:
    fm = page.frontmatter or {}
    title = fm.get("title") if isinstance(fm, dict) else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    return _h1(page.body) or page.title or page.slug


def _page_summary(page: WikiPage) -> str:
    fm = page.frontmatter or {}
    if isinstance(fm, dict):
        for key in ("summary", "description"):
            value = fm.get(key)
            if isinstance(value, str) and value.strip():
                return _trim(value)
    return _trim(_first_paragraph(page.body))


def _page_href(page: WikiPage) -> str:
    return f"{page.kind}/{page.slug}.html"


_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_frontmatter(body: str) -> str:
    """Strip a leading YAML frontmatter block, if any."""
    return _FRONTMATTER_RE.sub("", body, count=1)


def _ascii_word_pattern(text: str) -> Dict[str, int]:
    """Cheap language fingerprint: histogram of ASCII vs non-ASCII chars."""
    ascii_count = 0
    non_ascii_count = 0
    for ch in text:
        if not ch.isalpha():
            continue
        if ord(ch) < 128:
            ascii_count += 1
        else:
            non_ascii_count += 1
    return {"ascii": ascii_count, "non_ascii": non_ascii_count}


def _detect_language(ctx: "ExportContext") -> str:
    """Best-effort dominant language code — ``"en"`` unless content is mostly non-ASCII."""
    totals = {"ascii": 0, "non_ascii": 0}
    seen_chars = 0
    for kind in _ORDERED_KINDS:
        for page in ctx.wiki_pages_by_kind.get(kind, []):
            counts = _ascii_word_pattern(page.body[:2000])
            totals["ascii"] += counts["ascii"]
            totals["non_ascii"] += counts["non_ascii"]
            seen_chars += len(page.body[:2000])
            if seen_chars >= 50_000:
                break
        if seen_chars >= 50_000:
            break
    if totals["non_ascii"] > totals["ascii"]:
        return "und"  # undetermined non-Latin
    return "en"


# --------------------------------------------------------------- llms.txt


def render_llms_txt(site_title: str, ctx: ExportContext) -> str:
    """Terse llmstxt.org output: title, blurb, link table grouped by kind."""

    lines: List[str] = []
    lines.append(f"# {site_title}")
    lines.append("")
    lines.append(
        "> Auto-generated wiki layer for AI agents and humans. "
        "Browse the same content as HTML at /, or fetch graph.jsonld for the structured view."
    )
    lines.append("")

    for kind in _ORDERED_KINDS:
        pages = list(ctx.wiki_pages_by_kind.get(kind, []))
        if not pages:
            continue
        lines.append(f"## {kind.title()}")
        lines.append("")
        for page in pages:
            title = _page_title(page)
            href = _page_href(page)
            summary = _page_summary(page)
            if summary:
                lines.append(f"- [{title}]({href}): {summary}")
            else:
                lines.append(f"- [{title}]({href})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------- llms-full.txt


def render_llms_full_txt(site_title: str, ctx: ExportContext) -> str:
    """Bigger flat dump of every wiki-layer page, with a 5MB safety cap."""

    head = render_llms_txt(site_title, ctx)
    out_parts: List[str] = [head, "\n# Full content\n"]

    running = sum(len(part.encode("utf-8")) for part in out_parts)
    truncated = False

    for kind in _ORDERED_KINDS:
        pages = list(ctx.wiki_pages_by_kind.get(kind, []))
        if not pages:
            continue
        section_header = f"\n## {kind.title()}\n"
        if running + len(section_header.encode("utf-8")) > _LLMS_FULL_CAP_BYTES:
            truncated = True
            break
        out_parts.append(section_header)
        running += len(section_header.encode("utf-8"))
        for page in pages:
            title = _page_title(page)
            href = _page_href(page)
            block = f"\n### {title}\n_{href}_\n\n{page.body.rstrip()}\n"
            block_bytes = len(block.encode("utf-8"))
            if running + block_bytes > _LLMS_FULL_CAP_BYTES:
                truncated = True
                break
            out_parts.append(block)
            running += block_bytes
        if truncated:
            break

    if truncated:
        out_parts.append(_LLMS_FULL_TRUNCATION_MARKER)
    return "".join(out_parts).rstrip() + "\n"


# --------------------------------------------------------------- graph.jsonld


_JSONLD_TYPE_BY_NODE: Dict[str, str] = {
    ResearchNodeType.PAPER.value: "ScholarlyArticle",
    ResearchNodeType.REPOSITORY.value: "SoftwareSourceCode",
    ResearchNodeType.CODE_PROJECT.value: "SoftwareSourceCode",
    ResearchNodeType.PROJECT.value: "SoftwareSourceCode",
    ResearchNodeType.SOURCE_DOCUMENT.value: "CreativeWork",
    ResearchNodeType.CONCEPT.value: "DefinedTerm",
    ResearchNodeType.TECHNICAL_TERM.value: "DefinedTerm",
    ResearchNodeType.ALGORITHM.value: "DefinedTerm",
    ResearchNodeType.MATHEMATICAL_CONCEPT.value: "DefinedTerm",
    ResearchNodeType.METHODOLOGICAL_CONCEPT.value: "DefinedTerm",
    ResearchNodeType.ARCHITECTURE_PATTERN.value: "DefinedTerm",
    ResearchNodeType.OBJECTIVE_FUNCTION.value: "DefinedTerm",
    ResearchNodeType.TRAINING_PARADIGM.value: "DefinedTerm",
    ResearchNodeType.INFERENCE_STRATEGY.value: "DefinedTerm",
    ResearchNodeType.EVALUATION_PROTOCOL.value: "DefinedTerm",
    ResearchNodeType.TASK.value: "DefinedTerm",
    ResearchNodeType.CAPABILITY.value: "DefinedTerm",
    ResearchNodeType.RESEARCH_FIELD.value: "DefinedTerm",
    ResearchNodeType.RESEARCH_TOPIC.value: "DefinedTerm",
    ResearchNodeType.PROBLEM_AREA.value: "DefinedTerm",
    ResearchNodeType.APPROACH_FAMILY.value: "DefinedTerm",
    ResearchNodeType.TREND.value: "DefinedTerm",
    ResearchNodeType.SYNTHESIS.value: "Article",
    ResearchNodeType.PERSON.value: "Person",
    ResearchNodeType.ORGANIZATION.value: "Organization",
    ResearchNodeType.MODEL.value: "SoftwareSourceCode",
    ResearchNodeType.DATASET.value: "Dataset",
    ResearchNodeType.BENCHMARK.value: "Dataset",
    ResearchNodeType.METRIC.value: "PropertyValue",
    ResearchNodeType.RESULT.value: "PropertyValue",
    ResearchNodeType.OPEN_QUESTION.value: "Question",
}


# Where the wiki-overall concept index lives (used by ``DefinedTerm.inDefinedTermSet``).
_CONCEPT_TERM_SET = "concepts/index.html"


def _schema_type_for(node: ResearchNode) -> str:
    return _JSONLD_TYPE_BY_NODE.get(node.type.value, "Thing")


def _kind_for_type(type_value: str) -> Optional[str]:
    """Map a node type to its on-disk kind dir (mirrors ``search._KIND_BY_TYPE``)."""
    if type_value in {
        ResearchNodeType.SOURCE_DOCUMENT.value,
    }:
        return "sources"
    if type_value == ResearchNodeType.PAPER.value:
        return "papers"
    if type_value in {
        ResearchNodeType.REPOSITORY.value,
        ResearchNodeType.PROJECT.value,
        ResearchNodeType.CODE_PROJECT.value,
    }:
        return "repos"
    if type_value in {
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
    }:
        return "concepts"
    if type_value in {
        ResearchNodeType.MODEL.value,
        ResearchNodeType.DATASET.value,
        ResearchNodeType.BENCHMARK.value,
        ResearchNodeType.METRIC.value,
        ResearchNodeType.RESULT.value,
        ResearchNodeType.ORGANIZATION.value,
        ResearchNodeType.PERSON.value,
    }:
        return "entities"
    if type_value in {
        ResearchNodeType.RESEARCH_FIELD.value,
        ResearchNodeType.RESEARCH_TOPIC.value,
        ResearchNodeType.PROBLEM_AREA.value,
        ResearchNodeType.APPROACH_FAMILY.value,
        ResearchNodeType.TREND.value,
    }:
        return "topics"
    if type_value == ResearchNodeType.SYNTHESIS.value:
        return "syntheses"
    if type_value == ResearchNodeType.OPEN_QUESTION.value:
        return "questions"
    return None


def _wiki_url_for(node: ResearchNode) -> Optional[str]:
    kind = _kind_for_type(node.type.value)
    if kind is None:
        return None
    return f"{kind}/{_slug(node.name)}.html"


_ARXIV_YEAR_RE = re.compile(r"^(\d{2})(\d{2})\.\d{4,6}$")


def _arxiv_published_date(arxiv_id: str) -> Optional[str]:
    """``2103.13413`` → ``2021-03``. Fragile but deterministic."""
    m = _ARXIV_YEAR_RE.match(arxiv_id)
    if not m:
        return None
    yy, mm = m.group(1), m.group(2)
    year = 1900 + int(yy) if int(yy) >= 91 else 2000 + int(yy)
    if not (1 <= int(mm) <= 12):
        return None
    return f"{year:04d}-{mm}"


def _by_id(graph: ResearchGraph) -> Dict[str, ResearchNode]:
    return {node.id: node for node in graph.nodes}


def _outgoing_by_type(graph: ResearchGraph) -> Dict[Tuple[str, str], List[str]]:
    """``(source_id, edge_type) -> [target_id, ...]`` (sorted; deterministic)."""
    out: Dict[Tuple[str, str], List[str]] = {}
    for edge in graph.edges:
        out.setdefault((edge.source, edge.type), []).append(edge.target)
    for key in out:
        out[key] = sorted(out[key])
    return out


def _entry_for_paper(
    node: ResearchNode,
    base: Dict[str, object],
    by_id: Mapping[str, ResearchNode],
    outgoing: Mapping[Tuple[str, str], List[str]],
) -> Dict[str, object]:
    md = node.metadata or {}
    arxiv_id = str(md.get("arxiv_id") or "").strip()
    base["headline"] = node.name
    if arxiv_id:
        base["identifier"] = arxiv_id
        base["sameAs"] = [f"https://arxiv.org/abs/{arxiv_id}"]
        published = _arxiv_published_date(arxiv_id)
        if published:
            base["datePublished"] = published
    # authors / publisher via graph edges
    authors: List[Dict[str, str]] = []
    for tgt in outgoing.get((node.id, "authored_by"), []):
        person = by_id.get(tgt)
        if person and person.type == ResearchNodeType.PERSON:
            authors.append({"@type": "Person", "@id": f"#{person.id}", "name": person.name})
    if authors:
        base["author"] = authors
    publishers: List[Dict[str, str]] = []
    for tgt in outgoing.get((node.id, "released_by"), []):
        org = by_id.get(tgt)
        if org and org.type == ResearchNodeType.ORGANIZATION:
            publishers.append(
                {"@type": "Organization", "@id": f"#{org.id}", "name": org.name}
            )
    if publishers:
        base["publisher"] = publishers
    # keywords: linked Concepts
    keywords: List[str] = []
    for edge_type in ("uses", "introduces", "extends"):
        for tgt in outgoing.get((node.id, edge_type), []):
            concept = by_id.get(tgt)
            if concept is None:
                continue
            if concept.type.value in {
                ResearchNodeType.CONCEPT.value,
                ResearchNodeType.TECHNICAL_TERM.value,
                ResearchNodeType.METHODOLOGICAL_CONCEPT.value,
                ResearchNodeType.ALGORITHM.value,
                ResearchNodeType.MATHEMATICAL_CONCEPT.value,
                ResearchNodeType.ARCHITECTURE_PATTERN.value,
                ResearchNodeType.TRAINING_PARADIGM.value,
                ResearchNodeType.INFERENCE_STRATEGY.value,
            }:
                if concept.name not in keywords:
                    keywords.append(concept.name)
    if keywords:
        base["keywords"] = sorted(keywords)
    return base


def _entry_for_repository(node: ResearchNode, base: Dict[str, object]) -> Dict[str, object]:
    md = node.metadata or {}
    repo_url = str(md.get("repo_url") or "").strip()
    github_repo = str(md.get("github_repo") or "").strip()
    code_repo = repo_url or (f"https://github.com/{github_repo}" if github_repo else "")
    if code_repo:
        base["codeRepository"] = code_repo
        base["sameAs"] = [code_repo]
    language = md.get("programming_language") or md.get("language")
    if isinstance(language, str) and language.strip():
        base["programmingLanguage"] = language.strip()
    return base


def _entry_for_definedterm(node: ResearchNode, base: Dict[str, object]) -> Dict[str, object]:
    base["inDefinedTermSet"] = _CONCEPT_TERM_SET
    base["termCode"] = _slug(node.name)
    return base


def _entry_for_synthesis(
    node: ResearchNode,
    base: Dict[str, object],
    by_id: Mapping[str, ResearchNode],
) -> Dict[str, object]:
    md = node.metadata or {}
    kind = md.get("synthesis_kind")
    if isinstance(kind, str) and kind:
        base["articleSection"] = kind
    citations: List[Dict[str, str]] = []
    raw_inputs = md.get("input_ids") or []
    if isinstance(raw_inputs, (list, tuple)):
        for cit in raw_inputs:
            if not isinstance(cit, str) or not cit:
                continue
            citation: Dict[str, str] = {"@type": "Thing", "@id": f"#{cit}"}
            target = by_id.get(cit)
            if target is not None:
                citation["name"] = target.name
            citations.append(citation)
    base["mentions"] = citations
    return base


def _entry_for_organization(node: ResearchNode, base: Dict[str, object]) -> Dict[str, object]:
    md = node.metadata or {}
    url = md.get("url") or md.get("homepage")
    if isinstance(url, str) and url.strip():
        base["url"] = url.strip()
    return base


def render_graph_jsonld(graph: ResearchGraph, ctx: Optional[ExportContext] = None) -> str:
    """Render schema.org JSON-LD over the wiki layer of ``graph``.

    The root object is a ``Dataset`` whose ``@graph`` lists one entry per
    wiki-layer node (each with its own ``@id``). Code-graph types and
    assertion-layer types are excluded.
    """

    site_title = ctx.site_title if ctx is not None else "Tesserae"
    by_id = _by_id(graph)
    outgoing = _outgoing_by_type(graph)

    parts: List[Dict[str, object]] = []
    for node in graph.nodes:
        if not is_wiki_layer(node):
            continue
        node_id = f"#{node.id}"
        entry: Dict[str, object] = {
            "@id": node_id,
            "@type": _schema_type_for(node),
            "name": node.name,
        }
        if node.description:
            entry["description"] = node.description
        if node.aliases:
            entry["alternateName"] = list(node.aliases)
        wiki_url = _wiki_url_for(node)
        if wiki_url:
            entry["url"] = wiki_url
        elif node.source_path:
            entry["url"] = node.source_path
        entry["additionalType"] = node.type.value

        type_value = node.type.value
        if type_value == ResearchNodeType.PAPER.value:
            entry = _entry_for_paper(node, entry, by_id, outgoing)
        elif type_value in {
            ResearchNodeType.REPOSITORY.value,
            ResearchNodeType.PROJECT.value,
            ResearchNodeType.CODE_PROJECT.value,
            ResearchNodeType.MODEL.value,
        }:
            entry = _entry_for_repository(node, entry)
        elif type_value == ResearchNodeType.SYNTHESIS.value:
            entry = _entry_for_synthesis(node, entry, by_id)
        elif type_value == ResearchNodeType.ORGANIZATION.value:
            entry = _entry_for_organization(node, entry)
        elif _schema_type_for(node) == "DefinedTerm":
            entry = _entry_for_definedterm(node, entry)
        parts.append(entry)

    date_modified: Optional[str] = None
    if ctx is not None and ctx.synthesis_history:
        latest = _latest_history_iso(ctx.synthesis_history)
        if latest != _EPOCH_ISO:
            date_modified = latest

    payload: Dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": site_title,
        "description": "Auto-generated knowledge graph of the Tesserae wiki layer.",
        "creator": {"@type": "Organization", "name": "Tesserae"},
        "@graph": parts,
    }
    if date_modified is not None:
        payload["dateModified"] = date_modified
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------- sitemap.xml


def _format_lastmod(when: Optional[datetime]) -> Optional[str]:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    # W3C datetime / sitemap-permitted ISO 8601.
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Stable "first seen" anchor for URLs that have no per-page lastmod.
# A hash of the URL maps deterministically into a window of dates after the
# anchor so the sitemap stays byte-stable across compiles.
_SITEMAP_ANCHOR = datetime(2024, 1, 1, tzinfo=timezone.utc)
_SITEMAP_FALLBACK_WINDOW_DAYS = 365


def _stable_lastmod_for_url(url: str) -> str:
    """Deterministic ISO date for a URL (used when no per-page lastmod is known)."""
    digest = hashlib.sha256(url.encode("utf-8")).digest()
    offset = int.from_bytes(digest[:4], "big") % _SITEMAP_FALLBACK_WINDOW_DAYS
    when = _SITEMAP_ANCHOR + timedelta(days=offset)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# Route family classification ------------------------------------------------

_DAILY_INDEX_PATHS: frozenset[str] = frozenset(
    {
        "index.html",
        "timeline/index.html",
        "papers/index.html",
        "concepts/index.html",
        "entities/index.html",
        "repos/index.html",
        "topics/index.html",
        "sources/index.html",
        "syntheses/index.html",
        "questions/index.html",
    }
)

_DETAIL_KIND_PREFIXES_HIGH_PRIORITY: Tuple[str, ...] = (
    "papers/",
    "repos/",
    "topics/",
    "syntheses/",
)
_DETAIL_KIND_PREFIXES_LOW_PRIORITY: Tuple[str, ...] = (
    "concepts/",
    "entities/",
    "sources/",
    "questions/",
)


def _changefreq_for(url: str) -> str:
    """Per-family ``<changefreq>`` value (sitemaps.org enum)."""
    normalized = url.lstrip("/")
    if normalized == "" or normalized == "/":
        return "daily"
    if normalized in _DAILY_INDEX_PATHS:
        return "daily"
    if normalized.startswith("timeline/") and normalized != "timeline/index.html":
        # Per-day timeline detail pages.
        return "weekly"
    if normalized == "about.html":
        return "monthly"
    if normalized.startswith("raw/"):
        return "monthly"
    if normalized.startswith("graph/"):
        return "weekly"
    # Wiki-kind detail pages.
    for prefix in _DETAIL_KIND_PREFIXES_HIGH_PRIORITY + _DETAIL_KIND_PREFIXES_LOW_PRIORITY:
        if normalized.startswith(prefix):
            return "weekly"
    return "weekly"


def _priority_for(url: str) -> str:
    """Per-family ``<priority>`` value (sitemaps.org range 0.0..1.0)."""
    normalized = url.lstrip("/")
    if normalized == "" or normalized == "/" or normalized == "index.html":
        return "1.0"
    if normalized in _DAILY_INDEX_PATHS:
        return "0.9"
    if normalized == "about.html":
        return "0.3"
    if normalized.startswith("raw/"):
        return "0.4"
    if normalized.startswith("graph/"):
        return "0.6"
    for prefix in _DETAIL_KIND_PREFIXES_HIGH_PRIORITY:
        if normalized.startswith(prefix):
            return "0.8"
    for prefix in _DETAIL_KIND_PREFIXES_LOW_PRIORITY:
        if normalized.startswith(prefix):
            return "0.6"
    if normalized.startswith("timeline/"):
        return "0.6"
    return "0.6"


def render_sitemap_xml(routes: Sequence[Tuple[str, Optional[datetime]]]) -> str:
    """Render a ``sitemaps.org``-compliant sitemap from ``(url, lastmod)`` pairs.

    Every URL gets a ``<changefreq>`` and ``<priority>`` derived from its
    route family. When a per-page ``lastmod`` is missing, a deterministic
    "first seen" date is hashed from the URL so two compiles produce
    byte-identical XML.
    """

    lines: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url, lastmod in routes:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(url)}</loc>")
        formatted = _format_lastmod(lastmod) or _stable_lastmod_for_url(url)
        lines.append(f"    <lastmod>{formatted}</lastmod>")
        lines.append(f"    <changefreq>{_changefreq_for(url)}</changefreq>")
        lines.append(f"    <priority>{_priority_for(url)}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- rss.xml


_RSS_RECENT_LIMIT = 30
_EPOCH_ISO = "1970-01-01T00:00:00Z"
_RSS_DESCRIPTION_LIMIT = 800


def _parse_iso(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _rss_pubdate(when: Optional[datetime]) -> str:
    """Format ``when`` for RSS. ``None`` becomes the Unix epoch (stable bogus)."""
    if when is None:
        when = _parse_iso(_EPOCH_ISO)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _synthesis_pubdate(page: WikiPage) -> Optional[datetime]:
    """Best-effort pubdate from on-page frontmatter (legacy fallback only)."""
    fm = page.frontmatter or {}
    if not isinstance(fm, dict):
        return None
    for key in ("generated_at", "updated_at", "published_at", "date"):
        parsed = _parse_iso(fm.get(key))
        if parsed is not None:
            return parsed
    return None


def _ledger_index(history: Sequence[Mapping[str, str]]) -> Dict[str, str]:
    """Reduce the append-only ledger to ``slug -> latest generated_at``."""
    out: Dict[str, str] = {}
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        slug = entry.get("slug")
        when = entry.get("generated_at")
        if not isinstance(slug, str) or not isinstance(when, str):
            continue
        prior = out.get(slug)
        if prior is None or when > prior:
            out[slug] = when
    return out


def _ledger_generator_index(history: Sequence[Mapping[str, str]]) -> Dict[str, str]:
    """Reduce the append-only ledger to ``slug -> latest generator``."""
    by_slug_time: Dict[str, str] = {}
    by_slug_gen: Dict[str, str] = {}
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        slug = entry.get("slug")
        when = entry.get("generated_at")
        gen = entry.get("generator")
        if not isinstance(slug, str) or not isinstance(when, str):
            continue
        prior = by_slug_time.get(slug)
        if prior is None or when > prior:
            by_slug_time[slug] = when
            if isinstance(gen, str):
                by_slug_gen[slug] = gen
    return by_slug_gen


def _latest_history_iso(history: Sequence[Mapping[str, str]]) -> str:
    """Most recent ``generated_at`` across the whole ledger, or epoch if empty."""
    latest = ""
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        when = entry.get("generated_at")
        if isinstance(when, str) and when > latest:
            latest = when
    return latest or _EPOCH_ISO


def _rss_description(page: WikiPage) -> str:
    """Synthesis body (frontmatter stripped, trimmed) for ``<description>``.

    The result is plain text — the renderer wraps it in CDATA so RSS readers
    don't HTML-decode any literal ``&``/``<`` in the markdown.
    """
    body = _strip_frontmatter(page.body or "")
    # Drop the leading ``# Title`` so the description doesn't duplicate the
    # ``<title>`` element in feed readers.
    lines = body.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    if not body:
        body = _page_summary(page) or _page_title(page)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) <= _RSS_DESCRIPTION_LIMIT:
        return body
    return body[: _RSS_DESCRIPTION_LIMIT - 1].rstrip() + "…"


def _cdata(text: str) -> str:
    """Wrap ``text`` in CDATA, escaping any inner ``]]>`` sequences."""
    safe = (text or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def _synthesis_kind(page: WikiPage) -> Optional[str]:
    fm = page.frontmatter or {}
    if isinstance(fm, dict):
        kind = fm.get("synthesis_kind")
        if isinstance(kind, str) and kind.strip():
            return kind.strip()
    return None


def _creator_for(page: WikiPage, generator_by_slug: Mapping[str, str]) -> str:
    """RSS ``<dc:creator>`` value: the LLM model id when generated by an LLM, else ``Tesserae``."""
    fm = page.frontmatter or {}
    generator: Optional[str] = None
    if isinstance(fm, dict):
        gen_field = fm.get("generator")
        if isinstance(gen_field, str) and gen_field.strip():
            generator = gen_field.strip()
    if generator is None:
        generator = generator_by_slug.get(page.slug)
    if isinstance(generator, str) and generator.startswith("llm-"):
        return generator
    return "Tesserae"


def render_rss_xml(
    site_title: str,
    recent_syntheses: Sequence[WikiPage],
    history: Sequence[Mapping[str, str]] = (),
    *,
    ctx: Optional[ExportContext] = None,
) -> str:
    """RSS 2.0 feed of the latest 30 synthesis pages.

    ``<lastBuildDate>`` and per-item ``<pubDate>`` are sourced from the
    append-only synthesis history ledger so two consecutive recompiles produce
    byte-identical output. When the ledger is empty (fresh clone), dates fall
    back to the Unix epoch — stable and clearly bogus.

    Each item carries ``<description>`` (CDATA, first ~800 chars of the
    synthesis body), ``<guid>``, ``<dc:creator>``, and ``<category>``.
    Channel-level adds ``atom:link rel="self"``, ``<copyright>``,
    ``<generator>``, ``<language>``.
    """

    items_to_render = list(recent_syntheses[:_RSS_RECENT_LIMIT])
    by_slug = _ledger_index(history)
    by_slug_gen = _ledger_generator_index(history)
    last_build_iso = _latest_history_iso(history)
    language = _detect_language(ctx) if ctx is not None else "en"
    current_year = _parse_iso(last_build_iso)
    copyright_year = current_year.year if current_year is not None else 1970

    lines: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        '<rss version="2.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:atom="http://www.w3.org/2005/Atom">'
    )
    lines.append("  <channel>")
    lines.append(f"    <title>{xml_escape(site_title)}</title>")
    lines.append(
        f"    <description>{xml_escape('Recent syntheses from ' + site_title)}</description>"
    )
    lines.append("    <link>/</link>")
    lines.append('    <atom:link href="/rss.xml" rel="self" type="application/rss+xml" />')
    lines.append(f"    <language>{xml_escape(language)}</language>")
    lines.append(
        f"    <copyright>{xml_escape(f'Copyright {copyright_year} {site_title}')}</copyright>"
    )
    lines.append("    <generator>tesserae</generator>")
    lines.append(
        f"    <lastBuildDate>{_rss_pubdate(_parse_iso(last_build_iso))}</lastBuildDate>"
    )

    for page in items_to_render:
        title = _page_title(page)
        href = _page_href(page)
        ledger_iso = by_slug.get(page.slug)
        pub_dt = _parse_iso(ledger_iso) if ledger_iso else _synthesis_pubdate(page)
        pubdate = _rss_pubdate(pub_dt)
        guid = page.slug  # node-id-like stable token; ``isPermaLink="false"`` flags non-URL.
        description = _rss_description(page)
        creator = _creator_for(page, by_slug_gen)
        category = _synthesis_kind(page)
        lines.append("    <item>")
        lines.append(f"      <title>{xml_escape(title)}</title>")
        lines.append(f"      <link>{xml_escape(href)}</link>")
        lines.append(f'      <guid isPermaLink="false">{xml_escape(f"synthesis:{guid}")}</guid>')
        lines.append(f"      <description>{_cdata(description)}</description>")
        lines.append(f"      <dc:creator>{xml_escape(creator)}</dc:creator>")
        if category:
            lines.append(f"      <category>{xml_escape(category)}</category>")
        lines.append(f"      <pubDate>{pubdate}</pubDate>")
        lines.append("    </item>")

    lines.append("  </channel>")
    lines.append("</rss>")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- robots / ai-readme


def render_robots_txt() -> str:
    """AI-friendly robots.txt — explicit Allow + sitemap pointer."""

    return "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n"


def render_ai_readme(site_title: str, ctx: ExportContext) -> str:
    """A short markdown briefing telling agents what each route is for."""

    lines: List[str] = []
    lines.append(f"# {site_title} — agent guide")
    lines.append("")
    lines.append(
        "This site is an auto-generated wiki layer over a research graph. "
        "Everything is content-hashed and idempotent; routes are stable across compiles."
    )
    lines.append("")
    lines.append("## Where to look first")
    lines.append("")
    lines.append("- `/llms.txt` — terse table of every wiki page.")
    lines.append("- `/llms-full.txt` — full text of every wiki page (capped at ~5MB).")
    lines.append("- `/graph.jsonld` — schema.org JSON-LD of every wiki-layer node.")
    lines.append("- `/sitemap.xml` — every renderable URL with a last-modified timestamp.")
    lines.append("- `/rss.xml` — the latest 30 synthesis pages (digests, weekly rollups).")
    lines.append("")
    lines.append("## Per-page siblings")
    lines.append("")
    lines.append(
        "Every `path/foo.html` is paired with a `path/foo.txt` (plain text) and a "
        "`path/foo.json` (structured record). Use the `.json` for programmatic reads."
    )
    lines.append("")
    lines.append("## Wiki-layer kinds")
    lines.append("")
    for kind in _ORDERED_KINDS:
        count = len(list(ctx.wiki_pages_by_kind.get(kind, [])))
        lines.append(f"- `/{kind}/` — {count} page(s).")
    lines.append("")
    lines.append("## What is *not* surfaced")
    lines.append("")
    lines.append(
        "Code-graph nodes (CodeClass / CodeFunction / CodeModule / Dependency / SourceFile) "
        "and assertion-layer nodes (Claim variants / EvidenceSpan) live in `graph.json` for "
        "MCP and Cognee consumers, but they have no HTML route and no entry in "
        "`search-index.json`."
    )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------- siblings


def write_siblings(html_path: Path, page_record: Mapping[str, object]) -> None:
    """Write ``foo.txt`` and ``foo.json`` next to ``foo.html``.

    ``page_record`` is the canonical structured record for the page — keys::

        {
            "title": str,
            "kind": str,
            "body_text": str,
            "source_path": str,
            "links": list[str],
        }

    Extra keys are preserved in the JSON sibling but ignored by the text one.
    """

    html_path = Path(html_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path = html_path.with_suffix(".txt")
    json_path = html_path.with_suffix(".json")

    title = str(page_record.get("title", "")).strip()
    body_text = str(page_record.get("body_text", "")).strip()
    plain_lines = []
    if title:
        plain_lines.append(title)
        plain_lines.append("=" * max(3, min(80, len(title))))
        plain_lines.append("")
    if body_text:
        plain_lines.append(body_text)
    txt_path.write_text("\n".join(plain_lines).rstrip() + "\n", encoding="utf-8")

    record: Dict[str, object] = {
        "title": title,
        "kind": str(page_record.get("kind", "")),
        "body_text": body_text,
        "source_path": str(page_record.get("source_path", "")),
        "links": list(page_record.get("links", []) or []),
    }
    for key, value in page_record.items():
        if key not in record:
            record[key] = value
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "ExportContext",
    "slice_export_context_for_topic",
    "render_llms_txt",
    "render_llms_full_txt",
    "render_graph_jsonld",
    "render_sitemap_xml",
    "render_rss_xml",
    "render_robots_txt",
    "render_ai_readme",
    "write_siblings",
]
