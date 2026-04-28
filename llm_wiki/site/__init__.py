"""``llm_wiki.site`` package.

This module is the real ``StaticSiteBuilder`` for the redesigned LLM-Wiki:
it consumes a ``ResearchGraph`` plus a markdown wiki layer (``WikiPageStore``)
and emits a complete static site under ``output_dir``. The site honours the
information architecture from §3.1 of the redesign spec — sources, concepts,
entities, papers, repos, topics, syntheses, questions, plus timeline / graph
view / about — and ships the AI-friendly exports (llms.txt, llms-full.txt,
graph.jsonld, search-index.json, sitemap.xml, rss.xml, robots.txt,
ai-readme.md, manifest.json).

Per-``CodeClass`` / ``CodeFunction`` HTML pages are deliberately not rendered
— they remain in ``graph.json`` so MCP/Cognee/Graphiti consumers still see
them, but they have no URL of their own and no entry in ``search-index.json``.

The builder is deterministic: re-running ``write_site`` over the same input
produces byte-identical output across all files, including ``manifest.json``.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..research_graph import ResearchGraph
from ..wiki_store import WikiPage, WikiPageStore
from .exports import (
    ExportContext,
    render_ai_readme,
    render_graph_jsonld,
    render_llms_full_txt,
    render_llms_txt,
    render_robots_txt,
    render_rss_xml,
    render_sitemap_xml,
    write_siblings,
)
from .js import JS_BUNDLE
from .pages import (
    ROUTE_FOR_KIND,
    SiteContext,
    page_href,
    render_about,
    render_concept_detail,
    render_concepts_index,
    render_entities_index,
    render_entity_detail,
    render_graph_view,
    render_home,
    render_paper_detail,
    render_papers_index,
    render_question_detail,
    render_questions_index,
    render_repo_detail,
    render_repos_index,
    render_source_detail,
    render_sources_index,
    render_synthesis_detail,
    render_syntheses_index,
    render_timeline,
    render_timeline_day,
    render_topic_detail,
    render_topics_index,
)
from .search import build_search_index
from .tokens import CSS


__all__ = ["StaticSiteBuilder"]


# Wiki-layer kinds we render index pages + detail pages for.
_WIKI_KINDS: Tuple[str, ...] = (
    "sources",
    "concepts",
    "entities",
    "papers",
    "repos",
    "topics",
    "syntheses",
    "questions",
)


# Map kind → (index renderer, detail renderer).
_RENDERERS: Mapping[str, Tuple[object, object]] = {
    "sources": (render_sources_index, render_source_detail),
    "concepts": (render_concepts_index, render_concept_detail),
    "entities": (render_entities_index, render_entity_detail),
    "papers": (render_papers_index, render_paper_detail),
    "repos": (render_repos_index, render_repo_detail),
    "topics": (render_topics_index, render_topic_detail),
    "syntheses": (render_syntheses_index, render_synthesis_detail),
    "questions": (render_questions_index, render_question_detail),
}


_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)


def _strip_frontmatter(body: str) -> str:
    """Return ``body`` with any leading YAML frontmatter block removed."""
    match = _FRONTMATTER_RE.match(body)
    if match:
        return body[match.end():]
    return body


def _extract_links(body: str) -> List[str]:
    """Return the list of href targets found in ``body`` markdown.

    Used for the ``.json`` sibling artifact. Order is preserved (first
    occurrence wins) so the JSON output is deterministic.
    """
    seen: List[str] = []
    seen_set: set[str] = set()
    for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", body):
        href = match.group(1).strip()
        if href and href not in seen_set:
            seen.append(href)
            seen_set.add(href)
    return seen


def _safe_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        cleaned = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


@dataclass
class StaticSiteBuilder:
    """Render a static LLM-Wiki site from a graph + markdown wiki layer."""

    site_title: str = "LLM-Wiki"

    # ------------------------------------------------------------------ public

    def write_site(
        self,
        graph: ResearchGraph,
        wiki_root: Union[str, Path, None] = None,
        output_dir: Union[str, Path, None] = None,
    ) -> Dict[str, object]:
        """Render the full site.

        Two call shapes are supported (the second is the legacy two-arg
        signature still used by ``llm_wiki.project.ProjectWiki.build_site``):

            write_site(graph, wiki_root, output_dir)   # new
            write_site(graph, output_dir)              # legacy

        For the legacy shape the wiki layer is treated as empty: index pages
        render with empty-state messaging, no detail pages are emitted, and
        the graph still drives ``graph.json`` / ``search-index.json`` /
        ``llms.txt`` etc.
        """
        # Disambiguate the legacy two-arg call: when only two positional args
        # arrive, the second is the output dir and the wiki layer is empty.
        if output_dir is None:
            if wiki_root is None:
                raise TypeError("write_site() requires an output directory")
            output_dir = wiki_root
            wiki_root = None

        out = Path(output_dir)
        # Preserve the append-only build history ledger across rebuilds: read
        # any existing entries before wiping the site directory, then re-emit
        # them (plus this build's entry) at the end. The ledger lives next to
        # ``manifest.json`` and is the only file in ``out`` that is allowed to
        # carry a build-time timestamp.
        prior_build_history = _read_build_history(out / ".build-history.jsonl")
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)

        # ------------------------------------------------------------ load wiki
        wiki_pages_by_kind: Dict[str, List[WikiPage]] = {kind: [] for kind in _WIKI_KINDS}
        synthesis_history: List[Dict[str, str]] = []
        if wiki_root is not None:
            store = WikiPageStore(wiki_root)
            for kind in _WIKI_KINDS:
                wiki_pages_by_kind[kind] = list(store.list_pages(kind))
            synthesis_history = _read_synthesis_history(
                Path(wiki_root) / "syntheses" / ".history.jsonl"
            )

        # ----------------------------------------------------------- contexts
        site_ctx = SiteContext.build(
            graph=graph,
            wiki_pages_by_kind=wiki_pages_by_kind,
            site_title=self.site_title,
        )

        # ------------------------------------------------------ static assets
        (out / "assets").mkdir(parents=True, exist_ok=True)
        (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")
        (out / "assets" / "app.js").write_text(JS_BUNDLE, encoding="utf-8")

        # ------------------------------------------------- graph + search idx
        graph_payload = graph.model_dump()
        (out / "graph.json").write_text(
            json.dumps(graph_payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        search_index = build_search_index(graph, wiki_pages_by_kind)
        (out / "search-index.json").write_text(
            json.dumps(search_index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # --------------------------------------------------- routes inventory
        # We collect every emitted route here so sitemap.xml stays in lockstep.
        routes: List[Tuple[str, Optional[datetime]]] = []
        page_count = 0

        def _track(rel_url: str, lastmod: Optional[datetime] = None) -> None:
            nonlocal page_count
            routes.append((rel_url, lastmod))
            page_count += 1

        # ----------------------------------------------------------- top-level
        (out / "index.html").write_text(render_home(site_ctx), encoding="utf-8")
        _track("index.html")

        (out / "about.html").write_text(render_about(site_ctx), encoding="utf-8")
        _track("about.html")

        (out / "timeline").mkdir(parents=True, exist_ok=True)
        (out / "timeline" / "index.html").write_text(render_timeline(site_ctx), encoding="utf-8")
        _track("timeline/index.html")

        # Per-day timeline detail pages — one per ISO date with at least
        # one node anchored to it. Sorted lexicographically (== ISO chrono)
        # so the emission order is deterministic.
        for day in sorted(d for d, ids in site_ctx.activity_by_day.items() if ids):
            day_html = out / "timeline" / f"{day}.html"
            day_html.write_text(render_timeline_day(site_ctx, day), encoding="utf-8")

            # AI siblings next to each day page so MCP/Cognee consumers can
            # diff a day's structured fields without parsing HTML.
            ids = site_ctx.activity_by_day.get(day, frozenset())
            sources = list(site_ctx.sources_by_day.get(day, ()))
            day_record: Dict[str, object] = {
                "title": day,
                "kind": "timeline_day",
                "body": f"Activity for {day}",
                "body_text": f"Activity for {day}: {len(ids)} item(s) indexed.",
                "links": [],
                "source_path": "",
                "frontmatter": {
                    "date": day,
                    "activity": len(ids),
                    "sources": sources,
                },
            }
            write_siblings(day_html, day_record)
            _track(f"timeline/{day}.html")

        (out / "graph").mkdir(parents=True, exist_ok=True)
        (out / "graph" / "index.html").write_text(render_graph_view(site_ctx), encoding="utf-8")
        _track("graph/index.html")

        # --------------------------------------------------- index/detail kinds
        for kind in _WIKI_KINDS:
            kind_dir = out / kind
            kind_dir.mkdir(parents=True, exist_ok=True)
            index_renderer, detail_renderer = _RENDERERS[kind]
            (kind_dir / "index.html").write_text(index_renderer(site_ctx), encoding="utf-8")
            _track(f"{kind}/index.html")

            for page in sorted(wiki_pages_by_kind.get(kind, []), key=lambda p: p.slug):
                html_path = kind_dir / f"{page.slug}.html"
                html_path.write_text(detail_renderer(site_ctx, page), encoding="utf-8")

                # Per-page AI siblings (.txt + .json next to the .html).
                fm = page.frontmatter or {}
                source_path = ""
                if isinstance(fm, dict):
                    sp = fm.get("source_path") or fm.get("source") or ""
                    if isinstance(sp, str):
                        source_path = sp
                body_text = _strip_frontmatter(page.body).strip()
                record: Dict[str, object] = {
                    "title": page.title or page.slug,
                    "kind": page.kind,
                    "body": body_text,
                    "body_text": body_text,
                    "links": _extract_links(page.body),
                    "source_path": source_path,
                }
                # Extra structured fields for programmatic consumers.
                if isinstance(fm, dict) and fm:
                    record["frontmatter"] = {
                        str(k): fm[k] for k in sorted(fm.keys())
                    }
                write_siblings(html_path, record)

                lastmod = None
                if isinstance(fm, dict):
                    for key in ("generated_at", "updated_at", "published_at", "date"):
                        lastmod = _safe_datetime(fm.get(key))
                        if lastmod is not None:
                            break
                _track(f"{kind}/{page.slug}.html", lastmod)

        # ------------------------------------------------------------ exports
        export_ctx = ExportContext(
            site_title=self.site_title,
            graph=graph,
            wiki_pages_by_kind=wiki_pages_by_kind,
            routes=tuple(routes),
            synthesis_history=tuple(synthesis_history),
        )

        (out / "llms.txt").write_text(render_llms_txt(self.site_title, export_ctx), encoding="utf-8")
        (out / "llms-full.txt").write_text(
            render_llms_full_txt(self.site_title, export_ctx), encoding="utf-8"
        )
        (out / "graph.jsonld").write_text(
            render_graph_jsonld(graph, export_ctx), encoding="utf-8"
        )
        (out / "sitemap.xml").write_text(render_sitemap_xml(routes), encoding="utf-8")
        recent_syntheses = self._recent_syntheses(
            wiki_pages_by_kind.get("syntheses", []), synthesis_history
        )
        (out / "rss.xml").write_text(
            render_rss_xml(self.site_title, recent_syntheses, synthesis_history),
            encoding="utf-8",
        )
        (out / "robots.txt").write_text(render_robots_txt(), encoding="utf-8")
        (out / "ai-readme.md").write_text(
            render_ai_readme(self.site_title, export_ctx), encoding="utf-8"
        )

        # ---------------------------------------------------------- manifest
        manifest_payload = self._manifest(out)
        (out / "manifest.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # ---------------------------------------------- build-history ledger
        # Append a single line for this build, preserving any prior entries that
        # survived from before the rebuild. The ledger is the *only* file in
        # ``out`` that is allowed to differ between back-to-back compiles —
        # everything else is byte-identical when nothing real has changed.
        build_entry = {
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "file_count": int(manifest_payload.get("file_count", 0)),
            "total_bytes": sum(
                int(entry.get("size", 0))
                for entry in manifest_payload.get("files", [])
                if isinstance(entry, dict)
            ),
        }
        history_lines = list(prior_build_history)
        history_lines.append(json.dumps(build_entry, sort_keys=True, ensure_ascii=False))
        (out / ".build-history.jsonl").write_text(
            "\n".join(history_lines) + "\n", encoding="utf-8"
        )

        return {
            "site_path": str(out),
            "page_count": page_count,
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "search_entries": len(search_index),
            "html_pages": page_count,
        }

    # -------------------------------------------------------------- internals

    @staticmethod
    def _recent_syntheses(
        pages: Sequence[WikiPage],
        history: Sequence[Mapping[str, str]] = (),
    ) -> List[WikiPage]:
        """Return up to 30 synthesis pages sorted newest-first.

        Order is keyed off the synthesis history ledger (``slug -> latest
        generated_at``). Pages without a ledger entry fall to the end in slug
        order so the result remains deterministic across recompiles.
        """

        ledger: Dict[str, str] = {}
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            slug = entry.get("slug")
            when = entry.get("generated_at")
            if not isinstance(slug, str) or not isinstance(when, str):
                continue
            prior = ledger.get(slug)
            if prior is None or when > prior:
                ledger[slug] = when

        def _sort_key(p: WikiPage) -> Tuple[int, str, str]:
            stamp = ledger.get(p.slug, "")
            if not stamp:
                fm = p.frontmatter or {}
                if isinstance(fm, dict):
                    for key in ("generated_at", "updated_at", "published_at", "date"):
                        candidate = fm.get(key)
                        if isinstance(candidate, str) and candidate.strip():
                            stamp = candidate.strip()
                            break
            return (0 if stamp else 1, "" if not stamp else _negate_string(stamp), p.slug)

        ordered = sorted(pages, key=_sort_key)
        return ordered[:30]

    @staticmethod
    def _manifest(out: Path) -> Dict[str, object]:
        """Build a deterministic file inventory of the rendered site.

        Iterates ``sorted(out.rglob("*"))`` and records ``{path, sha256, size}``
        for every regular file. Two consecutive ``write_site`` calls over the
        same input must yield byte-identical manifests.

        Build-time timestamps live in ``.build-history.jsonl`` (next to this
        manifest); they are intentionally excluded from the manifest itself so
        the manifest is content-stable.
        """
        files: List[Dict[str, object]] = []
        for path in sorted(out.rglob("*")):
            if not path.is_file():
                continue
            # Skip the manifest itself — we are about to write it.
            if path.name == "manifest.json" and path.parent == out:
                continue
            # Skip the build-history ledger; it is the audit trail, not a
            # content-addressable artifact.
            if path.name == ".build-history.jsonl" and path.parent == out:
                continue
            data = path.read_bytes()
            files.append(
                {
                    "path": str(path.relative_to(out)).replace("\\", "/"),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                }
            )
        return {
            "version": "1",
            "generator": "llm-wiki",
            "file_count": len(files),
            "files": files,
        }


def _read_synthesis_history(path: Path) -> List[Dict[str, str]]:
    """Parse the synthesis history ledger; return ``[]`` if it doesn't exist.

    One JSON object per line. Malformed lines are skipped silently — this
    mirrors how every other "append-only audit log" tool we ship handles
    partial writes.
    """
    if not path.exists():
        return []
    out: List[Dict[str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append({str(k): str(v) for k, v in entry.items()})
    return out


def _read_build_history(path: Path) -> List[str]:
    """Return existing build-history lines so we can preserve them across rebuilds."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def _negate_string(value: str) -> str:
    """Return a string that sorts in *reverse* lexicographic order vs ``value``.

    Used so a timestamp like ``2026-04-27T12:00:00Z`` produces a sort key that
    lists newer dates first when sorted ascending — without invoking a real
    ``datetime`` parse and the timezone branches that come with it.
    """
    # Map every byte to its complement so larger inputs get smaller keys.
    return "".join(chr(0xFFFF - ord(ch)) for ch in value)
