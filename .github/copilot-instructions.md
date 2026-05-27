<!-- Managed by HarnessSync -->
<!-- Last synced: 2026-05-27T13:56:45Z -->
<!-- [harness-sync:start source=CLAUDE.md line=1-158] -->
# [Project rules from CLAUDE.md]

@AGENTS.md

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |

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
- iii console: `/Users/neo/.local/bin/iii-console -p <port>`

Use agentmemory for **durable, cross-session, cross-agent** facts — decisions
about the project, user-confirmed preferences, recurring constraints, named
artifacts with future obligations. Recall before answering questions that
reference prior sessions, plans, or outcomes the conversation does not already
carry.

- **DO** save: confirmed decisions, user role/expertise, project-scoped
  conventions discovered through conversation, dated commitments.
- **DO NOT** save: ephemeral task state (use TodoWrite), in-flight plans (use a
  plan), or anything derivable from `git log` / current code.
- If the local server is unreachable, fall back to the file-based memory at
  `~/.claude-personal1/.../memory/` and tell the user the MCP is down — don't
  silently lose the write.

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

<!-- [harness-sync:end] -->
<!-- End HarnessSync managed content -->

## Available Skill Behaviors

- **adapt**: Adapt existing designs to work effectively across different contexts - different screen sizes, devices, platforms, or us
- **animate**: Analyze a feature and strategically add animations and micro-interactions that enhance understanding, provide feedback, 
- **arrange**: Assess and improve layout and spacing that feels monotonous, crowded, or structurally weak — turning generic arrangement
- **audit**: Run systematic quality checks and generate a comprehensive audit report with prioritized issues and actionable recommend
- **bolder**: Increase visual impact and personality in designs that are too safe, generic, or visually underwhelming, creating more e
- **brainstorming**: Help turn ideas into fully formed designs and specs through natural collaborative dialogue.
- **clarify**: Identify and improve unclear, confusing, or poorly written interface text to make the product easier to understand and u
- **claude-md-improver**: Audit, evaluate, and improve CLAUDE.md files across a codebase to ensure Claude Code has optimal project context.
- **codex-cli-runtime**: Use this skill only inside the `codex:codex-rescue` subagent.
- **codex-result-handling**: When the helper returns Codex output:
- **colorize**: Strategically introduce color to designs that are too monochromatic, gray, or lacking in visual warmth and personality.
- **context-mode**: <context_mode_logic>
- **critique**: Use the frontend-design skill — it contains design principles, anti-patterns, and the **Context Gathering Protocol**. Fo
- **ctx-cloud-setup**: Interactive onboarding flow to connect this plugin to Context Mode Cloud.
- **ctx-cloud-status**: Display the current cloud sync configuration, connection health, and event statistics.
- **ctx-doctor**: Run diagnostics and display results directly in the conversation.
- **ctx-stats**: Show context savings for the current session.
- **ctx-upgrade**: Pull latest from GitHub and reinstall the plugin.
- **delight**: Identify opportunities to add moments of joy, personality, and unexpected polish that transform functional interfaces in
- **dispatching-parallel-agents**: You delegate tasks to specialized agents with isolated context. By precisely crafting their instructions and context, yo
- **distill**: Remove unnecessary complexity from designs, revealing the essential elements and creating clarity through ruthless simpl
- **executing-plans**: Load plan, review critically, execute all tasks, report when complete.
- **extract**: Identify reusable patterns, components, and design tokens, then extract and consolidate them into the design system for 
- **finishing-a-development-branch**: Guide completion of development work by presenting clear options and handling chosen workflow.
- **frontend-design**: This skill guides creation of distinctive, production-grade frontend interfaces that avoid generic "AI slop" aesthetics.
- **gpt-5-4-prompting**: Use this skill when `codex:codex-rescue` needs to ask Codex or another GPT-5.4-based workflow for help.
- **harden**: Strengthen interfaces against edge cases, errors, internationalization issues, and real-world usage scenarios that break
- **mempalace**: A searchable memory palace for AI — mine projects and conversations, then search them semantically.
- **normalize**: Analyze and redesign the feature to perfectly match our design system standards, aesthetics, and established patterns.
- **onboard**: Use the frontend-design skill — it contains design principles, anti-patterns, and the **Context Gathering Protocol**. Fo
- **optimize**: Identify and fix performance issues to create faster, smoother user experiences.
- **overdrive**: Start your response with:
- **polish**: Use the frontend-design skill — it contains design principles, anti-patterns, and the **Context Gathering Protocol**. Fo
- **quieter**: Reduce visual intensity in designs that are too bold, aggressive, or overstimulating, creating a more refined and approa
- **receiving-code-review**: Code review requires technical evaluation, not emotional performance.
- **release**: Cut a release of Tesserae. NEVER skip a step; NEVER --no-verify; NEVER force-push.
- **requesting-code-review**: Dispatch a code reviewer subagent to catch issues before they cascade. The reviewer gets precisely crafted context for e
- **subagent-driven-development**: Execute plan by dispatching fresh subagent per task, with two-stage review after each: spec compliance review first, the
- **systematic-debugging**: Random fixes waste time and create new bugs. Quick patches mask underlying issues.
- **teach-impeccable**: Gather design context for this project, then persist it for all future sessions.
- **test-driven-development**: Write the test first. Watch it fail. Write minimal code to pass.
- **typeset**: Assess and improve typography that feels generic, inconsistent, or poorly structured — turning default-looking text into
- **ui-verify**: Triggered whenever a user request involves "make X look like Y", "move Z next
- **understand**: Analyze the current codebase and produce a `knowledge-graph.json` file in `.understand-anything/`. This file powers the 
- **understand-chat**: Answer questions about this codebase using the knowledge graph at `.understand-anything/knowledge-graph.json`.
- **understand-dashboard**: Start the Understand Anything dashboard to visualize the knowledge graph for the current project.
- **understand-diff**: Analyze the current code changes against the knowledge graph at `.understand-anything/knowledge-graph.json`.
- **understand-domain**: Extracts business domain knowledge — domains, business flows, and process steps — from a codebase and produces an intera
- **understand-explain**: Provide a thorough, in-depth explanation of a specific code component.
- **understand-knowledge**: Analyzes a Karpathy-pattern LLM wiki — a three-layer knowledge base with raw sources, wiki markdown, and a schema file —
- **understand-onboard**: Generate a comprehensive onboarding guide from the project's knowledge graph.
- **using-git-worktrees**: Ensure work happens in an isolated workspace. Prefer your platform's native worktree tools. Fall back to manual git work
- **using-superpowers**: <SUBAGENT-STOP>
- **using-tesserae**: Tesserae is a project-memory compiler. It produces a typed knowledge graph from the user's documents and code, an Obsidi
- **verification-before-completion**: Claiming work is complete without verification is dishonesty, not efficiency.
- **writing-plans**: Write comprehensive implementation plans assuming the engineer has zero context for our codebase and questionable taste.
- **writing-rules**: Hookify rules are markdown files with YAML frontmatter that define patterns to watch for and messages to show when those
- **writing-skills**: **Writing skills IS Test-Driven Development applied to process documentation.**


## Assistant Personas

- **architecture-analyzer**: You are an expert software architect. Your job is to analyze a codebase's file structure, summaries, and import relation
- **article-analyzer**: You are a knowledge graph extraction expert. Your job is to analyze wiki articles and extract **implicit** knowledge — e
- **assemble-reviewer**: You are a quality reviewer for the assembled knowledge graph produced by `merge-batch-graphs.py`. The script has already
- **code-architect**: 1. TodoWrite/TodoRead — Claude Code task tracking is not available in vscode. Workaround: use a plain text TODO file or 
- **code-explorer**: 1. TodoWrite/TodoRead — Claude Code task tracking is not available in vscode. Workaround: use a plain text TODO file or 
- **code-reviewer**: 1. Sub-agent dispatch — Claude Code's Agent tool is not available in vscode. Workaround: break the task into sequential 
- **code-simplifier**: You are an expert code simplification specialist focused on enhancing code clarity, consistency, and maintainability whi
- **codex-rescue**: 1. Sub-agent dispatch — Claude Code's Agent tool is not available in vscode. Workaround: break the task into sequential 
- **comment-analyzer**: You are a meticulous code comment analyzer with deep expertise in technical documentation and long-term code maintainabi
- **conversation-analyzer**: You are a conversation analysis specialist that identifies problematic behaviors in Claude Code sessions that could be p
- **domain-analyzer**: You are a business domain analysis expert. Your job is to identify the business domains, processes, and flows within a c
- **file-analyzer**: You are an expert code analyst. Your job is to read source files and produce precise, structured knowledge graph data (n
- **graph-reviewer**: You are a rigorous QA validator for knowledge graphs produced by the Understand Anything analysis pipeline. Your job is 
- **grd-baseline-assessor**: <role>
- **grd-code-reviewer**: <role>
- **grd-codebase-mapper**: <role>
- **grd-critique-agent**: <role>
- **grd-debugger**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-deep-diver**: <role>
- **grd-eval-planner**: <role>
- **grd-eval-reporter**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-executor**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-experiment-runner**: <role>
- **grd-feasibility-analyst**: <role>
- **grd-hypothesizer**: <role>
- **grd-integration-checker**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-knowledge-miner**: <role>
- **grd-migrator**: <role>
- **grd-phase-researcher**: 1. MCP tool `mcp__context7__resolve` — requires 'context7' MCP server (not available in vscode). Workaround: invoke the 
- **grd-plan-checker**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-planner**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-product-owner**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-project-researcher**: 1. MCP tool `mcp__context7__resolve` — requires 'context7' MCP server (not available in vscode). Workaround: invoke the 
- **grd-research-synthesizer**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-roadmapper**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **grd-surveyor**: 1. MCP tool `mcp__context7__resolve` — requires 'context7' MCP server (not available in vscode). Workaround: invoke the 
- **grd-synthesizer**: <role>
- **grd-verifier**: 1. Claude Code plugin environment variables — not available in vscode. Workaround: use hardcoded paths or standard envir
- **knowledge-graph-guide**: You are an expert on Understand-Anything knowledge graphs. You help users navigate, query, and understand the graph file
- **pr-test-analyzer**: 1. Sub-agent dispatch — Claude Code's Agent tool is not available in vscode. Workaround: break the task into sequential 
- **project-scanner**: You are a meticulous project inventory specialist. Your job is to scan a codebase directory and produce a precise, struc
- **silent-failure-hunter**: 1. Sub-agent dispatch — Claude Code's Agent tool is not available in vscode. Workaround: break the task into sequential 
- **tour-builder**: You are an expert technical educator who designs learning paths through codebases. Your job is to create a guided tour o
- **type-design-analyzer**: You are a type design expert with extensive experience in large-scale software architecture. Your specialty is analyzing

