# Idempotent Agent-Instruction Pointer Install Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Agents discover the compiled Tesserae context at session start without manual wiring. Add `install_instruction_pointer(project_root, project_name)` — an idempotent, marker-delimited "## Tesserae" pointer block installed into the target repo's top-level `AGENTS.md` / `CLAUDE.md` (creating `AGENTS.md` when neither exists) — hooked into the setup apply flow (default on, wizard-confirmable, MCP-safe) and exposed as `tesserae export harness --install-pointer`.

**Origin:** OpenWiki research finding #1 (9-0 verified). OpenWiki's adoption trick is a thin pointer: it appends a fixed reference section to top-level `/AGENTS.md` and/or `/CLAUDE.md`, refreshing only when missing or stale, never rewriting for formatting-only differences (`openwiki/src/agent/prompt.ts` lines 57-78). Tesserae generates equivalent routing content (`tesserae/agent_harness.py` → `.tesserae/agent_harness/`) but the manifest says "Copy or symlink…" — installation is manual. Verified 2026-07-09: zero `AGENTS.md`/`CLAUDE.md` write handling exists in `tesserae/agent_harness.py`, `tesserae/setup/*.py`, or `tesserae/project_setup.py`.

**Architecture:** One pure renderer + one installer in `tesserae/agent_harness.py` (the module that already owns agent-facing instruction content). Setup hook: a boolean `install_agent_pointer` plan field (`setup/plan.py`) consumed by `apply_plan` (`setup/apply.py`), confirmed in the wizard (`setup/wizard.py`), allowlisted for the MCP apply path (`mcp_server.py`). Manual hook: `--install-pointer` flag on `tesserae export harness` (`cli.py` → `ProjectWiki.export_agent_harness` in `project.py`).

**Tech Stack:** Python 3 stdlib only (`pathlib`, `str.find`). No new dependencies. Tests in existing pytest files, run with `.venv/bin/python -m pytest`.

## Global Constraints

- **Byte-idempotence (broke 4x historically — non-negotiable):** the pointer block is a pure function of `project_name`. NO timestamps, NO node/edge counts, NO absolute paths, NO graph-derived content of any kind inside the block. Re-running install on a current file must be a byte-level no-op: compare exact bytes and **do not write** when status is `current`. The only new disk writes are: (a) `AGENTS.md` created with exactly `block + "\n"` when neither instruction file exists — stable because the block is a constant given `project_name`; (b) an in-place splice between markers when the block is stale — stable because the surrounding bytes are preserved verbatim and the new block is that same constant.
- **Top-level files only** (OpenWiki rule): consider only `<project_root>/AGENTS.md` and `<project_root>/CLAUDE.md`. Never touch nested instruction files.
- **Never mangle user content:** replace only between markers; if markers are malformed (begin without end, or end before begin), return status `malformed` and leave the file untouched.
- **`@AGENTS.md` include awareness:** when `CLAUDE.md` contains the literal `@AGENTS.md`, `AGENTS.md` exists, and `CLAUDE.md` does not already carry the markers, skip `CLAUDE.md` (status `skipped-include`) — Claude Code inlines `AGENTS.md`, so writing both would double-inject (this repo itself uses that pattern via HarnessSync).
- **MCP security:** the new intent key is a plain boolean; add it to `_MCP_SAFE_INTENT_KEYS` in `mcp_server.py`. No command strings involved.
- Reuse, don't duplicate: relative artifact paths in the block must match the names already emitted by `render_harness_context` (`.tesserae/graph.json`, `.tesserae/agent_harness/TESSERAE.md`).
- One commit per task; conventional messages.

## Non-Goals

- No hook into `tesserae refresh` (`_handle_refresh` pipeline) — setup installs it once; the block never goes stale on compile because it contains no compiled data. Revisit only if the block ever gains dynamic content (it should not).
- No per-target variants (gemini/kiro/cursor/opencode config installation stays manual via `.tesserae/agent_harness/`); this plan covers only the two instruction files agents read by convention.
- No removal/uninstall command (delete the marker block by hand).

## File Structure

- **Modify** `tesserae/agent_harness.py` — `POINTER_BEGIN`, `POINTER_END`, `render_pointer_block(project_name)`, `_splice_pointer(text, block)`, `install_instruction_pointer(project_root, project_name)`.
- **Modify** `tesserae/setup/plan.py` — `SetupPlan.install_agent_pointer: bool = True`; `build_plan` pops the override.
- **Modify** `tesserae/setup/apply.py` — `apply_plan` calls the installer after the config write; records an `actions_taken` entry.
- **Modify** `tesserae/setup/wizard.py` — `Confirm.ask` for the pointer; `render_review` row.
- **Modify** `tesserae/mcp_server.py` — add `"install_agent_pointer"` to `_MCP_SAFE_INTENT_KEYS`.
- **Modify** `tesserae/project.py` — `export_agent_harness(..., install_pointer: bool = False)`.
- **Modify** `tesserae/cli.py` — `--install-pointer` on `export harness` subparser + handler pass-through.
- **Test** `tests/test_agent_harness.py`, `tests/test_setup_plan.py`, `tests/test_setup_apply.py`, `tests/test_setup_mcp.py`, `tests/test_cli_commands.py`.

---

### Task 1: Pointer block renderer + idempotent installer

**Files:** Modify `tesserae/agent_harness.py`; Test `tests/test_agent_harness.py`

**Interfaces:**

```python
POINTER_BEGIN = "<!-- tesserae:pointer:begin -->"
POINTER_END = "<!-- tesserae:pointer:end -->"

def render_pointer_block(project_name: str) -> str: ...
def install_instruction_pointer(project_root: str | Path, project_name: str) -> dict[str, str]: ...
```

`render_pointer_block` returns exactly (no trailing newline; markers included):

```markdown
<!-- tesserae:pointer:begin -->
## Tesserae

Project `{project_name}` has a compiled Tesserae knowledge graph in `.tesserae/`.

Start here:
- `.tesserae/agent_harness/TESSERAE.md` — compiled context brief (artifacts, MCP config, agent instructions)
- `.tesserae/graph.json` — authoritative typed ResearchGraph (markdown pages are projections)

Query the graph via the local MCP server instead of grep-style rediscovery:

    python3 -m tesserae.mcp_server --graph .tesserae/graph.json

Preferred MCP tools: `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`, `compile_context`.
<!-- tesserae:pointer:end -->
```

`install_instruction_pointer` semantics (returns `{filename: status}`):

1. `targets = [p for p in (root/"AGENTS.md", root/"CLAUDE.md") if p.exists()]`.
2. Neither exists → write `AGENTS.md` = `block + "\n"`, return `{"AGENTS.md": "created"}`.
3. For each existing target, read text, then:
   - `CLAUDE.md` + `AGENTS.md` in targets + `"@AGENTS.md" in text` + `POINTER_BEGIN not in text` → `skipped-include`, no write.
   - `_splice_pointer(text, block)`:
     - both markers absent → `(text.rstrip("\n") + ("\n\n" if text.strip() else "") + block + "\n", "appended")`
     - both present in order → existing span `text[b : e+len(POINTER_END)]`; byte-equal to `block` → `(text, "current")`, else splice-replace that span only → `"updated"`
     - one marker / wrong order → `(text, "malformed")`
   - Write only when status is `appended` or `updated`.

- [ ] **Step 1: Write the failing tests** in `tests/test_agent_harness.py` (append a `# ---- pointer install (07-09)` section):

```python
from tesserae.agent_harness import (
    POINTER_BEGIN, POINTER_END, install_instruction_pointer, render_pointer_block,
)

def test_pointer_block_is_deterministic():
    a, b = render_pointer_block("demo"), render_pointer_block("demo")
    assert a == b
    assert a.startswith(POINTER_BEGIN) and a.endswith(POINTER_END)
    assert ".tesserae/graph.json" in a and "TESSERAE.md" in a

def test_install_pointer_creates_agents_md_when_neither_exists(tmp_path):
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "created"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text == render_pointer_block("demo") + "\n"
    assert not (tmp_path / "CLAUDE.md").exists()

def test_install_pointer_appends_to_existing_files_preserving_content(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Mine\n\nkeep me\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "appended", "CLAUDE.md": "appended"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\nkeep me\n")
    assert POINTER_BEGIN in text and text.endswith(POINTER_END + "\n")

def test_install_pointer_is_idempotent_second_run_no_write(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    install_instruction_pointer(tmp_path, "demo")
    before = (tmp_path / "AGENTS.md").read_bytes()
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "current"}
    assert (tmp_path / "AGENTS.md").read_bytes() == before  # byte-idempotent

def test_install_pointer_refreshes_stale_block_in_place(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "top\n\n" + POINTER_BEGIN + "\nOLD STALE BODY\n" + POINTER_END + "\n\nbottom\n",
        encoding="utf-8",
    )
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "updated"}
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "OLD STALE BODY" not in text
    assert text.startswith("top\n\n") and text.endswith("\n\nbottom\n")
    assert render_pointer_block("demo") in text

def test_install_pointer_skips_claude_md_with_agents_include(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "appended", "CLAUDE.md": "skipped-include"}
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"

def test_install_pointer_leaves_malformed_markers_untouched(tmp_path):
    body = "x\n" + POINTER_BEGIN + "\nno end marker\n"
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")
    result = install_instruction_pointer(tmp_path, "demo")
    assert result == {"AGENTS.md": "malformed"}
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == body
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_agent_harness.py -k pointer -v` → ImportError.

- [ ] **Step 3: Implement** in `tesserae/agent_harness.py` (module-level, below `write_text`):

```python
def render_pointer_block(project_name: str) -> str:
    # DETERMINISM: pure function of project_name — no counts, timestamps,
    # or graph content. This block is written into USER instruction files;
    # any dynamic value here reintroduces the byte-idempotence bug class.
    body = "\n".join([...per the literal template above...])
    return POINTER_BEGIN + "\n" + body + "\n" + POINTER_END

def _splice_pointer(text: str, block: str) -> tuple[str, str]:
    b, e = text.find(POINTER_BEGIN), text.find(POINTER_END)
    if b == -1 and e == -1:
        base = text.rstrip("\n")
        return (base + "\n\n" + block + "\n") if base.strip() else (block + "\n"), "appended"
    if b == -1 or e == -1 or e < b:
        return text, "malformed"
    span = text[b : e + len(POINTER_END)]
    if span == block:
        return text, "current"
    return text[:b] + block + text[e + len(POINTER_END):], "updated"

def install_instruction_pointer(project_root: str | Path, project_name: str) -> dict[str, str]:
    root = Path(project_root)
    block = render_pointer_block(project_name)
    targets = [p for p in (root / "AGENTS.md", root / "CLAUDE.md") if p.exists()]
    if not targets:
        (root / "AGENTS.md").write_text(block + "\n", encoding="utf-8")
        return {"AGENTS.md": "created"}
    has_agents = any(p.name == "AGENTS.md" for p in targets)
    results: dict[str, str] = {}
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if path.name == "CLAUDE.md" and has_agents and "@AGENTS.md" in text and POINTER_BEGIN not in text:
            results[path.name] = "skipped-include"
            continue
        new_text, status = _splice_pointer(text, block)
        if status in ("appended", "updated"):
            path.write_text(new_text, encoding="utf-8")
        results[path.name] = status
    return results
```

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/test_agent_harness.py -v` (all, including preexisting).

---

### Task 2: Setup-flow hook (plan field → apply → wizard → MCP allowlist)

**Files:** Modify `tesserae/setup/plan.py`, `tesserae/setup/apply.py`, `tesserae/setup/wizard.py`, `tesserae/mcp_server.py`; Test `tests/test_setup_plan.py`, `tests/test_setup_apply.py`, `tests/test_setup_mcp.py`

**Interfaces:**
- `SetupPlan.install_agent_pointer: bool = True` (after `codex_model`, before `external_tools`).
- `build_plan`: `install_agent_pointer = bool(overrides.pop("install_agent_pointer", True))`; pass to the `SetupPlan(...)` constructor.
- `apply_plan`: after the config write / before install actions:

```python
if plan.install_agent_pointer:
    from ..agent_harness import install_instruction_pointer
    pointer = install_instruction_pointer(project_root, plan.name)
    actions_taken.append({
        "id": "agent-pointer",
        "description": "Install Tesserae pointer block into AGENTS.md/CLAUDE.md",
        "status": "installed",
        "files": pointer,
    })
```

  Rationale for not gating behind `confirm_install_actions`: it executes no commands, is byte-idempotent, and is confirmed in the wizard / reviewable in the plan JSON — same trust level as the unconditional config write.
- `wizard.run_wizard`: after the companion multi-select, `install_pointer = Confirm.ask("Add a Tesserae pointer section to AGENTS.md/CLAUDE.md?", default=True)`; add `"install_agent_pointer": install_pointer` to the `build_plan` overrides. `render_review`: `if plan.install_agent_pointer: table.add_row("agent pointer", "AGENTS.md / CLAUDE.md marker block")`.
- `mcp_server.py`: add `"install_agent_pointer"` to `_MCP_SAFE_INTENT_KEYS` (~line 1889).

- [ ] **Step 1: Write the failing tests.** In `tests/test_setup_plan.py` (mirror `test_build_plan_applies_overrides` fixture style):

```python
def test_build_plan_records_install_agent_pointer_override(tmp_path):
    report = _report(tmp_path)  # reuse the file's existing detection fixture helper
    assert build_plan(report).install_agent_pointer is True
    plan = build_plan(report, overrides={"install_agent_pointer": False})
    assert plan.install_agent_pointer is False
    assert plan.intent["install_agent_pointer"] is False
```

In `tests/test_setup_apply.py` (mirror `test_apply_writes_config_without_running_installs` fixture style):

```python
def test_apply_installs_agent_pointer(tmp_path):
    plan = _plan(tmp_path)  # reuse the file's existing plan fixture helper
    apply_plan(plan)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "tesserae:pointer:begin" in text
    result = apply_plan(plan)  # second apply: pointer must be byte-stable
    entry = next(a for a in result.actions_taken if a["id"] == "agent-pointer")
    assert entry["files"]["AGENTS.md"] == "current"

def test_apply_skips_agent_pointer_when_disabled(tmp_path):
    plan = _plan(tmp_path, overrides={"install_agent_pointer": False})
    result = apply_plan(plan)
    assert not (tmp_path / "AGENTS.md").exists()
    assert all(a["id"] != "agent-pointer" for a in result.actions_taken)
```

In `tests/test_setup_mcp.py`: `test_mcp_apply_honors_install_agent_pointer_intent` — build a plan with `overrides={"install_agent_pointer": False}`, round-trip it through the `tesserae_setup_apply` handler (existing test harness in that file), assert no `AGENTS.md` was created.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_setup_plan.py tests/test_setup_apply.py tests/test_setup_mcp.py -v`.
- [ ] **Step 3: Implement** the four edits listed under Interfaces.
- [ ] **Step 4: Run to verify pass**, including the untouched wizard tests (`tests/test_setup_wizard.py` — update its scripted `Confirm` responses if the new prompt shifts the sequence).

---

### Task 3: `tesserae export harness --install-pointer`

**Files:** Modify `tesserae/project.py`, `tesserae/cli.py`; Test `tests/test_cli_commands.py`

**Interfaces:**
- `ProjectWiki.export_agent_harness(self, targets=None, output=None, install_pointer: bool = False)` — after `write_harness`, when `install_pointer`: `result["pointer"] = install_instruction_pointer(self.project_root, name)` (import `install_instruction_pointer` alongside the existing `AgentHarnessAdapter` import in `project.py`).
- `cli.py`: `p_harness.add_argument("--install-pointer", action="store_true", help="Also install/refresh the Tesserae pointer block in the project's AGENTS.md/CLAUDE.md")` in `_build_export_parser`; `_handle_export_agent_harness` forwards `install_pointer=getattr(args, "install_pointer", False)` and, when a `pointer` key is present, prints one line per file: `f"Pointer: {name} {status}"`.

- [ ] **Step 1: Write the failing test** in `tests/test_cli_commands.py` (reuse that file's compiled-project fixture pattern for `export harness`):

```python
def test_export_harness_install_pointer_flag(tmp_path, ...):
    # arrange: minimal project with compiled graph (reuse existing fixture/helper)
    rc = main(["export", "harness", "--project", str(proj), "--install-pointer"])
    assert rc == 0
    assert "tesserae:pointer:begin" in (proj / "AGENTS.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the two edits.
- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/test_cli_commands.py -k install_pointer -v`, then the full suite: `.venv/bin/python -m pytest tests/test_agent_harness.py tests/test_setup_plan.py tests/test_setup_apply.py tests/test_setup_wizard.py tests/test_setup_mcp.py tests/test_cli_commands.py`.

---

## Disk-write inventory (byte-idempotence audit)

| Write | Where | Why stable |
| --- | --- | --- |
| Create `AGENTS.md` (neither file exists) | `install_instruction_pointer` | content = `render_pointer_block(name) + "\n"`, a pure function of the config `name` |
| Append block to existing `AGENTS.md`/`CLAUDE.md` | `_splice_pointer` "appended" | happens at most once; thereafter exact span match returns `current` with **no write** |
| Replace span between markers | `_splice_pointer` "updated" | fires only when existing span != canonical block; surrounding bytes preserved verbatim |
| `.tesserae/config.json` | `apply_plan` (pre-existing) | unchanged by this plan |
| `.tesserae/agent_harness/*` | `write_harness` (pre-existing) | unchanged by this plan (its TESSERAE.md keeps node counts — that is why the pointer block must NOT embed it) |

## Risks / open questions

- **Default-on writes to user-owned files during `tesserae setup --yes`:** mitigated by wizard confirm, plan-review row, `install_agent_pointer: false` override, and the malformed-marker bail-out; but a user who never wanted any edit to their `CLAUDE.md` gets one on first apply. If reviewers object, flip the `build_plan` default to `detection`-independent `True` only on the wizard path and `False` on `--yes` — one-line change.
- **`@AGENTS.md` heuristic is literal substring match**; a commented-out `@AGENTS.md` would suppress the CLAUDE.md copy. Acceptable: AGENTS.md still carries the block.
- **Project rename** (`name` in config) changes the canonical block → one `updated` write on next install; deterministic thereafter.
