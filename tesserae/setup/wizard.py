"""Rich-based interactive setup wizard."""

from __future__ import annotations

import sys
from io import StringIO
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .detection import DetectionReport
from .plan import SetupPlan, build_plan


def _provider_choices(report: DetectionReport) -> list[tuple[str, str]]:
    """(value, label) provider options: detected, credentialed CLIs first,
    then the always-offered API-key and custom-endpoint providers."""
    choices: list[tuple[str, str]] = []
    claude = report.llm_clis.get("claude")
    if claude is not None and claude.available:
        choices.append(
            ("claude", f"claude — Claude CLI, logged in ({claude.version or claude.binary})")
        )
    codex = report.llm_clis.get("codex")
    if codex is not None and codex.available:
        choices.append(
            ("codex", f"codex — Codex CLI, logged in ({codex.version or codex.binary})")
        )
    choices.append(("anthropic", "anthropic — Anthropic API key"))
    choices.append(("custom", "custom — claude-compatible endpoint (base URL + API key)"))
    return choices


class WizardNotInteractive(RuntimeError):
    """Raised when run_wizard is invoked without a TTY."""


def _detection_panel(report: DetectionReport) -> Panel:
    lines: list[str] = []
    lines.append(f"[bold]Project[/bold]  {report.project.project_root}")
    py = report.python
    venv_marker = "[green]✓[/green]" if py.in_venv else "[dim]·[/dim]"
    tess_marker = (
        "[green]✓[/green]" if py.tesserae_importable else "[dim]·[/dim]"
    )
    lines.append(
        f"[bold]Python[/bold]   {py.version}   .venv {venv_marker}   "
        f"tesserae {tess_marker}"
    )
    lines.append("")
    lines.append("[bold]LLM CLIs detected[/bold]")
    for name, cli in report.llm_clis.items():
        if cli.available:
            version = cli.version or "(no version)"
            lines.append(
                f"  [green]✓[/green] {name:<8} {version:<24} {cli.binary or ''}"
            )
        else:
            lines.append(f"  [dim]·  {name:<8} not found[/dim]")
    lines.append("")
    lines.append("[bold]API keys[/bold]")
    for key, present in report.api_keys.items():
        mark = "[green]✓[/green]" if present else "[dim]·[/dim]"
        lines.append(f"  {mark} {key}")
    lines.append("")
    lines.append("[bold]Companions[/bold]")
    proj = report.project
    for label, present in (
        ("codegraph", proj.has_codegraph),
        ("cognee", proj.has_cognee),
    ):
        mark = "[green]✓[/green]" if present else "[dim]·[/dim]"
        lines.append(f"  {mark} {label}")
    return Panel("\n".join(lines), title="Tesserae setup", border_style="cyan")


def _multi_select(
    console: Console,
    title: str,
    items: list[tuple[str, bool]],
) -> list[str]:
    """Numbered toggle multi-select (Windows-safe; no raw termios)."""
    selections: dict[int, bool] = {i: pre for i, (_, pre) in enumerate(items)}
    while True:
        console.print(f"\n[bold]{title}[/bold]")
        for i, (label, _) in enumerate(items):
            marker = "[x]" if selections[i] else "[ ]"
            console.print(f"  {marker} {i + 1}) {label}")
        raw = Prompt.ask(
            "Toggle by number (comma-separated), or press Enter to accept",
            default="",
        )
        if not raw.strip():
            return [items[i][0] for i, on in selections.items() if on]
        for tok in raw.split(","):
            tok = tok.strip()
            if not tok.isdigit():
                continue
            idx = int(tok) - 1
            if 0 <= idx < len(items):
                selections[idx] = not selections[idx]


def render_review(plan: SetupPlan) -> str:
    """Render a plain-text review of a plan for MCP / non-TTY consumers."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=88)
    table = Table(title="Tesserae setup review", show_lines=False)
    table.add_column("Action")
    table.add_column("Detail")
    table.add_row("write config", str(plan.project_root / ".tesserae" / "config.json"))
    table.add_row("name", plan.name)
    table.add_row("extractor", plan.extractor)
    if plan.llm_provider:
        table.add_row("llm_provider", plan.llm_provider)
    if plan.llm_model:
        table.add_row("llm_model", plan.llm_model)
    if plan.llm_base_url:
        table.add_row("llm_base_url", plan.llm_base_url)
    if plan.llm_api_key:
        # Never echo the key: this rendering reaches MCP/non-TTY consumers.
        table.add_row("llm_api_key", "(set — will be stored in plaintext config.json)")
    if plan.claude_config_dir:
        table.add_row("claude_config_dir", plan.claude_config_dir)
    if plan.codex_home:
        table.add_row("codex_home", plan.codex_home)
    if plan.codex_model:
        table.add_row("codex_model", plan.codex_model)
    table.add_row("sources", ", ".join(plan.sources) or "(none)")
    if plan.install_agent_pointer:
        table.add_row("agent pointer", "AGENTS.md / CLAUDE.md marker block")

    tool_names = {
        str(t.get("id")): str(t.get("name") or t.get("id"))
        for t in plan.external_tools
    }
    for tool in plan.external_tools:
        name = tool.get("name") or tool.get("id")
        table.add_row(f"companion: {name}", str(tool.get("source") or tool.get("artifact") or ""))
    for action in plan.install_actions:
        label = tool_names.get(action.id, action.id)
        table.add_row(f"install: {label}", action.command)
    for action in plan.run_actions:
        label = tool_names.get(action.id, action.id)
        table.add_row(f"run: {label}", action.command)
    for warning in plan.warnings:
        table.add_row("[yellow]warning[/yellow]", warning)
    console.print(table)
    return buf.getvalue()


def run_wizard(
    detection: DetectionReport,
    defaults: Optional[SetupPlan] = None,
    *,
    console: Optional[Console] = None,
) -> SetupPlan:
    """Run the 5-step interactive wizard. Raises WizardNotInteractive without TTY."""
    if not sys.stdin.isatty():
        raise WizardNotInteractive(
            "run_wizard requires an interactive TTY. "
            "Use --yes or call build_plan directly."
        )

    console = console or Console()
    console.print(_detection_panel(detection))

    base_plan = defaults or build_plan(detection)
    name = Prompt.ask("Wiki name", default=base_plan.name)
    source_kind = Prompt.ask("Source kind", default=base_plan.source_kind)
    candidates = list(detection.project.default_sources)
    items = [(src, src in base_plan.sources) for src in candidates]
    sources = _multi_select(console, "Sources", items) if items else []
    extra = Prompt.ask(
        "Additional source paths (comma-separated, blank to skip)", default=""
    )
    sources.extend(p.strip() for p in extra.split(",") if p.strip())

    console.print("\n[bold]LLM provider[/bold]")
    provider_choices = _provider_choices(detection)
    provider_values = [value for value, _ in provider_choices]
    recommended_provider = (
        base_plan.llm_provider
        if base_plan.llm_provider in provider_values
        else provider_values[0]
    )
    for i, (value, label) in enumerate(provider_choices):
        marker = "[cyan]*[/cyan]" if value == recommended_provider else " "
        console.print(f"  {marker} {i + 1}) {label}")
    provider_raw = Prompt.ask(
        "Pick a provider (number, or Enter to keep recommended)",
        default=str(provider_values.index(recommended_provider) + 1),
    )
    try:
        llm_provider = provider_values[int(provider_raw) - 1]
    except (ValueError, IndexError):
        llm_provider = recommended_provider

    # Persisted only when the user types one: blank = auto-discovery at
    # runtime (a pinned dir would restrict multi-account ~/.claude* scans).
    claude_config_dir: Optional[str] = None
    codex_model = base_plan.codex_model
    llm_model = base_plan.llm_model
    llm_base_url = base_plan.llm_base_url
    llm_api_key = base_plan.llm_api_key
    if llm_provider == "claude":
        raw_dir = Prompt.ask(
            "CLAUDE_CONFIG_DIR (blank = auto; set only for multi-account)",
            default="",
        )
        claude_config_dir = raw_dir.strip() or None
    if llm_provider == "codex":
        codex_model = Prompt.ask("Codex model", default=codex_model or "gpt-5.4")
    if llm_provider == "custom":
        llm_base_url = Prompt.ask(
            "Base URL (claude-compatible endpoint)", default=llm_base_url or ""
        )
        llm_api_key = Prompt.ask(
            "API key (stored in plaintext config)",
            default=llm_api_key or "",
            password=True,
        )
        llm_model = Prompt.ask("Model name", default=llm_model or "")

    companion_items = [
        ("raganything", False),
        ("cognee", True),
    ]
    chosen = _multi_select(console, "Companion tools", companion_items)
    include_raganything = "raganything" in chosen
    enable_cognee = "cognee" in chosen

    install_raganything = False
    if include_raganything and not detection.python.raganything_importable:
        if detection.recommended.raganything_available:
            install_raganything = Confirm.ask("Install raganything now?", default=False)
        else:
            console.print(
                "[yellow]raganything requires Python 3.10+; skipping install option.[/yellow]"
            )

    install_pointer = Confirm.ask(
        "Add a Tesserae pointer section to AGENTS.md/CLAUDE.md?", default=True
    )

    plan = build_plan(
        detection,
        overrides={
            "name": name,
            "source_kind": source_kind,
            "sources": sources,
            "claude_config_dir": claude_config_dir,
            "codex_model": codex_model,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "llm_base_url": llm_base_url,
            "llm_api_key": llm_api_key,
            "include_raganything": include_raganything,
            "install_raganything": install_raganything,
            "enable_cognee": enable_cognee,
            "install_agent_pointer": install_pointer,
        },
    )

    console.print()
    console.print(render_review(plan), end="")
    if not Confirm.ask("Apply this plan?", default=True):
        raise KeyboardInterrupt("setup cancelled by user at review step")
    return plan
