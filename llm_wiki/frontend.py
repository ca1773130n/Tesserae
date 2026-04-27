"""Static frontend for compiled LLM-Wiki graphs.

The frontend intentionally mirrors the strong ideas from Pratiyush/llm-wiki:
precomputed search data, multiple first-class HTML pages, keyboard-first search,
human pages plus AI-readable exports, no npm/bundler, and rich navigation over
projects/sources/nodes instead of a single inert dashboard.
"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

from .research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType

RESEARCH_TYPES = {
    ResearchNodeType.PAPER.value,
    ResearchNodeType.RESEARCH_FIELD.value,
    ResearchNodeType.RESEARCH_TOPIC.value,
    ResearchNodeType.PROBLEM_AREA.value,
    ResearchNodeType.APPROACH_FAMILY.value,
    ResearchNodeType.TREND.value,
    ResearchNodeType.MODEL.value,
    ResearchNodeType.DATASET.value,
    ResearchNodeType.BENCHMARK.value,
    ResearchNodeType.METRIC.value,
    ResearchNodeType.RESULT.value,
    ResearchNodeType.METHODOLOGICAL_CONCEPT.value,
    ResearchNodeType.MATHEMATICAL_CONCEPT.value,
    ResearchNodeType.ALGORITHM.value,
    ResearchNodeType.ARCHITECTURE_PATTERN.value,
    ResearchNodeType.TASK.value,
    ResearchNodeType.CAPABILITY.value,
    ResearchNodeType.CLAIM.value,
    ResearchNodeType.CONTRIBUTION_CLAIM.value,
    ResearchNodeType.PERFORMANCE_CLAIM.value,
    ResearchNodeType.COMPARISON_CLAIM.value,
    ResearchNodeType.LIMITATION_CLAIM.value,
    ResearchNodeType.CAUSAL_CLAIM.value,
    ResearchNodeType.OPEN_QUESTION.value,
    ResearchNodeType.EVIDENCE_SPAN.value,
}
DEVELOPMENT_TYPES = {
    ResearchNodeType.CODE_PROJECT.value,
    ResearchNodeType.SOURCE_FILE.value,
    ResearchNodeType.CODE_MODULE.value,
    ResearchNodeType.CODE_CLASS.value,
    ResearchNodeType.CODE_FUNCTION.value,
    ResearchNodeType.DEPENDENCY.value,
    ResearchNodeType.REPOSITORY.value,
    ResearchNodeType.PROJECT.value,
}


@dataclass(frozen=True)
class StaticSiteBuilder:
    site_title: str = "LLM-Wiki"

    def write_site(self, graph: ResearchGraph, output_dir: str | Path) -> Dict[str, object]:
        out = Path(output_dir)
        if out.exists():
            shutil.rmtree(out)
        (out / "assets").mkdir(parents=True, exist_ok=True)
        (out / "nodes").mkdir(parents=True, exist_ok=True)
        (out / "sources").mkdir(parents=True, exist_ok=True)
        (out / "graph").mkdir(parents=True, exist_ok=True)

        graph_payload = graph.model_dump()
        context = SiteContext.from_graph(graph)
        search_index = build_search_index(graph, context)

        (out / "graph.json").write_text(json.dumps(graph_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out / "search-index.json").write_text(json.dumps(search_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out / "llms.txt").write_text(render_llms_txt(self.site_title, graph, context), encoding="utf-8")
        (out / "llms-full.txt").write_text(render_llms_full_txt(self.site_title, graph, context), encoding="utf-8")
        (out / "manifest.json").write_text(json.dumps(render_manifest(graph, search_index), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")
        (out / "assets" / "app.js").write_text(JS, encoding="utf-8")

        (out / "index.html").write_text(render_home(self.site_title, graph, context, search_index), encoding="utf-8")
        (out / "nodes" / "index.html").write_text(render_nodes_index(self.site_title, graph, context, search_index), encoding="utf-8")
        (out / "sources" / "index.html").write_text(render_sources_index(self.site_title, graph, context, search_index), encoding="utf-8")
        (out / "graph" / "index.html").write_text(render_graph_page(self.site_title, graph, context, search_index), encoding="utf-8")

        for node in graph.nodes:
            (out / node_href(node.id)).write_text(render_node_page(self.site_title, node, context, search_index), encoding="utf-8")
        for source_path in context.source_counts:
            if source_path != "unknown":
                (out / source_href(source_path)).write_text(render_source_page(self.site_title, source_path, graph, context, search_index), encoding="utf-8")

        source_pages = len([source for source in context.source_counts if source != "unknown"])
        return {
            "site_path": str(out),
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "search_entries": len(search_index),
            "html_pages": 4 + len(graph.nodes) + source_pages,
        }


@dataclass(frozen=True)
class SiteContext:
    nodes_by_id: Mapping[str, ResearchNode]
    outgoing: Mapping[str, List[ResearchEdge]]
    incoming: Mapping[str, List[ResearchEdge]]
    type_counts: Mapping[str, int]
    source_counts: Mapping[str, int]
    edge_counts: Mapping[str, int]

    @classmethod
    def from_graph(cls, graph: ResearchGraph) -> "SiteContext":
        nodes_by_id = {node.id: node for node in graph.nodes}
        outgoing: Dict[str, List[ResearchEdge]] = defaultdict(list)
        incoming: Dict[str, List[ResearchEdge]] = defaultdict(list)
        for edge in graph.edges:
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)
        return cls(
            nodes_by_id=nodes_by_id,
            outgoing=outgoing,
            incoming=incoming,
            type_counts=Counter(node.type.value for node in graph.nodes),
            source_counts=Counter(node.source_path or "unknown" for node in graph.nodes),
            edge_counts=Counter(edge.type for edge in graph.edges),
        )


def build_search_index(graph: ResearchGraph, context: SiteContext | None = None) -> List[Dict[str, object]]:
    context = context or SiteContext.from_graph(graph)
    entries: List[Dict[str, object]] = []
    for node in graph.nodes:
        source_path = node.source_path or ""
        metadata_text = json.dumps(node.metadata, ensure_ascii=False, sort_keys=True, default=str)
        description = node.description or source_path or node.id
        entries.append({
            "id": node.id,
            "title": node.name,
            "type": node.type.value,
            "kind": node_kind(node),
            "description": description,
            "source_path": source_path,
            "href": rel_href(node_href(node.id)),
            "degree": len(context.outgoing.get(node.id, [])) + len(context.incoming.get(node.id, [])),
            "text": " ".join([node.name, node.type.value, description, source_path, metadata_text]),
        })
    return entries


def render_home(title: str, graph: ResearchGraph, context: SiteContext, search_index: List[Dict[str, object]]) -> str:
    wiki_nodes = [n for n in graph.nodes if n.type in {ResearchNodeType.SOURCE_DOCUMENT, ResearchNodeType.CONCEPT}]
    development = [n for n in graph.nodes if node_kind(n) == "development"]
    high_degree = sorted(graph.nodes, key=lambda n: len(context.outgoing.get(n.id, [])) + len(context.incoming.get(n.id, [])), reverse=True)[:12]
    recent_sources = sorted(context.source_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    body = f"""
<section class="hero panel">
  <p class="eyebrow">self-indexed knowledge graph</p>
  <h1>{esc(title)}</h1>
  <p class="lead">A browsable LLM-Wiki built from this repository's docs, source code, tests, generated graph artifacts, and agent-facing exports.</p>
  <div class="actions">
    <a class="button primary" href="nodes/index.html">Browse nodes</a>
    <a class="button" href="sources/index.html">Source files</a>
    <a class="button" href="graph/index.html">Graph view</a>
    <button class="button" data-open-search>Command palette ⌘K</button>
  </div>
</section>
<section class="stats">{render_stats(context, len(graph.nodes), len(graph.edges))}</section>
<section class="grid two">
  <article class="panel"><h2>Wiki documents</h2><p class="muted">Repository README/docs become source-document pages with section headings and provenance, so each compile adds navigable wiki structure.</p>{render_node_cards(wiki_nodes[:10], context)}</article>
  <article class="panel"><h2>Code graph</h2><p class="muted">Source files, classes, functions, dependencies, and relations extracted from the repository.</p>{render_node_cards(development[:10], context)}</article>
</section>
<section class="grid two">
  <article class="panel"><h2>Most connected nodes</h2>{render_node_table(high_degree, context)}</article>
  <article class="panel"><h2>Top source files</h2>{render_source_table(recent_sources)}</article>
</section>
<section class="panel"><h2>Graph edge types</h2>{render_bar_list(context.edge_counts)}</section>
"""
    return page(title, "Home", body, search_index, active="home", depth=0)


def render_nodes_index(title: str, graph: ResearchGraph, context: SiteContext, search_index: List[Dict[str, object]]) -> str:
    by_type: Dict[str, List[ResearchNode]] = defaultdict(list)
    for node in graph.nodes:
        by_type[node.type.value].append(node)
    sections = []
    for node_type, nodes in sorted(by_type.items()):
        sections.append(f"<section class='panel node-section' data-type='{esc(node_type)}'><h2>{esc(node_type)} <span>{len(nodes)}</span></h2>{render_node_table(sorted(nodes, key=lambda n: n.name.lower()), context, limit=500, depth=1)}</section>")
    body = f"""
<section class="panel"><h1>Nodes</h1><p class="lead">Every typed graph node. Filter by type or use the command palette.</p><div class="filter-row"><input id="type-filter" placeholder="Filter node types or names…"><button class="button" data-open-search>Search all</button></div></section>
{''.join(sections)}
"""
    return page(title, "Nodes", body, search_index, active="nodes", depth=1)


def render_sources_index(title: str, graph: ResearchGraph, context: SiteContext, search_index: List[Dict[str, object]]) -> str:
    rows = sorted(context.source_counts.items(), key=lambda item: (item[0] == "unknown", item[0]))
    body = f"""
<section class="panel"><h1>Source files</h1><p class="lead">Raw evidence paths that produced graph nodes. Source files remain evidence; pages and HTML are projections.</p></section>
<section class="panel">{render_source_table(rows, limit=500, depth=1)}</section>
"""
    return page(title, "Sources", body, search_index, active="sources", depth=1)


def render_graph_legend(context: SiteContext) -> str:
    rows = []
    for type_name, count in sorted(context.type_counts.items(), key=lambda item: (-item[1], item[0]))[:12]:
        rows.append(f"<div class='row'><span class='dot' style='background:{graph_color(type_name)}'></span>{esc(type_name)} <b>{count}</b></div>")
    return "".join(rows)


def graph_view_payload(graph: ResearchGraph, context: SiteContext) -> Dict[str, object]:
    nodes = []
    for node in graph.nodes:
        degree = len(context.outgoing.get(node.id, [])) + len(context.incoming.get(node.id, []))
        nodes.append({
            "id": node.id,
            "label": node.name,
            "type": node.type.value,
            "path": node.source_path or "",
            "site_url": "../" + rel_href(node_href(node.id)),
            "in_degree": len(context.incoming.get(node.id, [])),
            "out_degree": len(context.outgoing.get(node.id, [])),
            "degree": degree,
            "color": graph_color(node.type.value),
        })
    edges = [{"source": edge.source, "target": edge.target, "type": edge.type} for edge in graph.edges]
    orphans = [node["id"] for node in nodes if node["in_degree"] == 0]
    top_hubs = sorted(nodes, key=lambda item: int(item["degree"]), reverse=True)[:10]
    avg_degree = (sum(int(node["degree"]) for node in nodes) / len(nodes)) if nodes else 0.0
    return {"nodes": nodes, "edges": edges, "stats": {"orphans": orphans, "top_hubs": top_hubs, "avg_degree": avg_degree}}


def graph_color(type_name: str) -> str:
    palette = {
        "CodeProject": "#7c3aed", "Repository": "#7c3aed", "Project": "#7c3aed",
        "SourceFile": "#2563eb", "CodeModule": "#2563eb", "CodeClass": "#0f766e", "CodeFunction": "#059669",
        "Dependency": "#d97706", "SourceDocument": "#64748b", "Concept": "#0891b2", "Paper": "#be185d",
    }
    return palette.get(type_name, "#64748b")


def render_graph_page(title: str, graph: ResearchGraph, context: SiteContext, search_index: List[Dict[str, object]]) -> str:
    payload = graph_view_payload(graph, context)
    top = sorted(graph.nodes, key=lambda n: len(context.outgoing.get(n.id, [])) + len(context.incoming.get(n.id, [])), reverse=True)[:24]
    body = f"""
<section class="panel graph-hero"><h1>Knowledge Graph</h1><p class="lead">Interactive vis-network graph copied from the reference llmwiki pattern: search, cluster by type, click through to node pages, right-click for node actions, and inspect graph health at a glance.</p></section>
<section class="graph-shell" aria-label="Interactive knowledge graph">
  <div class="graph-toolbar">
    <label class="graph-control">Search <input id="graph-search" type="search" placeholder="Search nodes…"></label>
    <button class="graph-control" id="cluster-toggle" type="button">Cluster: <b id="cluster-mode">off</b></button>
    <button class="graph-control" id="fit-graph" type="button">Fit</button>
  </div>
  <div id="network">
    <div id="offline-notice">vis-network failed to load — the JSON graph is still available at <a href="../graph.json">graph.json</a>.</div>
    <div id="legend">{render_graph_legend(context)}</div>
    <div id="stats-overlay"><h3>Stats</h3><div class="stat"><span>Nodes</span><b>{len(graph.nodes)}</b></div><div class="stat"><span>Edges</span><b>{len(graph.edges)}</b></div><div class="stat"><span>Orphans</span><b>{len(payload['stats']['orphans'])}</b></div><div class="stat"><span>Avg degree</span><b>{payload['stats']['avg_degree']:.2f}</b></div><h3>Top hubs</h3>{''.join(f"<div class='hub-item'><b>{item['degree']:3}</b> {esc(item['label'])}</div>" for item in payload['stats']['top_hubs'][:5])}</div>
    <div id="ctx-menu" role="menu" aria-label="Node actions"><div class="ctx-header" id="ctx-target">—</div><button type="button" role="menuitem" data-action="open">Open page <span class="ctx-kbd">Enter</span></button><button type="button" role="menuitem" data-action="neighbours">Find neighbours <span class="ctx-kbd">N</span></button><button type="button" role="menuitem" data-action="copy-id">Copy node id <span class="ctx-kbd">C</span></button></div>
  </div>
</section>
<section class="panel"><h2>Top connected nodes</h2>{render_node_table(top, context, depth=1)}</section>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js" integrity="sha384-yxKDWWf0wwdUj/gPeuL11czrnKFQROnLgY8ll7En9NYoXibgg3C6NK/UDHNtUgWJ" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script id="graph-data" type="application/json">{safe_json_for_script(payload)}</script>
<script>{GRAPH_JS}</script>
"""
    return page(title, "Graph", body, search_index, active="graph", depth=1)


def render_source_page(title: str, source_path: str, graph: ResearchGraph, context: SiteContext, search_index: List[Dict[str, object]]) -> str:
    nodes = sorted([node for node in graph.nodes if node.source_path == source_path], key=lambda n: (n.type.value, n.name))
    local_edges = [edge for edge in graph.edges if edge.source in {n.id for n in nodes} or edge.target in {n.id for n in nodes}]
    excerpt = render_source_excerpt(source_path)
    body = f"""
<section class="panel">
  <p class="eyebrow">source evidence</p>
  <h1>{esc(short_path(source_path))}</h1>
  <p class="lead"><code>{esc(source_path)}</code></p>
  <div class="stats"><span>{len(nodes)} nodes</span><span>{len(local_edges)} related edges</span><span>{len({n.type.value for n in nodes})} types</span></div>
</section>
<section class="panel"><h2>Source preview</h2>{excerpt}</section>
<section class="grid two">
  <article class="panel"><h2>Nodes from this source</h2>{render_node_table(nodes, context, limit=250, depth=1)}</article>
  <article class="panel"><h2>Type mix</h2>{render_bar_list(Counter(node.type.value for node in nodes))}</article>
</section>
<section class="panel"><h2>Edges touching this source</h2>{render_edge_table(local_edges[:250], context, depth=1)}</section>
"""
    return page(title, short_path(source_path), body, search_index, active="sources", depth=1)


def render_node_page(title: str, node: ResearchNode, context: SiteContext, search_index: List[Dict[str, object]]) -> str:
    out_edges = context.outgoing.get(node.id, [])
    in_edges = context.incoming.get(node.id, [])
    metadata = json.dumps(node.metadata, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    body = f"""
<section class="panel node-hero">
  <p class="eyebrow">{esc(node.type.value)} · {node_kind(node)}</p>
  <h1>{esc(node.name)}</h1>
  <p class="lead">{esc(node.description or node.source_path or node.id)}</p>
  <div class="meta-grid">
    <div><span>ID</span><code>{esc(node.id)}</code></div>
    <div><span>Source</span><code>{esc(node.source_path or 'unknown')}</code></div>
    <div><span>Degree</span><strong>{len(out_edges) + len(in_edges)}</strong></div>
  </div>
</section>
<section class="grid two">
  <article class="panel"><h2>Outgoing edges</h2>{render_edge_list(out_edges, context, outgoing=True, depth=1)}</article>
  <article class="panel"><h2>Incoming edges</h2>{render_edge_list(in_edges, context, outgoing=False, depth=1)}</article>
</section>
<section class="panel"><h2>Metadata</h2><pre><code>{esc(metadata)}</code></pre></section>
"""
    return page(title, node.name, body, search_index, active="nodes", depth=1)


def page(site_title: str, page_title: str, body: str, search_index: List[Dict[str, object]], active: str, depth: int) -> str:
    prefix = "../" * depth
    nav = [
        ("home", "Home", f"{prefix}index.html"),
        ("nodes", "Nodes", f"{prefix}nodes/index.html"),
        ("sources", "Sources", f"{prefix}sources/index.html"),
        ("graph", "Graph", f"{prefix}graph/index.html"),
        ("llms", "llms.txt", f"{prefix}llms.txt"),
    ]
    nav_html = "".join(f"<a class='{ 'active' if key == active else '' }' href='{href}'>{label}</a>" for key, label, href in nav)
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(page_title)} · {esc(site_title)}</title>
  <link rel="stylesheet" href="{prefix}assets/style.css">
</head>
<body>
  <div class="progress" id="progress"></div>
  <header class="topbar"><a class="brand" href="{prefix}index.html">LLM-Wiki</a><nav>{nav_html}</nav><button class="search-button" data-open-search>Search / ⌘K</button><button class="theme" id="theme-toggle">Theme</button></header>
  <main class="container">{body}</main>
  <div class="palette" id="palette" hidden><div class="palette-box"><input id="search" aria-label="Command palette" placeholder="Search nodes, source files, types…"><div id="results"></div><p class="muted small">Keyboard: / or ⌘K search · Esc close · click a result to open</p></div></div>
  <script id="search-data" type="application/json">{safe_json_for_script(search_index)}</script>
  <script src="{prefix}assets/app.js"></script>
</body>
</html>
"""


def render_stats(context: SiteContext, nodes: int, edges: int) -> str:
    cards = [stat("Nodes", nodes), stat("Edges", edges), stat("Sources", len([k for k in context.source_counts if k != "unknown"])), stat("Types", len(context.type_counts))]
    for key, value in sorted(context.type_counts.items(), key=lambda item: (-item[1], item[0]))[:8]:
        cards.append(stat(key, value))
    return "".join(cards)


def stat(label: str, value: int) -> str:
    return f"<div class='stat'><b>{value}</b><span>{esc(label)}</span></div>"


def render_node_cards(nodes: Sequence[ResearchNode], context: SiteContext) -> str:
    if not nodes:
        return "<p class='muted'>No nodes yet.</p>"
    return "<div class='cards'>" + "".join(render_node_card(node, context) for node in nodes) + "</div>"


def render_node_card(node: ResearchNode, context: SiteContext) -> str:
    degree = len(context.outgoing.get(node.id, [])) + len(context.incoming.get(node.id, []))
    return f"<a class='node-card' href='{rel_href(node_href(node.id))}'><span class='badge'>{esc(node.type.value)}</span><strong>{esc(node.name)}</strong><p>{esc(node.description or node.source_path or node.id)}</p><small>{degree} links</small></a>"


def render_node_table(nodes: Sequence[ResearchNode], context: SiteContext, limit: int = 50, depth: int = 0) -> str:
    if not nodes:
        return "<p class='muted'>No nodes.</p>"
    rows = []
    link_prefix = asset_prefix(depth)
    for node in nodes[:limit]:
        degree = len(context.outgoing.get(node.id, [])) + len(context.incoming.get(node.id, []))
        source_html = source_link(node.source_path, depth) if node.source_path else ""
        rows.append(f"<tr><td><a href='{link_prefix}{rel_href(node_href(node.id))}'>{esc(node.name)}</a></td><td><span class='badge'>{esc(node.type.value)}</span></td><td>{degree}</td><td>{source_html}</td></tr>")
    return "<table><thead><tr><th>Name</th><th>Type</th><th>Links</th><th>Source</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def source_link(source: str, depth: int = 0) -> str:
    return f"<a href='{asset_prefix(depth)}{rel_href(source_href(source))}'><code>{esc(source)}</code></a>"


def render_source_table(rows: Sequence[tuple[str, int]], limit: int = 50, depth: int = 0) -> str:
    if not rows:
        return "<p class='muted'>No source paths recorded.</p>"
    body = []
    for source, count in rows[:limit]:
        label = source if source != "unknown" else "Unknown / generated"
        if source != "unknown":
            label_html = source_link(source, depth)
        else:
            label_html = f"<code>{esc(label)}</code>"
        body.append(f"<tr><td>{label_html}</td><td>{count}</td></tr>")
    return "<table><thead><tr><th>Source path</th><th>Nodes</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"


def render_source_excerpt(source_path: str, max_lines: int = 80) -> str:
    path = Path(source_path)
    if not path.exists() or not path.is_file():
        return "<p class='muted'>Source file is not available on this machine.</p>"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"<p class='muted'>Could not read source: {esc(str(exc))}</p>"
    lines = text.splitlines()[:max_lines]
    if not lines:
        return "<p class='muted'>Source file is empty.</p>"
    body = "\n".join(f"{idx + 1:>4} | {line}" for idx, line in enumerate(lines))
    suffix = "\n…" if len(text.splitlines()) > max_lines else ""
    return f"<pre class='source-preview'><code>{esc(body + suffix)}</code></pre>"


def render_bar_list(counts: Mapping[str, int]) -> str:
    if not counts:
        return "<p class='muted'>No data.</p>"
    max_value = max(counts.values()) or 1
    items = []
    for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:24]:
        width = max(3, int(value / max_value * 100))
        items.append(f"<div class='bar-row'><span>{esc(key)}</span><div><i style='width:{width}%'></i></div><b>{value}</b></div>")
    return "<div class='bars'>" + "".join(items) + "</div>"


def render_edge_list(edges: Sequence[ResearchEdge], context: SiteContext, outgoing: bool, depth: int = 0) -> str:
    if not edges:
        return "<p class='muted'>No edges.</p>"
    rows = []
    link_prefix = asset_prefix(depth)
    for edge in edges[:100]:
        other_id = edge.target if outgoing else edge.source
        other = context.nodes_by_id.get(other_id)
        name = other.name if other else other_id
        rows.append(f"<li><span class='badge'>{esc(edge.type)}</span> <a href='{link_prefix}{rel_href(node_href(other_id))}'>{esc(name)}</a></li>")
    return "<ul class='edge-list'>" + "".join(rows) + "</ul>"


def render_edge_table(edges: Sequence[ResearchEdge], context: SiteContext, depth: int = 0) -> str:
    if not edges:
        return "<p class='muted'>No edges.</p>"
    rows = []
    link_prefix = asset_prefix(depth)
    for edge in edges:
        source = context.nodes_by_id.get(edge.source)
        target = context.nodes_by_id.get(edge.target)
        source_name = source.name if source else edge.source
        target_name = target.name if target else edge.target
        rows.append(
            f"<tr><td><a href='{link_prefix}{rel_href(node_href(edge.source))}'>{esc(source_name)}</a></td>"
            f"<td><span class='badge'>{esc(edge.type)}</span></td>"
            f"<td><a href='{link_prefix}{rel_href(node_href(edge.target))}'>{esc(target_name)}</a></td></tr>"
        )
    return "<table><thead><tr><th>Source</th><th>Relation</th><th>Target</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def render_svg_graph(nodes: Sequence[ResearchNode], context: SiteContext) -> str:
    if not nodes:
        return "<p class='muted'>No graph nodes.</p>"
    ids = [node.id for node in nodes]
    positions = {}
    cols = 8
    for idx, node_id in enumerate(ids):
        x = 80 + (idx % cols) * 135
        y = 80 + (idx // cols) * 105
        positions[node_id] = (x, y)
    lines = []
    for node in nodes:
        x1, y1 = positions[node.id]
        for edge in context.outgoing.get(node.id, [])[:8]:
            if edge.target in positions:
                x2, y2 = positions[edge.target]
                lines.append(f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' />")
    circles = []
    for node in nodes:
        x, y = positions[node.id]
        r = 10 + min(12, len(context.outgoing.get(node.id, [])) + len(context.incoming.get(node.id, [])))
        circles.append(f"<a href='../{rel_href(node_href(node.id))}'><circle cx='{x}' cy='{y}' r='{r}' class='{node_kind(node)}'><title>{esc(node.name)}</title></circle><text x='{x + r + 4}' y='{y + 4}'>{esc(shorten(node.name, 18))}</text></a>")
    height = 130 + ((len(nodes) + cols - 1) // cols) * 105
    return f"<svg class='graph-svg' viewBox='0 0 1160 {height}' role='img' aria-label='High degree graph'>{''.join(lines)}{''.join(circles)}</svg>"


def render_llms_txt(title: str, graph: ResearchGraph, context: SiteContext) -> str:
    lines = [f"# {title}", "", "Compiled LLM-Wiki site for humans and AI agents.", "", "## Entry points", "", "- index.html — dashboard", "- nodes/index.html — typed node browser", "- sources/index.html — source evidence paths", "- graph/index.html — graph overview", "- graph.json — authoritative graph JSON", "- search-index.json — search index", "- llms-full.txt — fuller plain text dump", "", "## Counts", "", f"- nodes: {len(graph.nodes)}", f"- edges: {len(graph.edges)}", ""]
    lines.append("## Top node types")
    for name, count in sorted(context.type_counts.items(), key=lambda item: (-item[1], item[0]))[:20]:
        lines.append(f"- {name}: {count}")
    lines.append("\n## Top nodes")
    for node in sorted(graph.nodes, key=lambda n: len(context.outgoing.get(n.id, [])) + len(context.incoming.get(n.id, [])), reverse=True)[:50]:
        lines.append(f"- {node.name} ({node.type.value}) — {node.source_path or node.id}")
    return "\n".join(lines) + "\n"


def render_llms_full_txt(title: str, graph: ResearchGraph, context: SiteContext) -> str:
    lines = [render_llms_txt(title, graph, context), "\n## All nodes\n"]
    for node in graph.nodes:
        lines.append(f"### {node.name}\n- id: {node.id}\n- type: {node.type.value}\n- source: {node.source_path or ''}\n- description: {node.description or ''}\n")
    return "\n".join(lines)


def render_manifest(graph: ResearchGraph, search_index: List[Dict[str, object]]) -> Dict[str, object]:
    graph_text = graph.to_json(indent=2)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "llm_wiki.frontend.StaticSiteBuilder",
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "search_entries": len(search_index),
        "sha256_graph": hashlib.sha256(graph_text.encode("utf-8")).hexdigest(),
        "assets": ["index.html", "nodes/index.html", "sources/index.html", "graph/index.html", "graph.json", "search-index.json", "llms.txt", "llms-full.txt"],
    }


def node_kind(node: ResearchNode) -> str:
    if node.type.value in DEVELOPMENT_TYPES:
        return "development"
    if node.type.value in RESEARCH_TYPES:
        return "research"
    return "knowledge"


def node_href(node_id: str) -> str:
    return f"nodes/{slug(node_id)}.html"


def rel_href(path: str) -> str:
    return path.replace(" ", "%20")


def short_path(path: str, max_parts: int = 3) -> str:
    parts = [part for part in Path(path).parts if part not in {"/", ""}]
    if not parts:
        return path
    return "/".join(parts[-max_parts:])


def asset_prefix(depth: int) -> str:
    return "../" * depth


def source_href(source_path: str) -> str:
    return f"sources/{slug(source_path)}.html"


def slug(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    if len(safe.encode("utf-8")) > 96:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        safe = safe.encode("utf-8")[:80].decode("utf-8", errors="ignore").strip("-") + "-" + digest
    return safe or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def shorten(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def safe_json_for_script(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


GRAPH_JS = r"""
(function(){
  const raw = document.getElementById('graph-data');
  const payload = JSON.parse(raw ? raw.textContent : '{"nodes":[],"edges":[]}');
  const container = document.getElementById('network');
  const offline = document.getElementById('offline-notice');
  if (!container || typeof vis === 'undefined') { if (offline) offline.classList.add('show'); return; }
  if (offline) offline.remove();
  const baseColors = {};
  const nodes = new vis.DataSet(payload.nodes.map(n => {
    const orphan = (n.in_degree || 0) === 0;
    const color = n.color || '#7c3aed';
    baseColors[n.id] = {background: color, border: orphan ? '#ef4444' : color};
    return {id:n.id,label:n.label,group:n.type,value:Math.max(n.degree||1,1),path:n.path,site_url:n.site_url,type:n.type,
      color:{background:color,border:orphan?'#ef4444':color,highlight:{background:'#facc15',border:'#facc15'}},borderWidth:orphan?3:1,
      title:`${n.type} · ${n.in_degree||0} inbound · ${n.out_degree||0} outbound\n${n.path||n.id}`};
  }));
  const edges = new vis.DataSet(payload.edges.map(e => ({from:e.source,to:e.target,arrows:'to',title:e.type||'',color:{color:'rgba(148,163,184,.45)'}})));
  const network = new vis.Network(container,{nodes,edges},{nodes:{shape:'dot',font:{color:getCss('--text'),size:12,face:'system-ui'},scaling:{min:8,max:34,label:{enabled:true,min:10,max:18}}},edges:{smooth:{enabled:true,type:'dynamic'}},physics:{barnesHut:{gravitationalConstant:-4200,springLength:130,springConstant:.035},stabilization:{iterations:220}},interaction:{hover:true,tooltipDelay:120,navigationButtons:true,keyboard:true}});
  document.getElementById('fit-graph')?.addEventListener('click',()=>network.fit({animation:true}));
  document.getElementById('graph-search')?.addEventListener('input', e => {
    const q = (e.target.value||'').toLowerCase().trim();
    const update = [];
    nodes.forEach(n => { const hit = !q || String(n.label).toLowerCase().includes(q) || String(n.id).toLowerCase().includes(q) || String(n.type).toLowerCase().includes(q); update.push({id:n.id,color: hit ? baseColors[n.id] : {background:'rgba(100,100,100,.12)',border:'rgba(100,100,100,.22)'}}); });
    nodes.update(update);
  });
  let clustered=false;
  document.getElementById('cluster-toggle')?.addEventListener('click',()=>{
    clustered=!clustered; document.getElementById('cluster-mode').textContent=clustered?'type':'off';
    if(clustered){ [...new Set(payload.nodes.map(n=>n.type))].forEach(t=>{ try{ network.cluster({joinCondition:n=>n.group===t,clusterNodeProperties:{id:'cluster:'+t,label:t+' ('+payload.nodes.filter(x=>x.type===t).length+')',color:{background:(payload.nodes.find(x=>x.type===t)||{}).color||'#64748b'}}}); }catch(e){} }); }
    else { payload.nodes.forEach(n=>{ const id='cluster:'+n.type; if(network.isCluster(id)) network.openCluster(id); }); }
  });
  network.on('click', params => { if(!params.nodes?.length) return; const n=nodes.get(params.nodes[0]); if(n?.site_url) window.open(n.site_url,'_blank','noopener'); });
  const menu=document.getElementById('ctx-menu'), target=document.getElementById('ctx-target'); let ctx=null;
  network.on('oncontext', params => { params.event.preventDefault(); const id=network.getNodeAt(params.pointer.DOM); if(!id||!menu) return; ctx=nodes.get(id); target.textContent=ctx.label||ctx.id; menu.style.left=Math.min(params.event.clientX,innerWidth-260)+'px'; menu.style.top=Math.min(params.event.clientY,innerHeight-180)+'px'; menu.classList.add('show'); });
  document.addEventListener('click',e=>{ if(menu && !menu.contains(e.target)) menu.classList.remove('show'); });
  menu?.addEventListener('click', async e => { const b=e.target.closest('button[data-action]'); if(!b||!ctx) return; menu.classList.remove('show'); if(b.dataset.action==='open'&&ctx.site_url) window.open(ctx.site_url,'_blank','noopener'); if(b.dataset.action==='copy-id') await navigator.clipboard?.writeText(String(ctx.id)); if(b.dataset.action==='neighbours'){ const keep=new Set([ctx.id]); payload.edges.forEach(edge=>{ if(edge.source===ctx.id) keep.add(edge.target); if(edge.target===ctx.id) keep.add(edge.source); }); const update=[]; nodes.forEach(n=>update.push({id:n.id,color:keep.has(n.id)?baseColors[n.id]:{background:'rgba(100,100,100,.12)',border:'rgba(100,100,100,.22)'}})); nodes.update(update); } });
  function getCss(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#e5e7eb'; }
})();
"""


CSS = r"""
:root{--bg:#f8fafc;--panel:#ffffff;--panel2:#f1f5f9;--text:#0f172a;--muted:#64748b;--border:#dbe3ee;--accent:#7c3aed;--accent2:#0f766e;--danger:#dc2626;--shadow:0 16px 45px rgba(15,23,42,.10);--font:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;--mono:'JetBrains Mono','SFMono-Regular',ui-monospace,monospace}[data-theme=dark]{--bg:#090b16;--panel:#111827;--panel2:#0f172a;--text:#e5e7eb;--muted:#94a3b8;--border:#273245;--accent:#a78bfa;--accent2:#2dd4bf;--shadow:0 20px 70px rgba(0,0,0,.35)}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at top left,rgba(124,58,237,.18),transparent 34rem),var(--bg);color:var(--text);font-family:var(--font);line-height:1.55}.progress{position:fixed;top:0;left:0;height:3px;background:var(--accent);z-index:50;width:0}.topbar{position:sticky;top:0;z-index:20;display:flex;gap:16px;align-items:center;padding:12px 22px;background:color-mix(in srgb,var(--bg) 88%,transparent);border-bottom:1px solid var(--border);backdrop-filter:blur(18px)}.brand{font-weight:900;color:var(--text);text-decoration:none}.topbar nav{display:flex;gap:10px;flex:1}.topbar nav a,.button,.search-button,.theme{border:1px solid var(--border);border-radius:999px;padding:8px 12px;color:var(--text);background:var(--panel);text-decoration:none;cursor:pointer}.topbar nav a.active,.button.primary{background:var(--accent);border-color:var(--accent);color:white}.container{max-width:1220px;margin:0 auto;padding:28px 20px 64px}.panel{background:color-mix(in srgb,var(--panel) 95%,transparent);border:1px solid var(--border);border-radius:18px;padding:22px;box-shadow:var(--shadow);margin-bottom:20px}.hero{padding:42px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;color:var(--accent);font-weight:800;font-size:.78rem}.lead{font-size:1.08rem;color:var(--muted);max-width:850px}h1{font-size:clamp(2.1rem,5vw,4rem);line-height:1;margin:.2em 0}h2{margin-top:0}.actions,.filter-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:12px;margin-bottom:20px}.stat{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:16px}.stat b{font-size:1.8rem;display:block}.stat span,.muted{color:var(--muted)}.small{font-size:.85rem}.grid.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}.cards{display:grid;gap:12px}.node-card{display:block;padding:14px;border:1px solid var(--border);border-radius:14px;background:var(--panel2);text-decoration:none;color:var(--text)}.node-card strong{display:block;margin:6px 0}.node-card p{margin:0;color:var(--muted);font-size:.92rem}.node-card small{color:var(--accent2)}.badge{display:inline-block;font:800 .72rem var(--mono);padding:3px 7px;border-radius:999px;background:color-mix(in srgb,var(--accent) 15%,transparent);color:var(--accent);white-space:nowrap}.count{color:var(--muted);font-size:1rem}table{width:100%;border-collapse:collapse;font-size:.92rem}th,td{padding:10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}td code,pre{font-family:var(--mono);font-size:.86rem;white-space:pre-wrap}.bars{display:grid;gap:10px}.bar-row{display:grid;grid-template-columns:170px 1fr 56px;gap:10px;align-items:center}.bar-row div{height:10px;background:var(--panel2);border-radius:999px;overflow:hidden}.bar-row i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2))}.meta-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.meta-grid div{border:1px solid var(--border);border-radius:12px;padding:12px;background:var(--panel2)}.meta-grid span{display:block;color:var(--muted);font-size:.8rem}.edge-list{list-style:none;margin:0;padding:0}.edge-list li{padding:8px 0;border-bottom:1px solid var(--border)}.palette{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:40;padding:7vh 18px}.palette-box{max-width:850px;margin:0 auto;background:var(--panel);border:1px solid var(--border);border-radius:18px;padding:16px;box-shadow:var(--shadow)}#search,#type-filter{width:100%;padding:14px;border-radius:12px;border:1px solid var(--border);background:var(--panel2);color:var(--text);font-size:1rem}.result{display:block;padding:12px;border-bottom:1px solid var(--border);text-decoration:none;color:var(--text)}.result p{margin:4px 0 0;color:var(--muted)}.graph-svg{width:100%;height:auto;min-height:420px}.graph-svg line{stroke:var(--border);stroke-width:1.2}.graph-svg circle{fill:var(--accent);opacity:.85}.graph-svg circle.development{fill:var(--accent2)}.graph-svg text{fill:var(--text);font-size:12px}a{color:var(--accent)}@media(max-width:760px){.topbar{flex-wrap:wrap}.topbar nav{order:3;width:100%;overflow:auto}.hero{padding:24px}.grid.two{grid-template-columns:1fr}.bar-row{grid-template-columns:1fr}.bar-row b{text-align:right}}@media print{.topbar,.palette,.progress{display:none}.panel{box-shadow:none}}
"""

JS = r"""
const data = JSON.parse(document.getElementById('search-data')?.textContent || '[]');
const palette = document.getElementById('palette');
const input = document.getElementById('search');
const results = document.getElementById('results');
function openSearch(){ palette.hidden=false; setTimeout(()=>input?.focus(), 0); renderSearch(data.slice(0,12)); }
function closeSearch(){ palette.hidden=true; }
function renderSearch(items){ if(!results) return; results.innerHTML = items.slice(0,40).map(x => `<a class="result" href="${x.href}"><span class="badge">${escapeHtml(x.type)}</span> <strong>${escapeHtml(x.title)}</strong><p>${escapeHtml(x.description || x.source_path || x.id)}</p></a>`).join('') || '<p class="muted">No matches.</p>'; }
function doSearch(){ const q=(input?.value || '').toLowerCase().trim(); if(!q){renderSearch(data.slice(0,12)); return;} const terms=q.split(/\s+/).filter(Boolean); renderSearch(data.filter(x => terms.every(t => String(x.text||'').toLowerCase().includes(t)))); }
function escapeHtml(s){ return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
document.querySelectorAll('[data-open-search]').forEach(b => b.addEventListener('click', openSearch));
input?.addEventListener('input', doSearch);
palette?.addEventListener('click', e => { if(e.target === palette) closeSearch(); });
document.addEventListener('keydown', e => { if(e.key === '/' && !['INPUT','TEXTAREA'].includes(document.activeElement?.tagName||'')){ e.preventDefault(); openSearch(); } if((e.metaKey||e.ctrlKey) && e.key.toLowerCase()==='k'){ e.preventDefault(); openSearch(); } if(e.key==='Escape') closeSearch(); });
const theme = document.getElementById('theme-toggle');
theme?.addEventListener('click', () => { const next=document.documentElement.dataset.theme==='dark'?'light':'dark'; document.documentElement.dataset.theme=next; localStorage.setItem('llmwiki-theme', next); });
const saved=localStorage.getItem('llmwiki-theme'); if(saved) document.documentElement.dataset.theme=saved;
window.addEventListener('scroll', () => { const h=document.documentElement.scrollHeight-window.innerHeight; document.getElementById('progress').style.width = h > 0 ? `${(window.scrollY/h)*100}%` : '0%'; });
const typeFilter=document.getElementById('type-filter');
typeFilter?.addEventListener('input', () => { const q=typeFilter.value.toLowerCase(); document.querySelectorAll('.type-section').forEach(sec => { sec.style.display = sec.innerText.toLowerCase().includes(q) ? '' : 'none'; }); });
"""
