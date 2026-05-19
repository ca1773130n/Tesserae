---
name: using-tesserae
description: Use when the user asks about the typed knowledge graph compiled by Tesserae, queries about past sessions ("what did we decide about X?", "what insights came up about Y?"), wiki / Obsidian vault content this project produces, or wants to compile, refresh, build the site, or sync the vault. Distinguishes when to use the bundled MCP tools (low-friction lookups during a conversation) versus the slash commands (workflow actions the user explicitly invokes).
---

# Using Tesserae from inside Claude Code

Tesserae is a project-memory compiler. It produces a typed knowledge graph from the user's documents and code, an Obsidian-friendly vault projection, and a queryable MCP server. This skill tells you when to reach for which surface.

## What's available

The Tesserae plugin gives you two distinct surfaces:

1. **MCP tools** — read-only graph queries the agent calls directly during a conversation. Tool names land under the plugin namespace: `mcp__plugin_tesserae_tesserae__<tool>`. The tools you'll use most:
   - `mcp__plugin_tesserae_tesserae__ask` — natural-language Q&A against the compiled graph
   - `mcp__plugin_tesserae_tesserae__search_nodes` — keyword/semantic node lookup
   - `mcp__plugin_tesserae_tesserae__node_context` — fetch one node plus its 1-hop neighbourhood
   - `mcp__plugin_tesserae_tesserae__list_sessions` — Session envelopes for the active project (newest first, with per-kind finding counts)
   - `mcp__plugin_tesserae_tesserae__find_session_findings` — every Session-derived finding linked to a node (filter by kind: `insight`, `decision`, `question`, `todo`, `hypothesis`, `takeaway`)
   - `mcp__plugin_tesserae_tesserae__list_projects` / `activate_project` — multi-project registry navigation

2. **Slash commands** — workflow actions the user explicitly invokes:
   - `/tesserae:compile` — full project compile
   - `/tesserae:ask "<question>"` — same as the MCP `ask` tool but for human invocation
   - `/tesserae:refresh` — sessions-import → compile → vault-sync, with summary
   - `/tesserae:status` — node/edge counts + last compile + session count
   - `/tesserae:setup` — interactive wizard (`disable-model-invocation: true` — never auto-invoke this; the user has to run it themselves)
   - `/tesserae:sessions-import`, `/tesserae:build-site`, `/tesserae:serve`, `/tesserae:obsidian-sync` — the remaining 1:1 CLI wrappers

## Decision: MCP tool or slash command?

| User's intent | Reach for |
|---|---|
| "What did we decide about X?" / "what's known about Y?" | `mcp__plugin_tesserae_tesserae__ask` — single call, in-conversation answer |
| "Show me everything linked to this paper" | `mcp__plugin_tesserae_tesserae__node_context` |
| "What sessions touched arxiv-XXXX?" / "what decisions came up about it?" | `mcp__plugin_tesserae_tesserae__find_session_findings` with the node id |
| "Recompile the graph" / "I made changes, update memory" | Suggest `/tesserae:refresh` (sessions-import + compile + vault-sync) |
| Just a status check | Suggest `/tesserae:status` |
| Interactive project setup | Suggest `/tesserae:setup` — do not auto-invoke |
| Build/preview the static site | `/tesserae:build-site && /tesserae:serve` |

**Rule of thumb**: read = MCP, write = slash command. Most MCP tools are graph queries against the already-compiled state; a few registry tools (`activate_project`, `register_project`, `unregister_project`) mutate the multi-project registry, but they don't run extraction or writes against any project's graph. The slash commands are the ones that run extraction, network calls, and file writes — those should always be the user's choice to initiate.

## Node type cheat sheet

Grouped by category. Helps decode MCP responses without guessing what a returned `type` field means.

**Research artifacts**
`Paper`, `Repository`, `SourceDocument`, `Project`, `Model`, `Dataset`, `Benchmark`, `Metric`, `Result`, `Organization`

**Concepts & techniques**
`Concept`, `TechnicalTerm`, `MathematicalConcept`, `MethodologicalConcept`, `Algorithm`, `ObjectiveFunction`, `ArchitecturePattern`, `TrainingParadigm`, `InferenceStrategy`, `EvaluationProtocol`, `Task`, `Capability`

**Field structure**
`ResearchField`, `ResearchTopic`, `ProblemArea`, `ApproachFamily`, `Trend`

**Code graph** (when the project includes source)
`CodeProject`, `SourceFile`, `CodeModule`, `CodeClass`, `CodeFunction`, `Dependency`

**Claims & evidence**
`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`, `OpenQuestion`, `EvidenceSpan`

**Synthesis layer** (compiler-generated overviews)
`Synthesis`

**Session graph** (private envelope + six finding kinds)
`Session` (envelope — private, no vault page, queryable via MCP only), `SessionInsight`, `SessionDecision`, `SessionQuestion`, `SessionTODO`, `SessionHypothesis`, `SessionTakeaway`

**Private** (in the graph for query reachability; no vault page)
`Person`, `Stub`

## Common recipes

### "I just made significant edits — make sure the graph reflects them"

Suggest `/tesserae:refresh`. It chains:
1. `tesserae sessions discover --import` (capture any new agent sessions inside this project)
2. `tesserae project compile` (rebuild the graph)
3. `tesserae project obsidian-sync` (push to the vault if configured)

The user gets a single-line summary at the end.

### "What's the latest insight we extracted about <topic>?"

Use the MCP tools in sequence:
1. `search_nodes` with the topic to find candidate nodes
2. `find_session_findings` with the chosen node id, filter `kinds=["insight"]`

Surface the bodies + the session each came from. Don't suggest a recompile unless the user signals their corpus has changed.

### "How fresh is the graph?"

Suggest `/tesserae:status` — shows last-compile timestamp + per-kind counts in one screen.

### Hooks already running in the background

The plugin's `SessionStart` hook prints a one-liner with the graph counts at the start of every session. The `SessionEnd` hook backgrounds a `sessions discover --import` + `project compile` (not a full refresh — vault-sync is intentionally skipped at session-close so it doesn't race with an Obsidian client that may already be syncing). So by the next session, this conversation's insights are already graph nodes; the user only needs `/tesserae:refresh` explicitly if they want the vault projection updated in the same pass.

## Anti-patterns

- Don't auto-invoke `/tesserae:setup` — it's a `disable-model-invocation` interactive wizard. Suggest it; the user runs it.
- Don't suggest `/tesserae:compile` when the user only asked a question. Prefer the `ask` MCP tool — no recompile needed.
- Don't recite the full node-type list to the user. Use it internally to interpret MCP responses; surface only what's relevant to their question.
- Don't fabricate a finding kind. The six are exactly: insight / decision / question / todo / hypothesis / takeaway. Anything else is wrong.
