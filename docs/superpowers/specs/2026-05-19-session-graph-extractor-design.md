# Session graph extractor — design

> Status: design • 2026-05-19 • owner: Tesserae maintainers • supersedes: nothing

Tesserae compiles a typed knowledge graph from a `data/` directory of
documents. Today that graph stops at what's in the documents — every
conversation the user and their coding agent have *about* those
documents is lost on close-tab. The point of this spec is to make
those conversations a first-class part of the graph, so the agent
keeps accumulating project-specific understanding that the next
session can query back.

## Goal

After `tesserae project compile`, the typed graph contains structured
findings extracted from local Claude Code / Codex / equivalent
sessions, each linked to the documents that came up in that session.
An agent can ask `tesserae project ask "what did we conclude about
3D Gaussian Splatting?"` and get back specific insight nodes with
provenance back to the session that produced them and the paper(s)
they reference.

## Non-goals

- Real-time session capture (we read what's on disk, we don't hook
  into the agent's IPC).
- Cross-project memory (each project's `.tesserae/` keeps its own
  sessions; no global aggregation).
- Re-summarising the same session every compile when it hasn't
  changed (caching is core, not a stretch goal).
- Replacing the existing `Synthesis` projector. Daily/weekly digests
  continue to render the same way; session findings are an *input* to
  the next compile, not an alternative output channel.
- Editing or filtering transcripts. We read what's already normalised
  by the existing `discover_harness_sessions` flow; this spec doesn't
  touch transcript normalisation.

## User-visible behaviour

```bash
tesserae project compile                # auto-imports sessions, runs extraction
tesserae project compile --no-sessions  # skip session extraction entirely
tesserae project compile --sessions-llm=false  # structural pass only, no LLM
```

The default is: if `.tesserae/harness_sessions/` exists (auto-populated
by the implicit `discover_harness_sessions` call), the session graph
extractor runs. The LLM extraction layer runs only when
`ANTHROPIC_API_KEY` (or the configured equivalent) is set; without it,
only the structural pass runs and the graph gains `SessionActivity`
metadata but no finding nodes.

Query examples the agent can run after a compile:

- `tesserae project ask "what decisions did we make about extractor dedup?"`
- `tesserae project ask --backend wiki --kind SessionDecision`
- MCP: `find_related_nodes(node_id="Paper:arxiv-2601-17835:...", edge_type="discussed_in")`

## Architecture

A new module `tesserae/session_graph.py` exposes
`SessionGraphExtractor` with the same shape as
`ResearchGraphExtractor`: it produces a `ResearchGraph` slice that
`project.merge_graphs` combines with the document slice and the code
slice. The extractor runs after the document pass during
`ProjectWiki._compile_pipeline`:

```
document extractors  ─┐
code graph extractor ─┼─→ merge_graphs  ─→ projection layer
session extractor    ─┘  (id-dedup,
                          cross-type and
                          same-type merges)
```

### Module layout

```
tesserae/
  session_graph.py             # new: orchestration entrypoint
  session_graph_structural.py  # new: deterministic activity pass
  session_graph_llm.py         # new: LLM extraction pass + JSON parsing
  llm_synthesis.py             # existing: reused for LLM backend abstraction
  harness_sessions.py          # existing: source of truth for session records
```

Splitting structural and LLM passes into separate files keeps the
deterministic path independently testable (no LLM fixtures needed)
and lets us add a `pure-deterministic` fallback in the rare case the
LLM never returns parseable JSON.

### Where session findings live on disk

```
.tesserae/
  harness_sessions/                    # normalised HarnessSession JSON
    <date>-<slug>-<hash>.json
  session_findings/                    # NEW: extractor output cache
    <session_id>.findings.json         # cached LLM output, keyed by content_hash
    <session_id>.activity.json         # cached structural output
```

The cache files let the next compile skip both passes when neither the
transcript nor the session metadata has changed (content_hash of the
normalised session record). Stale caches are pruned by id-comparison
against the live `harness_sessions/` set.

## Schema additions

### Node types (six new entries in `ResearchNodeType`)

| Type | Semantics | Example body |
|---|---|---|
| `SessionInsight` | An observation / learned fact / pattern noticed. | "GS-Slam reuses NeRF-Studio's bundle adjustment loop, not its rasteriser." |
| `SessionDecision` | An explicit choice the user and agent agreed on. | "Cache LLM outputs by `(session_id, content_hash)` rather than session_id alone." |
| `SessionQuestion` | An unresolved question raised during the session. (Distinct from `OpenQuestion` which captures unresolved questions surfaced inside academic papers.) | "Does the spec say whether the dedup pass runs before or after the link-fixup?" |
| `SessionTODO` | An actionable follow-up. | "Add a `--sessions-llm=false` smoke test to test_session_graph.py." |
| `SessionHypothesis` | A testable assumption not yet verified. | "The slug collisions on `ssim` go away once we use public-aware ownership." |
| `SessionTakeaway` | A condensed key point worth remembering. | "The whole-graph compile is fast enough to re-run end-to-end after every extractor change." |

Distinction from `OpenQuestion`: the existing `OpenQuestion` type
captures questions surfaced from academic papers' "future work"
sections. `SessionQuestion` captures questions raised during
agent/user conversation. They're never collapsed even when text
matches, because the provenance source and lifecycle differ.

The six are added at the end of the enum to keep historical id
ordering stable.

### Edge types (new entries in `ALLOWED_EDGE_TYPES`)

| Edge | From | To | Meaning |
|---|---|---|---|
| `derived_from_session` | any `Session*` | `Session` node (sub-type) | Provenance back to the session that produced this finding. |
| `discussed_in` | any doc node (Paper / Repository / Concept / …) | `Session` node | A document came up during this session. |
| `references` | any `Session*` | any doc node | This finding refers to / is about that doc. |
| `supersedes` | `Session*` | `Session*` | A later finding refines / replaces an earlier one (LLM-emitted when transcripts cite "we previously thought X, now Y"). |

### A `Session` node type

We also add a single `Session` node type per discovered harness
session. It carries the structural data already in the
`HarnessSession` dataclass (started_at, project_root, files_touched,
tools_used, token totals, etc.) as `metadata`. Every finding edges
back to its parent `Session` via `derived_from_session`. This lets
queries answer "what did we work on yesterday" and "which session
produced this insight" in one hop.

`Session` nodes are private — `is_public_research_node(Session) ==
False` — so they don't get vault pages by default. They live in the
graph for query reachability and MCP retrieval.

### Metadata on finding nodes

Each `Session*` node carries:

```jsonc
{
  "session_id": "...",                  // links back to Session node
  "turn_ids": [12, 13, 17],             // turns the LLM cited as source
  "extractor": "session-llm",           // or "session-structural" for activity-only nodes
  "llm_model": "claude-sonnet-4-...",   // when extractor=session-llm
  "content_hash": "sha256-...",         // for cache invalidation
  "confidence": 0.0..1.0                // LLM-self-reported, optional
}
```

## Extraction pipeline

### Pass 1 — structural (always-on, no LLM)

For each `HarnessSession` in `.tesserae/harness_sessions/`:

1. Mint one `Session` node, id-seed = `harness:<session_id>`.
2. Read `files_touched` → emit `discussed_in` edges to the matching
   doc node IDs (resolved via the same path-to-node-id index the
   ResearchGraphExtractor already builds for cross-reference linking).
3. Read `commands_run` → store as activity metadata on the Session
   node. Don't mint nodes for shell commands.
4. Read `decisions` (already populated by `discover_harness_sessions`)
   → mint one `SessionDecision` per entry with `extractor =
   "session-structural"`. These are the structurally-recoverable
   subset; the LLM pass enriches and adds more.

This pass alone gives the user a graph where every session is queryable
(`which sessions worked on paper Y?`), without paying for any LLM
calls. It runs on every compile, every session.

### Pass 2 — LLM extraction (when API key present)

For each session not already in cache (`session_findings/<session_id>.findings.json`
exists AND its `content_hash` matches the current session record):

1. Build the LLM prompt:
   - System: a brief on what tesserae is + the six finding types + the
     JSON schema for the response.
   - Context: the list of doc node IDs in the current project graph
     (truncated/scored so the LLM has plausible reference targets,
     not the whole 2000-node corpus).
   - User: the normalised transcript (assistant + user turns, with
     turn IDs).
2. Call the configured backend via the existing `llm_synthesis._synthesizer`
   abstraction. One call per session for sessions under the context
   budget; over-budget sessions split into overlapping windows of
   ~30 turns with a 5-turn overlap.
3. Parse the returned JSON list of findings:
   ```json
   [
     {
       "kind": "decision",
       "body": "...",
       "turn_ids": [17, 19, 22],
       "references": ["Paper:arxiv-2601-...", "Concept:gaussian-splatting:..."]
     },
     ...
   ]
   ```
4. For each finding, mint a `Session<Kind>` node with:
   - `id_seed = session:<session_id>:<kind>:<sha1(body)[:12]>` so the
     id is stable across re-extractions of the same content.
   - `derived_from_session` edge to the Session node.
   - `references` edges to each cited doc node, OR fall back to
     `discussed_in` from the structural pass if the LLM cited nothing.
5. Persist the raw JSON to `session_findings/<session_id>.findings.json`
   for the next compile's cache check.

If the LLM returns malformed JSON or fails entirely, the session keeps
only its structural data. The structural pass is the floor; the LLM
pass is enrichment.

### Pass 3 — graph merge

The session extractor returns a `ResearchGraph` slice. `merge_graphs`
combines it with the document and code slices via the existing
id-dedup → same-type-aliased-merge → cross-type-merge pipeline.

One nuance for the cross-type merger: `Session*` types are NOT in
`_CROSS_TYPE_MERGE_PRIORITY`, so a same-named Paper and SessionInsight
will not be collapsed. That's correct — a Paper called "Gaussian
Splatting" and an insight about Gaussian Splatting are different
entities.

## Linking strategy (recap of the design call)

Primary: the LLM's `references: [<id>, ...]` list. We trust it because
the prompt gives it the actual graph node IDs as the only legal
targets, and the JSON-mode response constrains the format.

Fallback: when the LLM omits citations on a finding, fall back to
structural — look at the `files_touched` for the turn range the
finding cites in `turn_ids`, and emit `references` edges to whichever
doc nodes resolve from those file paths. If neither LLM cite nor
structural fallback yields a target, the finding is kept but with no
`references` edges; it still has `derived_from_session` so it's
reachable.

## Caching and idempotence

Two cache files per session:

```
.tesserae/session_findings/<session_id>.findings.json
.tesserae/session_findings/<session_id>.activity.json
```

`findings.json` contains the LLM-extracted JSON list plus the
`content_hash` of the source session record at extraction time.
`activity.json` contains the structural pass output. On compile:

1. Compute `content_hash(session)` from the normalised
   HarnessSession dict.
2. If cache file exists AND its `content_hash` matches, skip the
   relevant pass and use the cached findings.
3. Otherwise, run the pass and write the cache atomically (tmp +
   rename, matching the pattern from
   `project.compile`'s graph write).

Stale caches (no matching session in `harness_sessions/`) are pruned
at the start of each compile.

Node IDs are deterministic and content-addressed (`stable_id` over
`session:<session_id>:<kind>:<sha1(body)[:12]>`), so two compiles
producing the same findings produce the same IDs. The same body
extracted from the same session always lands on the same node — no
duplicate Insight nodes accumulating per recompile.

## Configuration

`.tesserae/config.json` gains:

```jsonc
{
  "sessions": {
    "enabled": true,                              // default true
    "llm_enabled": "auto",                        // auto | true | false
    "max_turns_per_chunk": 30,                    // LLM-chunking threshold
    "max_tokens_per_call": 30000,                 // soft budget
    "model": "claude-sonnet-4-7-20251201",        // override default backend model
    "include_doc_id_context": 200                 // top-N doc ids passed in prompt
  }
}
```

CLI flags map onto these:

- `--sessions / --no-sessions` → `sessions.enabled`
- `--sessions-llm=auto|true|false` → `sessions.llm_enabled`
- `--sessions-model <name>` → `sessions.model`

When `llm_enabled = "auto"` (default), the LLM pass runs iff the
configured backend has credentials. `true` errors loudly if there's
no backend; `false` always runs structural-only.

## Vault projection

Findings render under a new vault directory:

```
<vault>/
  sessions/
    <date>-<session-slug>/
      _session.md             # Session-level overview (when/what files/who)
      insights.md             # all SessionInsight findings from this session
      decisions.md            # all SessionDecision findings
      questions.md            # SessionQuestion
      todos.md                # SessionTODO
      hypotheses.md           # SessionHypothesis
      takeaways.md            # SessionTakeaway
```

Each finding renders as a list item with the body, the source session
link, and wikilinks to its referenced docs. The doc page on the other
side of the link gains an `## Discussed in sessions` section in
`Incoming` form when sessions referenced it (using the existing
`render_edge_section` flow with `edge.type = "discussed_in"`).

`Session` nodes themselves stay private — `is_public_research_node`
returns False for them — so they're queryable in MCP but don't get
flooded into the public vault.

## CLI / MCP surface changes

CLI: see Configuration section above. Additionally, `tesserae sessions
list` is extended to show per-session finding counts:

```
2026-05-13-paper-deep-dive-3d-gaussian-splatting  insights=4 decisions=2 questions=1
2026-05-14-concept-trace-multi-view-consistency   insights=7 decisions=0 questions=3
```

MCP: two new tools

- `list_sessions(project_id, since=None, limit=20)` → array of Session
  nodes
- `find_session_findings(node_id, kinds=None)` → array of `Session*`
  nodes linked to `node_id` via `discussed_in` / `references`

The existing `find_related_nodes` already handles the new edge types
generically.

## Test strategy

| Layer | Test type | What |
|---|---|---|
| Structural extractor | unit | Fixed `HarnessSession` JSON → fixed graph slice. No LLM. |
| LLM JSON parser | unit | Various LLM responses (well-formed, partial, malformed, no citations) → expected node/edge counts. |
| Caching | unit | Same content_hash → no LLM call. Changed content_hash → new LLM call. Stale caches pruned. |
| End-to-end compile | integration | Demo corpus + `examples/demo-corpus/.harness-sessions/claude-code/*.json` → finding counts match a golden file. |
| Idempotence | integration | Two compiles produce byte-identical `graph.json`. |
| LLM-off path | integration | `--sessions-llm=false` produces same graph minus finding nodes; Session nodes and structural edges survive. |

Existing test invariants are preserved: 1042 tests pass + 13
pre-existing failures unchanged. New tests added in
`tests/test_session_graph_structural.py`,
`tests/test_session_graph_llm.py`, `tests/test_session_graph_e2e.py`.

## Open questions / risks

- **Cost containment.** A user with 100 historical sessions of 50
  turns each on first compile would trigger 100 LLM calls. Mitigations:
  the content-hash cache only re-extracts changed sessions on
  subsequent compiles; the `sessions.max_tokens_per_call` budget
  bounds per-call cost; `--no-sessions` disables entirely.
- **LLM citation hallucination.** The LLM may cite a doc id that
  doesn't exist. Mitigation: at parse time, we validate every
  `references` entry against the live `node_by_id` index; invalid ids
  are dropped and a `link-fix` audit line is written to
  `.tesserae/sessions/extraction.log`.
- **Privacy of transcripts.** Transcripts can contain user-confidential
  content. The opt-in semantics are explicit and tiered:
  - With `sessions.llm_enabled = false` (or `--sessions-llm=false`):
    zero outbound transmission. Only the structural pass runs.
  - With `sessions.llm_enabled = "auto"` (default) and no LLM backend
    credentials configured: zero outbound transmission. Structural pass
    only, no error.
  - With `sessions.llm_enabled = "auto"` AND a configured backend
    (e.g. `ANTHROPIC_API_KEY` set): the act of configuring the backend
    IS the opt-in. The **full transcript** of each not-yet-cached
    session goes to the LLM. The `redacted_preview` field is for
    human-readable session lists, not for LLM prompts — extraction
    quality on a redacted preview is too poor to be worth it.
  - With `sessions.llm_enabled = true`: same as the previous case, but
    errors loudly if no backend is configured.

  In all cases the transcript itself stays on the user's disk; only
  the LLM's structured JSON output is persisted to the graph and the
  per-session cache.
- **Stale findings when a session is deleted.** If a user manually
  removes a session JSON, the next compile prunes its findings cache
  and the merge_graphs id-dedup means no leftover Session* nodes
  survive (because they're only present in the cached extractor
  slice).
- **Schema-version migration.** Adding six node types is additive; no
  migration needed for existing `.tesserae/graph.json` files because
  the graph is regenerated on every compile.

## Out of scope (deferred follow-ups)

- **Synthesis-as-input** (the "D" sub-feature): re-extracting from
  the projector's own `Synthesis` pages would give a different
  feedback loop. Deferred until this lands and we see how often
  Synthesis pages produce findings worth re-extracting from.
- **Cross-project session aggregation** (one tesserae setup
  pulling sessions from many projects' `harness_sessions/`).
- **Real-time hooks** into the agent so findings appear without a
  full `project compile` pass.
- **LLM-generated summary deltas** ("here's what changed in the
  graph since last week") — that's a Synthesis-projector concern.
