# Setup rewrite — sophisticated wizard, MCP plan/apply, environment detection

**Date:** 2026-05-27
**Status:** approved — implementation pending
**Authors:** brainstormed with user (ca1773130n@gmail.com)

## Problem

Today's `tesserae setup` has three failure modes:

1. **MCP slash command is a stub.** `commands/setup.md` is marked
   `disable-model-invocation: true` and just prints "run it in a terminal."
   `/tesserae:setup` cannot actually set anything up.
2. **CLI interactive wizard is barebones.** `interactive_setup_plan` in
   `tesserae/project_setup.py` is a sequence of plain `input()` calls — no
   sections, no detection, no preview, no checkbox selection. It also crashes
   immediately under any non-TTY caller (slash commands, CI).
3. **No LLM / environment detection.** The wizard never inspects whether
   Claude Code, Codex, or any other LLM CLI is available, nor whether the
   active Python can support optional deps (raganything needs ≥3.10). All
   defaults are hardcoded.

The goal is one coherent setup system that works equally well from a real
terminal, a slash command, and an MCP-tool-driven agent loop.

## Non-goals

- Rewriting the `.tesserae/config.json` schema or any consumer of it.
- Changing `tesserae compile` / refresh / vault-sync behavior.
- Building a fully custom TUI framework. We add `rich` (one dep) and stop.
- Detecting non-standard user-specific config locations (e.g. `~/.claude-personal*`).
  Multi-account is handled via free-text override + `CLAUDE_CONFIG_DIR` env var only.

## Architecture

Four layers under a new `tesserae/setup/` package, each independently testable
with no I/O leaking across boundaries:

```
tesserae/setup/
├── __init__.py      # public API: detect, build_plan, run_wizard, apply_plan
├── detection.py     # pure env probing → DetectionReport
├── plan.py          # SetupPlan model + build_plan(detection, overrides)
├── wizard.py        # rich-based interactive wizard → SetupPlan
└── apply.py         # apply_plan(plan) → SetupResult (all .tesserae writes + installers)
```

`tesserae/project_setup.py` becomes a thin re-export shim so existing imports
(`from tesserae.project_setup import apply_setup_plan, ...`) keep working
without code-site changes. New code uses `tesserae.setup`.

### Dependencies

- **Add `rich >= 13`** to the base `[project] dependencies` in
  `pyproject.toml`. ~1 MB, pure Python, widely used. Required because the new
  wizard panels / tables / prompts assume it.
- No other new deps. Multi-select checkbox built as a ~40-line helper using
  `rich.live.Live` + raw `termios` stdin reads (fallback to numbered toggle
  menu when `tty.setraw` is unavailable, e.g. Windows).

## Layer 1 — `detection.py`

```python
def detect(project_root: Path) -> DetectionReport: ...

class DetectionReport(BaseModel):
    llm_clis: dict[str, LlmCli]      # "claude", "codex", "gemini", "aider", "cursor", "gh_copilot"
    api_keys: dict[str, bool]        # presence only — never the value
    config_dirs: list[ConfigDir]     # ~/.claude, ~/.codex, ~/.gemini (standard paths only)
    project: ProjectFingerprint      # has_git, has_tesserae, has_understand_anything, ...
    python: PythonEnv                # version, in_venv, tesserae_importable, raganything_importable
    recommended: Recommendations     # derived defaults + warnings
```

**Probing rules (all best-effort, never raise):**

- LLM CLIs: `shutil.which("claude")`, etc. If found, run `<bin> --version`
  with a 1-second timeout and capture the first line. Failure → `version=None,
  available=True`.
- API keys: `os.environ.get("ANTHROPIC_API_KEY") is not None` — boolean only.
  Never log or store the value.
- Config dirs: only documented defaults (`~/.claude`, `~/.codex`,
  `~/.gemini`). Custom multi-account paths are entered by the user in the
  wizard, not auto-discovered.
- Python env: `sys.executable`, `sys.version_info`, presence of
  `VIRTUAL_ENV`, `importlib.util.find_spec("tesserae")` etc.
- Project fingerprint: file/dir existence checks under `project_root`.

**Recommendations rules:**

| Condition                                          | Recommended value                  |
|----------------------------------------------------|-------------------------------------|
| `claude` CLI present                               | `extractor = "claude-cli"`          |
| `CLAUDE_CONFIG_DIR` env set                        | `claude_config_dir = $CLAUDE_CONFIG_DIR` |
| `~/.claude` exists, env var unset                  | `claude_config_dir = "~/.claude"`   |
| Only `codex` present                               | `extractor = "codex"`               |
| No LLM CLI, no API keys                            | `extractor = "deterministic"`       |
| Python < 3.10                                      | warning + `raganything_available = False` |
| `.understand-anything/knowledge-graph.json` exists | `include_understand_anything = True` |

API keys influence `extractor` only when no CLI is present — we prefer CLIs
over raw-API extractors because they handle auth via OS keychain.

## Layer 2 — `plan.py`

```python
def build_plan(
    detection: DetectionReport,
    *,
    overrides: dict | None = None,
) -> SetupPlan: ...
```

`SetupPlan` is a pydantic model. Fields:

```python
class SetupPlan(BaseModel):
    # identity
    project_root: Path
    name: str
    source_kind: str = "Repository"
    sources: list[str]

    # extraction (new — currently CLI-flag-only, never persisted)
    extractor: Literal["deterministic", "claude-cli", "codex", "selective-claude"]
    claude_config_dir: str | None = None
    claude_model: str | None = None
    codex_model: str | None = None

    # companion tools (existing shape, untouched)
    external_tools: list[dict]              # passthrough to .tesserae/config.json
    memory_backends: dict[str, dict]        # passthrough

    # explicit action lists (new — replaces scattered auto_install bools)
    install_actions: list[InstallAction]
    run_actions: list[RunAction]

    # provenance (new — for drift detection in apply)
    detection: DetectionReport
    warnings: list[str]
    created_at: datetime

class InstallAction(BaseModel):
    id: str                # "raganything", "cognee", "understand-anything"
    description: str       # human-readable
    command: str           # shell command, with {python}, {project} placeholders
    required: bool = False

class RunAction(BaseModel):
    id: str
    description: str
    command: str
```

`build_plan` consumes `detection.recommended` to populate defaults, then
applies `overrides` (a flat dict of any `SetupPlan` field) on top. Validation
errors raise `PlanValidationError` with a clean message.

Two key behaviors moved from today's scattered code:
- **Extractor lives in the plan**, gets written to `.tesserae/config.json`
  under `extraction: { backend, claude_config_dir, ... }`. Future
  `tesserae compile` reads this as default.
- **Install/run actions are first-class list items** rather than nested
  `auto_install: bool` on each tool. The apply layer can iterate and skip
  individual actions cleanly.

## Layer 3 — `wizard.py`

```python
def run_wizard(
    detection: DetectionReport,
    defaults: SetupPlan | None = None,
    *,
    console: rich.console.Console | None = None,
) -> SetupPlan: ...
```

Raises `WizardNotInteractive` if `sys.stdin.isatty()` is False. Callers
(`tesserae setup --yes`, MCP) handle this by calling `build_plan` directly.

### Step layout

**Step 0 — Banner.** A `rich.panel.Panel` summarizing the detection report
(project root, Python, venv, LLM CLIs with version, API keys with presence
checkmarks, companion artifacts found). No prompts.

**Step 1 — Project basics.** Three prompts:
- Wiki name (`rich.prompt.Prompt`, default = sanitized project dir name)
- Source kind (`rich.prompt.Prompt`, default = "Repository")
- Sources (custom multi-select checkbox over discovered candidates +
  free-text "add more paths" line)

**Step 2 — Extractor backend.** Single-select with the recommended option
pre-highlighted. Branching prompts:
- `claude-cli` → prompt for `claude_config_dir` (default from detection,
  free text for multi-account)
- `codex` → prompt for `codex_model` (default "gpt-5.4")
- `selective-claude` → both of the above
- `deterministic` → no further prompts

**Step 3 — Companion tools.** Multi-select checkbox. For each tool selected
but not installed, expand an inline confirm prompt: "Install it now?
[Y/n]". Tools whose requirements aren't met (e.g. raganything on Python
3.9) are shown grayed out with the warning attached.

**Step 4 — Review.** A `rich.table.Table` listing every action (write
config, install X, run Y) with row colors (green=write, yellow=install,
cyan=run, red=warning). Final prompt: `[Apply] [Edit step N] [Cancel]`.
"Edit step N" jumps back to that step preserving prior answers.

### `--yes` (non-interactive CLI mode)

Skip Steps 1–3, run Step 0 and Step 4 (review-only, auto-confirm). Used by
CI and any caller passing `--yes`. The `tesserae setup` subcommand keeps all
its existing flags so the user can still pin every field from the command
line.

## Layer 4 — `apply.py`

```python
def apply_plan(
    plan: SetupPlan,
    *,
    confirm_install_actions: bool = False,
    confirm_run_actions: bool = False,
    drift_policy: Literal["warn", "abort", "ignore"] = "warn",
) -> SetupResult: ...

class SetupResult(BaseModel):
    config_path: Path
    actions_taken: list[ActionResult]
    warnings: list[str]
    drift: dict[str, tuple[Any, Any]]   # field → (old, new)
```

Algorithm:

1. **Drift check.** Re-run `detect()`. Compare to `plan.detection`. If
   material changes (LLM disappeared, Python version changed,
   `~/.claude` moved), honor `drift_policy`:
   - `"abort"` → raise `DriftError` with the diff
   - `"warn"` → continue but append to `warnings`
   - `"ignore"` → silent
2. **Write config.** Build `.tesserae/config.json` from the plan
   unconditionally — this is the safe step (overwriting prior config is
   the documented contract of `tesserae setup`).
3. **Install actions.** Skip entirely unless `confirm_install_actions=True`.
   For each action: `subprocess.run(shell=True, cwd=project_root)`, capture
   stdout/stderr (cap 2000 chars), append to `actions_taken`. Non-zero exit
   → fail the action but continue (configurable later if needed).

   **Security note.** `shell=True` is used because the Understand Anything
   installer is a `curl -fsSL <url> | bash -s {platform}` pipeline that
   needs a real shell. All command strings originate from hardcoded values
   in `tesserae/project.py` (`default_*_backend_config`,
   `understand_anything_install_command`) — never from user input or
   network sources. `{python}`, `{project}`, `{platform}` placeholders are
   `shlex.quote`-d before substitution (existing `expand_tool_command`
   behavior, preserved). Where a command does not need shell features (pip
   installs), the implementation prefers `shlex.split` + `shell=False` —
   this is an implementation detail per action, not a layer-wide choice.
4. **Run actions.** Same shape as install, gated by `confirm_run_actions`.
5. **Materialize understand-anything projection** (existing
   `materialize_understand_anything_source` logic, moved from
   `project_setup.py`).
6. Return `SetupResult`.

## Layer 5 — MCP integration (`tesserae/mcp_server.py`)

Two new tools wired into the existing `_register_tools` / `tools` registry:

### `tesserae_setup_plan`

```
Args:
  project_root: str = "."
  overrides: dict | None = None
Returns:
  {
    "plan": <SetupPlan JSON>,
    "rendered_summary": "<text panel suitable for showing to user>"
  }
```

Calls `detect()` → `build_plan(detection, overrides)`. Never writes. The
`rendered_summary` is `wizard.render_review(plan)` — the same Step 4 review
table, but as plain text. Agent shows this to the user.

### `tesserae_setup_apply`

```
Args:
  plan: dict                          # SetupPlan JSON from _plan, possibly mutated
  confirm_install_actions: bool = False
  confirm_run_actions: bool = False
  drift_policy: str = "warn"
Returns:
  {
    "config_path": str,
    "actions_taken": [...],
    "warnings": [...],
    "drift": {...}
  }
```

Parses `plan` back into `SetupPlan`, calls `apply_plan`. Returns structured
result.

**Agent flow:**
1. `tesserae_setup_plan` → show summary to user
2. Ask user: "install raganything? run the understand-anything refresh?"
3. `tesserae_setup_apply` with the confirms set accordingly
4. Report result

## Layer 6 — Slash command

`commands/setup.md` flips from a terminal-only stub to a model-invokable
command:

```yaml
---
description: Run Tesserae setup — detect environment, propose a plan, apply with confirmation.
argument-hint: ""
allowed-tools:
  - "mcp__tesserae__tesserae_setup_plan"
  - "mcp__tesserae__tesserae_setup_apply"
---

Run `tesserae_setup_plan` for the current project. Show the rendered
summary to the user. Ask whether to install any flagged dependencies and
whether to run any post-setup refresh commands. Then call
`tesserae_setup_apply` with the appropriate `confirm_*` flags.
```

`scripts/tesserae-setup-help.sh` stays as a fallback for users who want the
old terminal-only path (documented in the README, not auto-invoked).

## CLI changes

`tesserae setup` keeps every existing flag — the rewrite only changes:

- `--interactive` (default when no flags + stdin is a TTY) now invokes
  `setup.run_wizard` instead of `interactive_setup_plan`.
- When `sys.stdin.isatty()` is False and no `--yes`, the command fails with
  a clear "run with --yes or from a terminal" message instead of crashing on
  EOF.
- `--yes` mode prints the Step 4 review panel before applying (today it
  applies silently).

## Testing strategy

- **`detection.py`**: unit tests with `monkeypatch`-faked `shutil.which`,
  env vars, and `Path.exists`. No real subprocess calls.
- **`plan.py`**: parameterized tests over recommendation rules. Round-trip
  serialization (`SetupPlan.model_dump_json()` → `SetupPlan.model_validate_json`).
- **`wizard.py`**: not unit-tested for the interactive flow (raw stdin
  handling is tedious). Smoke test that `run_wizard` raises
  `WizardNotInteractive` under a non-TTY pytest harness. Manual test
  checklist in PR description for the TTY paths.
- **`apply.py`**: tests with a `tmp_path` project root, `subprocess.run`
  monkeypatched to a fake. Drift detection tested by mutating the recorded
  detection vs the live detection.
- **MCP tools**: integration test that calls `tesserae_setup_plan` then
  `tesserae_setup_apply` and asserts `.tesserae/config.json` matches the
  plan.

## Migration / backwards compatibility

- `tesserae/project_setup.py` shrinks to ~30 lines of re-exports. Existing
  callers (CLI, MCP server, tests) keep importing `apply_setup_plan`,
  `build_setup_plan`, `interactive_setup_plan`, `render_setup_summary`,
  `refresh_configured_external_tools`, `materialize_understand_anything_source`
  from there with no code change.
- The `interactive_setup_plan` re-export points at the new wizard. Behavior
  changes (panels, detection summary) but signature stays.
- `.tesserae/config.json` gains an optional `extraction` block. Older
  configs without it remain valid; consumers default to `deterministic`.
- `commands/setup.md` is the only slash-command file that changes
  meaningfully. The mirror files under `.zed/`, `.opencode/`,
  `.codecompanion/`, `.github/prompts/` are regenerated by HarnessSync from
  `commands/setup.md` — no manual edits.

## Open questions resolved during brainstorm

- **TUI lib:** `rich` (single dep, ~1 MB). Not stdlib-only.
- **Detection scope:** LLM CLIs + API keys + project structure + Python env.
  All four.
- **Multi-account config dirs:** user-provided override only; no globbing
  of custom naming patterns (e.g. `~/.claude-personal*`).
- **MCP shape:** two tools (`_plan` + `_apply`), not one. Stateless,
  idempotent.
- **Detection use:** pre-fill, user confirms. Not auto-pick.
