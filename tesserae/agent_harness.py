"""Agent-harness exports for popular coding assistants.

The harness is a dependency-free set of context/config files that lets external
coding agents discover the compiled Tesserae graph and its MCP server from a
project workspace. It is intentionally file-based so Claude Code, Codex, Gemini,
Kiro, Cursor, and OpenCode can all consume it without bespoke runtime plugins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .research_graph import ResearchGraph, ResearchNode


SUPPORTED_AGENT_HARNESSES = ["claude-code", "codex", "gemini", "kiro", "cursor", "opencode"]


@dataclass(frozen=True)
class AgentHarnessAdapter:
    project_name: str = "tesserae_project"

    def write_harness(
        self,
        graph: ResearchGraph,
        output_dir: str | Path,
        mcp_command: str = "python3",
        mcp_args: Optional[Sequence[str]] = None,
        targets: Optional[Iterable[str]] = None,
        topic: Optional[str] = None,
    ) -> List[Path]:
        selected = list(targets or SUPPORTED_AGENT_HARNESSES)
        unknown = sorted(set(selected) - set(SUPPORTED_AGENT_HARNESSES))
        if unknown:
            raise ValueError(f"Unsupported agent harness target(s): {', '.join(unknown)}")

        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        args = list(mcp_args or ["-m", "tesserae.mcp_server", "--graph", ".tesserae/graph.json"])
        # The harness lives under the project's ``.tesserae/`` artifacts dir, so the
        # project root is the parent of ``output_dir`` (best-effort; topic scoping
        # falls back to ``node.description`` when this isn't a real project root).
        project_root = root.parent if topic else None
        summary = render_harness_context(
            self.project_name, graph, mcp_command, args, topic=topic, project_root=project_root
        )
        manifest = {
            "project_name": self.project_name,
            "supported_targets": SUPPORTED_AGENT_HARNESSES,
            "selected_targets": selected,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "mcp": {"command": mcp_command, "args": args},
            "notes": "Copy or symlink the target-specific files into the project root for the corresponding agent.",
        }

        written: List[Path] = []
        common = root / "TESSERAE.md"
        common.write_text(summary, encoding="utf-8")
        written.append(common)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(manifest_path)

        for target in selected:
            writer = TARGET_WRITERS[target]
            written.extend(writer(root, summary, self.project_name, mcp_command, args))
        return written


def render_harness_context(
    project_name: str,
    graph: ResearchGraph,
    mcp_command: str,
    mcp_args: Sequence[str],
    topic: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> str:
    if topic:
        from .context_compiler import compile_context

        node_index = {n.id: n for n in graph.nodes}
        bundle = compile_context(
            graph,
            str(project_root) if project_root is not None else None,
            query=topic,
            depth=2,
            budget=8_000,
        )
        # PITFALL 6: preserve PPR/compile_context rank order — do NOT re-sort.
        top_nodes = [node_index[nid] for nid in bundle.selected_nodes if nid in node_index]
        if not top_nodes:  # topic matched nothing -> graceful static fallback
            top_nodes = sorted(graph.nodes, key=node_sort_key)[:12]
    else:
        top_nodes = sorted(graph.nodes, key=node_sort_key)[:12]
    lines = [
        f"# Tesserae Harness: {project_name}",
        "",
        "This project has a compiled Tesserae research graph. Treat markdown pages as a human-readable projection; the graph JSON is authoritative.",
        "",
        "## Artifacts",
        "",
        "- `.tesserae/graph.json` — authoritative typed ResearchGraph",
        "- `.tesserae/markdown_projection/` — Obsidian/VS Code markdown projection",
        "- `.tesserae/obsidian_vault/` — generated Obsidian vault",
        "- `.tesserae/temporal_facts.jsonl` — temporal/provenance fact projection",
        "- `.tesserae/graphiti_episodes.jsonl` — Graphiti-compatible episode export",
        "- `.tesserae/cognee_bundle/` — Cognee JSONL bundle",
        "",
        "## MCP server",
        "",
        "Use the local MCP server to query the graph:",
        "",
        "```text",
        f"command: {mcp_command}",
        f"args: {json.dumps(list(mcp_args), ensure_ascii=False)}",
        "```",
        "",
        "Expected MCP tools: `schema`, `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`.",
        "",
        "## Graph summary",
        "",
        f"- Nodes: {len(graph.nodes)}",
        f"- Edges: {len(graph.edges)}",
        "",
        "## Representative nodes",
        "",
    ]
    for node in top_nodes:
        lines.append(f"- **{node.name}** (`{node.type.value}`) — {node.description or node.source_path or node.id}")
    if not top_nodes:
        lines.append("_No nodes yet. Run `python3 -m tesserae.cli compile` first._")
    lines.extend([
        "",
        "## Agent instructions",
        "",
        "- Prefer MCP graph queries before grep-style rediscovery.",
        "- Preserve the controlled ontology; do not invent node or edge types outside the Tesserae schema.",
        "- Keep markdown projection generated; update sources and re-run compile instead of hand-editing generated pages.",
        "- When adding code, run the project tests before reporting success.",
        "",
    ])
    return "\n".join(lines)


def node_sort_key(node: ResearchNode) -> tuple:
    return (node.type.value, node.name.lower())


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def claude_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    settings = {"mcpServers": {project_name: {"command": command, "args": list(args)}}}
    return [
        write_text(root / "claude" / "CLAUDE.md", summary),
        write_text(root / "claude" / ".claude" / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2) + "\n"),
    ]


def codex_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    config = f"""# Add to ~/.codex/config.toml or project Codex config if supported.\n[mcp_servers.{project_name}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(list(args))}\n"""
    return [write_text(root / "codex" / "AGENTS.md", summary), write_text(root / "codex" / "mcp.toml", config)]


def gemini_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    settings = {"mcpServers": {project_name: {"command": command, "args": list(args)}}}
    return [write_text(root / "gemini" / "GEMINI.md", summary), write_text(root / "gemini" / ".gemini" / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2) + "\n")]


def kiro_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    mcp = {"mcpServers": {project_name: {"command": command, "args": list(args)}}}
    return [write_text(root / "kiro" / ".kiro" / "steering" / "tesserae.md", summary), write_text(root / "kiro" / ".kiro" / "settings" / "mcp.json", json.dumps(mcp, ensure_ascii=False, indent=2) + "\n")]


def cursor_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    rule = "---\nalwaysApply: true\n---\n\n" + summary
    mcp = {"mcpServers": {project_name: {"command": command, "args": list(args)}}}
    return [write_text(root / "cursor" / ".cursor" / "rules" / "tesserae.mdc", rule), write_text(root / "cursor" / ".cursor" / "mcp.json", json.dumps(mcp, ensure_ascii=False, indent=2) + "\n")]


def opencode_writer(root: Path, summary: str, project_name: str, command: str, args: Sequence[str]) -> List[Path]:
    config = {"mcp": {project_name: {"type": "local", "command": [command, *list(args)]}}}
    return [write_text(root / "opencode" / "AGENTS.md", summary), write_text(root / "opencode" / "opencode.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")]


TARGET_WRITERS = {
    "claude-code": claude_writer,
    "codex": codex_writer,
    "gemini": gemini_writer,
    "kiro": kiro_writer,
    "cursor": cursor_writer,
    "opencode": opencode_writer,
}
