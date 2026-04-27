"""Link-integrity check across the rendered static site.

Every internal ``href`` produced by the builder must resolve to a real file
under the output directory. External links (``http``/``https``/``mailto``)
and pure in-page anchors (``#section``) are skipped, as are the AI siblings
(`.txt` and `.json`) — those are validated separately by the smoke test.

The test stands up its own toy graph + wiki layer so it runs without any
project-level fixture.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urldefrag

import pytest

from llm_wiki.site import StaticSiteBuilder
from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from llm_wiki.wiki_store import WikiPage, WikiPageStore


def _toy_graph() -> ResearchGraph:
    nodes = [
        ResearchNode(
            id="Paper:demo",
            name="Demo Paper",
            type=ResearchNodeType.PAPER,
            description="A demo paper.",
            source_path="data/papers/demo.pdf",
        ),
        ResearchNode(
            id="Concept:gs",
            name="Gaussian Splatting",
            type=ResearchNodeType.CONCEPT,
            description="A 3D scene representation.",
            source_path="data/papers/demo.pdf",
        ),
        ResearchNode(
            id="Repository:demo",
            name="demo-repo",
            type=ResearchNodeType.REPOSITORY,
            description="A demo repo.",
        ),
    ]
    edges = [
        ResearchEdge(source="Paper:demo", target="Concept:gs", type="mentioned_in"),
        ResearchEdge(source="Repository:demo", target="Paper:demo", type="implemented_in"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _seed_wiki(root: Path) -> None:
    """Seed a wiki layer whose slugs match :meth:`WikiPageStore.slug_for`.

    The page renderers now mint related/mentions card hrefs from
    ``slug_for(node.name)`` (matching what :class:`WikiLayerProjector`
    writes to disk), so the seed slugs here must be derived from the node
    *names*, not the node ids. ``slug_for("Demo Paper")`` → ``"demo-paper"``,
    etc.
    """
    store = WikiPageStore(root)
    seeds = [
        ("sources", "Demo Source", "# Demo Source\n\nA short blurb.\n"),
        ("concepts", "Gaussian Splatting", "# Gaussian Splatting\n\nA 3D scene representation.\n"),
        ("papers", "Demo Paper", "# Demo Paper\n\nA paper page.\n"),
        ("repos", "demo-repo", "# demo-repo\n\nA demo repo page.\n"),
    ]
    pages: List[WikiPage] = []
    for kind, name, body in seeds:
        slug = store.slug_for(name)
        pages.append(
            WikiPage(
                kind=kind,
                slug=slug,
                title=name,
                body=body,
                path=store.path_for(kind, slug),
                frontmatter={"title": name},
            )
        )
    pages.append(
        WikiPage(
            kind="syntheses",
            slug="pulse",
            title="Project pulse",
            body=(
                "# Project pulse\n\n"
                "- A new paper landed.\n"
                "- A new concept emerged.\n"
            ),
            path=store.path_for("syntheses", "pulse"),
            frontmatter={
                "synthesis_kind": "pulse",
                "generated_at": "2026-04-27T12:00:00Z",
            },
        )
    )
    for page in pages:
        store.write_page(page)


class _HrefCollector(HTMLParser):
    """Collect every ``<a href>`` outside of the AI siblings footer.

    The footer is a ``<footer class="ai-siblings">`` block whose links are
    page-relative (``foo.txt`` / ``foo.json`` / ``foo.html``) and intentionally
    skipped per the task contract.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: List[str] = []
        self._in_ai_siblings = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        if tag.lower() == "footer":
            for name, value in attrs:
                if name.lower() == "class" and "ai-siblings" in (value or ""):
                    self._in_ai_siblings += 1
                    return
        if self._in_ai_siblings:
            return
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "footer" and self._in_ai_siblings:
            self._in_ai_siblings -= 1


def _collect_hrefs(html: str) -> List[str]:
    parser = _HrefCollector()
    parser.feed(html)
    parser.close()
    return parser.hrefs


_EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-.]*:")
_PROTOCOLLESS_RE = re.compile(r"^//")


def _is_external(href: str) -> bool:
    return bool(_EXTERNAL_RE.match(href) or _PROTOCOLLESS_RE.match(href))


def _resolve(html_path: Path, href: str, site_root: Path) -> Path:
    """Resolve ``href`` (relative to ``html_path``) against ``site_root``."""
    href = href.split("?", 1)[0]
    href = href.split("#", 1)[0]
    if not href:
        return html_path  # in-page anchor
    if href.startswith("/"):
        return (site_root / href.lstrip("/")).resolve()
    base = html_path.parent
    return (base / href).resolve()


def _walk_site_for_broken_links(out: Path) -> List[str]:
    """Walk every emitted HTML file and return any internal hrefs that 404."""
    site_root = out.resolve()
    html_files = sorted(out.rglob("*.html"))
    assert html_files, "expected at least one HTML page"

    broken: List[str] = []
    for html_path in html_files:
        text = html_path.read_text(encoding="utf-8")
        for href in _collect_hrefs(text):
            stripped = href.strip()
            if not stripped:
                continue
            if _is_external(stripped):
                continue
            if stripped.startswith("#"):
                continue
            target_only, _, _frag = stripped.partition("#")
            target_only = target_only.split("?", 1)[0]
            if target_only.endswith(".txt") or target_only.endswith(".json"):
                continue

            resolved = _resolve(html_path, stripped, site_root)
            try:
                resolved.relative_to(site_root)
            except ValueError:
                broken.append(
                    f"{html_path.relative_to(out)} -> {stripped} escapes site root"
                )
                continue
            if not resolved.exists():
                broken.append(
                    f"{html_path.relative_to(out)} -> {stripped} (resolved {resolved.relative_to(site_root)}) missing"
                )
    return broken


def test_every_internal_href_resolves(tmp_path: Path) -> None:
    out = tmp_path / "site"
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _seed_wiki(wiki)
    StaticSiteBuilder(site_title="Demo Wiki").write_site(_toy_graph(), wiki, out)

    broken = _walk_site_for_broken_links(out)
    assert not broken, "broken internal links:\n" + "\n".join(broken)


def test_wiki_corpus_site_has_no_broken_links(
    tmp_path: Path, wiki_sample_graph
) -> None:
    """End-to-end link integrity over the production-style ``wiki_corpus`` fixture.

    Builds the wiki layer with :class:`WikiLayerProjector` (the same projector
    ``project compile`` runs in production), renders the full site, then walks
    every emitted ``.html`` file and asserts every internal href resolves to a
    real file. This catches the "graph nodes named X, wiki pages named Y"
    bug class that motivated the redesign hot-fix.
    """
    from llm_wiki.wiki_projector import WikiLayerProjector

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    store = WikiPageStore(wiki)
    WikiLayerProjector(store).project(wiki_sample_graph)

    out = tmp_path / "site"
    StaticSiteBuilder(site_title="Wiki Corpus").write_site(
        wiki_sample_graph, wiki, out
    )

    broken = _walk_site_for_broken_links(out)
    assert not broken, "broken internal links:\n" + "\n".join(broken)


def test_cross_references_emit_only_valid_internal_hrefs(
    tmp_path: Path, wiki_sample_graph
) -> None:
    """The ``Cross-references in raw data`` section may surface a path that
    has no matching wiki page. Those must be rendered as ``link-broken``
    spans (not anchors), so the link walker never sees a 404.
    """
    from llm_wiki.wiki_projector import WikiLayerProjector

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    store = WikiPageStore(wiki)
    WikiLayerProjector(store).project(wiki_sample_graph)

    out = tmp_path / "site"
    StaticSiteBuilder(site_title="Wiki Corpus").write_site(
        wiki_sample_graph, wiki, out
    )

    # Walk every detail page and confirm any ``cross-refs`` section's anchors
    # all resolve to a real file under the site root.
    site_root = out.resolve()
    issues: list[str] = []
    for html_path in sorted(out.rglob("*.html")):
        text = html_path.read_text(encoding="utf-8")
        match = re.search(
            r'<section id="cross-refs"[^>]*>(.*?)</section>', text, flags=re.DOTALL
        )
        if not match:
            continue
        section_html = match.group(1)
        for href_match in re.finditer(r'<a [^>]*href="([^"]+)"', section_html):
            href = href_match.group(1)
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = (html_path.parent / href.split("#", 1)[0].split("?", 1)[0]).resolve()
            try:
                target.relative_to(site_root)
            except ValueError:
                issues.append(f"{html_path.relative_to(out)} -> {href} escapes site root")
                continue
            if not target.exists():
                issues.append(f"{html_path.relative_to(out)} -> {href} missing")
    assert not issues, "broken cross-ref hrefs:\n" + "\n".join(issues)
