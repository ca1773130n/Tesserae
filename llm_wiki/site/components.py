"""Pure HTML component renderers for the redesigned LLM-Wiki site.

Every function returns an HTML string. None of them touch the filesystem,
none of them mutate state, and they accept simple Python primitives so they
can be unit-tested without a graph or a SiteContext.

Design references:
  - Information architecture: §3 of the redesign design spec
  - Visual design tokens / components: §5.1 / §5.3
  - Page anatomy: §3.3
"""

from __future__ import annotations

import html as _html
import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Left-rail route taxonomy (kept here so ``page_shell`` is self-contained).
# Mirrors §3.1 of the design spec — any HTML route that is part of the rail
# needs an entry here. ``key`` is what callers pass as ``active``.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _RailEntry:
    key: str
    label: str
    href: str  # relative to the site root (no leading ``./``)


_RAIL_TOP: tuple[_RailEntry, ...] = (
    _RailEntry("home", "Home", "index.html"),
    _RailEntry("timeline", "Recent activity", "timeline/index.html"),
)

_RAIL_LIBRARY: tuple[_RailEntry, ...] = (
    _RailEntry("sources", "Sources", "sources/index.html"),
    _RailEntry("concepts", "Concepts", "concepts/index.html"),
    _RailEntry("entities", "Entities", "entities/index.html"),
    _RailEntry("papers", "Papers", "papers/index.html"),
    _RailEntry("repos", "Repos", "repos/index.html"),
    _RailEntry("topics", "Topics", "topics/index.html"),
    _RailEntry("syntheses", "Syntheses", "syntheses/index.html"),
    _RailEntry("questions", "Open questions", "questions/index.html"),
)

_RAIL_TOOLS: tuple[_RailEntry, ...] = (
    _RailEntry("graph", "Graph view", "graph/index.html"),
    _RailEntry("about", "About / schema", "about.html"),
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _esc(value: object) -> str:
    return _html.escape("" if value is None else str(value), quote=True)


def _prefix(depth: int) -> str:
    if depth <= 0:
        return ""
    return "../" * int(depth)


# ---------------------------------------------------------------------------
# breadcrumbs
# ---------------------------------------------------------------------------

def breadcrumbs(items: list[tuple[str, str]]) -> str:
    """Render a breadcrumb trail.

    ``items`` is a list of ``(label, href)`` pairs, **in order**. The last
    entry is treated as the current page: its ``href`` is preserved (so it
    can still be linked) but it picks up the ``crumb-current`` class so the
    style sheet can colour it.
    """
    if not items:
        return '<nav class="breadcrumbs" aria-label="Breadcrumb"></nav>'

    parts: list[str] = []
    last = len(items) - 1
    for idx, (label, href) in enumerate(items):
        is_current = idx == last
        cls = "crumb crumb-current" if is_current else "crumb"
        if href:
            parts.append(
                f'<a class="{cls}" href="{_esc(href)}"'
                + (' aria-current="page"' if is_current else "")
                + f">{_esc(label)}</a>"
            )
        else:
            parts.append(f'<span class="{cls}">{_esc(label)}</span>')
        if not is_current:
            parts.append('<span class="sep" aria-hidden="true">/</span>')
    return '<nav class="breadcrumbs" aria-label="Breadcrumb">' + "".join(parts) + "</nav>"


# ---------------------------------------------------------------------------
# badge / tag chip
# ---------------------------------------------------------------------------

_BADGE_TONES = {"neutral", "warm", "good", "warn"}


def badge(kind_label: str, *, tone: str = "neutral") -> str:
    """Render a single coloured badge. Tone falls back to ``neutral``."""
    tone = tone if tone in _BADGE_TONES else "neutral"
    return f'<span class="badge tone-{tone}">{_esc(kind_label)}</span>'


def tag_chip(label: str, href: str | None = None) -> str:
    """Render a tag chip. With an ``href`` it becomes a link, else a span."""
    if href:
        return f'<a class="tag-chip" href="{_esc(href)}">{_esc(label)}</a>'
    return f'<span class="tag-chip">{_esc(label)}</span>'


# ---------------------------------------------------------------------------
# card
# ---------------------------------------------------------------------------

def card(
    title: str,
    href: str,
    kind_label: str,
    description: str = "",
    footer: str = "",
) -> str:
    """A clickable summary card used on indices and home."""
    desc_html = (
        f'<p class="card-desc">{_esc(description)}</p>' if description else ""
    )
    footer_html = (
        f'<div class="card-footer">{_esc(footer)}</div>' if footer else ""
    )
    return (
        f'<a class="card" href="{_esc(href)}">'
        f'<div class="card-kind">{_esc(kind_label)}</div>'
        f'<span class="card-title">{_esc(title)}</span>'
        f"{desc_html}"
        f"{footer_html}"
        f"</a>"
    )


# ---------------------------------------------------------------------------
# tables / edge lists
# ---------------------------------------------------------------------------

_DEFAULT_NODE_COLUMNS: tuple[str, ...] = ("title", "kind", "mentions", "source")
_COLUMN_LABELS = {
    "title": "Title",
    "kind": "Kind",
    "mentions": "Mentions",
    "source": "Source",
    "year": "Year",
    "tags": "Tags",
}


def node_table(
    rows: list[dict],
    *,
    depth: int = 0,
    columns: tuple[str, ...] = _DEFAULT_NODE_COLUMNS,
) -> str:
    """Render a wiki-layer node table.

    Each row is a plain dict produced by the page renderers (Subagent E).
    Required keys per row:

      - ``title``  — display text for the title cell
      - ``href``   — relative href from site root for the title link
      - ``kind``   — string label for the kind badge (optional)
      - ``mentions`` — int (optional)
      - ``source`` — str (optional)
      - ``tone``   — badge tone for the kind badge (optional)

    ``depth`` rewrites href prefixes so the same dicts can be rendered from
    a leaf detail page two levels deep.
    """
    if not rows:
        return '<p class="muted">No nodes.</p>'

    prefix = _prefix(depth)
    head = "".join(
        f"<th>{_esc(_COLUMN_LABELS.get(col, col.title()))}</th>" for col in columns
    )
    body_rows: list[str] = []
    for row in rows:
        cells: list[str] = []
        for col in columns:
            if col == "title":
                href = prefix + str(row.get("href", "")).lstrip("/")
                cells.append(
                    f'<td><a href="{_esc(href)}">{_esc(row.get("title", ""))}</a></td>'
                )
            elif col == "kind":
                kind_label = row.get("kind", "")
                if kind_label:
                    cells.append(
                        f'<td>{badge(kind_label, tone=row.get("tone", "neutral"))}</td>'
                    )
                else:
                    cells.append("<td></td>")
            elif col == "mentions":
                value = row.get("mentions", "")
                cells.append(f"<td>{_esc(value) if value != '' else ''}</td>")
            elif col == "source":
                source = row.get("source", "")
                if source:
                    cells.append(f"<td><code>{_esc(source)}</code></td>")
                else:
                    cells.append("<td></td>")
            else:
                cells.append(f"<td>{_esc(row.get(col, ''))}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        '<table class="node-table">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )


def edge_list(rows: list[dict], *, depth: int = 0) -> str:
    """Render the ``Outgoing/Incoming`` edge list used on detail pages.

    Each row dict expects ``relation`` (string), ``other_title`` and
    ``other_href`` keys.
    """
    if not rows:
        return '<p class="muted">No edges.</p>'

    prefix = _prefix(depth)
    items: list[str] = []
    for row in rows:
        rel = row.get("relation", "")
        other_title = row.get("other_title", "")
        other_href = prefix + str(row.get("other_href", "")).lstrip("/")
        items.append(
            "<li>"
            f"{badge(rel, tone='neutral')} "
            f'<a href="{_esc(other_href)}">{_esc(other_title)}</a>'
            "</li>"
        )
    return '<ul class="edge-list">' + "".join(items) + "</ul>"


# ---------------------------------------------------------------------------
# SVG widgets
# ---------------------------------------------------------------------------

def sparkline_svg(values: list[int], *, width: int = 120, height: int = 28) -> str:
    """Render a small inline SVG sparkline. Empty data returns a stub.

    The values are scaled into the ``width`` x ``height`` box; the polyline
    floor is at the bottom edge so a flat line sits low on the chart.
    """
    if not values:
        return (
            f'<svg class="sparkline" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-label="No data">'
            f'<title>No data</title></svg>'
        )

    max_v = max(values) or 1
    n = len(values)
    if n == 1:
        # Center the single sample.
        points = [(width / 2, height - (values[0] / max_v) * (height - 4) - 2)]
    else:
        step = (width - 2) / (n - 1)
        points = []
        for idx, value in enumerate(values):
            x = 1 + idx * step
            y = height - (value / max_v) * (height - 4) - 2
            points.append((x, y))

    point_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    area_points = (
        f"1,{height} "
        + " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        + f" {width - 1},{height}"
    )
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="Sparkline of {n} values">'
        f'<polygon class="sparkline-area" points="{area_points}"/>'
        f'<polyline points="{point_str}"/>'
        f"</svg>"
    )


def heatmap_svg(weeks: list[list[int]], *, weeks_back: int = 26) -> str:
    """Render the activity heatmap (GitHub-style 7-row grid).

    ``weeks`` is a list of week-columns; each column is a list of 7 ints
    (Mon..Sun). Cells without a corresponding entry are rendered empty.
    Only the most-recent ``weeks_back`` columns are kept.
    """
    cell = 12
    gap = 2
    cols = (weeks or [])[-weeks_back:]
    width = max(weeks_back, 1) * (cell + gap) + gap
    height = 7 * (cell + gap) + gap

    if not cols:
        return (
            f'<svg class="heatmap" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" '
            f'aria-label="No activity yet"><title>No activity yet</title></svg>'
        )

    flat = [v for col in cols for v in col if v > 0]
    max_v = max(flat) if flat else 1

    def _level(v: int) -> int:
        if v <= 0:
            return 0
        ratio = v / max_v
        if ratio <= 0.25:
            return 1
        if ratio <= 0.5:
            return 2
        if ratio <= 0.75:
            return 3
        return 4

    cells: list[str] = []
    for col_idx, week in enumerate(cols):
        for row_idx in range(7):
            v = week[row_idx] if row_idx < len(week) else 0
            level = _level(v)
            x = gap + col_idx * (cell + gap)
            y = gap + row_idx * (cell + gap)
            cls = f"day l-{level}" if level else "day"
            cells.append(
                f'<rect class="{cls}" x="{x}" y="{y}" width="{cell}" '
                f'height="{cell}" rx="2" ry="2">'
                f"<title>{v} on day {row_idx} (week {col_idx})</title>"
                "</rect>"
            )

    return (
        f'<svg class="heatmap" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="Activity heatmap, last {len(cols)} weeks">'
        + "".join(cells)
        + "</svg>"
    )


# ---------------------------------------------------------------------------
# AI siblings footer (§3.3)
# ---------------------------------------------------------------------------

def ai_siblings_footer(html_path_rel: str) -> str:
    """Render the per-page download footer for the .txt and .json siblings.

    ``html_path_rel`` is the page's path relative to the site root (e.g.
    ``"papers/something.html"``). The footer links to siblings that share
    the same stem with ``.txt`` and ``.json`` extensions and back to the
    original ``.html`` for symmetry.
    """
    base = html_path_rel
    if base.endswith(".html"):
        stem = base[: -len(".html")]
    else:
        stem = base.rsplit(".", 1)[0] if "." in base else base
    txt_href = f"{stem}.txt"
    json_href = f"{stem}.json"
    return (
        '<footer class="ai-siblings" aria-label="AI-readable siblings">'
        "<strong>For AI agents:</strong>"
        f'<a href="{_esc(txt_href)}" download>plain text (.txt)</a>'
        f'<a href="{_esc(json_href)}" download>structured (.json)</a>'
        f'<a href="{_esc(html_path_rel)}">this page (.html)</a>'
        "</footer>"
    )


# ---------------------------------------------------------------------------
# table of contents
# ---------------------------------------------------------------------------

def toc(headings: list[tuple[int, str, str]]) -> str:
    """Render a right-rail table of contents.

    ``headings`` is a list of ``(level, text, anchor)`` tuples, where
    ``level`` is 2/3/4 (the ``<h2>``, ``<h3>``, ``<h4>`` of the article)
    and ``anchor`` is the in-page anchor (without the leading ``#``).
    """
    if not headings:
        return (
            '<aside class="toc" role="doc-toc">'
            '<h2>On this page</h2>'
            '<p class="muted small">No sections.</p>'
            "</aside>"
        )

    items: list[str] = []
    for level, text, anchor in headings:
        lvl = max(2, min(4, int(level)))
        items.append(
            f'<li><a class="toc-l-{lvl}" href="#{_esc(anchor)}">{_esc(text)}</a></li>'
        )
    return (
        '<aside class="toc" role="doc-toc">'
        '<h2>On this page</h2>'
        '<ol>' + "".join(items) + '</ol>'
        '</aside>'
    )


# ---------------------------------------------------------------------------
# page shell — the outermost wrapper
# ---------------------------------------------------------------------------

def _render_rail(
    *,
    active: str,
    counts: Mapping[str, int],
    prefix: str,
) -> str:
    def _section(title: str, entries: Sequence[_RailEntry]) -> str:
        items: list[str] = []
        for entry in entries:
            count = counts.get(entry.key, 0)
            cls = "active" if entry.key == active else ""
            count_html = (
                f'<span class="count">{_esc(count)}</span>' if count else ""
            )
            items.append(
                f'<li><a class="{cls}" href="{_esc(prefix + entry.href)}">'
                f"<span>{_esc(entry.label)}</span>{count_html}</a></li>"
            )
        return (
            f"<h2>{_esc(title)}</h2><ul>" + "".join(items) + "</ul>"
        )

    return (
        '<aside class="rail" aria-label="Site sections">'
        + _section("Overview", _RAIL_TOP)
        + _section("Library", _RAIL_LIBRARY)
        + _section("Tools", _RAIL_TOOLS)
        + "</aside>"
    )


def page_shell(
    title: str,
    *,
    head: str,
    body: str,
    depth: int = 0,
    active: str = "home",
    site_title: str = "LLM-Wiki",
    counts: Mapping[str, int] | None = None,
    toc_html: str = "",
    breadcrumbs_html: str = "",
    ai_siblings_html: str = "",
) -> str:
    """Render the top-level HTML document.

    ``depth`` is the number of directories the rendered page lives below the
    site root: it controls the ``../`` prefix applied to ``assets/style.css``,
    ``assets/app.js`` and rail links.

    ``head`` is injected verbatim **after** the standard meta+stylesheet
    tags so callers can add per-page meta (open graph, canonical link, etc.)
    without losing the defaults.

    ``body`` is the article HTML. ``toc_html`` / ``breadcrumbs_html`` /
    ``ai_siblings_html`` are slot-style optional pieces — pass the output
    of the matching component function or leave empty.
    """
    prefix = _prefix(depth)
    counts = dict(counts or {})
    rail = _render_rail(active=active, counts=counts, prefix=prefix)
    toc_block = (
        f'<aside class="toc-rail">{toc_html}</aside>' if toc_html else '<aside class="toc-rail" hidden></aside>'
    )

    # Top-bar nav mirrors the rail's headline categories so the site is
    # navigable even when the rail is collapsed on mobile.
    nav_links: list[str] = []
    for entry in (
        _RAIL_TOP[0],
        _RAIL_LIBRARY[3],  # Papers
        _RAIL_LIBRARY[1],  # Concepts
        _RAIL_LIBRARY[6],  # Syntheses
        _RAIL_TOOLS[0],    # Graph view
    ):
        cls = "active" if entry.key == active else ""
        nav_links.append(
            f'<a class="{cls}" href="{_esc(prefix + entry.href)}">{_esc(entry.label)}</a>'
        )
    nav_html = "".join(nav_links)

    return (
        "<!doctype html>\n"
        '<html lang="en" data-theme="light">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)} · {_esc(site_title)}</title>\n"
        f'<link rel="stylesheet" href="{_esc(prefix)}assets/style.css">\n'
        f'<script defer src="{_esc(prefix)}assets/app.js"></script>\n'
        f"{head}\n"
        "</head>\n"
        "<body>\n"
        '<header class="topbar">\n'
        f'<a class="brand" href="{_esc(prefix)}index.html">{_esc(site_title)}</a>\n'
        f"<nav aria-label=\"Primary\">{nav_html}</nav>\n"
        '<button class="search-button" data-open-search type="button">Search /</button>\n'
        '<button class="theme-toggle" data-toggle-theme type="button">Theme</button>\n'
        "</header>\n"
        '<div class="shell">\n'
        f"{rail}\n"
        '<main class="main" id="main">\n'
        f"{breadcrumbs_html}\n"
        f"<article>{body}</article>\n"
        f"{ai_siblings_html}\n"
        "</main>\n"
        f"{toc_block}\n"
        "</div>\n"
        '<div class="palette" id="palette" hidden>\n'
        '<div class="palette-box"><input id="search" type="search" '
        'placeholder="Search the wiki…" aria-label="Search"></div>\n'
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )


__all__ = [
    "ai_siblings_footer",
    "badge",
    "breadcrumbs",
    "card",
    "edge_list",
    "heatmap_svg",
    "node_table",
    "page_shell",
    "sparkline_svg",
    "tag_chip",
    "toc",
]
