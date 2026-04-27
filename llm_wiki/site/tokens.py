"""Design tokens and stylesheet for the redesigned LLM-Wiki site.

The full CSS string is exposed as ``CSS``. It is consumed by
``StaticSiteBuilder`` (Subagent G) which writes it to ``assets/style.css``.

The tokens mirror §5.1 of ``docs/superpowers/specs/2026-04-27-wiki-frontend-redesign-design.md``
(warm terracotta accent, serif body, system-fallback fonts, dark theme variant).
Layout primitives implement §5.2: 1280px page max width, 240px left rail,
220px right TOC, 720px reading column. Mobile first; the rail unlocks at
``min-width: 768px`` and the TOC unlocks at ``min-width: 1024px``.
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
  --rail-w: 240px;
  --toc-w: 220px;
  --read-w: 720px;
  --page-w: 1280px;
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
.topbar nav { display: flex; gap: var(--space-3); flex: 1; flex-wrap: wrap; }
.topbar nav a {
  color: var(--ink-muted);
  text-decoration: none;
  font-size: .92rem;
  padding: 4px 8px;
  border-radius: 4px;
}
.topbar nav a.active { color: var(--accent); }
.topbar nav a:hover { color: var(--ink); background: var(--surface-2); }
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
.shell {
  max-width: var(--page-w);
  margin: 0 auto;
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-5);
  padding: var(--space-5) var(--space-4);
}
.rail {
  display: none; /* mobile: hidden by default */
  font-family: var(--type-sans);
  font-size: .92rem;
}
.rail h2 {
  font-family: var(--type-sans);
  font-size: .72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--ink-muted);
  margin: var(--space-5) 0 var(--space-2);
}
.rail h2:first-child { margin-top: 0; }
.rail ul { list-style: none; padding: 0; margin: 0; }
.rail li { margin: 0; }
.rail a {
  display: flex;
  justify-content: space-between;
  align-items: center;
  text-decoration: none;
  color: var(--ink);
  padding: 4px 8px;
  border-radius: 4px;
}
.rail a .count { color: var(--ink-muted); font-size: .82rem; font-variant-numeric: tabular-nums; }
.rail a.active { background: var(--accent-soft); color: var(--accent); }
.rail a:hover { background: var(--surface-2); }

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
  .rail { display: block; position: sticky; top: 64px; align-self: start; max-height: calc(100vh - 80px); overflow-y: auto; }
  .rail-toggle { display: none; }
}

@media (min-width: 1024px) {
  .shell {
    grid-template-columns: var(--rail-w) minmax(0, 1fr) var(--toc-w);
  }
  .toc-rail {
    display: block;
    position: sticky;
    top: 64px;
    align-self: start;
    max-height: calc(100vh - 80px);
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
"""

__all__ = ["CSS"]
