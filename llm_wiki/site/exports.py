"""AI-friendly export artifacts for the static wiki site.

These renderers emit the non-HTML files an LLM-Wiki ships next to the HTML:

- ``llms.txt`` / ``llms-full.txt`` (llmstxt.org convention)
- ``graph.jsonld`` (schema.org Dataset JSON-LD; wiki-layer nodes only)
- ``sitemap.xml`` / ``rss.xml`` / ``robots.txt`` / ``ai-readme.md``
- per-page sibling artifacts (``foo.txt`` and ``foo.json`` next to ``foo.html``)

Everything here is wiki-layer only. Code-graph types (``CodeClass`` etc.) and
assertion-layer types (``Claim`` variants, ``EvidenceSpan``) stay out of these
exports — the wiki layer is the user-facing surface.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as xml_escape

from ..research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from ..wiki_store import WikiPage
from .search import WIKI_LAYER_TYPES, is_wiki_layer


# ----------------------------------------------------------------- ExportContext


@dataclass(frozen=True)
class ExportContext:
    """Shared input bundle for every renderer in this module.

    ``Subagent E`` defines an isomorphic ``SiteContext`` for its page
    renderers; the orchestrator (Subagent G) constructs both. Keep the field
    names in lockstep — do not import ``SiteContext`` here.
    """

    site_title: str
    graph: ResearchGraph
    wiki_pages_by_kind: Mapping[str, Sequence[WikiPage]] = field(default_factory=dict)
    routes: Sequence[Tuple[str, Optional[datetime]]] = field(default_factory=tuple)


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
    ResearchNodeType.SYNTHESIS.value: "Article",
    ResearchNodeType.PERSON.value: "Person",
    ResearchNodeType.ORGANIZATION.value: "Organization",
}


def _schema_type_for(node: ResearchNode) -> str:
    return _JSONLD_TYPE_BY_NODE.get(node.type.value, "Thing")


def render_graph_jsonld(graph: ResearchGraph, ctx: Optional[ExportContext] = None) -> str:
    """Render schema.org JSON-LD over the wiki layer of ``graph``.

    The root object is a ``Dataset`` whose ``hasPart`` lists one entry per
    wiki-layer node. Code-graph types and assertion-layer types are excluded.
    """

    site_title = ctx.site_title if ctx is not None else "LLM-Wiki"

    parts: List[Dict[str, object]] = []
    for node in graph.nodes:
        if not is_wiki_layer(node):
            continue
        entry: Dict[str, object] = {
            "@id": f"#{node.id}",
            "@type": _schema_type_for(node),
            "name": node.name,
        }
        if node.description:
            entry["description"] = node.description
        if node.aliases:
            entry["alternateName"] = list(node.aliases)
        if node.source_path:
            entry["url"] = node.source_path
        entry["additionalType"] = node.type.value
        parts.append(entry)

    payload: Dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": site_title,
        "description": "Auto-generated knowledge graph of the LLM-Wiki wiki layer.",
        "hasPart": parts,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------- sitemap.xml


def _format_lastmod(when: Optional[datetime]) -> Optional[str]:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    # W3C datetime / sitemap-permitted ISO 8601.
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_sitemap_xml(routes: Sequence[Tuple[str, Optional[datetime]]]) -> str:
    """Render a ``sitemaps.org``-compliant sitemap from ``(url, lastmod)`` pairs."""

    lines: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url, lastmod in routes:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(url)}</loc>")
        formatted = _format_lastmod(lastmod)
        if formatted is not None:
            lines.append(f"    <lastmod>{formatted}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- rss.xml


_RSS_RECENT_LIMIT = 30


def _rss_pubdate(when: Optional[datetime]) -> str:
    if when is None:
        when = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _synthesis_pubdate(page: WikiPage) -> Optional[datetime]:
    fm = page.frontmatter or {}
    if not isinstance(fm, dict):
        return None
    for key in ("generated_at", "updated_at", "published_at", "date"):
        value = fm.get(key)
        if isinstance(value, str) and value.strip():
            try:
                cleaned = value.strip().replace("Z", "+00:00")
                return datetime.fromisoformat(cleaned)
            except ValueError:
                continue
    return None


def render_rss_xml(site_title: str, recent_syntheses: Sequence[WikiPage]) -> str:
    """RSS 2.0 feed of the latest 30 synthesis pages."""

    items_to_render = list(recent_syntheses[:_RSS_RECENT_LIMIT])
    lines: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<rss version="2.0">')
    lines.append("  <channel>")
    lines.append(f"    <title>{xml_escape(site_title)}</title>")
    lines.append(f"    <description>{xml_escape('Recent syntheses from ' + site_title)}</description>")
    lines.append("    <link>/</link>")
    lines.append(f"    <lastBuildDate>{_rss_pubdate(datetime.now(timezone.utc))}</lastBuildDate>")

    for page in items_to_render:
        title = _page_title(page)
        href = _page_href(page)
        summary = _page_summary(page) or title
        pubdate = _rss_pubdate(_synthesis_pubdate(page))
        guid = f"synthesis:{page.slug}"
        lines.append("    <item>")
        lines.append(f"      <title>{xml_escape(title)}</title>")
        lines.append(f"      <link>{xml_escape(href)}</link>")
        lines.append(f'      <guid isPermaLink="false">{xml_escape(guid)}</guid>')
        lines.append(f"      <description>{xml_escape(summary)}</description>")
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
    "render_llms_txt",
    "render_llms_full_txt",
    "render_graph_jsonld",
    "render_sitemap_xml",
    "render_rss_xml",
    "render_robots_txt",
    "render_ai_readme",
    "write_siblings",
]
