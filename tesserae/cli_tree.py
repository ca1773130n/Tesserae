"""Command-tree metadata for the tesserae CLI (v0.6.0 redesign).

Single source of truth for the grouped root help and the clean-break
"moved" stubs. Spec: docs/superpowers/specs/2026-06-07-cli-redesign-design.md.
"""
from __future__ import annotations

# (section title, [(command, one-line description), ...])
COMMAND_TREE: list[tuple[str, list[tuple[str, str]]]] = [
    ("EVERYDAY", [
        ("init", "Set up .tesserae (wizard by default; --yes non-interactive)"),
        ("compile", "Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)"),
        ("ingest", "Ingest a document file or URL into the knowledge base"),
        ("context", "Compile agent-ready context for a query"),
        ("ask", "LLM answer over the knowledge graph (planned retrieval)"),
        ("serve", "Browse the compiled site (auto-builds if missing)"),
        ("status", "Node/edge counts, last compile, vault state"),
    ]),
    ("AUTOMATION", [
        ("engine", "Refresh daemon: watch sessions/sources, coalesced recompiles"),
        ("refresh", "One-shot: import sessions + compile + sync vault"),
        ("research", "Autonomous research mode: investigate a query"),
    ]),
    ("ANALYSIS", [
        ("query", "raw retrieval: BM25/semantic + explicit backends"),
        ("lint", "Graph lint report (--fix-trivial, --severity, --json)"),
        ("doctor", "Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)"),
        ("summary", "Daily/weekly activity digest (sessions, findings, commits, PRs, docs)"),
        ("decisions", "Decisions across projects + time (human AskUserQuestion + agent)"),
    ]),
    ("GROUPS", [
        ("sessions", "import | discover | list — agent session history"),
        ("vault", "sync | sync-all | set-root | export | prune — Obsidian projection"),
        ("export", "harness | graphiti | site — artifact exports"),
        ("code", "ingest | sync — CodeGraph ⇄ project graph (hook-invoked)"),
        ("setup", "Machine-wide setup: LLM defaults + optional deps (interactive by default)"),
        ("config", "llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping"),
        ("projects", "register | list | unregister | mcp-config — registry"),
        ("sources", "add | list | remove — manage compile source dirs (local & global)"),
        ("federation", "status | explain — inspect cross-project federation"),
        ("integrations", "refresh raganything"),
        ("extract", "Low-level: extract a typed graph from markdown paths"),
    ]),
    ("LAB", [
        ("lab", "evolve | schema-drift — experimental LLM ops"),
    ]),
]

KNOWN_COMMANDS: frozenset[str] = frozenset(
    cmd for _, rows in COMMAND_TREE for cmd, _ in rows
)

# Old invocation prefix -> replacement hint. Keys are token TUPLES matched
# against the leading argv tokens, longest prefix first (3, then 2, then 1),
# so `project sessions import` prints its exact replacement instead of the
# group fallback. Drives the stub in main().
MOVED_COMMANDS: dict[tuple[str, ...], str] = {
    ("project", "init"): "tesserae init --bare",
    ("project", "setup"): "tesserae init",
    ("project", "ingest"): "tesserae compile <paths>",
    ("project", "ingest-code"): "tesserae code ingest",
    ("project", "sync-code"): "tesserae code sync",
    ("project", "research"): "tesserae research",
    ("project", "lint"): "tesserae lint",
    ("project", "query"): "tesserae query",
    ("project", "compile"): "tesserae compile",
    ("project", "context"): "tesserae context",
    ("project", "ask"): "tesserae ask",
    ("project", "build-site"): "tesserae export site",
    ("project", "deploy"): "tesserae export site --deploy",
    ("project", "serve"): "tesserae serve",
    ("project", "watch"): "tesserae export site --watch",
    ("project", "engine"): "tesserae engine",
    ("project", "daemon"): "tesserae engine",
    ("project", "refresh"): "tesserae refresh",
    ("project", "sessions", "import"): "tesserae sessions import",
    ("project", "sessions", "discover"): "tesserae sessions discover",
    ("project", "sessions", "list"): "tesserae sessions list",
    ("project", "sessions"): "tesserae sessions",
    ("project", "obsidian-sync"): "tesserae vault sync",
    ("project", "export-obsidian"): "tesserae vault export",
    ("project", "export-agent-harness"): "tesserae export harness",
    ("project", "export-graphiti"): "tesserae export graphiti",
    ("project", "sync-graphiti"): "tesserae export graphiti --sync",
    ("project", "mcp-config"): "tesserae projects mcp-config",
    ("project", "refresh-raganything"): "tesserae integrations refresh raganything",
    ("project", "refresh-understand-anything"): "removed — code-structure nodes are extracted natively; see tesserae code ingest",
    ("project", "evolve"): "tesserae lab evolve",
    ("project", "schema-drift"): "tesserae lab schema-drift",
    ("project",): "tesserae <command> (see tesserae --help)",
    ("wiki", "register"): "tesserae projects register",
    ("wiki", "list"): "tesserae projects list",
    # Terminal removal (value starts with "removed"): main() prints it as a
    # removal notice, not a "has moved →" redirect — there is no replacement.
    ("wiki", "activate"): "removed — all registered projects are active; see `tesserae projects list`",
    ("wiki", "unregister"): "tesserae projects unregister",
    ("wiki", "obsidian-set-root"): "tesserae vault set-root",
    ("wiki", "obsidian-sync-all"): "tesserae vault sync-all",
    ("wiki",): "tesserae projects",
    ("llm-defaults", "--show"): "tesserae config show",
    ("llm-defaults",): "tesserae config llm",
    ("config", "setup"): "tesserae setup",
}


def package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("tesserae")
    except PackageNotFoundError:
        return "unknown"


def render_root_help() -> str:
    lines = [f"tesserae {package_version()} — a context engine", "", "usage: tesserae <command> [options]", ""]
    width = max(len(cmd) for _, rows in COMMAND_TREE for cmd, _ in rows) + 2
    for section, rows in COMMAND_TREE:
        lines.append(section)
        for cmd, desc in rows:
            lines.append(f"  {cmd:<{width}}{desc}")
        lines.append("")
    lines.append("Run `tesserae <command> --help` for command details.")
    return "\n".join(lines) + "\n"


def moved_replacement(argv: list[str]) -> tuple[str, str] | None:
    """Longest-prefix match against MOVED_COMMANDS.

    Returns (matched_old_prefix, replacement_hint) or None.
    """
    for take in (3, 2, 1):
        key = tuple(argv[:take])
        if key in MOVED_COMMANDS:
            return " ".join(key), MOVED_COMMANDS[key]
    return None


def looks_like_extraction_path(token: str) -> bool:
    """Bare extraction (`tesserae notes/x.md`) → stub to `tesserae extract`.

    Fires only for markdown-flavoured tokens: those ending in
    ``.md``/``.markdown`` (existing or not), or existing directories that
    contain at least one ``*.md`` file at any depth. Other existing paths
    (e.g. ``setup.py``, a plain ``docs`` dir) fall through to the normal
    unknown-command message.
    """
    from pathlib import Path

    if token.endswith((".md", ".markdown")):
        return True
    path = Path(token)
    try:
        if path.is_dir():
            return next(path.rglob("*.md"), None) is not None
    except OSError:
        return False
    return False
