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
  --surface-2: #ece8df;        /* polish: bumped for light-theme contrast (was #f3f1ec) */
  --ink: #1f1d1a;
  --ink-muted: #4a463f;        /* polish: darker for WCAG AA on --surface-2 (was #5b574f) */
  --accent: #a3441f;            /* polish: WCAG AA against --bg (was #b3502b) */
  --accent-soft: #f4d4c2;
  --link: #7a3010;              /* polish: AA against --surface (was #8a3a18) */
  --rule: #d8d3c8;              /* polish: stronger 1px borders on light */
  --code-bg: #ece8df;
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
  --rail-w: 200px;
  --toc-w: 200px;
  --read-w: min(1280px, 88ch);
  --page-w: min(100vw - 16px, 1720px);
  --topbar-height: 56px;
}

[data-theme="dark"] {
  --bg: #14130f;
  --surface: #1c1b17;
  --surface-2: #232118;
  --ink: #ece7dc;
  --ink-muted: #b6b0a0;       /* polish: lighter for WCAG AA on --surface-2 (was #a59f90) */
  --accent: #e8915f;           /* polish: AA on --bg in dark theme (was #e08555) */
  --accent-soft: #432215;
  --link: #f3aa82;             /* polish: AA on dark surfaces (was #f0a075) */
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

/* Accessibility utility — visually hidden but exposed to AT.
   Used by the skip-link, the search-palette aria-live region, and
   any other text we render only for screen readers. */
.visually-hidden,
.skip-link {
  position: absolute;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* Skip-to-content link (WCAG 2.4.1). The link is the very first focusable
   element on every page. It only becomes visible when the user tabs to it
   so keyboard / screen-reader users can jump past the topbar straight to
   ``#main``. The visible state pins it to the top-left corner with the
   accent surface so it's unmistakable. */
.skip-link:focus,
.skip-link:focus-visible {
  position: fixed;
  top: 8px;
  left: 8px;
  z-index: 100;
  width: auto;
  height: auto;
  padding: 8px 14px;
  margin: 0;
  clip: auto;
  overflow: visible;
  background: var(--accent);
  color: #fff;
  border-radius: var(--radius);
  text-decoration: none;
  font-family: var(--type-sans);
  font-weight: 600;
  outline: 2px solid var(--ink);
  outline-offset: 2px;
}

/* Universal focus ring (WCAG 2.4.7).
   Every interactive element shows a 2 px accent outline on keyboard focus.
   We use ``:focus-visible`` so mouse users don't get a permanent outline
   on click — only keyboard / programmatic focus paints the ring. */
a:focus-visible,
button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible,
summary:focus-visible,
[tabindex]:focus-visible,
[data-toggle-theme]:focus-visible,
[data-toggle-rail]:focus-visible,
[data-toggle-toc]:focus-visible,
[data-open-search]:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 2px;
}

/* Some inputs already paint their own ring via box-shadow; keep that
   replacement as a *visible* affordance (≥ 2 px) so it satisfies AA. */
.doc-tree-search:focus-visible,
.graph-page .graph-search input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

h1, h2, h3, h4, h5, h6 {
  font-family: var(--type-serif);
  line-height: 1.25;
  margin: 1.6em 0 .6em;
  color: var(--ink);
}
/* Unified heading scale across every page (home / index / detail / timeline /
   about). Detail pages used to clamp at 32px while index pages popped at
   ~43px — the inconsistency made hero pages feel disconnected from the rest
   of the site. Now every <h1> rendered inside <main> sits at the same
   ~30px size, with subtle responsive scaling for narrow viewports. */
h1 { font-size: clamp(1.6rem, 2vw, 1.95rem); margin-top: 0; line-height: 1.18; }
h2 { font-size: 1.35rem; }
h3 { font-size: 1.15rem; }
h4 { font-size: 1.02rem; }

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
  gap: var(--space-3);
  padding: 10px clamp(12px, 2vw, 24px);
  background: color-mix(in srgb, var(--bg) 86%, transparent);
  border-bottom: 1px solid var(--rule);
  -webkit-backdrop-filter: saturate(140%) blur(10px);
  backdrop-filter: saturate(140%) blur(10px);
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
.topbar .search-button {
  font-family: var(--type-sans);
  font-size: .85rem;
  padding: 6px 10px 6px 12px;
  border: 1px solid var(--rule);
  border-radius: 999px;
  background: var(--surface);
  color: var(--ink-muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 200px;
  justify-content: flex-start;
  transition: border-color 160ms ease, color 160ms ease, background 160ms ease;
}
.topbar .search-button .icon { flex-shrink: 0; opacity: .8; }
.topbar .search-button .search-button-label { flex: 1; text-align: left; }
.topbar .search-button .search-button-kbd {
  font-family: var(--type-mono);
  font-size: .72rem;
  padding: 1px 6px;
  border: 1px solid var(--rule);
  border-bottom-width: 2px;
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--ink-muted);
  line-height: 1.2;
}
.topbar .search-button:hover {
  border-color: var(--accent);
  color: var(--ink);
  background: color-mix(in srgb, var(--accent-soft) 60%, var(--surface));
}
.topbar .search-button:hover .icon { opacity: 1; color: var(--accent); }

/* Theme toggle — circular icon button. The two SVGs are stacked; the
   ``data-theme`` attribute on <html> drives which one shows. Sun glows
   when in dark mode (next click → light), moon when in light mode. */
.topbar .theme-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid var(--rule);
  border-radius: 999px;
  background: var(--surface);
  color: var(--ink-muted);
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: color 200ms ease, border-color 200ms ease, background 200ms ease, transform 200ms ease;
}
.topbar .theme-toggle:hover {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
}
.topbar .theme-toggle:active { transform: scale(.94); }
.topbar .theme-toggle .icon {
  position: absolute;
  inset: 0;
  margin: auto;
  width: 18px;
  height: 18px;
  transition: transform 320ms cubic-bezier(.4, .0, .2, 1), opacity 220ms ease;
}
.topbar .theme-toggle .icon-sun {
  transform: rotate(-90deg) scale(.4);
  opacity: 0;
}
.topbar .theme-toggle .icon-moon {
  transform: rotate(0) scale(1);
  opacity: 1;
}
[data-theme="dark"] .topbar .theme-toggle .icon-sun {
  transform: rotate(0) scale(1);
  opacity: 1;
}
[data-theme="dark"] .topbar .theme-toggle .icon-moon {
  transform: rotate(90deg) scale(.4);
  opacity: 0;
}

/* Rail toggle — proper icon button on mobile chrome. */
.topbar .rail-toggle {
  font-family: var(--type-sans);
  font-size: .85rem;
  padding: 6px 10px;
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--ink);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.topbar .rail-toggle:hover { border-color: var(--accent); color: var(--accent); }

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
  /* NOTE: do NOT set ``align-items: start`` here — that collapses the
     TOC column to its content height which leaves no space for sticky
     positioning to slide against. The grid default (``stretch``) is
     what makes the TOC column tall enough for the sticky inner aside
     to follow long-article scrolls. */
  gap: var(--space-3);
  /* Restore breathing room around both rails. The previous 2-8 px shell gutter
     made the file tree and TOC feel glued to the browser edge. */
  padding: var(--space-5) clamp(14px, 2vw, 28px);
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

/* Slim, translucent overlay scrollbar — matches the macOS-style viewport
   scrollbar so the rail / TOC / palette / table-scroll containers don't
   show chunky OS chrome inside the layout. Track is invisible; thumb is
   a 6 px translucent pill that gets a touch more opaque on hover. Firefox
   uses ``scrollbar-width: thin`` plus ``scrollbar-color`` since it doesn't
   honour the WebKit pseudo-elements. */
.rail,
.toc-rail .toc,
aside.toc,
.palette-results,
.table-scroll,
.doc-tree,
.toc-rail {
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--ink) 22%, transparent) transparent;
}
.rail::-webkit-scrollbar,
.toc-rail .toc::-webkit-scrollbar,
aside.toc::-webkit-scrollbar,
.palette-results::-webkit-scrollbar,
.table-scroll::-webkit-scrollbar,
.doc-tree::-webkit-scrollbar,
.toc-rail::-webkit-scrollbar {
  width: 8px;
  height: 8px;
  background: transparent;
}
.rail::-webkit-scrollbar-track,
.toc-rail .toc::-webkit-scrollbar-track,
aside.toc::-webkit-scrollbar-track,
.palette-results::-webkit-scrollbar-track,
.table-scroll::-webkit-scrollbar-track,
.doc-tree::-webkit-scrollbar-track,
.toc-rail::-webkit-scrollbar-track {
  background: transparent;
  border: 0;
}
.rail::-webkit-scrollbar-thumb,
.toc-rail .toc::-webkit-scrollbar-thumb,
aside.toc::-webkit-scrollbar-thumb,
.palette-results::-webkit-scrollbar-thumb,
.table-scroll::-webkit-scrollbar-thumb,
.doc-tree::-webkit-scrollbar-thumb,
.toc-rail::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--ink) 18%, transparent);
  border-radius: 999px;
  border: 2px solid transparent;
  background-clip: padding-box;
  transition: background 160ms ease;
}
.rail:hover::-webkit-scrollbar-thumb,
.toc-rail .toc:hover::-webkit-scrollbar-thumb,
aside.toc:hover::-webkit-scrollbar-thumb,
.palette-results:hover::-webkit-scrollbar-thumb,
.table-scroll:hover::-webkit-scrollbar-thumb,
.doc-tree:hover::-webkit-scrollbar-thumb,
.toc-rail:hover::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--ink) 38%, transparent);
  background-clip: padding-box;
}
.rail::-webkit-scrollbar-corner,
.toc-rail .toc::-webkit-scrollbar-corner,
aside.toc::-webkit-scrollbar-corner,
.palette-results::-webkit-scrollbar-corner,
.table-scroll::-webkit-scrollbar-corner,
.doc-tree::-webkit-scrollbar-corner,
.toc-rail::-webkit-scrollbar-corner {
  background: transparent;
}

/* ---- Doc-tree explorer (Issue 3) ---------------------------------------- */
.doc-tree-search-row { padding: 0 4px var(--space-3); }
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
  padding-inline-start: 8px;
}
/* Top-level list hugs the rail edge; nested lists indent. */
.doc-tree > details > .doc-tree-list,
.doc-tree-root > .doc-tree-list { padding-inline-start: 2px; }
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
  margin: var(--space-4) 4px var(--space-2);
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
    /* Comfortable desktop gutters for left and right rails. */
    padding: var(--space-6) clamp(18px, 2vw, 32px);
  }
  .rail {
    display: block;
    position: sticky;
    top: calc(var(--topbar-height, 56px) + 16px);
    align-self: start;
    max-height: calc(100vh - var(--topbar-height, 56px) - 32px);
    overflow-y: auto;
    padding-inline: 10px;
  }
  .rail-toggle { display: none; }
}

@media (min-width: 900px) {
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
  /* The wrapper grid-stretches to match <main>'s height (no align-self
     override) — that's what gives the inner sticky aside enough column
     to slide against. With grid stretch the rail keeps full column
     height even when its content is short. */
  .toc-rail {
    display: block;
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
    padding-inline: 12px;
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
.breadcrumbs .crumb-current {
  color: var(--ink);
  /* Long arxiv-style titles (e.g. "An Outlook into the Future of
     Egocentric Vision") would otherwise wrap the breadcrumb to two
     lines and visually duplicate the H1 below. Truncate to one line
     with ellipsis; the full title stays in the H1. */
  display: inline-block;
  max-width: min(60ch, 70vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
/* Cap the article H1 so a multi-clause paper title doesn't render at
   60 px on a wide monitor — the previous ``clamp(28px, 6vw, 48px)`` on
   ``.hero h1`` is fine for the home page hero, but a paper-detail page
   needs a more contained title. */
.article-body h1 {
  font-size: clamp(1.6rem, 2vw, 1.95rem);
  line-height: 1.18;
  margin-block: 0 12px;
  overflow-wrap: anywhere;
}
/* The page-meta line (aliases / source path) shouldn't compete with
   the H1 — small, muted, no extra block padding. */
.article-body .page-meta {
  font-size: 12px;
  color: var(--ink-muted);
  margin-block: 0 18px;
}
.article-body .page-meta code {
  font-size: 11px;
}

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
  transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
}
.card:hover {
  transform: translateY(-2px);
  border-color: var(--accent);
  box-shadow: 0 6px 20px rgba(20, 18, 15, .08);
}
[data-theme="dark"] .card:hover {
  box-shadow: 0 6px 24px rgba(0, 0, 0, .35);
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

/* Compact activity heatmap (home page).
   The full 26-week grid lives directly under the stat row on the home
   page. The previous version capped at 320×110 which made the widget
   feel cramped against a 1280px reading column; we now let it span the
   prose width with comfortable upper bounds and a real bottom margin so
   the next section breathes. */
.activity--compact {
  margin: var(--space-5) auto var(--space-7);
  padding: var(--space-4) var(--space-4) var(--space-5);
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-3);
}
.activity--compact .heatmap {
  width: 100%;
  height: auto;
  max-height: 220px;
}
.activity-title {
  font-family: var(--type-sans);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: .02em;
  text-transform: uppercase;
  color: var(--ink-muted);
  margin: 0;
}

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

/* Command palette
   ------------------------------------------------------------
   Spotlight-style search dialog. Triggered by ``/`` or ``cmd+k`` and the
   topbar magnifier button. Layout: a fixed dim overlay, a centered card
   with an icon + input row, optional type-filter tabs, a scrollable
   result list, a status line and a keyboard-hint footer. */
.palette {
  position: fixed;
  inset: 0;
  background: rgba(8, 6, 4, .42);
  -webkit-backdrop-filter: blur(6px);
  backdrop-filter: blur(6px);
  z-index: 50;
  padding: clamp(48px, 9vh, 96px) 16px 16px;
  animation: palette-fade-in 140ms ease-out;
}
.palette[hidden] { display: none; }

@keyframes palette-fade-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}
@keyframes palette-pop-in {
  from { opacity: 0; transform: translateY(-4px) scale(.98); }
  to   { opacity: 1; transform: none; }
}

.palette-box {
  max-width: 640px;
  width: 100%;
  margin: 0 auto;
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: 12px;
  box-shadow: 0 24px 80px rgba(8, 6, 4, .35), 0 2px 8px rgba(8, 6, 4, .12);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  max-height: min(72vh, 640px);
  font-family: var(--type-sans);
  animation: palette-pop-in 160ms cubic-bezier(.16, 1, .3, 1);
}

/* --- Input row ---------------------------------------------------------- */
.palette-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--rule);
  background: var(--surface);
  flex: 0 0 auto;
}
.palette-input-icon {
  flex-shrink: 0;
  color: var(--ink-muted);
}
.palette-box input#search {
  flex: 1;
  width: 100%;
  border: 0;
  outline: none;
  padding: 4px 0;
  font-family: var(--type-sans);
  font-size: 15px;
  line-height: 1.4;
  background: transparent;
  color: var(--ink);
  letter-spacing: 0;
}
.palette-box input#search::placeholder {
  color: var(--ink-muted);
  opacity: .85;
}
.palette-box input#search:focus { outline: none; }
.palette-close {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink-muted);
  border-radius: 4px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: color 140ms ease, background 140ms ease;
}
.palette-close:hover { color: var(--ink); background: var(--surface-2); }

/* --- Type filter tabs --------------------------------------------------- */
.palette-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule);
  background: var(--surface-2);
  flex: 0 0 auto;
}
.palette-tab {
  font-family: var(--type-sans);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: .02em;
  padding: 4px 10px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--ink-muted);
  cursor: pointer;
  line-height: 1.4;
  transition: color 140ms ease, background 140ms ease, border-color 140ms ease;
}
.palette-tab:hover { color: var(--ink); background: var(--surface); }
.palette-tab.is-active,
.palette-tab[aria-selected="true"] {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

/* --- Status line -------------------------------------------------------- */
.palette-status {
  font-family: var(--type-sans);
  font-size: 11px;
  letter-spacing: .04em;
  text-transform: uppercase;
  color: var(--ink-muted);
  padding: 8px 16px;
  border-bottom: 1px solid var(--rule);
  background: var(--surface);
  flex: 0 0 auto;
}
.palette-status:empty { display: none; }

/* --- Result list -------------------------------------------------------- */
.palette-results {
  list-style: none;
  margin: 0;
  padding: 6px;
  overflow-y: auto;
  overscroll-behavior: contain;
  flex: 1 1 auto;
  font-family: var(--type-sans);
}
.palette-results:empty { display: none; }
.palette-result {
  margin: 0;
  padding: 0;
  border-radius: 8px;
}
.palette-result + .palette-result { margin-top: 1px; }
.palette-result-link {
  display: grid;
  grid-template-columns: auto 1fr auto;
  grid-template-rows: auto auto;
  column-gap: 10px;
  row-gap: 2px;
  align-items: center;
  padding: 9px 12px;
  border-radius: 8px;
  text-decoration: none;
  color: var(--ink);
  cursor: pointer;
  transition: background 100ms ease;
  min-block-size: 0;
}
.palette-result-link:hover {
  background: var(--surface-2);
  color: var(--ink);
}
.palette-result.is-active > .palette-result-link,
.palette-result-link:focus-visible {
  background: var(--accent-soft);
  color: var(--ink);
  outline: none;
}
.palette-result.is-active > .palette-result-link .palette-result-title {
  color: var(--accent);
}

.palette-result-kind {
  grid-column: 1;
  grid-row: 1 / span 2;
  align-self: center;
  font-family: var(--type-sans);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
  padding: 3px 7px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--ink-muted);
  border: 1px solid var(--rule);
  white-space: nowrap;
  min-width: 56px;
  text-align: center;
}
.palette-result.is-active > .palette-result-link .palette-result-kind {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.palette-result-title {
  grid-column: 2;
  grid-row: 1;
  font-family: var(--type-sans);
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.palette-result-title mark {
  background: color-mix(in srgb, var(--accent) 22%, transparent);
  color: inherit;
  padding: 0 2px;
  border-radius: 2px;
}

.palette-result-summary {
  grid-column: 2;
  grid-row: 2;
  font-family: var(--type-sans);
  font-size: 11.5px;
  line-height: 1.4;
  color: var(--ink-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.palette-result-summary mark {
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  color: inherit;
  padding: 0 1px;
  border-radius: 2px;
}

.palette-result-recency {
  grid-column: 3;
  grid-row: 1 / span 2;
  align-self: center;
  font-family: var(--type-mono);
  font-size: 10.5px;
  letter-spacing: .02em;
  color: var(--ink-muted);
  white-space: nowrap;
  padding-inline-start: 6px;
}

/* --- Keyboard hint footer ---------------------------------------------- */
.palette-hint {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  padding: 8px 14px;
  border-top: 1px solid var(--rule);
  background: var(--surface-2);
  font-family: var(--type-sans);
  font-size: 11px;
  color: var(--ink-muted);
  flex: 0 0 auto;
}
.palette-hint kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: var(--type-mono);
  font-size: 10px;
  font-weight: 500;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  margin-right: 4px;
  border: 1px solid var(--rule);
  border-bottom-width: 2px;
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
  line-height: 1;
}
.palette-hint span:last-child { margin-left: auto; }
@media (max-width: 520px) {
  .palette-hint span:last-child { margin-left: 0; }
  .palette-hint { gap: 10px; }
}

/* Lock body scroll while the palette is open. */
body.palette-open { overflow: hidden; }

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
  gap: var(--space-3);
  /* F-11 — graph route is a tool, not a doc. Drop top margin so the
     toolbar (and the canvas right under it) sits at the top of the
     first viewport. */
  margin-top: var(--space-2);
}
.graph-page .graph-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
  justify-content: flex-start;
}
/* F-11 — inline toolbar title replaces the previous hero <h1>. ~16px so
   it reads as a tool label, not a documentation heading. */
.graph-page .graph-toolbar-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  font-family: var(--type-mono);
  letter-spacing: 0.02em;
  color: var(--ink);
}
/* F-11 — circular ``?`` button on the right of the toolbar; clicking it
   toggles ``[data-graph-help-open]`` on the wrapper which reveals the
   popover below. The ``?`` keyboard shortcut (graph.js) flips the same
   state. */
.graph-page .graph-help-button {
  margin-left: auto;
  width: 28px;
  height: 28px;
  padding: 0;
  font-family: var(--type-mono);
  font-size: 0.95rem;
  line-height: 1;
  color: var(--ink-muted);
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: 999px;
  cursor: pointer;
  transition: color 160ms ease, border-color 160ms ease, background 160ms ease;
}
.graph-page .graph-help-button:hover,
.graph-page .graph-help-button:focus {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
}
.graph-page .graph-help-button[aria-expanded="true"] {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
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
/* Fullscreen mode (Issue 4). The wrapper covers the viewport; the
   toolbar sits on top, the legend bottom-left. The focus detail panel
   (``#graph-focus-panel``) is positioned absolutely inside the wrapper
   so the Fullscreen API still draws it on top of the canvas. */
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
/* Issue 6 — Auto-browse mode cursor cue. The toolbar's Auto-browse
   button toggles the ``is-auto-browsing`` class on the wrapper so the
   user has an unmistakable visual indicator (the cursor) that the
   graph is in tour mode and the camera is moving on its own. */
.graph-canvas-wrapper.is-auto-browsing,
.graph-canvas-wrapper.is-auto-browsing .graph-canvas,
.graph-canvas-wrapper.is-auto-browsing canvas { cursor: progress; }
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

/* F-5 — floating focus-detail panel. Pinned to the bottom-right corner
   of ``.graph-canvas-wrapper`` (NOT the page rail), semi-transparent so
   the canvas underneath stays visible, internally scrollable so a long
   description doesn't push content past the viewport. ``[hidden]``
   gates visibility — graph.js toggles it as focus/unfocus happens. */
.graph-canvas-wrapper .graph-focus-panel {
  position: absolute;
  right: var(--space-3);
  bottom: var(--space-3);
  width: clamp(240px, 28vw, 360px);
  max-height: 60vh;
  overflow-y: auto;
  background: rgba(20,20,20,0.78);
  color: #fff;
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 8px;
  padding: 14px 16px 12px;
  font-size: 13px;
  line-height: 1.5;
  z-index: 40;
  box-shadow: 0 12px 32px rgba(0,0,0,0.32);
}
.graph-canvas-wrapper .graph-focus-panel[hidden] { display: none; }
.graph-canvas-wrapper .graph-focus-panel-title {
  margin: 0 24px 4px 0;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.3;
}
.graph-canvas-wrapper .graph-focus-panel-meta {
  margin: 0 0 8px;
  font-family: var(--type-mono);
  font-size: 11px;
  opacity: 0.78;
}
.graph-canvas-wrapper .graph-focus-panel-desc {
  margin: 0 0 10px;
  font-family: var(--type-serif);
  font-size: 12.5px;
  opacity: 0.92;
}
.graph-canvas-wrapper .graph-focus-panel-open {
  display: inline-block;
  margin-bottom: 8px;
  padding: 6px 12px;
  font-family: var(--type-mono);
  font-size: 12px;
  background: rgba(255,255,255,0.12);
  color: #fff;
  border: 1px solid rgba(255,255,255,0.24);
  border-radius: 4px;
  text-decoration: none;
}
.graph-canvas-wrapper .graph-focus-panel-open:hover,
.graph-canvas-wrapper .graph-focus-panel-open:focus {
  background: rgba(255,255,255,0.2);
  border-color: rgba(255,255,255,0.4);
}
.graph-canvas-wrapper .graph-focus-panel-open[hidden] { display: none; }
.graph-canvas-wrapper .graph-focus-panel-neighbors {
  margin-top: 6px;
  font-family: var(--type-mono);
  font-size: 11px;
  opacity: 0.78;
}
.graph-canvas-wrapper .graph-focus-panel-close {
  position: absolute;
  top: 6px;
  right: 8px;
  width: 24px;
  height: 24px;
  padding: 0;
  background: transparent;
  color: #fff;
  border: none;
  border-radius: 4px;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  opacity: 0.7;
}
.graph-canvas-wrapper .graph-focus-panel-close:hover,
.graph-canvas-wrapper .graph-focus-panel-close:focus {
  opacity: 1;
  background: rgba(255,255,255,0.12);
}
[data-theme="light"] .graph-canvas-wrapper .graph-focus-panel {
  background: rgba(255,255,255,0.94);
  color: var(--ink);
  border-color: var(--rule);
}
[data-theme="light"] .graph-canvas-wrapper .graph-focus-panel-open {
  background: var(--surface);
  color: var(--accent);
  border-color: var(--rule);
}
[data-theme="light"] .graph-canvas-wrapper .graph-focus-panel-close {
  color: var(--ink);
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
/* F-11 — ``.graph-help`` is now a popover, not an inline help line.
   Hidden by default; revealed when the wrapper carries
   ``[data-graph-help-open]`` (toggled by the ``?`` button or the ``?``
   keyboard shortcut). Pinned to the top-right inside
   ``.graph-canvas-wrapper`` so the Fullscreen API still draws it on
   top of the canvas. */
.graph-page .graph-help {
  display: none;
  position: absolute;
  top: 56px;
  right: var(--space-3);
  z-index: 30;
  max-width: 320px;
  padding: 12px 14px;
  font-family: var(--type-mono);
  font-size: 0.78rem;
  background: var(--surface);
  color: var(--ink);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.graph-page .graph-help[hidden] { display: none; }
.graph-canvas-wrapper[data-graph-help-open] .graph-help {
  display: block;
}
.graph-page .graph-help-stats,
.graph-page .graph-help-shortcuts {
  margin: 0 0 6px;
}
.graph-page .graph-help-shortcuts:last-child { margin-bottom: 0; }
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
/* The graph route does not ship a right TOC rail (Issue 1). Hover
   preview lives in the cursor tooltip above; focused-node detail lives
   in the floating ``#graph-focus-panel`` (F-5) pinned to the bottom-
   right of the canvas wrapper. */
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
button:not([class*="graph-legend"]):not(.tag-chip):not(.palette-tab):not(.palette-close):not(.theme-toggle):not(.search-button):not(.rail-toggle):not(.doc-tree-folder-summary),
a.button,
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
button:not([class*="graph-legend"]):not(.tag-chip):not(.palette-tab):not(.palette-close):not(.theme-toggle):not(.search-button):not(.rail-toggle):not(.doc-tree-folder-summary):hover,
a.button:hover,
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

/* Agent-session pages: compact Pratiyush-style memory/timeline view. */
.session-page .hero {
  padding-block: 8px 2px;
}
.session-page .lead {
  font-size: 1rem;
  line-height: 1.55;
}
.session-page .stats {
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin: var(--space-3) 0 var(--space-4);
}
.session-page .stat {
  padding: 12px 14px;
  gap: 4px;
}
.session-page .stat b,
.session-page .stat .stat-value {
  font-size: clamp(1.15rem, 1.8vw, 1.7rem);
}
.session-page .stat span,
.session-page .stat .stat-label {
  font-size: 11px;
}
.session-table {
  font-size: .86rem;
  table-layout: fixed;
  width: 100%;
}
.session-table th,
.session-table td {
  padding: 7px 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: top;
}
.session-table th:nth-child(1),
.session-table td:nth-child(1) { width: 34%; }
.session-table th:nth-child(2),
.session-table td:nth-child(2) { width: 10%; }
.session-table th:nth-child(3),
.session-table td:nth-child(3) { width: 10%; }
.session-table th:nth-child(4),
.session-table td:nth-child(4) { width: 9%; }
.session-table th:nth-child(5),
.session-table td:nth-child(5) { width: 12%; }
.session-table th:nth-child(6),
.session-table td:nth-child(6),
.session-table th:nth-child(7),
.session-table td:nth-child(7) { width: 6%; }
.session-table th:nth-child(8),
.session-table td:nth-child(8) { width: 9%; }
.session-table td code {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}
.session-table .session-link {
  font-weight: 600;
}
.session-page code {
  font-size: 14px;
}
.session-page .panel li,
.session-page .panel p,
.session-page .panel dd {
  font-size: 1rem;
}
.session-detail-rail {
  background: var(--surface);
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  padding-block: 12px;
}
.session-turn-nav ol {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 2px;
}
.session-turn-nav a {
  display: grid;
  grid-template-columns: auto 1fr;
  column-gap: 8px;
  row-gap: 1px;
  padding: 7px 8px;
  border-radius: 5px;
  color: var(--ink);
  text-decoration: none;
  font-family: var(--type-sans);
}
.session-turn-nav a:hover,
.session-turn-nav a:focus-visible {
  background: var(--accent-soft);
  color: var(--ink);
}
.session-turn-nav-index {
  grid-row: 1 / span 2;
  font-family: var(--type-mono);
  font-size: 12px;
  color: var(--accent);
}
.session-turn-nav-role {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
  color: var(--ink-muted);
}
.session-turn-nav-summary {
  font-size: 12px;
  line-height: 1.35;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-turn-nav-more {
  padding: 8px;
  font-family: var(--type-sans);
  font-size: 12px;
  color: var(--ink-muted);
}
.session-conversation {
  gap: 12px;
}
.session-turn-list {
  display: grid;
  gap: 12px;
}
.session-turn {
  border: 1px solid var(--rule);
  border-radius: var(--radius);
  background: var(--surface-2);
  overflow: hidden;
}
.session-turn-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule);
  font-family: var(--type-sans);
  font-size: 12px;
  color: var(--ink-muted);
}
.session-turn-role {
  font-weight: 700;
  color: var(--ink);
  text-transform: uppercase;
  letter-spacing: .05em;
}
.session-turn-index {
  font-family: var(--type-mono);
  color: var(--accent);
}
.session-turn-header time {
  margin-left: auto;
  font-family: var(--type-mono);
  font-size: 11px;
}
.session-turn-text {
  margin: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  padding: 12px;
  font-size: 15px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.session-turn--user .session-turn-role { color: var(--accent); }
.session-turn--assistant .session-turn-role { color: var(--ink); }
.session-turn--tool .session-turn-role { color: var(--ink-muted); }

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

/* Markdown-rendered tables: cell borders + padding so the body reads as a
   real table, not rows of plain text. The horizontal-scroll affordance for
   wide tables on narrow viewports is provided by the outer
   ``<div class="table-scroll">`` wrapper that the markdown post-processor
   emits — keeping ``display: block`` on the table itself silently dropped
   cell borders on some rendering paths because ``border-collapse: collapse``
   needs a regular ``display: table`` context to compute cell edges. */
.article-body table,
.markdown-body table {
  width: 100%;
  border-collapse: collapse;
  margin: var(--space-3) 0;
  font-family: var(--type-sans);
  font-size: .92rem;
  border: 1px solid var(--rule);
}
.article-body table th,
.article-body table td,
.markdown-body table th,
.markdown-body table td {
  border: 1px solid var(--rule);
  padding: 8px 12px;
  text-align: left;
  vertical-align: top;
  line-height: 1.5;
}
.article-body table th,
.markdown-body table th {
  background: var(--surface-2);
  font-weight: 600;
  color: var(--ink);
}
.article-body table tbody tr:nth-child(even),
.markdown-body table tbody tr:nth-child(even) {
  background: color-mix(in srgb, var(--surface-2) 50%, transparent);
}
.article-body table code,
.markdown-body table code {
  font-size: .85rem;
}

/* Markdown-rendered images: never overflow the prose column. The
   markdown parser emits bare ``<img>`` tags whose intrinsic dimensions
   come from the source asset, so unconstrained they spill into the
   right TOC rail. ``max-width: 100%`` + ``height: auto`` keeps the
   aspect ratio; we don't touch ``display`` so inline icons inside a
   paragraph stay inline. */
.article-body img,
.markdown-body img,
.raw-markdown img {
  max-width: 100%;
  height: auto;
}
/* Standalone images (markdown emits these as the only child of a <p>):
   give them block presentation, breathing room, and a soft 1px rule so
   they read as a discrete figure rather than a floating sliver. */
.article-body p > img:only-child,
.markdown-body p > img:only-child,
.raw-markdown p > img:only-child {
  display: block;
  margin: var(--space-3) auto;
  border-radius: var(--radius);
  border: 1px solid var(--rule);
}

/* ============================================================
   Light-theme polish — overrides for [data-theme="light"]
   ============================================================
   The site is dark-first; the light theme inherits the :root tokens
   above (already light) but a handful of components were tuned for
   dark surfaces and need explicit overrides so the light variant
   reads as intentional rather than washed-out. */

[data-theme="light"] {
  /* Mirror the :root values explicitly so a JS toggle to ``light``
     resets every token after a previous ``dark`` mount.  The values
     match the :root block above. */
  --bg: #fafaf7;
  --surface: #ffffff;
  --surface-2: #ece8df;
  --ink: #1f1d1a;
  --ink-muted: #4a463f;
  --accent: #a3441f;
  --accent-soft: #f4d4c2;
  --link: #7a3010;
  --rule: #d8d3c8;
  --code-bg: #ece8df;
  --shadow: 0 1px 2px rgba(20, 18, 15, .06);
}

/* Code blocks need a stronger lift off the page background on the
   light surface so the eye reads them as a callout, not as prose. */
[data-theme="light"] .code,
[data-theme="light"] code,
[data-theme="light"] pre,
[data-theme="light"] pre code,
[data-theme="light"] .raw-text {
  background: var(--surface-2);
}
[data-theme="light"] pre {
  border-color: var(--rule);
  box-shadow: inset 0 1px 0 rgba(20, 18, 15, .03);
}

/* Subtype chip — readable inactive state on light surfaces, clear
   active fill, AA-rated count pill. */
[data-theme="light"] .subtype-chip {
  background: var(--surface);
  border-color: var(--rule);
  color: var(--ink);
}
[data-theme="light"] .subtype-chip .chip-count {
  background: var(--surface-2);
  color: var(--ink-muted);
}
[data-theme="light"] .subtype-chip:hover {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}
[data-theme="light"] .subtype-chip.is-active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

/* Doc-tree active leaf — terracotta accent over a soft tint reads as
   a focused row without overwhelming the surrounding tree. */
[data-theme="light"] .doc-tree-leaf.is-active > a {
  background: var(--accent-soft);
  color: var(--accent);
  border-left-color: var(--accent);
}

/* Auto-link dotted underline — bump to 1.5 px for retina visibility
   on light prose, and deepen the hover color so the affordance
   stays AA against --bg. */
[data-theme="light"] .auto-link {
  border-bottom: 1.5px dotted var(--ink-muted);
  color: var(--ink);
}
[data-theme="light"] .auto-link:hover,
[data-theme="light"] .auto-link:focus {
  border-bottom-color: var(--accent);
  color: var(--accent);
}

/* Topbar brand + sticky chrome — make the backdrop blur a touch
   stronger on light theme so scrolled content reads cleanly under
   it without the bar disappearing into the page. */
[data-theme="light"] .topbar {
  background: color-mix(in srgb, var(--bg) 92%, transparent);
}

/* AI siblings footer chips — better contrast inside the muted card. */
[data-theme="light"] .ai-siblings a {
  background: var(--surface);
  border-color: var(--rule);
  color: var(--accent);
}
[data-theme="light"] .ai-siblings a:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
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
  .rail-toggle,
  .toc-toggle,
  .button,
  button:not(.palette-tab):not(.palette-close):not(.theme-toggle):not(.doc-tree-folder-summary),
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
  .topbar .theme-toggle {
    width: 40px;
    height: 40px;
    min-block-size: 40px;
  }
  .topbar .search-button {
    min-width: 0;
    padding: 6px 12px;
  }
  .topbar .search-button .search-button-kbd { display: none; }
}
@media (max-width: 900px) {
  .topbar .search-button .search-button-label,
  .topbar .search-button .search-button-kbd { display: none; }
  .topbar .search-button {
    width: 40px;
    height: 40px;
    min-width: 0;
    padding: 0;
    justify-content: center;
    border-radius: 999px;
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
  /* iOS HIG: extend padding into the home-indicator safe area while
     keeping a real 8 px gap above (max() picks the larger of the two). */
  padding: 6px 8px max(8px, env(safe-area-inset-bottom));
  font-family: var(--type-sans);
}
.mobile-bottom-nav ul {
  list-style: none;
  /* 5 quick-access slots that always fit, regardless of label length —
     pure flex was prone to overflow on the smallest viewports (320 px). */
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  align-items: stretch;
  padding: 0;
  margin: 0;
  gap: 4px;
}
.mobile-bottom-nav li { display: flex; min-width: 0; }
.mobile-bottom-nav a {
  display: flex;
  flex: 1 1 0;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  text-decoration: none;
  color: var(--ink-muted);
  font-size: .68rem;
  letter-spacing: .04em;
  text-transform: uppercase;
  padding: 6px 2px;
  border-radius: 6px;
  gap: 2px;
  min-width: 0;
  min-block-size: 44px;
}
.mobile-bottom-nav a .label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
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

  /* Hero pulse — single column; headline matches detail-page H1. */
  .hero h1 { font-size: clamp(22px, 4.5vw, 28px); }
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
    /* Comfortable desktop gutters for left and right rails. */
    padding: var(--space-6) clamp(18px, 2vw, 32px);
  }
  .main {
    /* Detail and index pages share one column width so a user navigating
     between them never sees the body re-flow. Graph still opts out via
     ``.main--graph``. */
    max-width: var(--read-w);
    margin-inline: auto;
    width: 100%;
  }
  .main--wide {
    /* Index/listing pages historically stretched wider than detail
     pages; the user wants them aligned now so they all share
     ``--read-w``. Bump the cap a touch so very-wide tables still
     breathe on ultra-wide screens. */
    max-width: var(--read-w);
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
    --rail-w: 220px;
    --toc-w: 240px;
    --read-w: min(1440px, 96ch);
  }
  .main,
  .main--wide {
    max-width: var(--read-w);
  }
  .main--graph {
    max-width: min(100vw - 360px, 1900px);
  }
}
"""


CSS = CSS + MOBILE_CSS


__all__ = ["CSS", "MOBILE_CSS"]
