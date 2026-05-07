"""Interactive setup helpers for project-local LLM-Wiki workspaces."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .project import ProjectWiki, sanitize_server_name


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"


@dataclass
class SetupPlan:
    project_root: Path
    name: str
    source_kind: str = "Repository"
    sources: List[str] = field(default_factory=list)
    external_tools: List[dict] = field(default_factory=list)
    run_external_tools: bool = False


@dataclass
class SetupResult:
    wiki: ProjectWiki
    config_path: Path
    ran_tools: List[dict] = field(default_factory=list)


def _rel(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def discover_default_sources(project_root: str | Path) -> List[str]:
    root = Path(project_root).resolve()
    candidates = ["README.md", "docs", "src", "lib", "app", "packages", "data"]
    return [item for item in candidates if (root / item).exists()]


def understand_anything_artifact(project_root: str | Path) -> Optional[str]:
    root = Path(project_root).resolve()
    artifact = root / ".understand-anything" / "knowledge-graph.json"
    return _rel(root, artifact) if artifact.exists() else None


def understand_anything_projection_path() -> str:
    return ".llm-wiki/external/understand-anything.md"


def build_setup_plan(
    project_root: str | Path,
    *,
    name: Optional[str] = None,
    source_kind: str = "Repository",
    sources: Optional[Iterable[str | Path]] = None,
    include_understand_anything: bool = False,
    run_understand_anything: bool = False,
    understand_anything_command: Optional[str] = None,
) -> SetupPlan:
    root = Path(project_root).resolve()
    source_list = [str(source) for source in sources] if sources is not None else discover_default_sources(root)
    external_tools: List[dict] = []

    ua_artifact = understand_anything_artifact(root)
    if include_understand_anything or ua_artifact:
        artifact = ua_artifact or ".understand-anything/knowledge-graph.json"
        projection = understand_anything_projection_path()
        if projection not in source_list:
            source_list.append(projection)
        external_tools.append(
            {
                "id": "understand-anything",
                "name": "Understand Anything",
                "artifact": artifact,
                "source": projection,
                "refresh_command": understand_anything_command or "",
                "auto_refresh": bool(run_understand_anything and understand_anything_command),
                "enabled": True,
            }
        )

    return SetupPlan(
        project_root=root,
        name=name or sanitize_server_name(root.name),
        source_kind=source_kind,
        sources=source_list,
        external_tools=external_tools,
        run_external_tools=run_understand_anything,
    )


def _paint(text: str, color: str, enabled: bool) -> str:
    return f"{color}{text}{RESET}" if enabled else text


def render_setup_summary(plan: SetupPlan, *, color: bool = True) -> str:
    lines = [
        _paint("LLM-Wiki setup", BOLD + CYAN, color),
        f"Project: {_paint(str(plan.project_root), DIM, color)}",
        f"Name:    {_paint(plan.name, GREEN, color)}",
        f"Kind:    {_paint(plan.source_kind, MAGENTA, color)}",
        "",
        _paint("Sources", BOLD, color),
    ]
    if plan.sources:
        lines.extend(f"  {_paint('✓', GREEN, color)} {source}" for source in plan.sources)
    else:
        lines.append(f"  {_paint('!', YELLOW, color)} no sources selected yet")
    lines.append("")
    lines.append(_paint("External tools", BOLD, color))
    if plan.external_tools:
        for tool in plan.external_tools:
            command = tool.get("refresh_command") or "configure later"
            source = tool.get("source") or tool.get("artifact")
            lines.append(f"  {_paint('◆', CYAN, color)} {tool['name']} → {source} ({command})")
    else:
        lines.append(f"  {_paint('·', DIM, color)} none selected")
    return "\n".join(lines) + "\n"


def _ask_yes_no(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{suffix}] ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _ask_list(prompt: str, default: Sequence[str]) -> List[str]:
    rendered_default = ", ".join(default)
    raw = input(f"{prompt} [{rendered_default}] ").strip()
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


def interactive_setup_plan(project_root: str | Path, *, color: bool = True) -> SetupPlan:
    root = Path(project_root).resolve()
    print(_paint("\n◆ LLM-Wiki project setup", BOLD + CYAN, color))
    print(_paint("Choose sources and companion tools. Press Enter to accept defaults.\n", DIM, color))
    default_sources = discover_default_sources(root)
    name_raw = input(f"Wiki name [{sanitize_server_name(root.name)}] ").strip()
    source_kind = input("Source kind [Repository] ").strip() or "Repository"
    sources = _ask_list("Sources", default_sources)
    ua_default = bool(understand_anything_artifact(root))
    include_ua = _ask_yes_no("Use Understand Anything graph artifact?", ua_default)
    ua_command = ""
    run_ua = False
    if include_ua:
        ua_command = input("Refresh command for Understand Anything before compile [leave blank to skip] ").strip()
        run_ua = bool(ua_command) and _ask_yes_no("Run that refresh command now?", False)
    plan = build_setup_plan(
        root,
        name=name_raw or None,
        source_kind=source_kind,
        sources=sources,
        include_understand_anything=include_ua,
        run_understand_anything=run_ua,
        understand_anything_command=ua_command or None,
    )
    print()
    print(render_setup_summary(plan, color=color), end="")
    if not _ask_yes_no("Write this .llm-wiki setup?", True):
        raise KeyboardInterrupt("setup cancelled")
    return plan


def run_external_tools(plan: SetupPlan) -> List[dict]:
    results: List[dict] = []
    if not plan.run_external_tools:
        return results
    return run_tool_configs(plan.project_root, plan.external_tools, only_auto=False)


def run_tool_configs(project_root: str | Path, tools: Sequence[dict], *, only_auto: bool = True) -> List[dict]:
    root = Path(project_root).resolve()
    results: List[dict] = []
    for tool in tools:
        if not tool.get("enabled", True):
            continue
        if only_auto and not tool.get("auto_refresh"):
            continue
        command = str(tool.get("refresh_command") or "").strip()
        if command:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=root,
                text=True,
                capture_output=True,
            )
            result = {
                "id": tool.get("id"),
                "status": "passed" if completed.returncode == 0 else "failed",
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            }
            results.append(result)
            if completed.returncode != 0:
                raise RuntimeError(f"External tool failed: {tool.get('name')} ({completed.returncode})")
        else:
            results.append({"id": tool.get("id"), "status": "skipped", "reason": "no refresh_command"})
        if tool.get("id") == "understand-anything":
            materialized = materialize_understand_anything_source(root, tool)
            results.append({"id": tool.get("id"), "status": "materialized", "source": materialized})
    return results


def materialize_understand_anything_source(project_root: str | Path, tool: dict) -> str:
    root = Path(project_root).resolve()
    artifact = root / str(tool.get("artifact") or ".understand-anything/knowledge-graph.json")
    source = root / str(tool.get("source") or understand_anything_projection_path())
    source.parent.mkdir(parents=True, exist_ok=True)
    if not artifact.exists():
        source.write_text(
            "# Understand Anything Knowledge Graph\n\n"
            f"Expected artifact: `{_rel(root, artifact)}`\n\n"
            "The artifact does not exist yet. Run the configured Understand Anything refresh command, then compile again.\n",
            encoding="utf-8",
        )
        return _rel(root, source)
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except Exception as exc:
        source.write_text(
            "# Understand Anything Knowledge Graph\n\n"
            f"Artifact: `{_rel(root, artifact)}`\n\n"
            f"Could not parse JSON: `{exc}`\n",
            encoding="utf-8",
        )
        return _rel(root, source)
    project = payload.get("project", {}) if isinstance(payload, dict) else {}
    nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
    edges = payload.get("edges", []) if isinstance(payload, dict) else []
    layers = payload.get("layers", []) if isinstance(payload, dict) else []
    tour = payload.get("tour", []) if isinstance(payload, dict) else []
    lines = [
        "# Understand Anything Knowledge Graph",
        "",
        f"Source artifact: `{_rel(root, artifact)}`",
        "",
        "This page is generated by `llm_wiki project setup` / external-tool refresh so LLM-Wiki can compile Understand Anything output as project memory without vendoring Understand Anything.",
        "",
        "## Summary",
        "",
        f"- Project: {project.get('name') or root.name}",
        f"- Description: {project.get('description') or 'n/a'}",
        f"- Languages: {', '.join(project.get('languages') or []) if isinstance(project.get('languages'), list) else project.get('languages', 'n/a')}",
        f"- Frameworks: {', '.join(project.get('frameworks') or []) if isinstance(project.get('frameworks'), list) else project.get('frameworks', 'n/a')}",
        f"- Nodes: {len(nodes) if isinstance(nodes, list) else 0}",
        f"- Edges: {len(edges) if isinstance(edges, list) else 0}",
        f"- Layers: {len(layers) if isinstance(layers, list) else 0}",
        f"- Tour steps: {len(tour) if isinstance(tour, list) else 0}",
        "",
        "## Representative nodes",
        "",
    ]
    for node in (nodes[:40] if isinstance(nodes, list) else []):
        if not isinstance(node, dict):
            continue
        name = node.get("name") or node.get("id") or "unnamed"
        ntype = node.get("type") or "node"
        summary = node.get("summary") or ""
        path = node.get("filePath") or ""
        lines.append(f"- **{name}** (`{ntype}`){f' — `{path}`' if path else ''}: {summary}".rstrip())
    lines.extend(["", "## Representative edges", ""])
    for edge in (edges[:60] if isinstance(edges, list) else []):
        if not isinstance(edge, dict):
            continue
        lines.append(f"- `{edge.get('source')}` --{edge.get('type', 'related_to')}--> `{edge.get('target')}`")
    source.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return _rel(root, source)


def refresh_configured_external_tools(project_root: str | Path, *, only_auto: bool = True) -> List[dict]:
    wiki = ProjectWiki.load(project_root)
    cfg = wiki.config()
    return run_tool_configs(wiki.project_root, cfg.get("external_tools", []), only_auto=only_auto)


def apply_setup_plan(plan: SetupPlan) -> SetupResult:
    ran_tools = run_external_tools(plan)
    wiki = ProjectWiki.init(plan.project_root, name=plan.name, source_kind=plan.source_kind, sources=plan.sources)
    for tool in plan.external_tools:
        if tool.get("id") == "understand-anything":
            materialize_understand_anything_source(plan.project_root, tool)
    cfg = wiki.config()
    cfg["setup"] = {
        "wizard": "llm_wiki project setup",
        "updated": date.today().isoformat(),
    }
    cfg["external_tools"] = plan.external_tools
    wiki.paths.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SetupResult(wiki=wiki, config_path=wiki.paths.config, ran_tools=ran_tools)
