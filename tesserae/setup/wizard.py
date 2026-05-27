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


EXTRACTOR_CHOICES = ["claude-cli", "codex", "selective-claude", "deterministic"]


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
        ("understand-anything", proj.has_understand_anything),
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
    if plan.claude_config_dir:
        table.add_row("claude_config_dir", plan.claude_config_dir)
    if plan.codex_model:
        table.add_row("codex_model", plan.codex_model)
    table.add_row("sources", ", ".join(plan.sources) or "(none)")
    for action in plan.install_actions:
        table.add_row(f"install: {action.id}", action.command)
    for action in plan.run_actions:
        table.add_row(f"run: {action.id}", action.command)
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

    console.print("\n[bold]Extractor backend[/bold]")
    for i, choice in enumerate(EXTRACTOR_CHOICES):
        marker = "[cyan]*[/cyan]" if choice == base_plan.extractor else " "
        console.print(f"  {marker} {i + 1}) {choice}")
    extractor_raw = Prompt.ask(
        "Pick a backend (number, or Enter to keep recommended)",
        default=str(EXTRACTOR_CHOICES.index(base_plan.extractor) + 1),
    )
    try:
        extractor = EXTRACTOR_CHOICES[int(extractor_raw) - 1]
    except (ValueError, IndexError):
        extractor = base_plan.extractor

    claude_config_dir = base_plan.claude_config_dir
    codex_model = base_plan.codex_model
    if extractor in {"claude-cli", "selective-claude"}:
        claude_config_dir = Prompt.ask(
            "CLAUDE_CONFIG_DIR (leave default unless multi-account)",
            default=claude_config_dir or "~/.claude",
        )
    if extractor == "codex":
        codex_model = Prompt.ask("Codex model", default=codex_model or "gpt-5.4")

    companion_items = [
        ("understand-anything", detection.recommended.include_understand_anything),
        ("raganything", False),
        ("cognee", True),
    ]
    chosen = _multi_select(console, "Companion tools", companion_items)
    include_ua = "understand-anything" in chosen
    include_raganything = "raganything" in chosen
    enable_cognee = "cognee" in chosen

    install_ua = False
    if include_ua and not detection.project.has_understand_anything:
        install_ua = Confirm.ask("Install Understand Anything now?", default=True)
    install_raganything = False
    if include_raganything and not detection.python.raganything_importable:
        if detection.recommended.raganything_available:
            install_raganything = Confirm.ask("Install raganything now?", default=False)
        else:
            console.print(
                "[yellow]raganything requires Python 3.10+; skipping install option.[/yellow]"
            )

    plan = build_plan(
        detection,
        overrides={
            "name": name,
            "source_kind": source_kind,
            "sources": sources,
            "extractor": extractor,
            "claude_config_dir": claude_config_dir,
            "codex_model": codex_model,
            "include_understand_anything": include_ua,
            "install_understand_anything": install_ua,
            "include_raganything": include_raganything,
            "install_raganything": install_raganything,
            "enable_cognee": enable_cognee,
        },
    )

    console.print()
    console.print(render_review(plan), end="")
    if not Confirm.ask("Apply this plan?", default=True):
        raise KeyboardInterrupt("setup cancelled by user at review step")
    return plan
