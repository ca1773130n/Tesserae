# Session graph extractor — implementation plan

> Status: plan • 2026-05-19 • spec: [`docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md`](../specs/2026-05-19-session-graph-extractor-design.md)

Six phases. Each phase ends with green tests + a single commit. Run
`pytest tests/ --ignore=tests/evals -q` after every phase — the
13-pre-existing-failures baseline holds throughout.

The phases are ordered so the schema lands first, then the deterministic
extractor (testable without LLM), then the LLM layer (testable with
fixtures), then the compile wiring, then projection, then docs +
cleanup. Each phase is independently mergeable: if we pause after any
of them, the codebase is still consistent and tests still pass.

## Phase 1 — Schema additions

**Goal**: graph schema knows about Session* nodes and their edges, but
nothing extracts them yet.

**Files touched**

- `tesserae/research_graph.py`:
  - Add to `ResearchNodeType` enum at end (preserve ordering):
    `SESSION = "Session"`, `SESSION_INSIGHT = "SessionInsight"`,
    `SESSION_DECISION = "SessionDecision"`,
    `SESSION_QUESTION = "SessionQuestion"`,
    `SESSION_TODO = "SessionTODO"`,
    `SESSION_HYPOTHESIS = "SessionHypothesis"`,
    `SESSION_TAKEAWAY = "SessionTakeaway"`.
  - Add to `ALLOWED_EDGE_TYPES`: `derived_from_session`,
    `discussed_in`, `references`, `supersedes`.
  - `is_public_research_node`: `Session` returns `False`. The six
    `Session<Kind>` types return `True` (they get vault pages).
  - `_CROSS_TYPE_MERGE_PRIORITY`: do NOT add Session* types. They
    must not collapse with same-named Paper / Concept nodes.

- `tesserae/markdown_projection.py`:
  - `directory_for_node`: route `Session<Kind>` types to `"sessions"`.
    The `Session` type itself returns whatever — it never reaches the
    projection (filtered by `is_public_research_node`).
  - Add `Session<Kind>` → `_CALLOUT_BY_NODE_TYPE` entries so each
    finding renders with an Obsidian callout chip (e.g.
    `> [!note] Insight`, `> [!important] Decision`,
    `> [!question] Question`, `> [!todo] TODO`,
    `> [!example] Hypothesis`, `> [!summary] Takeaway`).

- `tests/test_research_graph.py`:
  - One test that asserts the seven new type values are in
    `ALLOWED_NODE_TYPES` and parse round-trip via `ResearchNodeType(value)`.
  - One test that asserts `is_public_research_node` returns False for
    Session and True for each Session<Kind>.

- `tests/test_public_predicate.py`:
  - Extend the existing `test_every_node_type_classifies` to ensure
    the new types land in a public bucket (or private for Session).

**Verification**

- `pytest tests/test_research_graph.py tests/test_public_predicate.py tests/test_markdown_projection.py -q` green.
- `pytest tests/ --ignore=tests/evals -q` still shows the
  1042-pass / 13-pre-existing-fail baseline.

**Commit message** — `feat(schema): add Session and 6 Session* node types + edge vocabulary`

---

## Phase 2 — Structural extractor (no LLM)

**Goal**: a deterministic pass that turns the existing
`HarnessSession` records into a `ResearchGraph` slice of `Session`
nodes + `discussed_in` edges + structurally-recoverable
`SessionDecision` nodes (from the `decisions` field already populated
by `discover_harness_sessions`).

**Files touched**

- `tesserae/session_graph_structural.py` **(new)**:
  - Function `extract_structural(sessions: Iterable[HarnessSession],
    doc_path_index: Mapping[str, str]) -> ResearchGraph`.
    - `doc_path_index` maps absolute file paths → node IDs (a Paper's
      `source_path` → its node ID).
  - For each session:
    - Mint `Session` node, `id_seed = harness:<session_id>`.
      Metadata = full HarnessSession dict minus `raw_transcript_path`
      and `redacted_preview` (those stay on disk).
    - For each `files_touched` path that resolves in
      `doc_path_index`, emit `discussed_in` edge from the doc node to
      the Session node. (No node minted on the doc side — it already
      exists.)
    - For each entry in `decisions`, mint a `SessionDecision` node
      (`id_seed = session:<session_id>:decision:<sha1(text)[:12]>`)
      with `extractor = "session-structural"` and a
      `derived_from_session` edge to the Session node.
  - Function `build_doc_path_index(graph: ResearchGraph) ->
    Dict[str, str]` that walks `graph.nodes` and indexes
    `source_path` → `node.id` for any node with a `source_path`.

- `tests/test_session_graph_structural.py` **(new)**:
  - Fixture: a hand-rolled `HarnessSession` with three
    `files_touched` (two resolving, one not) and two `decisions`.
  - Fixture: a tiny `ResearchGraph` with two Paper nodes whose
    `source_path` matches the resolving files.
  - Asserts:
    - One Session node minted.
    - Two `discussed_in` edges from Papers → Session (not from
      the unresolving path).
    - Two `SessionDecision` nodes minted, each with a
      `derived_from_session` edge.
    - Idempotence: calling `extract_structural` twice on the same
      input produces equal graphs (same node IDs, same edge set).

**Verification**

- `pytest tests/test_session_graph_structural.py -q` — all new tests pass.
- No import of the LLM module — confirms the structural path is LLM-free.

**Commit message** — `feat(session-graph): structural extractor (no LLM) — Session nodes + discussed_in/derived_from_session edges`

---

## Phase 3 — LLM extraction

**Goal**: an LLM-backed pass that returns six kinds of structured
findings per session, gated by backend availability, cached by
content_hash.

**Files touched**

- `tesserae/session_graph_llm.py` **(new)**:
  - Function `extract_with_llm(session: HarnessSession, transcript:
    str, doc_id_context: List[Tuple[str, str]], synthesizer) ->
    List[Finding]` where `Finding` is a small dataclass with `kind,
    body, turn_ids, references`.
  - `doc_id_context` is `[(node_id, display_name)]` — the top-N
    candidate doc nodes (scored by per-session file-touch overlap +
    title relevance; for v1, simply take all doc node ids with a
    capped limit of `sessions.include_doc_id_context`).
  - Prompt scaffolding lives in `_PROMPT_SYSTEM` and `_PROMPT_USER`
    module constants. The system prompt enumerates the six kinds
    + the strict JSON-list schema. The user prompt embeds the
    transcript and the doc-ID context.
  - JSON parsing: tolerant. If the response is wrapped in ```json
    fences, strip them. If the response is valid JSON but a single
    finding has malformed fields, drop that finding and keep the
    rest. If the response is total garbage, return `[]` and log a
    warning.
  - `references` validation: cross-check each cited ID against the
    `{nid for nid, _ in doc_id_context}` set; drop unknowns,
    emit a single audit-log line per dropped citation.
  - Auto-chunking: if `len(transcript_turns) >
    config.max_turns_per_chunk`, split into windows of
    `max_turns_per_chunk` turns with a 5-turn overlap, call once per
    window, concat the results.

- `tesserae/session_graph.py` **(new — orchestrator)**:
  - `class SessionGraphExtractor`:
    - `__init__(self, project_paths, config, doc_graph,
      synthesizer=None)`.
    - `extract(self) -> ResearchGraph`:
      1. Discover sessions via the existing
         `discover_harness_sessions` call (only if
         `.tesserae/harness_sessions/` is empty or stale).
      2. Build `doc_path_index` and `doc_id_context` from
         `doc_graph`.
      3. Pass 1 (structural): always run. Produces structural slice.
      4. Pass 2 (LLM): run iff `_should_run_llm()` returns True
         (gates on `config.llm_enabled` + `synthesizer is not None`).
         For each session, check
         `.tesserae/session_findings/<id>.findings.json` cache; skip
         if `content_hash` matches; otherwise call `extract_with_llm`,
         mint Session<Kind> nodes with id_seed
         `session:<sid>:<kind>:<sha1(body)[:12]>`, write cache.
      5. Merge structural + LLM slices via `merge_graphs` and return.
  - `_session_content_hash(session: HarnessSession) -> str`: stable
    hash over the full normalized JSON of the session.
  - `_prune_stale_caches(cache_dir, live_session_ids: Set[str])`:
    remove any `<id>.findings.json` / `<id>.activity.json` whose id
    is not in the live set.

- `tests/test_session_graph_llm.py` **(new)**:
  - Mock synthesizer using a fixture that returns canned JSON strings
    (well-formed, partial-malformed, fenced, total-garbage).
  - Asserts:
    - Well-formed JSON → expected number of Finding objects.
    - Partial-malformed JSON → only the valid findings survive.
    - Fenced JSON (```json … ```) → unwrapped correctly.
    - Garbage → empty list, warning logged, no crash.
    - Citations to unknown IDs → dropped from references.
    - Over-budget transcript → multiple synthesizer calls (one per
      chunk).

- `tests/test_session_graph_cache.py` **(new)**:
  - Same content_hash on two consecutive `extract()` calls → only one
    synthesizer call total.
  - Different content_hash → re-extraction; new findings persisted.
  - Stale cache (id no longer in harness_sessions) → cache file pruned.

**Verification**

- `pytest tests/test_session_graph_llm.py tests/test_session_graph_cache.py -q` green.
- Test suite still at 1042/13 baseline.

**Commit message** — `feat(session-graph): LLM extractor + content-hash cache for findings`

---

## Phase 4 — Wire into `project compile`

**Goal**: `tesserae project compile` runs the new extractor by default.
Flags work. Config keys round-trip.

**Files touched**

- `tesserae/project.py`:
  - Add `SessionExtractionOptions` dataclass alongside
    `CognifyOptions` with the fields from the spec's Configuration
    section.
  - `ProjectWiki._compile_pipeline` (or wherever the document +
    code extractors run today):
    1. After document extraction completes, build the doc graph from
       what was extracted so far.
    2. Build a `SessionGraphExtractor` instance, passing
       `session_options`, the doc graph, and the existing synthesizer
       (resolved the same way the synthesis projector resolves it).
    3. Call `.extract()`, merge the returned slice into the running
       graph via `merge_graphs`.
    4. If `session_options.enabled` is False, skip steps 1-3
       entirely; the graph is identical to today.
  - `ProjectPaths`: add `session_findings: Path` field.

- `tesserae/cli.py`:
  - `project compile` argparser gains `--sessions/--no-sessions`,
    `--sessions-llm=auto|true|false`, `--sessions-model <name>`.
  - These map onto `SessionExtractionOptions` and override the
    config-file values.

- `tests/test_project_compile_sessions.py` **(new)**:
  - End-to-end fixture using the demo corpus + a tiny mocked
    synthesizer. Run `tesserae project compile` programmatically;
    assert that:
    - With `--no-sessions`: graph has no Session* nodes.
    - With `--sessions --sessions-llm=false`: graph has Session
      nodes and structural Decision nodes, no LLM-extracted findings.
    - With `--sessions --sessions-llm=true` and a mock synthesizer:
      finding nodes appear and link to doc nodes.

**Verification**

- `pytest tests/test_project_compile_sessions.py -q` green.
- `tesserae project compile` runs end-to-end against
  `examples/demo-corpus/` and emits a graph with Session findings
  (smoke-tested with a recorded synthesizer fixture).
- `pytest tests/ --ignore=tests/evals -q` baseline holds.

**Commit message** — `feat(compile): wire SessionGraphExtractor into project compile + CLI flags`

---

## Phase 5 — Vault projection

**Goal**: findings render as readable markdown under `sessions/` in
the Obsidian projection.

**Files touched**

- `tesserae/markdown_projection.py`:
  - `directory_for_node` already routes Session<Kind> → `"sessions"`
    (Phase 1). Refine to a per-session subdirectory keyed by the
    Session node's `started_at` and `slug`: e.g.
    `sessions/2026-05-19-paper-deep-dive/insights.md`. (Implementation
    detail: each Session<Kind> node has a `session_id` metadata
    field that points at its parent Session node; we resolve the
    subdir name from the parent Session node's `started_at` + `slug`
    metadata.)
  - One file per (session, kind) — bundle all findings of one kind
    from one session into one markdown file (instead of one file per
    finding). Each finding renders as a list item with body, source
    turns, and `[[wikilinks]]` to references.
  - Also emit a per-session `_session.md` overview page (Session
    node's metadata as a frontmatter card + a small TOC listing
    counts of each finding kind).
  - Doc pages on the other side of `discussed_in` edges: extend
    `render_node_page` so the existing `Incoming` section surfaces
    "Discussed in sessions" with the date and link to the session
    overview.

- `tesserae/vault_pull.py`:
  - Update `prune_orphan_pages` / `expected_files` computation to
    include the new `sessions/...` layout.

- `tests/test_markdown_projection.py` (extend) + 
  `tests/test_session_projection.py` **(new)**:
  - Fixture graph: one Session + 2 Insights + 1 Decision + Paper
    references → expected directory layout + file contents.
  - Idempotence: two projections produce byte-identical files.

**Verification**

- `pytest tests/test_session_projection.py tests/test_markdown_projection.py -q` green.
- Full test baseline holds.
- Manual: `tesserae project compile && tesserae project obsidian-sync`
  produces the new `sessions/` layout in the vault without
  duplicating existing pages.

**Commit message** — `feat(projection): vault layout for session findings under sessions/<date>-<slug>/`

---

## Phase 6 — MCP tools + docs

**Goal**: `list_sessions` and `find_session_findings` MCP tools work;
README + docs/integrations explain the feature.

**Files touched**

- `tesserae/mcp_server.py`:
  - Add `list_sessions(project_id, since=None, limit=20)` tool
    returning Session nodes from the graph.
  - Add `find_session_findings(node_id, kinds=None)` tool returning
    Session<Kind> nodes connected to `node_id` via `discussed_in` /
    `references` edges, optionally filtered by kind.

- `tesserae/cli.py`:
  - Extend `tesserae sessions list` to compute per-session finding
    counts by walking the current `graph.json`.

- `docs/integrations/sessions.md` **(new)**: user-facing doc — what
  the feature does, how to enable / disable, what the cache layout
  looks like, privacy guarantees, troubleshooting (LLM cost,
  malformed responses).

- 7 i18n translations of `docs/integrations/sessions.md` under
  `docs/i18n/integrations/sessions.{ko,zh,ja,ru,es,fr,de}.md`.

- `README.md` (and 7 translations): one paragraph in the "Quickstart"
  section about session findings, pointing at the integration doc.

- `tests/test_mcp_sessions.py` **(new)**:
  - Spin up the MCP server fixture against a graph with Session
    findings; assert both new tools return expected payloads.

**Verification**

- `pytest tests/test_mcp_sessions.py -q` green.
- Full baseline holds.
- Render-check: `docs/integrations/sessions.md` and all 7
  translations open cleanly on GitHub.

**Commit message** — `feat(mcp, docs): list_sessions / find_session_findings MCP tools + integration doc in 8 languages`

---

## Cross-cutting invariants

These hold across every phase:

- **Test baseline**: `1042 passed, 13 pre-existing failed` after
  every commit. Any new failure must either be fixed in the same
  commit or proven to be pre-existing.
- **Atomic writes**: every new write to `.tesserae/session_findings/`
  uses the tmp + rename pattern from `batch.py` and the latest
  `project.py` atomic-write fixes.
- **Privacy default**: with no LLM backend configured, the new
  extractor produces only structural data (Session nodes,
  discussed_in edges, structural SessionDecisions). It must never
  outbound-call without an explicit credentialed backend.
- **Idempotence**: two consecutive `project compile` runs without
  intervening changes must produce byte-identical `graph.json` and
  byte-identical `sessions/` projection.
- **Deletion-safe**: removing a HarnessSession JSON and recompiling
  must remove all its Session* nodes from the graph and its files
  from the vault on the next prune.

## Rollback plan

Each phase is one commit on `main`. To roll back any phase:
`git revert <commit>` — the schema additions are additive, the
extractor is gated by `sessions.enabled` defaulting to True, so a
revert of any single phase leaves the codebase consistent.

The most-likely-to-need-rollback phase is Phase 3 (LLM extraction)
because of cost/correctness risk. Phases 1-2 and 4-6 are mechanical
enough that a roll-forward fix is usually cheaper than a revert.

## Open implementation questions (to resolve before / during execution)

- **Which synthesizer abstraction to reuse?** `llm_synthesis.py`
  has the `_synthesizer` Anthropic wrapper. Confirm during Phase 3
  that its interface fits "give me JSON for this prompt" without
  reaching into Anthropic-specific assumptions, otherwise we factor
  a thin `LLMJSONExtractor` interface.
- **Doc-ID context scoring**: v1 dumps the first N doc IDs sorted by
  `started_at` desc. If finding quality is poor, upgrade to a
  per-session relevance score based on `files_touched` overlap and
  title-in-transcript token matches. Don't block Phase 3 on this.
- **Session matching for multi-project setups**: `discover_harness_sessions`
  already has `session_matches_project` for filtering by `cwd`.
  Confirm during Phase 4 that we're passing the right project_root;
  otherwise sessions from sibling projects pollute the graph.

---

**Estimated time**: ~2 working days for a competent implementer
familiar with the codebase. Phases 1-2 (~3 hours), Phase 3 (~6 hours
incl. test fixtures), Phase 4 (~3 hours), Phase 5 (~4 hours),
Phase 6 (~3 hours + translations).
