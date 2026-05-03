"""Design tokens and stylesheet for the redesigned LLM-Wiki site.

The full CSS string is exposed as ``CSS``. It is consumed by
``StaticSiteBuilder`` (Subagent G) which writes it to ``assets/style.css``.

The tokens mirror §5.1 of ``docs/superpowers/specs/2026-04-27-wiki-frontend-redesign-design.md``
(warm terracotta accent, serif body, system-fallback fonts, dark theme variant).
Layout primitives originally implemented §5.2 (1280×720 reading column);
the polish pass widens the desktop layout so PCs no longer waste 70% of
the viewport — the shell now spans up to 1640 px (1800 px ultra-wide),
with a 1100 px / 75ch prose cap on detail pages and a ``main--wide``
modifier that opens index/listing routes to the full viewport. Mobile
first; the rail unlocks at ``min-width: 768px`` and the TOC at
``min-width: 1024px``.
"""

from __future__ import annotations


CSS: str = r"""
/* ============================================================
   LLM-Wiki — design tokens (§5.1) and component styles (§5.3)
   ============================================================ */

:root {
  --bg: #fafaf7;
  --surface: #ffffff;
  --surface-2: #f3f1ec;
  --ink: #1f1d1a;
  --ink-muted: #5b574f;
  --accent: #b3502b;       /* warm terracotta */
  --accent-soft: #f4d4c2;
  --link: #8a3a18;
  --rule: #e6e2da;
  --code-bg: #f3f1ec;
  --good: #2a6f4f;
  --warn: #c08a1a;
  --danger: #b03b3b;
  --shadow: 0 1px 2px rgba(20, 18, 15, .06);
  --radius: 6px;
  --type-serif: "Source Serif 4", "Iowan Old Style", Georgia, serif;
  --type-sans: "Inter", -apple-system, system-ui, sans-serif;
  --type-mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;
  --space-6: 32px;
  --space-7: 48px;
  --space-8: 64px;
  --rail-w: 280px;
  --toc-w: 260px;
  --read-w: min(1100px, 75ch);
  --page-w: min(100vw - 32px, 1640px);
  --topbar-height: 56px;
}

[data-theme="dark"] {
  --bg: #14130f;
  --surface: #1c1b17;
  --surface-2: #232118;
  --ink: #ece7dc;
  --ink-muted: #a59f90;
  --accent: #e08555;
  --accent-soft: #432215;
  --link: #f0a075;
  --rule: #2c2a23;
  --code-bg: #1f1d18;
  --shadow: 0 1px 2px rgba(0, 0, 0, .5);
}

/* Reset + base
   ------------------------------------------------------------ */
*, *::before, *::after { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--type-serif);
  font-size: 17px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

h1, h2, h3, h4, h5, h6 {
  font-family: var(--type-serif);
  line-height: 1.2;
  margin: 1.6em 0 .6em;
  color: var(--ink);
}
h1 { font-size: clamp(1.9rem, 3.4vw, 2.7rem); margin-top: 0; }
h2 { font-size: 1.55rem; }
h3 { font-size: 1.25rem; }
h4 { font-size: 1.05rem; }

p { margin: 0 0 1em; }
small, .small { font-size: .85rem; }

a { color: var(--link); text-decoration: underline; text-underline-offset: 2px; }
a:hover { color: var(--accent); }

code, pre, kbd, samp {
  font-family: var(--type-mono);
  font-size: .92em;
}
code {
  background: var(--code-bg);
  padding: .08em .35em;
  border-radius: 4px;
}
pre {
  background: var(--code-bg);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  padding: var(--space-4);
  overflow-x: auto;
  font-size: 14px;
  line-height: 1.55;
}
pre code { background: transparent; padding: 0; border-radius: 0; }

hr {
  border: 0;
  border-top: 1px solid var(--rule);
  margin: var(--space-6) 0;
}

.muted { color: var(--ink-muted); }
.eyebrow {
  font-family: var(--type-sans);
  text-transform: uppercase;
  letter-spacing: .12em;
  font-size: .72rem;
  font-weight: 600;
  color: var(--ink-muted);
}

/* Top nav
   ------------------------------------------------------------ */
.topbar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-5);
  background: color-mix(in srgb, var(--bg) 88%, transparent);
  border-bottom: 1px solid var(--rule);
  backdrop-filter: blur(8px);
  font-family: var(--type-sans);
}
.topbar .brand {
  font-weight: 700;
  text-decoration: none;
  color: var(--ink);
  font-family: var(--type-serif);
  font-size: 1.1rem;
}
/* Primary nav (Issue 3): horizontal list of every public route, left-
   aligned next to the brand. The active route picks up the accent color
   plus a 2 px bottom border. Counts render in brackets next to the
   label. The mobile drawer takes over below 768 px (handled in MOBILE_CSS). */
.topbar nav {
  display: flex;
  gap: 2px;
  flex: 1;
  flex-wrap: wrap;
  align-items: stretch;
  margin-inline-start: var(--space-3);
}
.topbar nav a {
  color: var(--ink-muted);
  text-decoration: none;
  font-size: .9rem;
  padding: 6px 10px;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: color 140ms ease, border-color 140ms ease, background 140ms ease;
}
.topbar nav a .topnav-count {
  color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
  font-size: .78rem;
  opacity: .75;
}
.topbar nav a.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
.topbar nav a.active .topnav-count { color: var(--accent); opacity: .85; }
.topbar nav a:hover { color: var(--ink); background: var(--surface-2); }
.topbar nav a:hover.active { color: var(--accent); }
.topbar .search-button,
.topbar .theme-toggle {
  font-family: var(--type-sans);
  font-size: .88rem;
  padding: 6px 10px;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
  cursor: pointer;
}
.topbar .search-button:hover,
.topbar .theme-toggle:hover { border-color: var(--accent); color: var(--accent); }

/* Layout grid (§5.2)
   ------------------------------------------------------------ */
/* CRITICAL: ``position: sticky`` (TOC + rail) breaks the moment any
   ancestor declares ``overflow: hidden``, ``overflow: scroll``,
   ``overflow: auto`` *or* ``overflow-x: clip``. We previously set
   ``html, body { overflow-x: clip }`` which silently disabled sticky on
   every long article. Don't do that — clip horizontal overflow on the
   specific elements that actually need it (e.g. ``pre``, tables, the
   table-scroll wrapper) instead.

   Sticky also needs the parent (the grid row) to be taller than the
   sticky element. ``align-items: start`` on the shell grid prevents
   ``stretch`` from forcing every column to the same height — without
   ``start`` the grid pulls the TOC column to span the full main column
   and there's nothing for sticky to slide against. */
.shell {
  max-width: var(--page-w);
  margin: 0 auto;
  display: grid;
  grid-template-columns: 1fr;
  align-items: start;
  gap: var(--space-5);
  padding: var(--space-5) clamp(12px, 4vw, 24px);
  /* sticky position requires no overflow:hidden on ancestors. */
  overflow: visible;
}
/* Left rail (Issue 3): the rail is now an Obsidian-style file explorer
   wrapped around ``.doc-tree``. It still rides at the same DOM ID
   (``rail``) so the mobile drawer toggle keeps working. Padding is
   trimmed because the doc tree owns its own internal density. */
.rail {
  display: none; /* mobile: hidden by default */
  font-family: var(--type-sans);
  font-size: .92rem;
}

/* ---- Doc-tree explorer (Issue 3) ---------------------------------------- */
.doc-tree-search-row { padding: 0 var(--space-2) var(--space-3); }
.doc-tree-search {
  width: 100%;
  padding: 6px 10px;
  font-family: var(--type-mono);
  font-size: 12px;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
  transition: border-color 140ms ease, box-shadow 140ms ease;
}
.doc-tree-search:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.doc-tree {
  display: block;
  font-family: var(--type-mono);
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink);
}
.doc-tree-list {
  list-style: none;
  margin: 0;
  padding-inline-start: 12px;
}
.doc-tree-folder {
  margin: 0;
}
.doc-tree-folder-summary {
  cursor: pointer;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border-radius: 3px;
  user-select: none;
  color: var(--ink-muted);
}
.doc-tree-folder-summary::-webkit-details-marker { display: none; }
.doc-tree-folder-summary::before {
  content: "▸";
  font-size: 10px;
  width: 10px;
  display: inline-block;
  color: var(--ink-muted);
  transition: transform 120ms ease;
}
details[open] > .doc-tree-folder-summary::before { transform: rotate(90deg); }
.doc-tree-folder-summary:hover { background: var(--surface-2); color: var(--ink); }
.doc-tree-folder-summary .doc-tree-name { color: var(--ink); }
.doc-tree-count {
  color: var(--ink-muted);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.doc-tree-folder-item { margin: 0; }
.doc-tree-leaf {
  margin: 0;
  list-style: none;
  position: relative;
}
.doc-tree-leaf > a,
.doc-tree-leaf > .doc-tree-name {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px 3px 16px;
  border-left: 2px solid transparent;
  text-decoration: none;
  color: var(--ink);
  border-radius: 0 3px 3px 0;
  word-break: break-all;
}
.doc-tree-leaf > a:hover { background: var(--surface-2); color: var(--ink); }
.doc-tree-leaf.is-active > a {
  background: var(--accent-soft);
  color: var(--accent);
  border-left-color: var(--accent);
}
.doc-tree-leaf[hidden] { display: none; }
.doc-tree-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  font-size: 9px;
  font-weight: 700;
  font-family: var(--type-sans);
  color: var(--ink-muted);
  background: var(--surface-2);
  border: 1px solid var(--rule);
  border-radius: 2px;
  flex-shrink: 0;
  letter-spacing: 0;
}
.doc-tree-disabled { color: var(--ink-muted); opacity: .65; }
.doc-tree-truncated { padding: 3px 6px 3px 16px; font-style: italic; }
.doc-tree-folder.doc-tree-root > .doc-tree-folder-summary {
  font-weight: 600;
  color: var(--ink);
  text-transform: none;
  letter-spacing: 0;
}
.rail-section-label {
  font-family: var(--type-sans);
  font-size: .68rem;
  font-weight: 700;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--ink-muted);
  margin: var(--space-4) var(--space-2) var(--space-2);
}
.rail-section-label:first-child { margin-top: 0; }
.rail-drawer-nav {
  display: none; /* hidden on desktop; topbar covers this */
}
.rail-drawer-nav-list {
  list-style: none;
  padding: 0;
  margin: 0 0 var(--space-3);
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-family: var(--type-sans);
  font-size: .92rem;
}
.rail-drawer-nav-list a {
  display: flex;
  justify-content: space-between;
  align-items: center;
  text-decoration: none;
  color: var(--ink);
  padding: 8px 10px;
  border-radius: 4px;
}
.rail-drawer-nav-list a.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.rail-drawer-nav-list a:hover { background: var(--surface-2); }
.rail-drawer-nav-list .rail-nav-count {
  color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
  font-size: .8rem;
}

.main {
  min-width: 0;
  max-width: var(--read-w);
  margin: 0 auto;
  width: 100%;
}
.toc-rail {
  display: none;
  font-family: var(--type-sans);
  font-size: .88rem;
}

/* Mobile rail drawer */
.rail-toggle {
  display: inline-flex;
  align-items: center;
  font-family: var(--type-sans);
  font-size: .88rem;
  padding: 6px 10px;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
  cursor: pointer;
}

@media (min-width: 768px) {
  .shell {
    grid-template-columns: var(--rail-w) 1fr;
    padding: var(--space-6) var(--space-5);
  }
  .rail {
    display: block;
    position: sticky;
    top: calc(var(--topbar-height, 56px) + 16px);
    align-self: start;
    max-height: calc(100vh - var(--topbar-height, 56px) - 32px);
    overflow-y: auto;
  }
  .rail-toggle { display: none; }
}

@media (min-width: 1024px) {
  .shell {
    grid-template-columns: var(--rail-w) minmax(0, 1fr) var(--toc-w);
  }
  /* Issue 1 — the graph route drops the right rail entirely so the
     canvas can extend the full content column width. ``page_shell``
     emits ``shell--graph`` + ``main--graph`` for that route; the right
     TOC ``aside`` is omitted from the markup so the grid simply has
     two columns instead of three. */
  .shell--graph {
    grid-template-columns: var(--rail-w) minmax(0, 1fr);
  }
  /* The wrapper takes its own height (``align-self: start``) so its
     child ``aside.toc`` has somewhere to slide against. We do NOT make
     the wrapper itself sticky — the *inner* aside.toc is the sticky
     element, which means a long TOC can scroll internally without
     dragging the wrapper around. */
  .toc-rail {
    display: block;
    align-self: start;
  }
  /* The inner ``aside.toc`` (rendered by ``components.toc`` and by the
     graph control panel) sticks so the rail follows long article scrolls.
     ``top`` clears the sticky topbar; ``max-height`` bounds the rail to
     the visible viewport so an oversized TOC scrolls internally instead
     of overflowing the page. */
  .toc-rail .toc,
  aside.toc {
    position: sticky;
    top: calc(var(--topbar-height, 56px) + 16px);
    align-self: start;
    max-height: calc(100vh - var(--topbar-height, 56px) - 32px);
    overflow-y: auto;
  }
}

/* Breadcrumbs (§3.3)
   ------------------------------------------------------------ */
.breadcrumbs {
  font-family: var(--type-sans);
  font-size: .85rem;
  color: var(--ink-muted);
  margin: 0 0 var(--space-3);
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.breadcrumbs a { color: var(--ink-muted); text-decoration: none; }
.breadcrumbs a:hover { color: var(--accent); text-decoration: underline; }
.breadcrumbs .sep { color: var(--rule); }
.breadcrumbs .crumb-current { color: var(--ink); }

/* Cards (§5.3)
   ------------------------------------------------------------ */
.card {
  display: block;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  padding: var(--space-4);
  background: var(--surface);
  text-decoration: none;
  color: var(--ink);
  box-shadow: var(--shadow);
  transition: transform .12s ease, border-color .12s ease;
}
.card:hover {
  transform: translateY(-1px);
  border-color: var(--accent);
}
.card .card-kind {
  font-family: var(--type-sans);
  font-size: .72rem;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--ink-muted);
  margin-bottom: var(--space-2);
}
.card .card-title {
  font-weight: 600;
  font-family: var(--type-serif);
  font-size: 1.05rem;
  display: block;
  margin-bottom: var(--space-2);
}
.card .card-desc {
  color: var(--ink-muted);
  font-size: .94rem;
  margin: 0 0 var(--space-2);
}
.card .card-footer {
  font-family: var(--type-sans);
  font-size: .82rem;
  color: var(--ink-muted);
  margin-top: var(--space-2);
}

.card-grid {
  display: grid;
  gap: var(--space-3);
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
}

/* Badges (§5.3)
   ------------------------------------------------------------ */
.badge {
  display: inline-block;
  font-family: var(--type-sans);
  font-size: .72rem;
  font-weight: 600;
  letter-spacing: .04em;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--rule);
  background: var(--surface-2);
  color: var(--ink-muted);
  white-space: nowrap;
}
.badge.tone-warm { background: var(--accent-soft); color: var(--accent); border-color: transparent; }
.badge.tone-good { background: color-mix(in srgb, var(--good) 15%, transparent); color: var(--good); border-color: transparent; }
.badge.tone-warn { background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); border-color: transparent; }
.badge.tone-neutral { /* default */ }

/* Tag chips
   ------------------------------------------------------------ */
.tag-chip {
  display: inline-flex;
  align-items: center;
  font-family: var(--type-sans);
  font-size: .78rem;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--rule);
  background: transparent;
  color: var(--ink-muted);
  text-decoration: none;
  margin-right: 4px;
}
.tag-chip:hover { color: var(--accent); border-color: var(--accent); }

/* Subtype chips (index page filter strip)
   ------------------------------------------------------------ */
.subtype-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: var(--space-3) 0 var(--space-4);
  padding: 0;
  font-family: var(--type-sans);
}
.subtype-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--type-sans);
  font-size: .82rem;
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid var(--rule);
  background: transparent;
  color: var(--ink-muted);
  cursor: pointer;
  line-height: 1.2;
  transition: background-color .12s ease, color .12s ease, border-color .12s ease;
}
.subtype-chip:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.subtype-chip .chip-count {
  font-variant-numeric: tabular-nums;
  font-size: .72rem;
  color: var(--ink-muted);
  background: var(--surface-2);
  padding: 1px 6px;
  border-radius: 999px;
}
.subtype-chip.is-active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}
.subtype-chip.is-active .chip-count {
  background: rgba(255, 255, 255, .18);
  color: #fff;
}

/* Index listing table (sortable + filterable)
   ------------------------------------------------------------ */
.index-listing {
  margin-top: var(--space-2);
}
.index-table .badge {
  font-size: .7rem;
}

/* Raw document viewer
   ------------------------------------------------------------ */
.raw-page {
  margin-top: var(--space-3);
}
.raw-page .raw-markdown {
  font-family: var(--type-serif);
}
.raw-meta {
  font-family: var(--type-sans);
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--ink-muted);
  font-size: .72rem;
}
.raw-path {
  font-size: .85rem;
  color: var(--ink-muted);
  word-break: break-all;
}
.raw-text {
  background: var(--code-bg);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  padding: 12px 16px;
  overflow-x: auto;
  font-family: var(--type-mono);
  font-size: .82rem;
  line-height: 1.5;
}
.raw-asset {
  margin: var(--space-3) 0;
}
.raw-image img {
  display: block;
  max-width: 100%;
  height: auto;
  border-radius: var(--radius);
  border: 1px solid var(--rule);
}
.raw-pdf embed {
  width: 100%;
  height: 900px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
}
.raw-html-frame {
  width: 100%;
  height: 720px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--surface);
}
.raw-download {
  padding: var(--space-3);
  border: 1px dashed var(--rule);
  border-radius: var(--radius);
  background: var(--surface-2);
}

/* Tables
   ------------------------------------------------------------ */
.node-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--type-sans);
  font-size: .92rem;
  margin: var(--space-3) 0;
}
.node-table th, .node-table td {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule);
  vertical-align: top;
}
.node-table th {
  font-weight: 600;
  font-size: .78rem;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--ink-muted);
}
.node-table tbody tr:nth-child(even) { background: var(--surface-2); }
.node-table tbody tr:hover { background: var(--accent-soft); }
.node-table td a { text-decoration: none; color: var(--ink); }
.node-table td a:hover { color: var(--accent); text-decoration: underline; }
.node-table td code { font-size: .82rem; }

/* Edge list
   ------------------------------------------------------------ */
.edge-list {
  list-style: none;
  padding: 0;
  margin: var(--space-2) 0;
  font-family: var(--type-sans);
}
.edge-list li {
  padding: 6px 0;
  border-bottom: 1px solid var(--rule);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: .92rem;
}
.edge-list li:last-child { border-bottom: 0; }

/* TOC (§3.3 right rail)
   ------------------------------------------------------------ */
.toc {
  font-family: var(--type-sans);
}
.toc h2 {
  font-family: var(--type-sans);
  font-size: .72rem;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--ink-muted);
  margin: 0 0 var(--space-2);
}
.toc ol { list-style: none; padding: 0; margin: 0; }
.toc li { margin: 0; }
.toc a {
  display: block;
  padding: 3px 0;
  color: var(--ink-muted);
  text-decoration: none;
  font-size: .88rem;
  line-height: 1.3;
  border-left: 2px solid transparent;
  padding-left: 8px;
}
.toc a:hover, .toc a.active {
  color: var(--accent);
  border-left-color: var(--accent);
}
.toc .toc-l-2 { padding-left: 8px; }
.toc .toc-l-3 { padding-left: 20px; }
.toc .toc-l-4 { padding-left: 32px; }

/* TOC scrollspy: the currently-visible section's <li> picks up
   ``is-active`` from JS_TOC_SCROLLSPY. The accent border + colour shift
   make the active item visually pop while scrolling. */
.toc li.is-active > a {
  color: var(--accent);
  border-left: 2px solid var(--accent);
  padding-left: 8px;
  margin-left: -10px;
}

/* Canonical article shell (every detail page).
   ------------------------------------------------------------
   Header / body / footer slots keep alignment byte-stable across
   sources / concepts / entities / papers / repos / topics / syntheses /
   questions / raw / timeline-day / about — the rail/TOC gutter and the
   first-section vertical rhythm are owned here, not by the renderers. */
.article {
  display: block;
  max-width: 100%;
}
.article-header {
  margin: 0 0 var(--space-4);
}
.article-header .breadcrumbs {
  margin-bottom: 0;
}
.article-body {
  display: block;
}
.article-body > :first-child {
  margin-top: 0;
}
.article-body > section + section,
.article-body > section + footer {
  margin-top: var(--space-6);
}
.article-footer {
  margin-top: var(--space-6);
}

/* Sparkline
   ------------------------------------------------------------ */
.sparkline {
  display: inline-block;
  vertical-align: middle;
  overflow: visible;
}
.sparkline polyline {
  fill: none;
  stroke: var(--accent);
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.sparkline .sparkline-area {
  fill: var(--accent-soft);
  stroke: none;
  opacity: .6;
}

/* Heatmap (activity)
   ------------------------------------------------------------ */
.heatmap {
  display: block;
  width: 100%;
  height: auto;
  font-family: var(--type-sans);
}
.heatmap rect.day {
  fill: var(--surface-2);
  stroke: var(--bg);
  stroke-width: 1;
}
.heatmap rect.day.l-1 { fill: color-mix(in srgb, var(--accent) 20%, var(--surface-2)); }
.heatmap rect.day.l-2 { fill: color-mix(in srgb, var(--accent) 45%, var(--surface-2)); }
.heatmap rect.day.l-3 { fill: color-mix(in srgb, var(--accent) 70%, var(--surface-2)); }
.heatmap rect.day.l-4 { fill: var(--accent); }

/* AI siblings footer
   ------------------------------------------------------------ */
.ai-siblings {
  margin-top: var(--space-7);
  padding: var(--space-4);
  border-top: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--surface-2);
  font-family: var(--type-sans);
  font-size: .88rem;
  color: var(--ink-muted);
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
}
.ai-siblings strong {
  font-weight: 600;
  color: var(--ink);
}
.ai-siblings a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--accent);
  text-decoration: none;
  border: 1px solid var(--rule);
  background: var(--surface);
  border-radius: 4px;
  padding: 4px 10px;
}
.ai-siblings a:hover { border-color: var(--accent); }

/* Command palette (carry-forward; finalised by Subagent E's js.py)
   ------------------------------------------------------------ */
.palette {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, .35);
  z-index: 50;
  padding: 10vh 16px;
}
.palette[hidden] { display: none; }
.palette-box {
  max-width: 720px;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  box-shadow: 0 14px 60px rgba(0, 0, 0, .25);
  overflow: hidden;
}
.palette-box input {
  width: 100%;
  border: 0;
  border-bottom: 1px solid var(--rule);
  padding: var(--space-4);
  font-size: 1rem;
  background: var(--surface);
  color: var(--ink);
}
.palette-box input:focus { outline: 2px solid var(--accent); outline-offset: -2px; }

/* Reduced motion
   ------------------------------------------------------------ */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0s !important;
    transition-duration: 0s !important;
  }
}

/* Print
   ------------------------------------------------------------ */
@media print {
  .topbar, .rail, .toc-rail, .palette, .ai-siblings { display: none !important; }
  .shell { grid-template-columns: 1fr; padding: 0; }
  body { background: white; color: black; }
}

/* Graph view (§5.3 — interactive 3D force layout)
   ------------------------------------------------------------ */
.graph-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  margin-top: var(--space-4);
}
.graph-page .graph-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
  justify-content: flex-start;
}
.graph-page .graph-toolbar-group {
  display: inline-flex;
  gap: var(--space-2);
  padding: 2px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--surface);
}
.graph-page .graph-toolbar .button {
  display: inline-flex;
  align-items: center;
  padding: 6px 12px;
  font-family: var(--type-mono);
  font-size: 0.82rem;
  letter-spacing: 0.02em;
  border: 1px solid transparent;
  border-radius: calc(var(--radius) - 2px);
  background: transparent;
  color: var(--ink-muted);
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease, border-color 160ms ease;
}
.graph-page .graph-toolbar .button:hover {
  color: var(--ink);
  background: var(--surface-2);
}
.graph-page .graph-toolbar .button.is-active,
.graph-page .graph-toolbar .button[aria-pressed="true"] {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: var(--accent);
}
.graph-page .graph-search {
  flex: 1 1 240px;
  display: flex;
  justify-content: flex-end;
}
.graph-page .graph-search input {
  width: 240px;
  max-width: 100%;
  padding: 6px 12px;
  font-family: var(--type-mono);
  font-size: 0.85rem;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--ink);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.graph-page .graph-search input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.graph-page .graph-canvas {
  position: relative;
  /* Canvas spans the wide-content column; never full-bleed. Height
     scales with the viewport but stays bounded so the toolbar above
     and the page below remain reachable without scrolling.
     Desktop sizing: clamp(560px, 70vh, 880px). Mobile drops to a
     smaller clamp via the ``@media (max-width: 1023px)`` block below. */
  width: 100%;
  height: clamp(560px, 70vh, 880px);
  border-radius: var(--radius);
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 18%, rgba(96, 165, 250, 0.16), transparent 28%),
    radial-gradient(circle at 82% 24%, rgba(167, 139, 250, 0.13), transparent 30%),
    linear-gradient(135deg, #020617 0%, #0f172a 52%, #111827 100%);
  border: 1px solid rgba(148, 163, 184, 0.26);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.035), 0 24px 70px rgba(2, 6, 23, 0.28);
}
@media (max-width: 1023px) {
  .graph-page .graph-canvas {
    height: clamp(420px, 60vh, 640px);
  }
}
.graph-page .graph-canvas canvas { display: block; width: 100% !important; height: 100% !important; }
/* (Removed Issue 2) The bottom-right floating focused-node info panel
   is gone — it caused the page to blink on every click and gave the
   user a redundant view of what the focused-node label sprite already
   showed inline. The cursor-following tooltip below replaces it for
   hover preview; the focused-node label sprite shows focus details
   directly on the canvas. */
/* Fullscreen mode (Issue 4). The wrapper covers the viewport; the
   toolbar sits on top, the legend bottom-left, the rail-style info panel
   is repositioned to the right inside the fullscreen container. */
.graph-canvas-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  position: relative;
}
.graph-canvas-wrapper.is-fullscreen {
  width: 100vw;
  height: 100vh;
  background: var(--bg);
  padding: var(--space-3);
  gap: var(--space-2);
}
.graph-canvas-wrapper.is-fullscreen .graph-toolbar {
  position: absolute;
  top: var(--space-3);
  left: var(--space-3);
  right: var(--space-3);
  z-index: 12;
  background: var(--surface);
  padding: 6px 10px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.graph-canvas-wrapper.is-fullscreen .graph-canvas {
  flex: 1 1 auto;
  height: 100%;
  width: 100%;
  border-radius: 0;
}
.graph-canvas-wrapper.is-fullscreen .graph-legend {
  position: absolute;
  left: var(--space-3);
  bottom: var(--space-3);
  z-index: 12;
  background: var(--surface);
  padding: 6px 10px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
}
/* Cursor-following tooltip (Issue 2). Lives inside ``.graph-canvas-wrapper``
   so the Fullscreen API still draws it on top of the canvas. JS sets
   ``style.left`` / ``style.top`` per ``mousemove`` and toggles the ``hidden``
   attribute on hover-in / hover-out — no display:none thrashing on every
   interaction (which is what made the previous bottom-right panel blink).
   Dark theme by default; light-theme override below flips the surface. */
.graph-tooltip {
  position: absolute;
  pointer-events: none;
  background: rgba(20,20,20,0.78);
  color: #fff;
  -webkit-backdrop-filter: blur(6px);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 6px;
  padding: 10px 14px;
  max-width: 320px;
  font-size: 13px;
  line-height: 1.4;
  z-index: 50;
  transition: opacity 100ms ease;
}
.graph-tooltip[hidden] { display: none; }
.graph-tooltip strong {
  display: block;
  font-size: 14px;
  margin-bottom: 4px;
}
.graph-tooltip .graph-tooltip-meta {
  font-family: var(--type-mono);
  font-size: 11px;
  opacity: 0.78;
  margin-bottom: 4px;
}
.graph-tooltip .graph-tooltip-desc {
  font-family: var(--type-serif);
  font-size: 12px;
  opacity: 0.86;
}
.graph-tooltip .graph-tooltip-hint {
  display: block;
  margin-top: 6px;
  font-family: var(--type-mono);
  font-size: 10px;
  opacity: 0.6;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
[data-theme="light"] .graph-tooltip {
  background: rgba(255,255,255,0.92);
  color: var(--ink);
  border-color: var(--rule);
}
.graph-page .graph-error-banner {
  position: absolute;
  top: var(--space-4);
  left: var(--space-4);
  right: var(--space-4);
  padding: 10px 14px;
  font-family: var(--type-mono);
  font-size: 0.82rem;
  color: var(--danger);
  background: var(--surface);
  border: 1px solid var(--danger);
  border-radius: var(--radius);
  display: none;
  z-index: 7;
}
.graph-page .graph-error-banner.is-visible { display: block; }
.graph-page .graph-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}
.graph-page .graph-legend-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-family: var(--type-mono);
  font-size: 0.78rem;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: 999px;
  cursor: pointer;
  transition: background 160ms ease, color 160ms ease, border-color 160ms ease, opacity 160ms ease;
}
.graph-page .graph-legend-chip:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.graph-page .graph-legend-chip.is-off {
  opacity: 0.4;
  background: var(--surface-2);
}
.graph-page .graph-legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  display: inline-block;
}
.graph-page .graph-legend-label { letter-spacing: 0.02em; }
.graph-page .graph-legend-count {
  color: var(--ink-muted);
  margin-left: 2px;
}
.graph-page .graph-help {
  font-family: var(--type-mono);
  font-size: 0.78rem;
  margin: 0;
}
/* Toolbar size legend — explains the radius mapping. */
.graph-page .graph-size-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-family: var(--type-mono);
  font-size: 0.74rem;
  color: var(--ink-muted);
  background: var(--surface-2);
  border: 1px solid var(--rule);
  border-radius: 999px;
  white-space: nowrap;
}
/* (Removed) Right-rail graph control panel — the graph route no longer
   ships a right rail (Issue 1). Hover preview lives in the cursor
   tooltip above; focused-node display lives in the focused label
   sprite. */
.graph-page .visually-hidden {
  position: absolute;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* Stat row (home hero §5.3)
   ------------------------------------------------------------ */
.stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: var(--space-4) 0 var(--space-6);
  padding: 0;
  list-style: none;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px 20px;
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.stat b,
.stat .stat-value {
  font-family: var(--type-serif);
  font-weight: 600;
  font-size: clamp(28px, 3vw, 44px);
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.stat span,
.stat .stat-label {
  font-family: var(--type-sans);
  font-size: 13px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink-muted);
}

/* Button labels (consistent padding, hit area, icon+label gap)
   ------------------------------------------------------------ */
.button,
button:not([class*="graph-legend"]):not(.tag-chip),
a.button,
.theme-toggle,
.search-button,
.rail-toggle,
.toc-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 16px;
  letter-spacing: 0.02em;
  min-block-size: 36px;
  font-family: var(--type-sans);
  font-size: 0.92rem;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--ink);
  text-decoration: none;
  cursor: pointer;
  transition: border-color 160ms ease, color 160ms ease, background 160ms ease;
}
.button:hover,
button:not([class*="graph-legend"]):not(.tag-chip):hover,
a.button:hover,
.theme-toggle:hover,
.search-button:hover,
.rail-toggle:hover,
.toc-toggle:hover {
  border-color: var(--accent);
  color: var(--accent);
}

/* Panel / section spacing on detail pages
   ------------------------------------------------------------ */
section.panel,
.panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
  margin-block: 28px;
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
section.panel > h2,
section.panel > h3,
.panel > h2,
.panel > h3 {
  margin: 0;
}

/* Table scroll wrapper — keeps wide tables from busting mobile layout. */
.table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  margin: var(--space-3) 0;
}
.table-scroll > table { margin: 0; }

/* Auto-fill card grid (self-tunes 1/2/3/4-up by viewport).
   Used by index pages on top of the existing ``.card-grid``. */
.card-grid {
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
}

/* Card-row tap targets — every <a> inside a card or table row gets a
   reasonable hit area on touch devices. */
.card a,
.node-table tbody tr a {
  min-block-size: 44px;
  display: inline-flex;
  align-items: center;
}

/* Heatmap label glyphs (month names along the top, weekday names on left). */
.heatmap text.heatmap-label {
  font-family: var(--type-sans);
  font-size: 10px;
  fill: var(--ink-muted);
}

/* Auto-linker — subtle dotted underline so auto-generated links read as
   informative without dominating the page (Issue 3). Authored ``<a>``
   tags keep the heavier underline-offset treatment. */
.auto-link {
  border-bottom: 1px dotted var(--ink-muted);
  text-decoration: none;
  color: var(--ink);
}
.auto-link:hover,
.auto-link:focus {
  border-bottom-color: var(--accent);
  color: var(--accent);
}

/* Markdown-rendered tables get a horizontal scroll affordance on narrow
   viewports without needing the renderer to wrap each one. */
.markdown-body table {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  border-collapse: collapse;
}
"""


# ============================================================================
# Mobile overrides (§5.2 mobile + touch hit area + drawer rail)
# ----------------------------------------------------------------------------
# Layered as ``@media`` overrides on top of the desktop-first rules above so
# nothing existing has to change. Breakpoints:
#   < 480px         phone (single column, drawer rail, 2-up stat row)
#   480-767px       large phone (drawer rail, 4-up stats)
#   768-1023px      tablet (rail static; TOC drawer)
#   >= 1024px       desktop (existing layout, untouched)
# ============================================================================

MOBILE_CSS: str = r"""
/* ============================================================
   Mobile UX overrides — drawer rail, bottom nav, fluid type
   ============================================================ */

/* 1. Fluid body type — 17 px on phones, 16 px on tablets+. */
body {
  font-size: clamp(15px, 0.95rem + 0.3vw, 17px);
}

/* 2. Touch-safe minimum hit area for every clickable target.
      iOS HIG = 44pt; we hit it via min-block-size on inline-flex items.
      Scoped to <= 1023px so desktop keeps a denser 36px hit area. */
@media (max-width: 1023px) {
  .topbar nav a,
  .topbar .search-button,
  .topbar .theme-toggle,
  .rail-toggle,
  .toc-toggle,
  .button,
  button,
  a.button,
  a.card,
  .card,
  .tag-chip,
  .node-table td a,
  .edge-list a,
  .ai-siblings a,
  .mobile-bottom-nav a {
    min-block-size: 44px;
    display: inline-flex;
    align-items: center;
  }
}

/* The card body needs a bit more breathing room so the entire surface
   becomes one fat tap target. */
.card { padding: var(--space-4); }
.node-table td { padding: 12px 12px; line-height: 1.5; }
.edge-list li { padding: 10px 0; }

/* 3. Toggle buttons (mobile-only chrome) — hidden on desktop. */
.toc-toggle {
  display: none;
  font-family: var(--type-sans);
  font-size: .88rem;
  padding: 6px 12px;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
  cursor: pointer;
  margin: 0 0 var(--space-3);
}
.rail-toggle:focus-visible,
.toc-toggle:focus-visible,
.mobile-bottom-nav a:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* 4. Bottom nav — hidden by default; revealed under 768 px. */
.mobile-bottom-nav {
  display: none;
  position: fixed;
  left: 0; right: 0; bottom: 0;
  z-index: 40;
  background: color-mix(in srgb, var(--surface) 95%, transparent);
  border-top: 1px solid var(--rule);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  padding: 6px 8px calc(6px + env(safe-area-inset-bottom));
  font-family: var(--type-sans);
}
.mobile-bottom-nav ul {
  list-style: none;
  display: flex;
  justify-content: space-around;
  align-items: stretch;
  padding: 0;
  margin: 0;
  gap: 4px;
}
.mobile-bottom-nav li { flex: 1 1 0; display: flex; }
.mobile-bottom-nav a {
  flex: 1 1 0;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  text-decoration: none;
  color: var(--ink-muted);
  font-size: .68rem;
  letter-spacing: .04em;
  text-transform: uppercase;
  padding: 4px 2px;
  border-radius: 6px;
  gap: 2px;
}
.mobile-bottom-nav a .icon { font-size: 1.25rem; line-height: 1; }
.mobile-bottom-nav a.active { color: var(--accent); }
.mobile-bottom-nav a:hover { color: var(--ink); background: var(--surface-2); }

/* 5. Heatmap container — let it scroll horizontally if it overflows. */
.activity { overflow-x: auto; }

/* 6. Phone breakpoint (< 480 px) ----------------------------------------- */
@media (max-width: 479px) {
  .shell { padding: var(--space-4) var(--space-3); gap: var(--space-4); }

  /* Topbar — brand + hamburger only; hide secondary nav, search, theme. */
  .topbar { padding: var(--space-2) var(--space-3); gap: var(--space-2); }
  .topbar nav { display: none; }
  .topbar .search-button,
  .topbar .theme-toggle { display: none; }

  /* Rail becomes a fullscreen drawer triggered by [data-rail-open]. */
  .rail-toggle { display: inline-flex; margin-left: auto; }
  .rail {
    display: block;
    position: fixed;
    inset: 0 30% 0 0;
    z-index: 60;
    background: var(--surface);
    border-right: 1px solid var(--rule);
    padding: var(--space-5) var(--space-4) calc(var(--space-5) + env(safe-area-inset-bottom));
    overflow-y: auto;
    transform: translateX(-100%);
    transition: transform 220ms ease;
    box-shadow: 4px 0 24px rgba(0,0,0,.18);
  }
  [data-rail-open] .rail { transform: translateX(0); }
  [data-rail-open] body { overflow: hidden; }
  /* Reveal the primary-nav block inside the drawer (Issue 3 mobile). */
  .rail .rail-drawer-nav { display: block; }

  /* TOC becomes a bottom sheet revealed by .toc-toggle. */
  .toc-toggle { display: inline-flex; }
  .toc-rail {
    display: block;
    position: fixed;
    left: 0; right: 0; bottom: 0;
    max-height: 70vh;
    overflow-y: auto;
    z-index: 55;
    background: var(--surface);
    border-top: 1px solid var(--rule);
    border-radius: 12px 12px 0 0;
    padding: var(--space-4) var(--space-4) calc(var(--space-5) + env(safe-area-inset-bottom));
    transform: translateY(100%);
    transition: transform 220ms ease;
    box-shadow: 0 -8px 32px rgba(0,0,0,.18);
  }
  [data-toc-open] .toc-rail { transform: translateY(0); }

  /* Bottom nav appears. Add bottom padding to the page so the last line
     is not hidden under the bar. */
  .mobile-bottom-nav { display: block; }
  body { padding-bottom: calc(64px + env(safe-area-inset-bottom)); }

  /* Hero pulse — single column; headline scales with viewport. */
  .hero h1 { font-size: clamp(28px, 6vw, 48px); }
  .hero .pulse,
  .hero .pulse-cards,
  .pulse-cards { grid-template-columns: 1fr !important; }

  /* Stat row — 2-up; smaller text + tighter gap. */
  .stats,
  .stat-row,
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    gap: var(--space-3) !important;
  }
  .stat { font-size: .9rem; padding: 12px 14px; }
  .stat b,
  .stat .stat-value,
  .stat strong { font-size: clamp(22px, 7vw, 32px); }

  /* Cards stack one per row. */
  .card-grid { grid-template-columns: 1fr; gap: var(--space-3); }

  /* AI siblings — vertical stack, full-width tap targets. */
  .ai-siblings { flex-direction: column; align-items: stretch; gap: var(--space-2); }
  .ai-siblings a { justify-content: center; padding: 10px 12px; }

  /* Graph view — fit phones, stacked toolbar (Issue 1). The bottom
     floating overlay panel is gone (replaced by the cursor tooltip);
     mobile rules below just stack the toolbar + size the canvas. */
  .graph-page .graph-canvas {
    height: clamp(420px, 70vh, 720px);
    min-height: 0;
  }
  .graph-page .graph-toolbar { flex-direction: column; align-items: stretch; }
  .graph-page .graph-toolbar-group { width: 100%; justify-content: stretch; }
  .graph-page .graph-toolbar .button {
    flex: 1 1 0;
    justify-content: center;
    font-size: 16px;
    padding: 10px 12px;
  }
  .graph-page .graph-search { width: 100%; }
  .graph-page .graph-search input { width: 100%; font-size: 16px; }
  .graph-page .graph-tooltip { display: none; }
}

/* 7. Large phone / portrait tablet (480-767 px) ------------------------ */
@media (min-width: 480px) and (max-width: 767px) {
  .topbar nav { display: none; }
  .topbar .search-button,
  .topbar .theme-toggle { display: none; }
  .rail-toggle { display: inline-flex; margin-left: auto; }
  .rail {
    display: block;
    position: fixed;
    inset: 0 25% 0 0;
    z-index: 60;
    background: var(--surface);
    border-right: 1px solid var(--rule);
    padding: var(--space-5) var(--space-4) calc(var(--space-5) + env(safe-area-inset-bottom));
    overflow-y: auto;
    transform: translateX(-100%);
    transition: transform 220ms ease;
    box-shadow: 4px 0 24px rgba(0,0,0,.18);
  }
  [data-rail-open] .rail { transform: translateX(0); }
  [data-rail-open] body { overflow: hidden; }
  .rail .rail-drawer-nav { display: block; }

  .toc-toggle { display: inline-flex; }
  .toc-rail {
    display: block;
    position: fixed;
    left: 0; right: 0; bottom: 0;
    max-height: 70vh;
    overflow-y: auto;
    z-index: 55;
    background: var(--surface);
    border-top: 1px solid var(--rule);
    border-radius: 12px 12px 0 0;
    padding: var(--space-4) var(--space-4) calc(var(--space-5) + env(safe-area-inset-bottom));
    transform: translateY(100%);
    transition: transform 220ms ease;
    box-shadow: 0 -8px 32px rgba(0,0,0,.18);
  }
  [data-toc-open] .toc-rail { transform: translateY(0); }

  .mobile-bottom-nav { display: block; }
  body { padding-bottom: calc(64px + env(safe-area-inset-bottom)); }

  .stats,
  .stat-row,
  .stat-grid { grid-template-columns: repeat(4, minmax(0, 1fr)) !important; gap: var(--space-3) !important; }
  .hero .pulse-cards,
  .pulse-cards { grid-template-columns: 1fr !important; }

  .graph-page .graph-canvas { height: clamp(460px, 70vh, 720px); min-height: 0; }
  .graph-page .graph-toolbar { flex-direction: column; align-items: stretch; }
  .graph-page .graph-toolbar .button { font-size: 16px; padding: 10px 12px; }
  .graph-page .graph-search input { width: 100%; font-size: 16px; }
}

/* 8. Tablet landscape (768-1023 px) ------------------------------------ */
@media (max-width: 767px) {
  /* No-op: covered above; this guard prevents bottom nav from leaking. */
}
@media (min-width: 768px) and (max-width: 1023px) {
  /* Rail static at 200 px; TOC still drawer. */
  .shell { grid-template-columns: 200px 1fr; }
  .rail-toggle { display: none; }
  .toc-toggle { display: inline-flex; }
  .mobile-bottom-nav { display: none; }
  .toc-rail {
    display: block;
    position: fixed;
    left: 0; right: 0; bottom: 0;
    max-height: 70vh;
    overflow-y: auto;
    z-index: 55;
    background: var(--surface);
    border-top: 1px solid var(--rule);
    border-radius: 12px 12px 0 0;
    padding: var(--space-4) var(--space-4) calc(var(--space-5) + env(safe-area-inset-bottom));
    transform: translateY(100%);
    transition: transform 220ms ease;
    box-shadow: 0 -8px 32px rgba(0,0,0,.18);
  }
  [data-toc-open] .toc-rail { transform: translateY(0); }
}

/* 9. Desktop (>= 1024 px) — make sure mobile-only chrome stays hidden. */
@media (min-width: 1024px) {
  .rail-toggle, .toc-toggle, .mobile-bottom-nav { display: none !important; }
}

/* 10. Reduced motion — kill drawer slide animations. */
@media (prefers-reduced-motion: reduce) {
  .rail, .toc-rail { transition: none !important; }
}

/* ============================================================
   Mobile overlap fixes — Issue 2
   ============================================================
   Land last so they win the cascade against earlier @media blocks.
   - Topbar clearance via padding-block-start on <main>.
   - Bottom-nav clearance via padding-block-end on <main>.
   - TOC drawer overlay backdrop so the body never bleeds through.
   - Subtype chips: 44 px hit area + flex-wrap so they don't smash.
   - Long titles wrap aggressively so arxiv ids never push horizontal scroll.
   - Tables on detail pages get a horizontal scroll wrapper. */
@media (max-width: 1023px) {
  .main {
    padding-block-start: max(12px, env(safe-area-inset-top));
    padding-block-end: 88px; /* clear the mobile bottom nav */
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  .main h1, .main h2, .main h3, .main .eyebrow, .raw-title {
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  .breadcrumbs {
    flex-wrap: wrap;
    row-gap: 4px;
  }
  /* Subtype chips: real tap targets, no smash. */
  .subtype-chips { gap: 8px; }
  .subtype-chip {
    min-block-size: 44px;
    padding: 6px 14px;
    line-height: 1.2;
  }
  /* Backdrop for the TOC drawer — kicks in via [data-toc-open] state. */
  [data-toc-open]::before {
    content: "";
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, .4);
    z-index: 50;
    pointer-events: auto;
  }
  /* Backdrop for the rail drawer too. */
  [data-rail-open]::before {
    content: "";
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, .4);
    z-index: 50;
    pointer-events: auto;
  }
  /* Tables breathe inside their scroll wrapper. */
  .table-scroll { margin: var(--space-3) 0; }
  /* Auto-fill card grid stays tappable: every card a fat target. */
  .card { min-block-size: 44px; }
}

/* ============================================================
   Full-width desktop layout — Issue 1
   ============================================================
   Override the earlier ``.shell`` grid so the content column expands
   past the historical 720 px reading limit on wide monitors. Detail
   pages keep a humane prose width via ``--read-w`` (~75ch).
   Index pages opt in via ``main--wide`` and let the table use the full
   viewport. */
@media (min-width: 1280px) {
  .shell {
    max-width: var(--page-w);
    grid-template-columns: var(--rail-w) minmax(0, 1fr) var(--toc-w);
    gap: 24px;
    padding: var(--space-6) clamp(16px, 2vw, 24px);
  }
  .main {
    /* Detail pages cap at the prose-comfortable reading column. */
    max-width: var(--read-w);
    margin-inline: auto;
    width: 100%;
  }
  .main--wide {
    /* Index/listing pages stretch — they're tabular not prose. */
    max-width: min(100vw - 320px, 1640px);
  }
  .shell--wide {
    /* TOC rail stays available for now but the wide-main is allowed
       to consume the gap when there's no TOC content. */
    grid-template-columns: var(--rail-w) minmax(0, 1fr) var(--toc-w);
  }
  /* Issue 1 — graph route drops the right rail entirely. The canvas
     consumes the column the TOC used to occupy. Left rail (doc tree)
     stays visible. */
  .shell--graph {
    grid-template-columns: var(--rail-w) minmax(0, 1fr);
  }
  .main--graph {
    /* Canvas stretches to whatever width the column gives it. */
    max-width: min(100vw - 320px, 1800px);
  }
}

@media (min-width: 1024px) and (max-width: 1279px) {
  .main--graph { max-width: none; }
}

/* Ultra-wide (>= 1920 px): roomier rails, slightly wider content. */
@media (min-width: 1920px) {
  :root {
    --rail-w: 280px;
    --toc-w: 300px;
  }
  .main {
    max-width: min(1280px, 80ch);
  }
  .main--wide {
    max-width: min(100vw - 360px, 1800px);
  }
  .main--graph {
    max-width: min(100vw - 360px, 1900px);
  }
}
"""


CSS = CSS + MOBILE_CSS


__all__ = ["CSS", "MOBILE_CSS"]
