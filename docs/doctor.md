# `tesserae doctor` — project health checks

<!-- translations:start -->
<p align="center"><a href="i18n/doctor.ko.md">한국어</a> · <a href="i18n/doctor.zh.md">中文</a> · <a href="i18n/doctor.ja.md">日本語</a> · <a href="i18n/doctor.ru.md">Русский</a> · <a href="i18n/doctor.es.md">Español</a> · <a href="i18n/doctor.fr.md">Français</a> · <a href="i18n/doctor.de.md">Deutsch</a></p>
<!-- translations:end -->
`tesserae doctor` inspects a Tesserae workspace end to end — initialization,
graph integrity, registry consistency, freshness, locks, LLM login, and disk
hygiene — and prints a checklist. It is **read-only by default**; `--fix`
applies only the repairs that are safe to re-run and can never destroy live
state.

```bash
tesserae doctor                 # check the current project
tesserae doctor --fix           # apply the safe repairs, then re-check
tesserae doctor --all --json    # every registered project, JSON report
tesserae doctor --project ~/src/other
```

## What it checks

Twenty checks, grouped by category:

| Check | Category | What it verifies | `--fix` action |
|---|---|---|---|
| `project_initialized` | core | `.tesserae/` exists and looks like a Tesserae workspace | report-only (suggests `tesserae init`) |
| `graph_parse` | core | `graph.json` parses and has the expected shape | report-only (suggests `tesserae compile`) |
| `config_valid` | core | `.tesserae/config.json` parses and validates against the init template | report-only |
| `vault_configured` | core | the configured vault path resolves | **SAFE**: creates the resolved vault directory when it lives inside the project |
| `registry_consistent` | registry | `~/.tesserae/registry.json` entries point at real project roots | **SAFE**: prunes entries whose root is gone, drops the legacy `active` key; a missing graph is report-only |
| `graph_staleness` | freshness | git delta since the last compile's recorded `git_head` | report-only (suggests `tesserae refresh` — compiles are heavy) |
| `site_search_index` | freshness | the static site / `search-index.json` is newer than `graph.json` | **SAFE**: rebuilds the site |
| `backend_artifacts` | freshness | RAG-Anything artifacts are current | report-only (their refresh is LLM/network heavy) |
| `session_chunks` | freshness | [daily session-chunk](session-chunks.md) coverage has no gaps in the recent window | report-only (suggests `tesserae sessions chunk-backfill`) |
| `wiki_lint` | graph | graph ⇄ wiki drift + trivially fixable lint findings | **SAFE**: applies the lint trivial fixes (`fix_trivial`) |
| `compile_lock` | processes | whether a live compile lock is held, and by which pid | report-only — doctor **never kills or removes a live lock** |
| `daemon_pid` | processes | `daemon.pid` points at a live engine process | **SAFE**: removes the pidfile when its owner is dead |
| `llm_login` | environment | the configured LLM backend is actually usable (claude/codex CLI logged in, or API key present) | report-only (suggests `claude /login` / `codex login`) |
| `optional_deps` | environment | status of optional dependencies (memex, cognee, raganything) | report-only (installs are networked) |
| `embedding_backend` | environment | a real semantic embedding backend is available | report-only (suggests `pip install tesserae[semantic]`) |
| `environment` | environment | wholesale environment detection summary | report-only section |
| `build_history` | hygiene | `.build-history` size and shape | **SAFE**: trims it, always preserving the newest `git_head` entry (the staleness check depends on it) |
| `idempotence` | hygiene | the output-snapshot `idempotence_suspect` tripwire | report-only (it's a bug signal, not something to auto-repair) |
| `orphan_worktrees` | hygiene | stale `git worktree` registrations | **SAFE**: `git worktree prune`; deleting directories is report-only |
| `hook_log_bloat` | hygiene | `.tesserae/.session-*-hook.log` growth | **SAFE**: rotates/truncates logs over 10 MB |

A crashing check is reported as an error finding — doctor itself never raises.

## `--fix` policy

- `--fix` runs **only** the checks marked SAFE above, then re-detects so the
  report reflects the post-fix state.
- Every fix is idempotent: running `doctor --fix` twice leaves the second run
  clean.
- Doctor **never kills a process and never removes a live compile lock** — a
  held lock is reported with its owning pid and left alone.
- Heavy or networked operations (recompiles, dependency installs, backend
  refreshes) are never folded into `--fix`; doctor prints the command for you
  to run instead.

## Exit codes

Same convention as `tesserae lint`:

| Exit code | Meaning |
|---|---|
| `0` | healthy — no findings above OK |
| `1` | warnings present |
| `2` | errors present |

## Report artifacts

Every run writes both report forms into the workspace:

```text
.tesserae/doctor-report.md      # human checklist
.tesserae/doctor-report.json    # structured findings
```

`--json` additionally prints the JSON report to stdout instead of the markdown
checklist. `--all` iterates every project in the registry (ignoring
`--project`) and reports per project.

## MCP: `doctor_report`

The MCP server exposes the same report as the `doctor_report` tool (mirroring
`lint_report`, including its byte cap on returned content), so an agent can
check workspace health mid-conversation without shelling out. It requires a
project root — pass `graph_path`/`project` or configure a default graph.
