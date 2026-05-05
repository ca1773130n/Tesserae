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
from datetime import date as _date, timedelta as _timedelta
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
    _RailEntry("graph", "Graph", "graph/index.html"),
    _RailEntry("about", "About / schema", "about.html"),
)


# Top-bar primary nav (Issue 3) — horizontal nav next to the brand. Order
# matches the prior left-rail order: Home, then the library kinds, then
# tools (Graph / About). Active route is highlighted with the accent +
# 2 px bottom border via CSS.
_TOPNAV: tuple[_RailEntry, ...] = (
    _RailEntry("home", "Home", "index.html"),
    _RailEntry("sources", "Sources", "sources/index.html"),
    _RailEntry("concepts", "Concepts", "concepts/index.html"),
    _RailEntry("entities", "Entities", "entities/index.html"),
    _RailEntry("papers", "Papers", "papers/index.html"),
    _RailEntry("repos", "Repos", "repos/index.html"),
    _RailEntry("topics", "Topics", "topics/index.html"),
    _RailEntry("syntheses", "Syntheses", "syntheses/index.html"),
    _RailEntry("questions", "Questions", "questions/index.html"),
    _RailEntry("sessions", "Sessions", "sessions/index.html"),
    _RailEntry("graph", "Graph", "graph/index.html"),
    _RailEntry("about", "About", "about.html"),
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

    # Wrap in ``.table-scroll`` so wide tables get a horizontal scroll
    # affordance on narrow viewports instead of busting the page layout.
    return (
        '<div class="table-scroll">'
        '<table class="node-table">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
        "</div>"
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
            f'width="{width}" height="{height}" aria-hidden="true" focusable="false">'
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
        f'width="{width}" height="{height}" aria-hidden="true" focusable="false">'
        f'<title>Sparkline of {n} values</title>'
        f'<polygon class="sparkline-area" points="{area_points}"/>'
        f'<polyline points="{point_str}"/>'
        f"</svg>"
    )


_MONTH_NAMES: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def heatmap_svg(
    weeks: list[list[int]],
    *,
    weeks_back: int = 26,
    with_labels: bool = True,
    start_date: _date | None = None,
    day_href_prefix: str = "",
    cell_size: int = 12,
) -> str:
    """Render the activity heatmap (GitHub-style 7-row grid).

    ``weeks`` is a list of week-columns; each column is a list of 7 ints
    (Mon..Sun). Cells without a corresponding entry are rendered empty.
    Only the most-recent ``weeks_back`` columns are kept.

    When ``with_labels`` is ``True`` (default) the SVG renders:
      - Month-name labels along the top, one per first-week-of-month
        transition. The first label and any January transition include a
        2-digit year suffix (e.g. ``Nov '25``) so 26-week windows that
        cross a calendar boundary are unambiguous.
      - Weekday labels (Mon, Wed, Fri) on the left edge, mimicking GitHub.

    Pass ``with_labels=False`` for tight contexts (sparkline-sized).

    ``cell_size`` controls the on-canvas size of each day cell (default
    ``12``); the inter-cell gap and viewBox dimensions scale with it so
    callers can request a compact rendering (e.g. ``cell_size=8``) without
    the SVG getting stretched to fit a parent container. When
    ``cell_size <= 8`` the renderer omits the ``style="width:100%"``
    attribute so the SVG sticks to its intrinsic width — the surrounding
    wrapper (``.activity--compact``) caps the upper bound.

    When ``start_date`` is provided the renderer stamps each cell with a
    ``data-day-click="YYYY-MM-DD"`` attribute (computed from the cell's
    column and row offsets), so JS can hook day-level click handlers.
    Each ``<rect>`` is also wrapped in an ``<a xlink:href>`` pointing at
    ``{day_href_prefix}timeline/<YYYY-MM-DD>.html`` so the cell stays
    clickable when JS is off — graceful degradation. Pass
    ``day_href_prefix="../"`` from a depth-1 page (e.g. ``timeline/index.html``)
    or leave empty for site-root pages. ``start_date`` should be the Monday
    of the first week-column.
    """
    cell = max(4, int(cell_size))
    gap = max(1, cell // 6)
    compact = cell <= 8
    cols = (weeks or [])[-weeks_back:]

    # Reserved gutters for labels — scale with cell size so the labels keep
    # roughly their relative breathing room. The grid extents drive the
    # viewBox so a compact ``cell_size`` produces a genuinely smaller SVG.
    left_gutter = max(20, cell * 2 + 4) if with_labels else 0
    top_gutter = max(12, cell + 4) if with_labels else 0
    grid_w = max(weeks_back, 1) * (cell + gap) + gap
    grid_h = 7 * (cell + gap) + gap

    if with_labels:
        view_w = left_gutter + grid_w + 4
        view_h = top_gutter + grid_h + 4
    else:
        view_w, view_h = grid_w, grid_h

    label_attrs = ' class="heatmap-label"'

    fluid_style = '' if compact else 'style="width:100%;height:auto" '

    if not cols:
        empty_labels = ""
        if with_labels:
            # Render the weekday labels even on the empty stub so the layout
            # doesn't visually jump when data first appears.
            empty_labels = _heatmap_weekday_labels(top_gutter, cell, gap)
        return (
            f'<svg class="heatmap" viewBox="0 0 {view_w} {view_h}" '
            f'preserveAspectRatio="xMidYMid meet" '
            f'{fluid_style}'
            f'aria-hidden="true" focusable="false">'
            f'<title>No activity yet</title>'
            f"{empty_labels}"
            f"</svg>"
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

    # Optional date computation. When ``start_date`` is given, ``col_date``
    # for column ``c`` is ``start_date + c*7 days``; row ``r`` adds ``r``
    # days. The caller is responsible for passing a Monday.
    cells: list[str] = []
    month_label_cols: list[tuple[int, str]] = []
    last_month: int | None = None
    last_year: int | None = None
    for col_idx, week in enumerate(cols):
        # Track first-week-of-month transitions for the top labels.
        if start_date is not None and with_labels:
            col_first_day = start_date + _timedelta(days=col_idx * 7)
            if last_month is None or col_first_day.month != last_month:
                name = _MONTH_NAMES[col_first_day.month - 1]
                # Year suffix on:
                #   * the very first month label (so the start year is
                #     unambiguous), AND
                #   * any January transition (so callers know the year
                #     just rolled over).
                # We use the 2-digit ``'YY`` form so the label stays
                # compact next to the cell column.
                add_year = (
                    last_month is None
                    or col_first_day.month == 1
                    or (last_year is not None and col_first_day.year != last_year)
                )
                if add_year:
                    name = f"{name} '{col_first_day.year % 100:02d}"
                month_label_cols.append((col_idx, name))
                last_month = col_first_day.month
                last_year = col_first_day.year
        for row_idx in range(7):
            v = week[row_idx] if row_idx < len(week) else 0
            level = _level(v)
            x = left_gutter + gap + col_idx * (cell + gap)
            y = top_gutter + gap + row_idx * (cell + gap)
            cls = f"day l-{level}" if level else "day"
            day_attr = ""
            day_iso = ""
            if start_date is not None:
                day = start_date + _timedelta(days=col_idx * 7 + row_idx)
                day_iso = day.isoformat()
                day_attr = f' data-day-click="{day_iso}"'
            rect_html = (
                f'<rect class="{cls}" x="{x}" y="{y}" width="{cell}" '
                f'height="{cell}" rx="2" ry="2"{day_attr}>'
                f"<title>{v} on day {row_idx} (week {col_idx})</title>"
                "</rect>"
            )
            if day_iso and v > 0:
                # Wrap the cell in an SVG anchor so plain-HTML clicks work
                # even with JS off. Use ``xlink:href`` for older renderers
                # plus a plain ``href`` for SVG2-aware browsers. Only days
                # with activity get a link — empty cells are visual filler
                # whose target page (timeline/<day>.html) isn't emitted.
                href = f"{day_href_prefix}timeline/{day_iso}.html"
                rect_html = (
                    f'<a xlink:href="{_esc(href)}" href="{_esc(href)}">'
                    f"{rect_html}"
                    "</a>"
                )
            cells.append(rect_html)

    # When no start_date is supplied, fall back to a coarse month label
    # heuristic: divide the columns into ~quarters and label them. This
    # keeps the visual hint useful even for callers that don't track dates.
    if with_labels and not month_label_cols:
        n = len(cols)
        if n > 0:
            # Pick 4 evenly-spaced label positions. Use generic labels so the
            # output is deterministic without a real date to anchor against.
            for idx, name in enumerate(("now-6mo", "now-4mo", "now-2mo", "now")):
                col_idx = min(n - 1, int(idx * n / 4))
                month_label_cols.append((col_idx, name))

    label_svg = ""
    if with_labels:
        # Month labels along the top.
        month_label_svg: list[str] = []
        for col_idx, name in month_label_cols:
            x = left_gutter + gap + col_idx * (cell + gap)
            month_label_svg.append(
                f'<text{label_attrs} x="{x}" y="{top_gutter - 4}">{_esc(name)}</text>'
            )
        # Weekday labels (Mon, Wed, Fri) on the left.
        weekday_svg = _heatmap_weekday_labels(top_gutter, cell, gap)
        label_svg = "".join(month_label_svg) + weekday_svg

    # Declare the xlink namespace whenever we wrapped cells in <a xlink:href>.
    xlink_ns = (
        ' xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        if start_date is not None
        else ""
    )
    return (
        f'<svg class="heatmap"{xlink_ns} viewBox="0 0 {view_w} {view_h}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'{fluid_style}'
        f'aria-hidden="true" focusable="false">'
        f'<title>Activity heatmap, last {len(cols)} weeks</title>'
        + label_svg
        + "".join(cells)
        + "</svg>"
    )


def _heatmap_weekday_labels(top_gutter: int, cell: int, gap: int) -> str:
    """Emit the GitHub-style weekday labels (Mon/Wed/Fri only)."""
    # row indices 0=Mon, 2=Wed, 4=Fri.
    parts: list[str] = []
    for row_idx, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
        # baseline-adjusted y so text sits roughly centred on the row.
        y = top_gutter + gap + row_idx * (cell + gap) + cell - 2
        parts.append(
            f'<text class="heatmap-label" x="0" y="{y}">{name}</text>'
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# AI siblings footer (§3.3)
# ---------------------------------------------------------------------------

def ai_siblings_footer(html_path_rel: str) -> str:
    """Render the per-page download footer for the .txt and .json siblings.

    ``html_path_rel`` is the page's path relative to the site root (e.g.
    ``"papers/something.html"``). The footer is rendered *inside* the page
    itself, so the sibling links must be page-relative (not site-relative):
    ``papers/foo.html`` already lives in the ``papers/`` directory, so its
    ``.txt`` and ``.json`` siblings live next to it as plain ``foo.txt`` /
    ``foo.json`` — not ``papers/foo.txt``. We strip the directory prefix
    here so the footer never doubles the kind segment.
    """
    base = html_path_rel
    if base.endswith(".html"):
        stem = base[: -len(".html")]
    else:
        stem = base.rsplit(".", 1)[0] if "." in base else base
    # Take just the basename — the footer is rendered inside ``stem.html``
    # itself, so siblings sit next to it.
    file_stem = stem.rsplit("/", 1)[-1]
    file_basename_html = file_stem + ".html"
    txt_href = f"{file_stem}.txt"
    json_href = f"{file_stem}.json"
    return (
        '<footer class="ai-siblings" aria-label="AI-readable siblings">'
        "<strong>For AI agents:</strong>"
        f'<a href="{_esc(txt_href)}" download>plain text (.txt)</a>'
        f'<a href="{_esc(json_href)}" download>structured (.json)</a>'
        f'<a href="{_esc(file_basename_html)}">this page (.html)</a>'
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
            '<aside class="toc" role="doc-toc" aria-label="On this page">'
            '<h2>On this page</h2>'
            '<p class="muted small">No sections.</p>'
            "</aside>"
        )

    items: list[str] = []
    for level, text, anchor in headings:
        lvl = max(2, min(4, int(level)))
        # ``data-toc-target`` is what JS_TOC_SCROLLSPY pairs against the
        # heading ``id`` so the active <li> highlight follows the viewport.
        items.append(
            f'<li data-toc-target="{_esc(anchor)}">'
            f'<a class="toc-l-{lvl}" href="#{_esc(anchor)}">{_esc(text)}</a></li>'
        )
    return (
        '<aside class="toc" role="doc-toc" aria-label="On this page">'
        '<h2>On this page</h2>'
        '<ol>' + "".join(items) + '</ol>'
        '</aside>'
    )


# ---------------------------------------------------------------------------
# Doc tree (Issue 3) — Obsidian-style file explorer rendered into the left
# rail. The renderer is recursive: ``node`` is one of two shapes:
#
#   {"name": "data", "children": {<name>: <child node>, ...}, "count": int}
#   {"name": "digest.md", "leaf": True, "path": "data/.../digest.md",
#    "href": "raw/<safe>.html"}
#
# ``current_source_path`` highlights the leaf whose ``path`` matches it.
# Folders use ``<details>``; ``open`` is rendered when ``initially_open``
# is set on the folder dict (``SiteContext.build`` opens the latest
# ``data/research/daily/<latest>/`` chain).
# ---------------------------------------------------------------------------


_DOC_TREE_EXT_PILL: dict[str, str] = {
    ".md": "M",
    ".markdown": "M",
    ".json": "J",
    ".pdf": "P",
}


def _doc_tree_pill(name: str) -> str:
    """Return the small extension pill (``M`` / ``J`` / ``P``) for a leaf.

    Empty string for unknown extensions so the leaf renders without an
    icon (a flush text label) rather than a placeholder.
    """
    lower = name.lower()
    for ext, glyph in _DOC_TREE_EXT_PILL.items():
        if lower.endswith(ext):
            return f'<span class="doc-tree-pill" aria-hidden="true">{glyph}</span>'
    return ""


def doc_tree(
    node: Mapping[str, object],
    *,
    depth: int = 0,
    current_source_path: str = "",
    prefix: str = "",
) -> str:
    """Recursively render a folder/leaf as a collapsible HTML tree.

    ``prefix`` is the ``../`` chain that turns the leaf's site-relative
    ``href`` (``raw/<safe>.html``) into a path relative to the page that
    embeds the tree. Pass ``"../"`` from a depth-1 page, ``""`` from
    site-root pages.

    The renderer caps recursion at 6 levels (per spec) — anything deeper
    gets flattened into a single muted "...more" leaf so a runaway nesting
    can't blow the page up.
    """
    if depth > 6:
        return '<li class="doc-tree-leaf doc-tree-truncated muted">…more</li>'

    if node.get("leaf"):
        name = str(node.get("name") or "")
        path = str(node.get("path") or "")
        href = str(node.get("href") or "")
        is_active = bool(current_source_path) and path == current_source_path
        cls = "doc-tree-leaf"
        if is_active:
            cls += " is-active"
        pill = _doc_tree_pill(name)
        if href:
            link = f'<a href="{_esc(prefix + href.lstrip("/"))}">{pill}<span class="doc-tree-name">{_esc(name)}</span></a>'
        else:
            link = f'<span class="doc-tree-name doc-tree-disabled" title="No raw view available">{pill}{_esc(name)}</span>'
        return f'<li class="{cls}" data-doc-path="{_esc(path)}">{link}</li>'

    # Folder
    name = str(node.get("name") or "")
    children = node.get("children") or {}
    if not isinstance(children, Mapping):
        children = {}
    count = int(node.get("count") or 0)
    open_attr = " open" if node.get("initially_open") else ""
    badge_html = (
        f' <span class="doc-tree-count">[{count}]</span>' if count else ""
    )
    summary = (
        f'<summary class="doc-tree-folder-summary">'
        f'<span class="doc-tree-name">{_esc(name)}</span>'
        f"{badge_html}"
        f"</summary>"
    )
    inner: list[str] = []
    for child_name in sorted(children.keys(), key=_doc_tree_child_sort_key):
        child = children[child_name]
        if isinstance(child, Mapping):
            inner.append(
                doc_tree(
                    child,
                    depth=depth + 1,
                    current_source_path=current_source_path,
                    prefix=prefix,
                )
            )
    body = '<ul class="doc-tree-list">' + "".join(inner) + "</ul>"
    if depth == 0:
        # Root wrapper: no <li>, just a <details> directly under .doc-tree.
        return (
            f'<details class="doc-tree-folder doc-tree-root"{open_attr}>'
            f"{summary}{body}</details>"
        )
    return (
        f'<li class="doc-tree-folder-item">'
        f'<details class="doc-tree-folder"{open_attr}>'
        f"{summary}{body}</details></li>"
    )


_DATE_FOLDER_RE = __import__("re").compile(r"^\d{4}(?:-[A-Za-z0-9]+)+$")


def _doc_tree_child_sort_key(name: str) -> tuple:
    """Sort children alphabetically; sort date-stamped folders descending.

    Date-style folder names (``2026-04-29``, ``2026-W17``) are common in
    the research corpus and the user wants the most-recent week first.
    Detect them with a loose regex and invert the sort order by negating
    the codepoint sum (cheap, deterministic, no datetime parsing).
    """
    if _DATE_FOLDER_RE.match(name):
        # Sort date-like folders before non-date folders, descending by name.
        return (0, _NEG_STR(name))
    return (1, name.lower())


class _NEG_STR:
    """Reverse-string sort key — wraps a string so ``a < b`` iff ``a > b``."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def __lt__(self, other: "_NEG_STR") -> bool:  # type: ignore[override]
        return self._value > other._value

    def __eq__(self, other: object) -> bool:  # type: ignore[override]
        return isinstance(other, _NEG_STR) and self._value == other._value


# ---------------------------------------------------------------------------
# page shell — the outermost wrapper
# ---------------------------------------------------------------------------

def _render_doc_tree_rail(
    *,
    doc_tree_html: str,
    active: str,
    counts: Mapping[str, int],
    prefix: str,
) -> str:
    """Render the left rail with the doc-tree explorer + a search box.

    The rail also includes a "Primary nav" sub-block that is hidden on
    desktop (the topbar already shows the same links) and revealed inside
    the hamburger drawer on mobile so the drawer is self-contained — the
    user can reach every public route without opening the topbar.

    Falls back to an empty placeholder when no tree is supplied (e.g.
    legacy callers that don't yet pass ``doc_tree_html`` from the
    SiteContext). The rail still rides at the same DOM ID (``rail``) so
    the mobile drawer toggle keeps working.
    """
    if not doc_tree_html:
        # Server-rendered empty state so the rail isn't a blank column.
        doc_tree_html = '<p class="muted small">No documents.</p>'
    nav_items: list[str] = []
    for entry in _TOPNAV:
        cls = "active" if entry.key == active else ""
        count = counts.get(entry.key, 0)
        count_html = (
            f' <span class="rail-nav-count">[{_esc(count)}]</span>' if count else ""
        )
        nav_items.append(
            f'<li><a class="{cls}" href="{_esc(prefix + entry.href)}"'
            + (' aria-current="page"' if entry.key == active else "")
            + f"><span>{_esc(entry.label)}</span>{count_html}</a></li>"
        )
    drawer_nav = (
        '<nav class="rail-drawer-nav" aria-label="Primary (mobile)">'
        '<h2 class="rail-section-label">Browse</h2>'
        '<ul class="rail-drawer-nav-list">' + "".join(nav_items) + "</ul>"
        "</nav>"
    )
    return (
        '<aside class="rail" id="rail" aria-label="Document tree">'
        + drawer_nav
        + '<div class="doc-tree-search-row">'
        '<label class="visually-hidden" for="doc-tree-filter">Filter source files</label>'
        '<input class="doc-tree-search" id="doc-tree-filter" type="search" '
        'placeholder="Filter files…" aria-label="Filter source files" '
        'data-doc-tree-search>'
        "</div>"
        '<h2 class="rail-section-label">Files</h2>'
        f'<nav class="doc-tree" aria-label="Source explorer">{doc_tree_html}</nav>'
        "</aside>"
    )


# ---------------------------------------------------------------------------
# bottom nav (mobile-only chrome)
# ---------------------------------------------------------------------------

_BOTTOM_NAV: tuple[tuple[str, str, str, str], ...] = (
    # (key, label, href, icon glyph)
    ("home",      "Home",      "index.html",            "⌂"),  # ⌂
    ("concepts",  "Concepts",  "concepts/index.html",   "◆"),  # ◆
    ("papers",    "Papers",    "papers/index.html",     "¶"),  # ¶
    ("syntheses", "Syntheses", "syntheses/index.html",  "✱"),  # ✱
    ("sessions",  "Sessions",  "sessions/index.html",   "◷"),
    ("graph",     "Graph",     "graph/index.html",      "⁂"),  # ⁂
)


def _render_bottom_nav(*, active: str, prefix: str) -> str:
    items: list[str] = []
    for key, label, href, icon in _BOTTOM_NAV:
        cls = "active" if key == active else ""
        items.append(
            f'<li><a class="{cls}" href="{_esc(prefix + href)}" '
            f'aria-label="{_esc(label)}"'
            + (' aria-current="page"' if key == active else "")
            + ">"
            f'<span class="icon" aria-hidden="true">{_esc(icon)}</span>'
            f'<span class="label">{_esc(label)}</span>'
            "</a></li>"
        )
    return (
        '<nav class="mobile-bottom-nav" aria-label="Quick">'
        '<ul>' + "".join(items) + '</ul>'
        '</nav>'
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
    main_variant: str = "",
    doc_tree_html: str = "",
    rail_html: str | None = None,
    omit_toc: bool = False,
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

    ``main_variant`` toggles modifier classes on the ``<main>`` element:
      - ``"wide"``: index/listing routes that want to fill the desktop
        viewport rather than getting squished into the prose-comfortable
        reading column. The graph route uses this variant too — it gets
        the wide content column the index pages already use, the left
        rail stays present, and the right TOC slot renders a graph
        control panel instead of headings.
      - ``""`` (default): detail pages get the canonical
        ``<article class="article">`` shell with header/body/footer slots
        so every detail kind aligns identically.
    """
    prefix = _prefix(depth)
    counts = dict(counts or {})
    rail = rail_html if rail_html is not None else _render_doc_tree_rail(
        doc_tree_html=doc_tree_html,
        active=active,
        counts=counts,
        prefix=prefix,
    )
    # ``omit_toc`` lets the graph route suppress the right rail entirely
    # (Issue 1 — focused-node info now lives in a floating canvas overlay).
    if omit_toc:
        toc_block = ""
    else:
        toc_block = (
            f'<aside class="toc-rail" id="toc">{toc_html}</aside>' if toc_html else '<aside class="toc-rail" id="toc" hidden></aside>'
        )
    bottom_nav = _render_bottom_nav(active=active, prefix=prefix)
    if main_variant == "graph":
        # No right rail; main consumes the canvas-friendly column width.
        main_class = "main main--graph"
        shell_class = "shell shell--graph"
    elif main_variant == "wide":
        main_class = "main main--wide"
        shell_class = "shell shell--wide"
    else:
        main_class = "main"
        shell_class = "shell"

    # Topbar primary nav (Issue 3): the FULL list of routes — Home, every
    # library kind, Graph, About — left-aligned next to the brand. Counts
    # are stamped in brackets next to the label when non-zero. Active route
    # picks up ``.active`` for the accent + bottom-border treatment.
    nav_links: list[str] = []
    for entry in _TOPNAV:
        count = counts.get(entry.key, 0)
        cls = "active" if entry.key == active else ""
        count_html = (
            f' <span class="topnav-count">[{_esc(count)}]</span>' if count else ""
        )
        nav_links.append(
            f'<a class="{cls}" href="{_esc(prefix + entry.href)}"'
            + (' aria-current="page"' if entry.key == active else "")
            + f"><span>{_esc(entry.label)}</span>{count_html}</a>"
        )
    nav_html = "".join(nav_links)

    # Default detail pages use the canonical ``<article class="article">``
    # shell with explicit header / body / footer slots so every detail kind
    # (sources / concepts / entities / papers / repos / topics / syntheses /
    # questions / raw / timeline-day / about) aligns identically. Index and
    # graph routes opt out — they use the loose layout the rest of the page
    # has historically used.
    toc_toggle_html = (
        '<button class="toc-toggle" aria-controls="toc" aria-expanded="false" '
        'data-toggle-toc type="button">On this page</button>\n'
    ) if not omit_toc else ""
    if main_variant in ("wide", "graph"):
        main_inner = (
            f"{breadcrumbs_html}\n"
            + toc_toggle_html
            + f"<article>{body}</article>\n"
            + f"{ai_siblings_html}\n"
        )
    else:
        # Canonical detail-page article shape. Breadcrumbs live inside the
        # <header> so the first article section starts the same distance
        # below them on every page; ai-siblings live inside the <footer>
        # so they share the article's gutter.
        main_inner = (
            toc_toggle_html
            + '<article class="article">\n'
            + '<header class="article-header">\n'
            + f"{breadcrumbs_html}\n"
            + "</header>\n"
            + f'<div class="article-body">{body}</div>\n'
            + '<footer class="article-footer">\n'
            + f"{ai_siblings_html}\n"
            + "</footer>\n"
            + "</article>\n"
        )

    # Build the <main> aria-label so the graph route reads as
    # "Knowledge graph" while every other page just reads "Main content".
    if main_variant == "graph":
        main_aria = ' aria-label="Knowledge graph"'
    else:
        main_aria = ' aria-label="Main content"'

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
        # Skip-link (a11y): first focusable element on the page so keyboard
        # / screen-reader users can jump past the topnav directly to the
        # main content. Visually hidden until focused (see ``.skip-link``
        # rule in tokens.py).
        '<a class="skip-link" href="#main">Skip to content</a>\n'
        '<header class="topbar" role="banner">\n'
        f'<a class="brand" href="{_esc(prefix)}index.html">{_esc(site_title)}</a>\n'
        f"<nav role=\"navigation\" aria-label=\"Primary\">{nav_html}</nav>\n"
        '<button class="search-button" data-open-search type="button" aria-label="Open search">'
        '<svg class="icon" viewBox="0 0 20 20" width="14" height="14" aria-hidden="true" focusable="false">'
        '<circle cx="9" cy="9" r="6" fill="none" stroke="currentColor" stroke-width="1.6"/>'
        '<line x1="13.5" y1="13.5" x2="17" y2="17" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
        '</svg>'
        '<span class="search-button-label">Search</span>'
        '<kbd class="search-button-kbd" aria-hidden="true">/</kbd>'
        '</button>\n'
        '<button class="theme-toggle" data-toggle-theme type="button" aria-label="Toggle color theme">'
        '<svg class="icon icon-sun" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">'
        '<circle cx="12" cy="12" r="4.2" fill="currentColor"/>'
        '<g stroke="currentColor" stroke-width="1.8" stroke-linecap="round">'
        '<line x1="12" y1="2.5" x2="12" y2="5"/>'
        '<line x1="12" y1="19" x2="12" y2="21.5"/>'
        '<line x1="2.5" y1="12" x2="5" y2="12"/>'
        '<line x1="19" y1="12" x2="21.5" y2="12"/>'
        '<line x1="5.2" y1="5.2" x2="6.9" y2="6.9"/>'
        '<line x1="17.1" y1="17.1" x2="18.8" y2="18.8"/>'
        '<line x1="5.2" y1="18.8" x2="6.9" y2="17.1"/>'
        '<line x1="17.1" y1="6.9" x2="18.8" y2="5.2"/>'
        '</g>'
        '</svg>'
        '<svg class="icon icon-moon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">'
        '<path fill="currentColor" d="M20.5 14.6a8 8 0 0 1-10.9-10.5.6.6 0 0 0-.8-.8 9.5 9.5 0 1 0 12.5 12.1.6.6 0 0 0-.8-.8z"/>'
        '</svg>'
        '</button>\n'
        '<button class="rail-toggle" aria-controls="rail" aria-expanded="false" '
        'aria-label="Toggle navigation drawer" '
        'data-toggle-rail type="button">'
        '<svg class="icon" viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false">'
        '<line x1="3" y1="6" x2="17" y2="6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
        '<line x1="3" y1="10" x2="17" y2="10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
        '<line x1="3" y1="14" x2="17" y2="14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
        '</svg>'
        '<span class="rail-toggle-label">Menu</span>'
        '</button>\n'
        "</header>\n"
        f'<div class="{_esc(shell_class)}">\n'
        f"{rail}\n"
        f'<main class="{_esc(main_class)}" id="main"{main_aria}>\n'
        f"{main_inner}"
        "</main>\n"
        f"{toc_block}\n"
        "</div>\n"
        f"{bottom_nav}\n"
        '<div class="palette" id="palette" hidden role="dialog" aria-modal="true" aria-label="Search palette">\n'
        '<div class="palette-box">'
        '<div class="palette-input-row">'
        '<svg class="palette-input-icon" viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false">'
        '<circle cx="9" cy="9" r="6" fill="none" stroke="currentColor" stroke-width="1.6"/>'
        '<line x1="13.5" y1="13.5" x2="17" y2="17" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
        '</svg>'
        '<label class="visually-hidden" for="search">Search the wiki</label>'
        '<input id="search" type="search" '
        'placeholder="Search the wiki…" aria-label="Search the wiki" '
        'autocomplete="off" spellcheck="false">'
        '<button class="palette-close" type="button" aria-label="Close search" data-close-search>'
        '<svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true" focusable="false">'
        '<line x1="5" y1="5" x2="15" y2="15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
        '<line x1="15" y1="5" x2="5" y2="15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
        '</svg>'
        '</button>'
        '</div>'
        '<div class="visually-hidden" id="palette-live" aria-live="polite" aria-atomic="true"></div>'
        '<footer class="palette-hint" aria-hidden="true">'
        '<span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>'
        '<span><kbd>↵</kbd> open</span>'
        '<span><kbd>tab</kbd> filter type</span>'
        '<span><kbd>esc</kbd> close</span>'
        '</footer>'
        "</div>\n"
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )


__all__ = [
    "ai_siblings_footer",
    "badge",
    "breadcrumbs",
    "card",
    "doc_tree",
    "edge_list",
    "heatmap_svg",
    "node_table",
    "page_shell",
    "sparkline_svg",
    "tag_chip",
    "toc",
]
