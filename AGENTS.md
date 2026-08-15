# Tesserae — a context engine

> Tesserae is a **context engine**. It generates agent-ready context by
> reconstructing a *self-improving* knowledge base from your project, and runs
> on three pillars:
>
> 1. **Session monitoring** — watch live agent/work sessions and capture what
>    happens as it happens.
> 2. **Autonomous, proactive knowledge ingestion** — the engine pulls in and
>    reconstructs knowledge on its own, continuously improving the base rather
>    than waiting to be told.
> 3. **On-demand docs** — user-requested artifacts generated from that same base.
>
> The goal: the user can always organize realtime, evolving, comprehensive
> knowledge and hand it to agents as context. The typed graph, vault, and static
> site are *projections* of the knowledge base; the engine is the loop that keeps
> them fresh and feeds agents. **Frame every feature against this mission.**

<!--
  FILE SPLIT — read before editing this file or CLAUDE.md.

  AGENTS.md  (this file)  harness-neutral. Every agent on this project reads it.
  CLAUDE.md               Claude Code only. It imports this file on line 1, then
                          adds the context-mode routing rules — a Claude Code
                          plugin, meaningless to other harnesses.

  Each rule lives in exactly ONE of these files. CLAUDE.md imports this one, so
  duplicating a rule into both does not add emphasis — it just creates two
  copies that drift apart. If a rule applies to any agent, it belongs here.

  This file must never contain an import of itself. HarnessSync used to generate
  the body of this file from CLAUDE.md verbatim, which dragged CLAUDE.md's line-1
  self-import along and made this file import itself. That block is gone. If a
  future `harness-sync` run restores it, the fix is to stop targeting AGENTS.md
  from CLAUDE.md — not to re-merge the two.
-->

## UI Verification

These rules cover any change that affects what the user sees on screen — CSS,
component layout, graph rendering, GIF screencasts, billboard placement.

- ALWAYS verify a visual change with a screenshot (Playwright MCP `browser_navigate`
  + `browser_snapshot` or `browser_take_screenshot`) BEFORE claiming the task is
  done. "It compiles" is not verification.
- For graph/canvas tuning, check whether the value lives in **pixel space** (2D
  view, CSS) or **world space** (3D `three.js` / `3d-force-graph`) BEFORE tweaking
  magnitudes. Picking the wrong space wastes a full iteration round.
- When the user says element X should be "adjacent to", "next to", or "inline
  with" element Y, place X **in the same flex/grid container as Y** — never at
  opposite ends of a parent bar. Re-read the request literally if uncertain.
- After a layout change, measure the affected element's bounding box (`getBoundingClientRect`
  via `browser_evaluate`) and compare against what the user asked for. If it
  doesn't match, iterate before responding.

## CodeGraph — code intelligence over an indexed symbol graph

This project has a CodeGraph MCP server. The index lives in `.codegraph/` at the
project root — a SQLite knowledge graph of every symbol, edge, and file in the
workspace. Reads are sub-millisecond; the file watcher keeps it ~1s behind disk.
Consult it BEFORE writing or editing code, not during.

If `.codegraph/` is missing, run `codegraph init` once then `codegraph index` to
build it. Check readiness with `codegraph_status` (pass `projectPath` if the MCP
server reports "No CodeGraph project is loaded").

### Tool selection by intent

- **"What is the symbol named X?"** → `codegraph_search`
- **"What's the deal with this feature / area?"** → `codegraph_context` (PRIMARY — composes search + node + callers + callees in one call)
- **"What calls this?"** → `codegraph_callers`
- **"What does this call?"** → `codegraph_callees`
- **"What would changing this break?"** → `codegraph_impact`
- **"Show me this symbol's source / signature / docstring."** → `codegraph_node`
- **"Survey several related symbols in an area."** → `codegraph_explore` (ONE capped call; prefer over many node/Read)
- **"What's in directory X?"** → `codegraph_files`
- **"Is the index ready?"** → `codegraph_status`

### Common chains

- **Onboarding / "how does X work?"** → `codegraph_context` first, then `codegraph_explore` on the surfaced symbols. Only fall back to Read/Grep for details CodeGraph didn't cover.
- **Refactor planning** → `codegraph_search` → `codegraph_callers` → `codegraph_impact`. Do NOT walk callers manually.
- **Debugging a regression** → `codegraph_context` → `codegraph_callers` to find the entry points that exercise the symbol.

Answer directly using 2–3 CodeGraph calls — do NOT delegate the lookup to a
sub-agent that just re-runs grep + read. CodeGraph IS the pre-built search
index; repeating the work elsewhere costs more for the same answer.

## agentmemory — persistent cross-session memory MCP

The `@agentmemory/agentmemory` MCP server runs locally:

- REST API: `http://localhost:3111`
- Viewer:   `http://localhost:3113`
- Streams:  `ws://localhost:3112`
- iii console: `~/.local/bin/iii-console -p <port>`

Use agentmemory for **durable, cross-session, cross-agent** facts — decisions
about the project, user-confirmed preferences, recurring constraints, named
artifacts with future obligations. Recall before answering questions that
reference prior sessions, plans, or outcomes the conversation does not already
carry.

- **DO** save: confirmed decisions, user role/expertise, project-scoped
  conventions discovered through conversation, dated commitments.
- **DO NOT** save: ephemeral task state (use TodoWrite), in-flight plans (use a
  plan), or anything derivable from `git log` / current code.
- If the local server is unreachable, fall back to the harness's own file-based
  memory directory and tell the user the MCP is down — don't silently lose the
  write.

Treat both memory layers as **claims about a point in time**: before acting on a
recalled fact, verify it against the current code / git state.

## Process Hygiene

These rules cover bug fixes in long-running scripts, dev servers, seeders,
background workers — anything that loads source once and keeps running.

- When fixing a bug in a long-running script: KILL the existing process first,
  then restart from scratch. Source edits do NOT propagate to an already-running
  Python interpreter or Node process. "I fixed the bug" + "the old worker is
  still emitting garbage" is the same failure mode.
- Before guessing at config directory names, file-naming conventions, or
  project structure: grep/read the codebase first. One `grep -rn` call is
  always cheaper than a "WTF" round-trip.
- After the fix is in place, show the actual fresh-run output that proves the
  fix took effect — not a diff of the code change.

## Testing

Run the suite with `uv run pytest`. `pyproject.toml` scopes collection to
`tests/` and puts the repo root on `sys.path`; without that config, bare
`pytest` walks into the gitignored upstream clones under `evals/` and fails
collection on ~39 of their modules.

`evals/` holds full clones of `topoteretes/cognee` and `C-Bjorn/MegaMem` —
877MB, gitignored, carrying uncommitted local work (custom `claude_cli`/
`codex_cli` LLM adapters, smoke tests, `.env` files) that exists in **no** git
repository. It is not recoverable if removed. Never clean it.

## Translating docs

Every doc under `docs/` is mirrored into seven languages under `docs/i18n/`.
Before touching a mirror, read **[docs/i18n/GLOSSARY.md](docs/i18n/GLOSSARY.md)** —
the house rendering of the terms that have been mistranslated more than once,
plus the rules a pass has to hold (never translate inside backticks, keep table
rows and columns, keep bullet and bold counts, numerals stay numerals).

`tests/test_docs_i18n.py` enforces the mechanical half as a ratchet against
`tests/fixtures/docs_i18n_parity_baseline.json`: a mirror may not drop an
identifier or a structural element the English has, beyond what the baseline
already records. It cannot see a wrong translation — only a missing one. That is
what the glossary is for.

<!-- tesserae:pointer:begin -->
## Tesserae

Project `tesserae` has a compiled Tesserae knowledge graph in `.tesserae/`.

Start here:
- `.tesserae/agent_harness/TESSERAE.md` — compiled context brief (artifacts, MCP config, agent instructions)
- `.tesserae/graph.json` — authoritative typed ResearchGraph (markdown pages are projections)

Query the graph via the local MCP server instead of grep-style rediscovery:

    python3 -m tesserae.mcp_server --graph .tesserae/graph.json

Preferred MCP tools: `graph_map` (canonical entry point for graph navigation), `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`, `compile_context`.
<!-- tesserae:pointer:end -->
