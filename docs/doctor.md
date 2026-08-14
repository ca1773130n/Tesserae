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

The checks, grouped by category:

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
| `compile_lock` | processes | whether a live compile lock is held, and by which pid **and host** | report-only — doctor **never kills or removes a live lock** |
| `filesystem_locking` | processes | whether `.tesserae/` sits on a network filesystem, where `flock(2)` may be a silent no-op | report-only (it cannot prove cross-host enforcement — see below) |
| `daemon_pid` | processes | `daemon.<host>.pid` points at a live engine process | **SAFE**: removes **this host's** pidfile when its owner is dead; another machine's is reported, never touched |
| `llm_login` | environment | whether the config dirs the project would actually use exist | report-only — **does not verify credentials** (see below) |
| `optional_deps` | environment | status of optional dependencies (memex, raganything) | report-only (installs are networked) |
| `embedding_backend` | environment | a real semantic embedding backend is available | report-only (suggests `pip install tesserae[semantic]`) |
| `environment` | environment | wholesale environment detection summary | report-only section |
| `build_history` | hygiene | `.build-history` size and shape | **SAFE**: trims it, always preserving the newest `git_head` entry (the staleness check depends on it) |
| `idempotence` | hygiene | the output-snapshot `idempotence_suspect` tripwire | report-only (it's a bug signal, not something to auto-repair) |
| `orphan_worktrees` | hygiene | stale `git worktree` registrations | **SAFE**: `git worktree prune`; deleting directories is report-only |
| `hook_log_bloat` | hygiene | `.tesserae/.session-*-hook.log` growth | **SAFE**: rotates/truncates logs over 10 MB |
| `sidecars` | hygiene | `.tesserae/` entries against the sidecar registry ([`tesserae/sidecars.py`](sidecars.md)): orphaned `*.tmp.<pid>.<hex>` halves, manual `graph.json.bak-*` copies, unclassified entries | **SAFE**: removes orphaned tmp files whose writer pid is gone and that are over 24 h old; backups and unclassified entries are report-only |
| `code_scope_leftovers` | hygiene | leftovers from the retired code layer: `code-graph*.json`, code-typed rows in `sqlite.db` | report-only — the cleanup is a mass delete, so it lives on its own verb (see below) |

A crashing check is reported as an error finding — doctor itself never raises.

## What `llm_login` does and does not tell you

It reports that a config directory exists. It does **not** report that the CLI
inside it holds a valid token, and it says so in its own finding text.

The distinction is not pedantry. The check used to report `credentialed LLM CLI:
claude, codex` on the strength of files like `~/.claude/history.jsonl` — which
prove the CLI has been *used*, not that it can authenticate *now*. Run
back-to-back in the same second, `tesserae compile` printed `Claude CLI not
logged in (tried 1 config dir)` while doctor printed a green check. A diagnostic
that contradicts the failure you are standing in is worse than no diagnostic.

Verifying credentials means spending a real LLM call on every `tesserae doctor`,
which is not a cost this check takes on its own initiative. So it states only
what it checked. Use `tesserae compile` for the authoritative answer.

The check is scoped to the dirs the project would actually try, resolved through
the same path `ProjectWiki._build_json_client` uses — and it says nothing about
claude config dirs when the project's provider is `codex`.

## Shared disks and `flock(2)`

Every concurrency guarantee in Tesserae — the compile lock above all — rests on
`flock(2)` being enforced by the filesystem holding `.tesserae/`. Over NFS and
SMB that is configuration-dependent: without a working lock daemon, `flock` can
silently degrade to a no-op, and two hosts will then compile the same project at
the same time while each believes it holds an exclusive lock.

`filesystem_locking` reports what a single host can determine: the filesystem
type backing the project, whether it is a network filesystem, and whether an
`flock` acquisition succeeds at all. It warns on a network filesystem.

It **cannot** prove cross-host enforcement, and does not claim to. One host
taking a lock says nothing about whether a second host is prevented from taking
it. If you run Tesserae from several machines against shared storage, test that
directly on the real hardware before relying on the compile lock.

`filesystem_locking` is an `flock`-only probe and reports "unsupported on this
platform — skipped" on Windows. The locks themselves are not: `compile.lock`
and the agent-write lock take `flock(2)` where it exists and `msvcrt.locking`
where it does not, and the `compile_lock` check probes with those same two
helpers so it cannot report "unsupported" about a lock that works there. On an
interpreter carrying neither primitive, locking degrades to a no-op that says
so once per process rather than silently.

## `tesserae doctor migrate-code-scope`

A one-shot cleanup for a workspace compiled before source code left Tesserae's
scope. New compiles no longer produce the code layer, but an older workspace
still carries it, and most of it heals only when you ask.

```bash
tesserae doctor migrate-code-scope            # dry run — reports, deletes nothing
tesserae doctor migrate-code-scope --apply    # actually removes
```

It removes, in this order:

* projected pages under `.tesserae/markdown_projection/` whose own `type:`
  frontmatter names a retired code type;
* the same pages in the Obsidian vault — both the configured one and the
  in-project default, because a project that later pointed at a real vault
  leaves the old one behind full of them. A code page with non-empty
  `user-notes` content is kept and counted, never deleted;
* `code-graph.json` and `code-graph-cache.json`;
* SQLite sidecar rows (`node_provenance`, `edge_provenance`, `node_memory`)
  whose node or edge no longer exists, then `VACUUM`.

Two things to know:

**Read the survivor count, not the deletion count.** The projection directory
is overwhelmingly code-derived — measured here, 218,796 of 224,876 pages — so a
predicate bug that deleted everything and a correct run look nearly identical
in the number deleted. The report leads with how many non-code pages survive,
which is the number that would collapse if the predicate were wrong. Gating is
strictly per file, on that file's own frontmatter.

**Compile first, then migrate.** The `nodes` / `edges` tables and the
provenance sidecars are rewritten by every compile, so a compile is what makes
those rows garbage; this verb is what reclaims the space, because SQLite does
not shrink on `DELETE`. Running it beforehand is harmless — it says so and
finds nothing to reclaim. `VACUUM` is never run inside a compile: it takes an
exclusive lock and needs free disk on the order of the database file, and it is
skipped with a note when the disk cannot hold the rebuild.

It is deliberately not reachable from `--fix`, which is documented as safe
repairs only.

## `--fix` policy

- `--fix` runs **only** the checks marked SAFE above, then re-detects so the
  report reflects the post-fix state.
- Every fix is idempotent: running `doctor --fix` twice leaves the second run
  clean.
- Doctor **never kills a process and never removes a live compile lock** — a
  held lock is reported with its owning pid and host, and left alone.
- Doctor **never touches another machine's pidfile.** On shared storage the
  local process table says nothing about a pid written by a different host, so
  `daemon.<other-host>.pid` is reported and skipped unconditionally — it is not
  even read for liveness. Only this host's own pidfile is eligible for removal.
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

## `tesserae lint` — the finding codes

`doctor` runs lint's trivially-fixable subset; `tesserae lint` runs the whole
set and is where the detail lives. Every finding carries a stable code, so you
can grep a report or gate CI on one. `--severity {info,warning,error}` sets the
floor for the **exit code** — findings below it are still reported.

| Code | Severity | What it means |
|---|---|---|
| `AGENT_METADATA_KEY` | error | An agent node carries a metadata key outside the controlled set. The only error-level code; a malformed agent breaks scoped views. |
| `AGENT_WRITE_SKIPPED` | warning | A line in `.tesserae/agent-writes.jsonl` that replay skipped — that write is **not** in the graph. A truncated line is a torn concurrent append and the agent should re-file it; a hand-edited one should be corrected or removed. Replay skips and warns rather than failing, so one bad line never bricks every future compile — but a stderr line during a compile is not where you look for a write the agent believes it filed. |
| `ORPHAN_PAPER` | warning | A Paper with no outgoing edges and nothing but `mentioned_in` coming in — ingested, never connected. |
| `MISSING_IMPLEMENTED_IN` | warning | A Paper and a Repository share an `arxiv_id` but no `implemented_in` edge joins them. `--fix-trivial` adds it. |
| `STALE_CITATION` | warning | A wiki page links to a page that does not exist. |
| `DANGLING_HTML_LINK` | warning | Generated HTML points at a file that isn't there. |
| `GRAPH_WIKI_DRIFT` | warning | The graph and the wiki disagree — a public node with no page, or a page with no node. |
| `CONTRADICTING_CLAIMS` | warning · info | Two claims contradicted each other; reports how it was resolved. |
| `REASONING_EDGE_RATIO` | warning | Too few edges carry reasoning. A graph of bare `mentions` is a search index, not a knowledge base. |
| `SYNTHESIS_GHOST_INPUT` | warning | Synthesis frontmatter cites a node id that no longer exists. `--fix-trivial` prunes it. |
| `AGENT_FORGET_LEDGER` | warning | The last distill demoted findings — the ledger of what an agent stopped surfacing. |
| `INTERVAL_COVERAGE` | info | *How many facts carry no `valid_from`* and therefore sort last in any temporal answer. Previously silent; now stated as a percentage. |
| `LINT_PROBE_FAILED` | info | `INTERVAL_COVERAGE` could not run because the graph would not load — the check abstaining, said out loud rather than passing by default. |
| `PROCEDURAL_POOLS` | info | How much of the producer-owned procedural layer was actually minted. The reserved procedural retrieval slot is earned by provenance; this reports when it can't be filled honestly. |
| `AGENT_UNDISTILLED_BACKLOG` | info | An agent has accumulated scope findings well past its distill watermark. |
| `LOW_TITLE_QUALITY` | info | A Paper's title looks like a filename or a fragment rather than a title. |
| `SUGGESTED_MERGE` | info | Several Repository nodes share a `github_repo` URL — merge candidates, never merged automatically. |
| `SUGGESTED_SUBTYPE` | info | A cluster of same-typed nodes that schema-drift proposed a sub-type for — surfaced, never adopted automatically. Promotion is a human edit to `ResearchNodeType`, then `"approved": true` in `.tesserae/schema-drift-proposals.json`. |
| `PENDING_REVIEW` | info | Candidate merge pairs still awaiting a human verdict in `.tesserae/candidate-same-as.json`. A pair a reviewer rejected is never surfaced again, so this count is outstanding work rather than corpus size. Answer with `tesserae extract --apply-review-decisions … --reviewed-by <you>`. |
| `STALE_BUILD_HISTORY` | info | A build-history entry older than 90 days. |
| `CODE_GRAPH_BEHIND` · `CODE_GRAPH_HEAD_UNRESOLVED` · `CODE_GRAPH_STALE_FILE` | info | The opt-in code layer is out of step with `HEAD` — compiled at an older commit, at a commit git can no longer resolve, or over files that have since changed. |
| `CLAIM_SUPPORT_SKIPPED` · `CLAIM_SUPPORT_SUMMARY` | info | Results of the opt-in `--verify-claims` pass: what was sampled and how it scored, or why it did not run. |

`--fix-trivial` applies only the safe repairs (`MISSING_IMPLEMENTED_IN`,
`SYNTHESIS_GHOST_INPUT`). Everything else is reported for a human to judge.
`--verify-claims` is opt-in, needs an LLM backend, and costs one batched call.

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
