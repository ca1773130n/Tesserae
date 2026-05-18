# Session graph

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/sessions.ko.md">한국어</a> · <a href="../i18n/integrations/sessions.zh.md">中文</a> · <a href="../i18n/integrations/sessions.ja.md">日本語</a> · <a href="../i18n/integrations/sessions.ru.md">Русский</a> · <a href="../i18n/integrations/sessions.es.md">Español</a> · <a href="../i18n/integrations/sessions.fr.md">Français</a> · <a href="../i18n/integrations/sessions.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae's session graph turns your Claude Code / Codex conversations about a project into first-class nodes in the typed knowledge graph, linked back to the documents that came up. After a compile, you can ask `tesserae project ask "what did we decide about 3D Gaussian Splatting?"` and get back specific Insight / Decision / Question / TODO / Hypothesis / Takeaway nodes with provenance back to the session that produced them.

## How it works

The pipeline is two passes per session:

1. **Structural** (always-on, no LLM). Reads the normalised `HarnessSession` records that `tesserae sessions discover --import` writes to `.tesserae/harness_sessions/`. For each session it mints a `Session` envelope node, emits `discussed_in` edges from every doc the agent opened, and turns the existing `decisions` field into `SessionDecision` nodes.
2. **LLM** (opt-in, **no API key required**). Sends the normalised transcript turns (the `metadata["turns"]` field — not the raw transcript file) to Claude with a JSON-only finding schema. Returns six kinds of findings, each citing back to specific turns and specific doc node IDs in the current graph. Cached by content_hash + project_root_hash so unchanged sessions skip the call on the next compile.

   Backend resolution matches the rest of Tesserae's "common path uses no API keys" promise:
   1. **`claude` CLI over OAuth** (preferred) — if the `claude` binary is on PATH and signed in (`claude /login`), Tesserae shells out to it the same way the existing `ClaudeCLIResearchExtractor` does. Multi-account users can pin a specific config dir via `CLAUDE_CONFIG_DIR`.
   2. **`ANTHROPIC_API_KEY`** (fallback) — used only if the CLI isn't available, e.g. headless servers and CI.
   3. Neither configured → no LLM pass. The structural pass still runs.

## Setup

```bash
# One-time: sign into Claude via the CLI (skip if already signed in).
claude /login

# One-time: import sessions for this project into .tesserae/harness_sessions/.
# Filters by cwd so only sessions that ran inside this project are imported.
tesserae sessions discover --import

# Compile. Structural pass runs free. LLM pass runs automatically when the
# `claude` CLI is signed in — no API keys, no env vars to set.
tesserae project compile
```

To run compile without sessions (e.g. on a server without any harness history):

```bash
tesserae project compile --no-sessions
```

To force structural-only (skip the LLM call even when a key is set):

```bash
tesserae project compile --sessions-llm=false
```

## Configuration

`.tesserae/config.json` accepts a `sessions` block:

```jsonc
{
  "sessions": {
    "enabled": true,                              // default true
    "llm_enabled": "auto",                        // auto | true | false
    "max_turns_per_chunk": 30,                    // LLM-chunking threshold
    "model": "claude-sonnet-4-7-20251201",        // override default
    "include_doc_id_context": 200                 // top-N doc IDs sent to LLM
  }
}
```

CLI flags override config. `llm_enabled = "auto"` (default) runs the LLM pass when the `claude` CLI is signed in OR when `ANTHROPIC_API_KEY` is set; without either, only the structural pass runs (no error, no outbound calls).

## Query

Two MCP tools land on top of the existing search/wiki ones:

* `list_sessions(since?, limit?)` — Session envelopes (id, started_at, title, finding counts) for the active project.
* `find_session_findings(node_id, kinds?)` — every Session-derived finding linked to `node_id` via `discussed_in` or `references`, optionally filtered to insight / decision / question / todo / hypothesis / takeaway.

From the CLI:

```bash
tesserae sessions list                # human view, shows per-session finding counts
tesserae project ask "what did we decide about extractor dedup?"
```

## Privacy

* With no signed-in `claude` CLI AND no `ANTHROPIC_API_KEY` (or with `--sessions-llm=false`), there are zero outbound network calls. Only the structural pass runs.
* When the LLM pass runs, the **full normalised transcript turns** for not-yet-cached sessions are sent to Claude. The transcript file itself stays on disk; only the LLM's JSON output is persisted to the graph and the per-session cache. The CLI path routes through your existing OAuth session — no new auth surface, no API key handling.
* Cache files live at `.tesserae/session_findings/<session_id>.findings.json` with both a `content_hash` and a `project_root_hash`. A cache file copied between projects is rejected on read — no cross-project replay.
* Sessions are filtered through `session_matches_project` after loading, so a transcript whose `cwd` was a sibling project never produces nodes in this project's graph.

## Vault layout

Findings render under the Obsidian vault as one page per finding, grouped by session:

```
<vault>/
  sessions/
    <session-id-slug>/
      cache-findings-by-content-hash.md     # SessionDecision
      path-index-needs-basename-suppression.md   # SessionInsight
      …
```

User notes inside the `<!-- user-notes:start -->` … `<!-- user-notes:end -->` block on any finding page survive recompile — same contract as every other vault page.

## Troubleshooting

* **No Session nodes appear after compile.** Did you run `tesserae sessions discover --import` first? The compile path only consumes `.tesserae/harness_sessions/`; it does NOT scan `~/.claude/projects/` automatically (that scan can take minutes on machines with thousands of historical sessions).
* **LLM cost concerns.** The CLI path costs nothing — it runs under your existing Claude OAuth quota. Cost only applies on the `ANTHROPIC_API_KEY` fallback. The cache means each session is sent to the LLM at most once per content-hash. Long sessions chunk at `max_turns_per_chunk` (default 30) with 5-turn overlap. To bound any usage, lower `max_turns_per_chunk`, lower `include_doc_id_context`, or set `--sessions-llm=false`.
* **A finding cites a node ID that doesn't exist.** The orchestrator validates every cited reference against the live doc graph and silently drops unknowns. If you see the warning in logs, the LLM hallucinated a citation — the surviving references are still trustworthy.

## Spec

The full design lives at [docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md](../superpowers/specs/2026-05-19-session-graph-extractor-design.md). The implementation plan is [docs/superpowers/plans/2026-05-19-session-graph-extractor-plan.md](../superpowers/plans/2026-05-19-session-graph-extractor-plan.md).
