# CLI Redesign Implementation Plan (v0.6.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hidden-multiplexer CLI (`tesserae project <26 subcommands>`) with the spec'd flat-verb tree (`tesserae compile`, `tesserae sessions import`, …), clean-break stubs for every old command, and a flag diet on `init`/`compile`.

**Architecture:** A new `tesserae/cli_tree.py` owns the command-tree METADATA (groups, one-liners, moved-command table) and the grouped help formatter. `tesserae/cli.py` keeps every existing `_handle_*` function (behavior untouched) but its parsers are rebuilt around the new tree. Old invocations hit a stub table and exit 2 with the exact replacement. Blast radius (tests, plugin hooks, CI, docs) migrates in the same plan.

**Tech Stack:** Python 3.11, argparse, pytest. Spec: `docs/superpowers/specs/2026-06-07-cli-redesign-design.md` (read it first).

**Conventions for every task:** venv python is `.venv/bin/python`. TDD: write the failing test, watch it fail, implement, watch it pass, commit. Commit messages end with the Claude co-author trailer used throughout this repo's history (`git log -5`).

---

### Task 1: Command-tree metadata + grouped root help (`cli_tree.py`)

**Files:**
- Create: `tesserae/cli_tree.py`
- Test: `tests/test_cli_tree.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_cli_tree.py"""
from __future__ import annotations

import pytest


def test_root_help_shows_grouped_commands(capsys):
    from tesserae.cli import main

    rc = main([])  # bare invocation prints grouped help, exit 0
    out = capsys.readouterr().out
    assert rc == 0
    for section in ("EVERYDAY", "AUTOMATION", "GROUPS", "LAB"):
        assert section in out
    for cmd in ("init", "compile", "context", "ask", "serve", "status",
                "engine", "refresh", "sessions", "vault", "export",
                "config", "projects", "integrations", "extract", "lab"):
        assert f"\n  {cmd}" in out or f" {cmd} " in out


def test_root_help_flag_matches_bare(capsys):
    from tesserae.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "EVERYDAY" in capsys.readouterr().out


def test_unknown_command_exits_2_and_points_at_help(capsys):
    from tesserae.cli import main

    rc = main(["frobnicate"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "tesserae --help" in err
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_tree.py -q --tb=line`
Expected: 3 failed — bare `main([])` currently errors into the extraction parser (`SystemExit: 2` from missing `paths`), `--help` shows the extraction parser, `frobnicate` is treated as a path.

- [ ] **Step 3: Implement `tesserae/cli_tree.py`**

```python
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
        ("context", "Compile agent-ready context for a query"),
        ("ask", "Ask the project memory a question"),
        ("serve", "Browse the compiled site (auto-builds if missing)"),
        ("status", "Node/edge counts, last compile, vault state"),
    ]),
    ("AUTOMATION", [
        ("engine", "Refresh daemon: watch sessions/sources, coalesced recompiles"),
        ("refresh", "One-shot: import sessions + compile + sync vault"),
    ]),
    ("GROUPS", [
        ("sessions", "import | discover | list — agent session history"),
        ("vault", "sync | export | prune — Obsidian projection"),
        ("export", "harness | graphiti | site — artifact exports"),
        ("config", "llm | show — machine-wide defaults (~/.tesserae/config.json)"),
        ("projects", "list | activate | unregister | mcp-config — registry"),
        ("integrations", "refresh raganything|understand-anything"),
        ("extract", "Low-level: extract a typed graph from markdown paths"),
    ]),
    ("LAB", [
        ("lab", "evolve | schema-drift — experimental LLM ops"),
    ]),
]

KNOWN_COMMANDS: frozenset[str] = frozenset(
    cmd for _, rows in COMMAND_TREE for cmd, _ in rows
)

# Old invocation prefix -> replacement hint. Keys are matched against the
# leading argv tokens (longest match wins). Drives _print_moved_stub().
MOVED_COMMANDS: dict[str, str] = {
    "project init": "tesserae init --bare",
    "project setup": "tesserae init",
    "project ingest": "tesserae compile <paths>",
    "project compile": "tesserae compile",
    "project context": "tesserae context",
    "project ask": "tesserae ask",
    "project build-site": "tesserae export site",
    "project deploy": "tesserae export site --deploy",
    "project serve": "tesserae serve",
    "project watch": "tesserae engine",
    "project engine": "tesserae engine",
    "project daemon": "tesserae engine",
    "project refresh": "tesserae refresh",
    "project sessions": "tesserae sessions",
    "project obsidian-sync": "tesserae vault sync",
    "project export-obsidian": "tesserae vault export",
    "project export-agent-harness": "tesserae export harness",
    "project export-graphiti": "tesserae export graphiti",
    "project sync-graphiti": "tesserae export graphiti --sync",
    "project mcp-config": "tesserae projects mcp-config",
    "project refresh-raganything": "tesserae integrations refresh raganything",
    "project refresh-understand-anything": "tesserae integrations refresh understand-anything",
    "project evolve": "tesserae lab evolve",
    "project schema-drift": "tesserae lab schema-drift",
    "project": "tesserae <command> (see tesserae --help)",
    "wiki list": "tesserae projects list",
    "wiki activate": "tesserae projects activate",
    "wiki unregister": "tesserae projects unregister",
    "wiki": "tesserae projects",
    "llm-defaults": "tesserae config llm",
}


def render_root_help() -> str:
    lines = ["usage: tesserae <command> [options]", ""]
    width = max(len(cmd) for _, rows in COMMAND_TREE for cmd, _ in rows) + 2
    for section, rows in COMMAND_TREE:
        lines.append(section)
        for cmd, desc in rows:
            lines.append(f"  {cmd:<{width}}{desc}")
        lines.append("")
    lines.append("Run `tesserae <command> --help` for command details.")
    return "\n".join(lines) + "\n"


def moved_replacement(argv: list[str]) -> str | None:
    """Longest-prefix match of argv against MOVED_COMMANDS; None if no hit."""
    for take in (2, 1):
        key = " ".join(argv[:take])
        if key in MOVED_COMMANDS:
            return MOVED_COMMANDS[key]
    return None
```

- [ ] **Step 4: Rewire `main()` in `tesserae/cli.py`**

Replace the body of `main()` (currently the `project`/`llm-defaults`/`ask`/`wiki` prefix chain at ~line 2225, falling through to the extraction parser) with:

```python
def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    from .cli_tree import KNOWN_COMMANDS, moved_replacement, render_root_help

    if not argv or argv[0] in ("--help", "-h", "help"):
        print(render_root_help(), end="")
        if argv and argv[0] in ("--help", "-h"):
            raise SystemExit(0)
        return 0
    moved = moved_replacement(argv)
    if moved is not None:
        print(
            f"tesserae {' '.join(argv[:2])} has moved → {moved}",
            file=sys.stderr,
        )
        return 2
    if argv[0] not in KNOWN_COMMANDS:
        print(
            f"tesserae: unknown command {argv[0]!r} — see `tesserae --help`",
            file=sys.stderr,
        )
        return 2
    return _dispatch_command(argv[0], argv[1:])
```

Add a temporary `_dispatch_command` that routes the commands that already exist (`ask` → `_top_level_ask_handler` path) and raises `NotImplementedError` for the rest — Tasks 3–10 fill it in. Keep `project_main` intact for now (Task 12 deletes it).

```python
def _dispatch_command(command: str, rest: List[str]) -> int:
    if command == "ask":
        ask_parser = _build_top_level_ask_parser()
        return _top_level_ask_handler(ask_parser.parse_args(rest))
    raise NotImplementedError(f"tesserae {command}: wired in a later task")
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_cli_tree.py -q`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add tesserae/cli_tree.py tesserae/cli.py tests/test_cli_tree.py
git commit -m "feat(cli): grouped root help + command-tree metadata (redesign task 1)"
```

---

### Task 2: Clean-break stub messages — every mapping row asserted

**Files:**
- Test: `tests/test_cli_tree.py` (append)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize(
    "old, hint",
    [
        (["project", "compile"], "tesserae compile"),
        (["project", "setup", "--yes"], "tesserae init"),
        (["project", "init"], "tesserae init --bare"),
        (["project", "ingest", "x.md"], "tesserae compile <paths>"),
        (["project", "build-site"], "tesserae export site"),
        (["project", "deploy"], "tesserae export site --deploy"),
        (["project", "serve"], "tesserae serve"),
        (["project", "watch"], "tesserae engine"),
        (["project", "daemon"], "tesserae engine"),
        (["project", "obsidian-sync"], "tesserae vault sync"),
        (["project", "export-obsidian"], "tesserae vault export"),
        (["project", "export-agent-harness"], "tesserae export harness"),
        (["project", "export-graphiti"], "tesserae export graphiti"),
        (["project", "sync-graphiti"], "tesserae export graphiti --sync"),
        (["project", "mcp-config"], "tesserae projects mcp-config"),
        (["project", "refresh-raganything"], "tesserae integrations refresh raganything"),
        (["project", "refresh-understand-anything"], "tesserae integrations refresh understand-anything"),
        (["project", "evolve"], "tesserae lab evolve"),
        (["project", "schema-drift"], "tesserae lab schema-drift"),
        (["project", "sessions", "import"], "tesserae sessions"),
        (["wiki", "list"], "tesserae projects list"),
        (["wiki", "activate"], "tesserae projects activate"),
        (["wiki", "unregister"], "tesserae projects unregister"),
        (["llm-defaults", "--show"], "tesserae config llm"),
    ],
)
def test_moved_commands_print_one_line_stub(old, hint, capsys):
    from tesserae.cli import main

    rc = main(old)
    assert rc == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1, f"stub must be exactly one line, got: {err!r}"
    assert "has moved" in err and hint in err
```

- [ ] **Step 2: Run, verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_tree.py -q --tb=line`
Expected: the parametrized cases fail (`project …` still dispatches to `project_main`, `llm-defaults` still works) until Task 1's `main()` rewrite is in place; if Task 1 is done, they pass except rows whose hint text mismatches — fix `MOVED_COMMANDS` until green.

- [ ] **Step 3: Run full stub matrix to pass**

Run: `.venv/bin/python -m pytest tests/test_cli_tree.py -q`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli_tree.py tesserae/cli_tree.py
git commit -m "feat(cli): clean-break stubs for every legacy command (redesign task 2)"
```

---

### Task 3: Everyday verbs — `compile`, `context`, `serve`, `status` (+ `ask` done in Task 1)

**Files:**
- Modify: `tesserae/cli.py` (`_dispatch_command`; new `_build_<cmd>_parser` helpers)
- Test: `tests/test_cli_commands.py` (new)

The existing handlers stay: `_handle_compile`, `_handle_serve`, the context handler (find via `grep -n '"context"' tesserae/cli.py` — the `context_parser` block at ~line 1280), `_handle_build_site`. New parsers are built standalone (argparse `prog="tesserae compile"` etc.) instead of via `project_main`'s subparsers, then handed to the existing handlers unchanged.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_cli_commands.py — new-tree dispatch reaches the OLD handlers."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "argv, handler",
    [
        (["compile"], "_handle_compile"),
        (["context", "q"], "_handle_context"),
        (["serve"], "_handle_serve"),
        (["status"], "_handle_status"),
        (["refresh"], "_handle_refresh"),
        (["engine", "--once"], "_handle_engine"),
    ],
)
def test_verb_dispatches_to_handler(argv, handler, monkeypatch):
    import tesserae.cli as cli

    called = {}
    monkeypatch.setattr(cli, handler, lambda args: called.setdefault("args", args) or 0)
    rc = cli.main(argv)
    assert rc == 0
    assert "args" in called


def test_compile_accepts_paths_as_adhoc_ingest(monkeypatch):
    import tesserae.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "_handle_compile", lambda args: seen.setdefault("paths", args.paths) or 0)
    assert cli.main(["compile", "notes/a.md", "notes/b.md"]) == 0
    assert seen["paths"] == ["notes/a.md", "notes/b.md"]
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py -q --tb=line`
Expected: FAIL — `NotImplementedError` from `_dispatch_command`, and `_handle_status`/`_handle_context`/`_handle_engine`/`_handle_refresh` may not exist under those names yet.

- [ ] **Step 3: Implement**

1. In `cli.py`, locate each existing parser block inside `project_main` (compile ~969, context ~1280, serve ~1245, engine ~1262, refresh ~1272) and extract each into a module-level `_build_compile_parser()` / `_build_context_parser()` / … returning a standalone `argparse.ArgumentParser(prog="tesserae <cmd>")` with the SAME flags (flag diet happens in Task 11, not here). Add `paths` positional to compile: `parser.add_argument("paths", nargs="*", help="Ad-hoc markdown paths to ingest before compiling (replaces `project ingest`)")` and in `_handle_compile` prepend: if `args.paths`, call `wiki.ingest(args.paths, source_kind=None, changed_only=False)` before the normal compile (mirror `_handle_ingest`'s call — read it first).
2. Rename/alias the handlers the tests expect: if the existing handler is e.g. `_handle_project_context`, add `_handle_context = _handle_project_context`. The engine/refresh handlers follow the dispatch-dict names in `_PROJECT_HANDLERS` (~line 2192) — read that dict for exact names.
3. `_handle_status` is NEW (thin, read-only):

```python
def _handle_status(args: argparse.Namespace) -> int:
    wiki = ProjectWiki.load(args.project)
    from .research_graph import ResearchGraph  # local, mirrors other handlers
    graph = (
        load_graph_file(wiki.paths.graph) if wiki.paths.graph.exists() else ResearchGraph()
    )
    import datetime as _dt
    compiled = (
        _dt.datetime.fromtimestamp(wiki.paths.graph.stat().st_mtime).isoformat(timespec="seconds")
        if wiki.paths.graph.exists() else "never"
    )
    print(f"project:       {wiki.project_root}")
    print(f"nodes:         {len(graph.nodes)}")
    print(f"edges:         {len(graph.edges)}")
    print(f"last compile:  {compiled}")
    print(f"vault:         {wiki.effective_obsidian_vault()}")
    print(f"site:          {wiki.paths.site}")
    return 0
```

(`load_graph_file` is imported in cli.py already — verify with `grep -n load_graph_file tesserae/cli.py`; add the import from `.project` if missing.)
4. Extend `_dispatch_command` with a dict:

```python
_COMMAND_DISPATCH: dict[str, tuple[Callable[[], argparse.ArgumentParser], Callable[[argparse.Namespace], int]]] = {
    "compile": (_build_compile_parser, lambda a: _handle_compile(a)),
    "context": (_build_context_parser, lambda a: _handle_context(a)),
    "serve": (_build_serve_parser, lambda a: _handle_serve(a)),
    "status": (_build_status_parser, lambda a: _handle_status(a)),
    "engine": (_build_engine_parser, lambda a: _handle_engine(a)),
    "refresh": (_build_refresh_parser, lambda a: _handle_refresh(a)),
}


def _dispatch_command(command: str, rest: List[str]) -> int:
    if command == "ask":
        ask_parser = _build_top_level_ask_parser()
        return _top_level_ask_handler(ask_parser.parse_args(rest))
    entry = _COMMAND_DISPATCH.get(command)
    if entry is None:
        raise NotImplementedError(f"tesserae {command}: wired in a later task")
    build, handler = entry
    return handler(build().parse_args(rest))
```

NOTE: the lambdas must resolve handlers at CALL time (as written) so monkeypatching `cli._handle_compile` in tests takes effect.
5. `serve` auto-build: in `_handle_serve`, before serving, if the site index (check what `_handle_serve` reads — likely `wiki.paths.site / "index.html"`) is missing, call the build-site handler first and print one line `building site first (missing) …`. Add `--no-build` flag to skip.

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py tests/test_cli_tree.py -q`
Expected: all passed.

- [ ] **Step 5: Live smoke (Process Hygiene rule: show real output)**

Run in a scratch dir: `cd $(mktemp -d) && /Users/neo/Developer/Projects/Tesserae/.venv/bin/python -m tesserae status` → expect the "not initialized" error path, exit non-zero, no traceback. Then in the Tesserae repo: `.venv/bin/python -m tesserae status` → expect node/edge counts.

- [ ] **Step 6: Commit**

```bash
git add tesserae/cli.py tests/test_cli_commands.py
git commit -m "feat(cli): everyday verbs compile/context/serve/status + engine/refresh at top level (redesign task 3)"
```

---

### Task 4: `init` — wizard default, `--yes`, `--bare`

**Files:**
- Modify: `tesserae/cli.py`
- Test: `tests/test_cli_commands.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_init_bare_creates_workspace(tmp_path, monkeypatch):
    import tesserae.cli as cli

    rc = cli.main(["init", "--bare", "--project", str(tmp_path), "--name", "t"])
    assert rc == 0
    assert (tmp_path / ".tesserae" / "config.json").exists()


def test_init_yes_runs_setup_noninteractive(tmp_path, monkeypatch):
    import tesserae.cli as cli

    called = {}
    monkeypatch.setattr(cli, "_handle_setup", lambda args: called.setdefault("yes", args.yes) or 0)
    rc = cli.main(["init", "--yes", "--project", str(tmp_path)])
    assert rc == 0
    assert called["yes"] is True


def test_init_keeps_llm_flags(tmp_path):
    import json

    import tesserae.cli as cli

    rc = cli.main([
        "init", "--bare", "--project", str(tmp_path),
        "--llm-provider", "codex", "--codex-home", "/h/.codex-personal1",
    ])
    assert rc == 0
    cfg = json.loads((tmp_path / ".tesserae" / "config.json").read_text())
    assert cfg["llm_provider"] == "codex"
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py -q -k init --tb=line`
Expected: FAIL with `NotImplementedError: tesserae init`.

- [ ] **Step 3: Implement**

`_build_init_parser()`: flags exactly `--project`, `--name`, `--source` (append), `--source-kind`, `--yes`, `--bare`, plus `_add_llm_client_args(parser, persisted=True)`. Dispatch:

```python
def _handle_init_v2(args: argparse.Namespace) -> int:
    if args.bare:
        return _handle_init(args)          # old `project init` handler, unchanged
    # default: wizard (old `project setup`); --yes accepted by _handle_setup
    return _handle_setup(args)
```

`_handle_setup` reads many attrs off `args` (the 29 old flags). Give the namespace defaults so the wizard path works without them: after parsing, backfill `args.__dict__.setdefault(...)` for every attribute `_handle_setup` reads with the SAME defaults the old setup parser declared (enumerate them: `grep -n 'setup_parser.add_argument' tesserae/cli.py` and copy each `dest`/`default`). Put that backfill in one function `_backfill_setup_defaults(args)` directly above `_handle_init_v2` — Task 11 shrinks it when the flags become config keys.
Register `"init": (_build_init_parser, lambda a: _handle_init_v2(a))` in `_COMMAND_DISPATCH`.

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py -q -k init`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tesserae/cli.py tests/test_cli_commands.py
git commit -m "feat(cli): tesserae init — wizard default, --yes, --bare (redesign task 4)"
```

---

### Task 5: Groups — `sessions`, `vault`, `export`, `config`, `projects`, `integrations`, `lab`, `extract`

**Files:**
- Modify: `tesserae/cli.py`
- Test: `tests/test_cli_commands.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(
    "argv, handler",
    [
        (["sessions", "import"], "_handle_sessions_import"),
        (["sessions", "discover"], "_handle_sessions_discover"),
        (["sessions", "list"], "_handle_sessions_list"),
        (["vault", "sync"], "_handle_vault_sync"),
        (["vault", "export"], "_handle_vault_export"),
        (["export", "harness"], "_handle_export_harness"),
        (["export", "graphiti"], "_handle_export_graphiti_cmd"),
        (["export", "site"], "_handle_export_site"),
        (["projects", "list"], "_handle_projects_list"),
        (["projects", "mcp-config"], "_handle_projects_mcp_config"),
        (["integrations", "refresh", "raganything"], "_handle_integrations_refresh"),
        (["lab", "evolve"], "_handle_lab_evolve"),
        (["lab", "schema-drift"], "_handle_lab_schema_drift"),
        (["extract", "x.md"], "_handle_extract"),
    ],
)
def test_group_dispatch(argv, handler, monkeypatch):
    import tesserae.cli as cli

    called = {}
    monkeypatch.setattr(cli, handler, lambda args: called.setdefault("ok", True) or 0)
    assert cli.main(argv) == 0
    assert called.get("ok")


def test_config_llm_is_old_llm_defaults(monkeypatch, tmp_path):
    import json

    import tesserae.cli as cli
    import tesserae.llm_json as lj

    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", tmp_path / "config.json")
    assert cli.main(["config", "llm", "--llm-provider", "codex"]) == 0
    assert json.loads((tmp_path / "config.json").read_text())["llm_provider"] == "codex"
    assert cli.main(["config", "show"]) == 0


def test_export_site_deploy_flag(monkeypatch):
    import tesserae.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "_handle_export_site", lambda args: seen.setdefault("deploy", args.deploy) or 0)
    assert cli.main(["export", "site", "--deploy"]) == 0
    assert seen["deploy"] is True
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py -q -k "group or config_llm or export_site" --tb=line`
Expected: FAIL — `NotImplementedError` for each group.

- [ ] **Step 3: Implement**

Pattern (one builder per group, sub-subparsers inside):

```python
def _build_sessions_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tesserae sessions",
                                     description="Agent session history: import, discover, list.")
    sub = parser.add_subparsers(dest="subcommand", required=True)
    imp = sub.add_parser("import", help="Import normalized HarnessSession JSON files")
    # copy the EXACT flags from the old sessions_import parser (cli.py ~1216)
    ...
    return parser
```

For each group:
- **sessions**: move the three old sub-parsers (`import`/`discover`/`list`, cli.py ~1214–1226) verbatim; handlers: find the old names in `_PROJECT_HANDLERS` and alias to `_handle_sessions_import` etc.
- **vault**: `sync` = old `obsidian-sync` parser/handler (~1066); add `prune` as `sync --prune-orphans` preset (its handler calls the sync handler with `prune_orphans=True, prune_only=True`); `export` = old `export-obsidian` (~1210).
- **export**: `harness` = old `export-agent-harness` (~1205, keep `--target`, `--output`); `graphiti` = old `export-graphiti` (~1192) plus `--sync` flag that routes to the old `sync-graphiti` handler (~1197); `site` = old `build-site` (~1227) plus `--deploy` routing to the old `deploy` handler (~1231). Name the dispatch wrappers `_handle_export_harness`, `_handle_export_graphiti_cmd` (`_cmd` suffix avoids clashing with the existing `_handle_export_graphiti` if names collide — check first), `_handle_export_site`.
- **config**: `llm` = `_handle_llm_defaults` minus `--show`; `show` = `_handle_llm_defaults` with `args.show=True`. Alias `_handle_config_llm`/`_handle_config_show` wrappers.
- **projects**: reuse `_build_top_level_wiki_parser`'s sub-parsers (list/activate/unregister, ~736–759) + old `mcp-config` (~1187). Wrappers `_handle_projects_*`.
- **integrations**: `refresh <name>` with `name` choices `["raganything", "understand-anything"]`, routing to the two old refresh handlers (~1131, ~1058). Wrapper `_handle_integrations_refresh`.
- **lab**: `evolve` (~1040) and `schema-drift` (~1021) parsers/handlers moved verbatim.
- **extract**: wrap the legacy bare-paths extraction parser (the big parser at the bottom of `main()`, ~2240+) as `_build_extract_parser()` / `_handle_extract` — verbatim flags.

Register all in `_COMMAND_DISPATCH`. Group dispatch needs sub-routing — extend the dispatch value to a callable that takes `rest` directly:

```python
def _dispatch_command(command: str, rest: List[str]) -> int:
    router = _COMMAND_DISPATCH.get(command)
    if router is None:
        raise NotImplementedError(command)
    return router(rest)
```

and make every entry a `def _route_<cmd>(rest)` that parses + calls the right handler (simpler and uniform; refactor Task 3's entries to the same shape).

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py tests/test_cli_tree.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add tesserae/cli.py tests/test_cli_commands.py
git commit -m "feat(cli): sessions/vault/export/config/projects/integrations/lab/extract groups (redesign task 5)"
```

---

### Task 6: Delete the legacy surface — `project_main`, `wiki`, `llm-defaults` top-level

**Files:**
- Modify: `tesserae/cli.py`
- Test: existing `tests/test_cli_tree.py` stub matrix is the guard

- [ ] **Step 1: Confirm every old test caller is migrated FIRST**

Run: `grep -rn "project_main(" tests/ | wc -l` — Task 7 must be done if this is non-zero. If non-zero, STOP and do Task 7 first, then return here. (Order in this plan assumes 7 before 6 is allowed; executor: run Task 7 first if grep is non-zero.)

- [ ] **Step 2: Delete**

- Delete `project_main()` and its parser construction (every old subparser block already extracted in Tasks 3–5; what remains is the shell).
- Delete `_build_top_level_wiki_parser` dispatch from `main()` (already gone after Task 1) and the function if `projects` group reuses its internals via extracted builders instead.
- Delete the `llm-defaults` dispatch branch (the stub table covers the message).
- `grep -n "project_main\|_build_top_level_wiki_parser" tesserae/ tests/` must return only the stub-table references (or nothing).

- [ ] **Step 3: Run the cli suites**

Run: `.venv/bin/python -m pytest tests/test_cli_tree.py tests/test_cli_commands.py tests/test_llm_provider_config.py tests/test_project_cli.py -q`
Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add tesserae/cli.py
git commit -m "feat(cli)!: remove legacy project/wiki/llm-defaults surfaces (redesign task 6)"
```

---

### Task 7: Migrate test call sites (~60)

**Files:**
- Modify: every test file matching `grep -rln "project_main(\|main(\[" tests/`

- [ ] **Step 1: Enumerate**

Run: `grep -rn "project_main(\[" tests/ | cut -d: -f1 | sort -u`
Known minimum set: `tests/test_project_cli.py`, `tests/test_llm_provider_config.py`, `tests/test_cli_ask_scope.py`, `tests/test_cli_top_level_ask.py`, `tests/test_cli_raganything.py` — plus whatever the grep adds.

- [ ] **Step 2: Mechanical rewrite per the mapping table**

For each call: `project_main(["compile", ...])` → `main(["compile", ...])`; `project_main(["setup", "--yes", ...])` → `main(["init", "--yes", ...])`; `project_main(["build-site"])` → `main(["export", "site"])`; etc. — every rewrite comes from the spec's mapping table, no judgment calls. Import `main` instead of `project_main`.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/pytest tests/ -q --tb=line`
Expected: 0 failed. Any failure here is either a missed call site or a genuine dispatch bug from Tasks 3–5 — fix at the source, never by skipping the test.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test(cli): migrate all call sites to the new command tree (redesign task 7)"
```

---

### Task 8: Flag diet — `compile` and `init` keep ≤8 flags

**Files:**
- Modify: `tesserae/cli.py`, `tesserae/project.py` (config reads)
- Test: `tests/test_cli_commands.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_compile_flag_surface_is_small():
    import tesserae.cli as cli

    parser = cli._build_compile_parser()
    flags = [a for a in parser._actions if a.option_strings]
    # -h plus at most 8 real flags
    assert len(flags) <= 9, [a.option_strings for a in flags]


def test_removed_compile_flags_become_config_keys(tmp_path):
    """kuzu/graphiti/cognee knobs come from config.json now."""
    import json

    from tesserae.project import ProjectWiki

    wiki = ProjectWiki.init(tmp_path, name="t")
    cfg = json.loads(wiki.paths.config.read_text())
    cfg["compile_options"] = {"kuzu_output": str(tmp_path / "kuzu")}
    wiki.paths.config.write_text(json.dumps(cfg))
    assert ProjectWiki.load(tmp_path)._compile_options()["kuzu_output"] == str(tmp_path / "kuzu")
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py -q -k flag --tb=line`
Expected: FAIL — compile parser still carries ~26 flags; `_compile_options` doesn't exist.

- [ ] **Step 3: Implement**

1. Enumerate the current compile flags: `sed -n '969,1058p' tesserae/cli.py` (line numbers will have shifted — locate via `grep -n "_build_compile_parser" tesserae/cli.py`). KEEP: `paths`, `--project`, `--changed-only`, `--no-sessions`, `--limit`, `--refresh-external-tools`, `--llm-provider`, `--claude-config-dir`, `--codex-home`. EVERY other flag moves to a `compile_options` dict in `config.json` under its dest name (e.g. `--kuzu-output` → `compile_options.kuzu_output`), with the argparse default as the fallback.
2. Add to `ProjectWiki`:

```python
def _compile_options(self) -> dict:
    cfg = self.config() if self.paths.config.exists() else {}
    opts = cfg.get("compile_options")
    return dict(opts) if isinstance(opts, dict) else {}
```

3. In `_handle_compile`, where each removed flag was read (`args.<dest>`), read `wiki._compile_options().get("<dest>", <old argparse default>)` instead. Do this mechanically, one flag at a time, running `tests/test_project_cli.py` after each.
4. Same diet for `init`: `_build_init_parser` keeps the 8 flags from Task 4 (it already does); `_backfill_setup_defaults` now reads `cfg`-style defaults — leave as-is unless a test fails.
5. Document each moved flag as a config key row in `docs/quickstart.md`'s compile section — defer the prose to Task 10 (docs), but add the keys to the `config.json` template written by `ProjectWiki.init` ONLY if the old flag had a non-None default the compile path requires.

- [ ] **Step 4: Run, verify pass + no regression**

Run: `.venv/bin/python -m pytest tests/test_cli_commands.py tests/test_project_cli.py tests/test_incremental_compile.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add tesserae/cli.py tesserae/project.py tests/test_cli_commands.py
git commit -m "feat(cli)!: compile/init flag diet — integration knobs move to config.json (redesign task 8)"
```

---

### Task 9: Blast radius — plugin commands, hooks, CI, release skill

**Files:**
- Modify: every file from `grep -rln "tesserae project \|project_main" .claude-plugin/ .claude/ .github/ scripts/ 2>/dev/null` plus `grep -rln "tesserae project " --include="*.sh" --include="*.yml" --include="*.json" --include="*.md" . | grep -v docs/ | grep -v "\.tesserae/"`

- [ ] **Step 1: Enumerate exact call sites**

Run the greps above; list every hit before editing. Known: `.github/workflows/build-demo.yml` (smoke: `project setup --yes … && project compile && project build-site`), `.claude/skills/release/SKILL.md` step 5, plugin slash-command definitions and SessionStart/SessionEnd hook scripts (locate via `grep -rn "tesserae project" ~/.claude-personal2/plugins/cache/ --include="*.md" -l` is OUT of repo scope — only fix what lives in THIS repo; note out-of-repo hits in the final report instead).

- [ ] **Step 2: Rewrite each invocation per the mapping table**

`tesserae project setup --yes --no-color --source . …` → `tesserae init --yes --no-color --source . …` (verify `--no-color`/integration skip flags survive the diet — they're setup-wizard flags, kept as init wizard flags or config keys per Task 8's decision; if a CI flag was dieted, set the equivalent `config.json` key in the workflow's project dir before compile, or use the documented env var).
`tesserae project compile` → `tesserae compile`. `tesserae project build-site` → `tesserae export site`.

- [ ] **Step 3: Run the CI smoke locally (the release skill's step 5)**

```bash
.venv/bin/python -m tesserae init --yes --no-color --source . <surviving-skip-flags>
.venv/bin/python -m tesserae compile
.venv/bin/python -m tesserae export site
```
Expected: all three exit 0 (same artifacts as the old smoke).

- [ ] **Step 4: Commit**

```bash
git add .github/ .claude/skills/release/SKILL.md <other hits>
git commit -m "chore(cli): migrate CI smoke + release skill + in-repo callers to the new tree (redesign task 9)"
```

---

### Task 10: Docs migration (incl. 7-language i18n)

**Files:**
- Modify: every doc matching `grep -rln "tesserae project \|llm-defaults\|tesserae wiki " docs/ README*.md`

- [ ] **Step 1: Enumerate + dispatch**

This is fan-out work: enumerate the hits, then dispatch parallel doc agents (one per doc cluster, same protocol as the 2026-06-07 docs update) with the spec's mapping table pasted into each prompt. Every touched doc updates its 7 i18n counterparts (`docs/i18n/...`) with real translations. The quickstart/installation docs additionally gain the new `tesserae --help` output block and the `compile_options` config-key table from Task 8.

- [ ] **Step 2: Verify**

Run: `.venv/bin/python -m pytest tests/test_docs_i18n.py -q` → 3 passed.
Run: `grep -rn "tesserae project " docs/ README*.md | grep -v i18n | wc -l` → 0 (i18n checked by spot-sample per language).

- [ ] **Step 3: Commit**

```bash
git add docs/ README*.md
git commit -m "docs: migrate all command references to the new CLI tree (redesign task 10)"
```

---

### Task 11: Help polish — EXAMPLES epilogs

**Files:**
- Modify: `tesserae/cli.py` (every `_build_*_parser`)
- Test: `tests/test_cli_tree.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_every_command_help_has_examples(capsys):
    import tesserae.cli as cli

    for cmd in ("init", "compile", "context", "serve", "status", "engine",
                "refresh", "sessions", "vault", "export", "config",
                "projects", "integrations", "extract", "lab"):
        with pytest.raises(SystemExit):
            cli.main([cmd, "--help"])
        out = capsys.readouterr().out
        assert "examples:" in out.lower(), f"{cmd} --help lacks EXAMPLES"
```

- [ ] **Step 2: Run, verify failure** — `.venv/bin/python -m pytest tests/test_cli_tree.py -q -k examples --tb=line` → FAIL.

- [ ] **Step 3: Implement** — every builder gets `formatter_class=argparse.RawDescriptionHelpFormatter` and an `epilog` with 2–3 real invocations, e.g. compile:

```python
epilog = """examples:
  tesserae compile                       # full recompile of configured sources
  tesserae compile --changed-only        # idempotent no-op when nothing changed
  tesserae compile notes/idea.md         # ad-hoc ingest + compile
"""
```

Write real, runnable examples per command (use the spec's mapping table and each command's actual flags).

- [ ] **Step 4: Run, verify pass** — `.venv/bin/python -m pytest tests/test_cli_tree.py -q` → all passed.

- [ ] **Step 5: Commit**

```bash
git add tesserae/cli.py tests/test_cli_tree.py
git commit -m "feat(cli): EXAMPLES epilog on every command help (redesign task 11)"
```

---

### Task 12: Final gate

- [ ] **Step 1: Full suite**

Run: `.venv/bin/pytest tests/ -q --tb=line`
Expected: 0 failed (baseline today: 1574 passed, 6 skipped).

- [ ] **Step 2: Live help audit**

Run: `.venv/bin/python -m tesserae` and `.venv/bin/python -m tesserae --help` → grouped help, no extraction parser. Run `for c in init compile context ask serve status engine refresh sessions vault export config projects integrations extract lab; do .venv/bin/python -m tesserae $c --help >/dev/null || echo "BROKEN: $c"; done` → no BROKEN lines.

- [ ] **Step 3: Stub audit**

Run: `.venv/bin/python -m tesserae project compile; echo "exit=$?"` → one stderr line + `exit=2`.

- [ ] **Step 4: Commit anything outstanding; do NOT bump version** (0.6.0 release is a separate decision).
