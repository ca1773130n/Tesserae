"""Reporting helpers for ResearchGraph outputs."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Set

from .research_graph import ResearchGraph, ResearchNodeType

CLAIM_TYPES = {
    ResearchNodeType.CLAIM,
    ResearchNodeType.CONTRIBUTION_CLAIM,
    ResearchNodeType.PERFORMANCE_CLAIM,
    ResearchNodeType.COMPARISON_CLAIM,
    ResearchNodeType.LIMITATION_CLAIM,
    ResearchNodeType.CAUSAL_CLAIM,
}


class GraphReporter:
    def summarize(self, graph: ResearchGraph) -> Dict[str, object]:
        node_types = Counter(node.type.value for node in graph.nodes)
        edge_types = Counter(edge.type for edge in graph.edges)
        degree = defaultdict(int)
        for edge in graph.edges:
            degree[edge.source] += 1
            degree[edge.target] += 1
        nodes_by_id = {node.id: node for node in graph.nodes}
        # Filter dangling ids (edge endpoints with no node — e.g. a stale
        # reference) BEFORE sorting: the sort key dereferences nodes_by_id, so
        # a post-sort `if` filter is too late and KeyErrors on the dangling id.
        ranked = sorted(
            ((node_id, count) for node_id, count in degree.items() if node_id in nodes_by_id),
            key=lambda item: (-item[1], nodes_by_id[item[0]].name),
        )
        top_degree_nodes = [
            {"id": node_id, "name": nodes_by_id[node_id].name, "type": nodes_by_id[node_id].type.value, "degree": count}
            for node_id, count in ranked
        ][:20]
        trends = [
            {"id": node.id, "name": node.name, "source_count": node.metadata.get("source_count", 0)}
            for node in graph.nodes
            if node.type == ResearchNodeType.TREND
        ]
        trends.sort(key=lambda item: (-int(item.get("source_count") or 0), str(item["name"])))

        paper_dates = Counter(
            str(node.metadata.get("analysis_date"))
            for node in graph.nodes
            if node.type == ResearchNodeType.PAPER and node.metadata.get("analysis_date")
        )
        claim_nodes = [node for node in graph.nodes if node.type in CLAIM_TYPES]
        supported_claim_ids: Set[str] = {edge.source for edge in graph.edges if edge.type == "evidenced_by"}
        claims_without_evidence = [
            {"id": node.id, "name": node.name, "type": node.type.value}
            for node in sorted(claim_nodes, key=lambda item: item.name)
            if node.id not in supported_claim_ids
        ]
        orphan_nodes = [
            {"id": node.id, "name": node.name, "type": node.type.value}
            for node in sorted(graph.nodes, key=lambda item: (item.type.value, item.name))
            if node.id not in degree and node.type not in {ResearchNodeType.PAPER, ResearchNodeType.SOURCE_DOCUMENT}
        ][:50]
        aliases = [
            {"id": node.id, "name": node.name, "type": node.type.value, "alias_count": len(node.aliases), "aliases": node.aliases}
            for node in graph.nodes
            if node.aliases
        ]
        aliases.sort(key=lambda item: (-int(item["alias_count"]), str(item["name"])))

        return {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "node_types": dict(sorted(node_types.items())),
            "edge_types": dict(sorted(edge_types.items())),
            "top_degree_nodes": top_degree_nodes,
            "trends": trends,
            "papers_by_analysis_date": dict(sorted(paper_dates.items())),
            "claim_evidence": {
                "total_claims": len(claim_nodes),
                "supported_claims": len(claim_nodes) - len(claims_without_evidence),
                "unsupported_claims": len(claims_without_evidence),
            },
            "claims_without_evidence": claims_without_evidence[:50],
            "orphan_nodes": orphan_nodes,
            "alias_heavy_nodes": aliases[:20],
        }

    def render_markdown(self, report: Dict[str, object]) -> str:
        lines = ["# Research Graph Report", "", f"node_count: {report['node_count']}", f"edge_count: {report['edge_count']}", ""]
        lines.extend(["## Node Types", ""])
        for name, count in dict(report["node_types"]).items():
            lines.append(f"- {name}: {count}")
        lines.extend(["", "## Edge Types", ""])
        for name, count in dict(report["edge_types"]).items():
            lines.append(f"- {name}: {count}")
        lines.extend(["", "## Papers by Analysis Date", ""])
        dates = dict(report.get("papers_by_analysis_date", {}))
        if dates:
            for date, count in dates.items():
                lines.append(f"- {date}: {count}")
        else:
            lines.append("_None._")
        lines.extend(["", "## Claim Evidence Coverage", ""])
        coverage = dict(report.get("claim_evidence", {}))
        lines.append(f"- total_claims: {coverage.get('total_claims', 0)}")
        lines.append(f"- supported_claims: {coverage.get('supported_claims', 0)}")
        lines.append(f"- unsupported_claims: {coverage.get('unsupported_claims', 0)}")
        unsupported = list(report.get("claims_without_evidence", []))
        if unsupported:
            lines.extend(["", "### Claims Without Evidence", ""])
            for claim in unsupported:
                lines.append(f"- {claim['name']} ({claim['type']}) — `{claim['id']}`")
        lines.extend(["", "## Top Degree Nodes", ""])
        for node in list(report["top_degree_nodes"]):
            lines.append(f"- {node['name']} ({node['type']}): {node['degree']}")
        lines.extend(["", "## Trends", ""])
        trends = list(report["trends"])
        if trends:
            for trend in trends:
                lines.append(f"- {trend['name']} — source_count: {trend.get('source_count', 0)}")
        else:
            lines.append("_None._")
        aliases = list(report.get("alias_heavy_nodes", []))
        if aliases:
            lines.extend(["", "## Alias-Heavy Nodes", ""])
            for node in aliases:
                lines.append(f"- {node['name']} ({node['type']}): {node['alias_count']} aliases")
        orphans = list(report.get("orphan_nodes", []))
        if orphans:
            lines.extend(["", "## Orphan Nodes", ""])
            for node in orphans:
                lines.append(f"- {node['name']} ({node['type']}) — `{node['id']}`")
        return "\n".join(lines).rstrip() + "\n"
