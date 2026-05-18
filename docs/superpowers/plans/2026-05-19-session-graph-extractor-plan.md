# Session graph extractor — implementation plan

> Status: plan v2 (post-codex-review) • 2026-05-19 • spec: [`docs/superpowers/specs/2026-05-19-session-graph-extractor-design.md`](../specs/2026-05-19-session-graph-extractor-design.md)

**Changelog**

- v1 → v2 (this revision): integrated Codex's review. Six concrete
  changes: split the LLM-client interface into its own phase, moved
  structural-compile-wiring ahead of LLM extraction so the
  zero-credential path ships first, deferred markdown projection
  changes to their own phase, hardened path canonicalization, fixed
  same-type-dedup over-collapse risk for findings, and added
  project-scope guarantees to the cache + privacy story.

Seven phases. Each phase ends with green tests + a single commit. Run
`pytest tests/ --ignore=tests/evals -q` after every phase — the
13-pre-existing-failures baseline holds throughout.

The phases are ordered so the **structural-only path is shippable
after Phase 3** (no LLM, no API key, real value). LLM extraction
arrives via Phases 4–5 behind a feature flag. Each phase is
independently mergeable: if we pause after any of them, the codebase
is consistent and tests pass.

## Phase 1 — Schema additions (no projection routing)

**Goal**: graph schema knows about Session* nodes and their edges,
but nothing extracts them yet AND the projection layer doesn't yet
know where to put them. Phase 6 routes them. This split keeps Phase
1 atomic and avoids throwaway projection code if the layout changes
during design refinement.

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
    `Session<Kind>` types return `True` (they get vault pages once
    routed in Phase 6).
  - `_CROSS_TYPE_MERGE_PRIORITY`: do NOT add Session* types. They
    must not collapse with same-named Paper / Concept nodes.
  - **NEW (codex fix)**: in `_merge_same_type_aliased_duplicates`
    (or wherever the aggressive same-type dedup lives), exclude any
    `Session<Kind>` type from the aggressive-name dedup. Two
    SessionInsight nodes with identical body text but different
    `session_id` are legitimately separate findings — merging them
    loses provenance. Confirm by reading
    `tesserae/research_graph.py:_aggressive_dedup_key` and adding
    a `SESSION_FINDING_TYPES` skip set.

- `tests/test_research_graph.py`:
  - One test asserting the seven new type values are in
    `ALLOWED_NODE_TYPES` and parse round-trip via `ResearchNodeType(value)`.
  - One test asserting `is_public_research_node` returns False for
    Session and True for each Session<Kind>.
  - **NEW (codex fix)**: a test that constructs two `SessionDecision`
    nodes with identical normalized names but different
    `metadata.session_id`, runs them through
    `_merge_same_type_aliased_duplicates`, and asserts both survive.

- `tests/test_public_predicate.py`:
  - Extend `test_every_node_type_classifies` for the new types.

**Verification**

- `pytest tests/test_research_graph.py tests/test_public_predicate.py -q` green.
- `pytest tests/ --ignore=tests/evals -q` still shows the
  1042-pass / 13-pre-existing-fail baseline.

**Commit message** — `feat(schema): add Session + 6 Session* node types; protect findings from aggressive dedup`

---

## Phase 2 — Structural extractor (no LLM)

**Goal**: a deterministic pass that turns the existing
`HarnessSession` records into a `ResearchGraph` slice of `Session`
nodes + `discussed_in` edges + structurally-recoverable
`SessionDecision` nodes.

**Files touched**

- `tesserae/session_graph_structural.py` **(new)**:
  - Function `extract_structural(sessions: Iterable[HarnessSession],
    doc_path_index: DocPathIndex, project_root: Path) -> ResearchGraph`.
    Filters sessions through `session_matches_project(s,
    project_root)` before processing (privacy invariant — see
    Cross-cutting Invariants).
  - For each session:
    - Mint `Session` node, `id_seed = harness:<session_id>`.
      Metadata = full HarnessSession dict minus
      `raw_transcript_path`, `redacted_preview`, and `metadata[turns]`
      (these stay on disk; only the lightweight envelope ships into
      the graph).
    - For each `files_touched` path that resolves via
      `doc_path_index.lookup(path)`, emit `discussed_in` edge from
      the doc node to the Session node.
    - For each entry in `decisions`, mint a `SessionDecision` node
      (`id_seed = session:<session_id>:decision:<sha1(text)[:12]>`)
      with `metadata.session_id = <session_id>`, `extractor =
      "session-structural"`, and a `derived_from_session` edge to
      the Session node.

- **NEW (codex fix)** `tesserae/session_graph_path_index.py` **(new)**:
  Lifted into its own module because the resolution rules are
  non-trivial and reused by Phase 5.
  - `class DocPathIndex`: built from `(graph, project_root)`. Stores
    multiple keys per node so a `files_touched` entry resolves
    regardless of which form it takes.
  - For every node with `source_path`:
    - Index resolved-absolute (`Path(source_path).resolve()`).
    - Index project-relative (`relpath(source_path, project_root)`).
    - Index POSIX-normalized of both above.
    - Index the raw `source_path` string verbatim (defensive — some
      historical paths are loader ids, not real filesystem paths).
    - Index basename **as a low-confidence fallback**, returned only
      when no higher-confidence match exists. This stops `paper.md`
      from binding to the wrong paper when many directories contain a
      generic filename.
  - `lookup(query: str) -> Optional[str]`: tries the keys in
    decreasing-confidence order; returns the node id of the first hit.

- `tests/test_session_graph_path_index.py` **(new)**:
  - Fixture corpus with Papers using three different `source_path`
    flavors (absolute, project-relative, POSIX-normalized) plus a
    `Concept` with no `source_path` at all.
  - Fixture queries: absolute, relative, symlink-resolved,
    POSIX-normalized, loader-id, basename-only.
  - Asserts each lookup returns the expected node id, and that
    basename-only is dropped when an absolute match exists for a
    different node sharing the basename.

- `tests/test_session_graph_structural.py` **(new)**:
  - Fixture: a hand-rolled `HarnessSession` with three
    `files_touched` (each in a different path-flavor) and two
    `decisions`. Plus one session whose `cwd` is a sibling project
    — asserts `session_matches_project` correctly filters it out.
  - Asserts:
    - One Session node minted for the in-project session, zero for
      the sibling.
    - Two `discussed_in` edges from the resolved Papers → Session.
    - Two `SessionDecision` nodes with `derived_from_session`
      edges.
    - Idempotence: calling `extract_structural` twice on the same
      input produces equal graphs (same node IDs, same edge set).

**Verification**

- `pytest tests/test_session_graph_structural.py tests/test_session_graph_path_index.py -q` green.
- No LLM import — structural path is LLM-free.

**Commit message** — `feat(session-graph): structural extractor + multi-key path index + project-scope filter`

---

## Phase 3 — Wire structural-only path into `project compile`

**Goal** (reordered ahead of LLM per codex): `tesserae project
compile` runs the structural extractor by default. The
zero-credential path ships now, gathers real-world usage, and the
LLM layer arrives behind the same wiring later.

**Files touched**

- `tesserae/project.py`:
  - Add `SessionExtractionOptions` dataclass with the fields from
    the spec's Configuration section. v3 of the plan tightens the
    naming: `enabled`, `llm_enabled` ("auto" | True | False),
    `max_turns_per_chunk`, `max_tokens_per_call`, `model`,
    `include_doc_id_context`.
  - `ProjectPaths`: add `session_findings: Path` field pointing at
    `.tesserae/session_findings/`.
  - `ProjectWiki._compile_pipeline` (or wherever extractors run):
    after document extraction, build `DocPathIndex` from the doc
    graph, run `extract_structural`, merge the resulting slice into
    the running graph via `merge_graphs`.
  - When `session_options.enabled` is False, skip extraction; the
    graph is identical to today.

- `tesserae/cli.py`:
  - `project compile` argparser gains `--sessions / --no-sessions`,
    `--sessions-llm=auto|true|false` (parsed but only `enabled` is
    honored in Phase 3 — LLM flag is wired in Phase 5 once the
    backend exists).
  - `--sessions-model <name>` accepted but stored only.

- `tests/test_project_compile_sessions_structural.py` **(new)**:
  - End-to-end fixture using the demo corpus' bundled
    `.harness-sessions/`. Run `tesserae project compile`
    programmatically; assert:
    - With `--no-sessions`: graph has zero Session* nodes.
    - With default (`--sessions` implicit): graph has Session +
      structural SessionDecisions + discussed_in edges from Papers
      mentioned in `files_touched`.

**Verification**

- `pytest tests/test_project_compile_sessions_structural.py -q` green.
- Manual end-to-end: `tesserae project compile` against the demo
  corpus produces a graph that includes Session nodes and a non-zero
  number of `discussed_in` edges.
- `pytest tests/ --ignore=tests/evals -q` baseline holds.

**Commit message** — `feat(compile): wire structural session extractor into project compile + --sessions flag`

---

## Phase 4 — LLMJsonClient interface (codex-introduced spike)

**Goal**: provide a small, testable JSON-completion interface that
Phase 5 builds on. `llm_synthesis.LlmSynthesizer` is markdown-prose
oriented — it returns validated markdown bodies, not raw JSON — so
the LLM extractor cannot call it directly. This phase carves out
just the JSON-completion shape.

**Files touched**

- `tesserae/llm_json.py` **(new)**:
  - `class LLMJsonClient(Protocol)`: defines `complete_json(system:
    str, user: str, *, schema_name: str, cache_key: str | None =
    None, max_retries: int = 2) -> dict | list | None`. Returns
    parsed JSON. Returns `None` if no backend is configured or all
    retries failed.
  - `class AnthropicLLMJsonClient(LLMJsonClient)`: concrete impl
    that:
    - Reuses Anthropic client setup, retry, and prompt-caching
      helpers from `llm_synthesis` where they're reusable (factor
      them out if needed, but do not import the synthesizer's
      prose-oriented `synthesize()` method).
    - Requests `response_format = json_object` and parses with
      tolerance: strips ` ```json` fences, drops trailing commas
      with a minimal best-effort cleanup, returns `None` on
      unrecoverable parse error.
    - Honors the cache_key for Anthropic's prompt-cache when
      provided.
  - `def build_default_json_client() -> LLMJsonClient | None`:
    returns an `AnthropicLLMJsonClient` when `ANTHROPIC_API_KEY` is
    set, else `None`.

- `tesserae/llm_synthesis.py`:
  - If — and only if — refactoring is required to share retry /
    cache / client-setup helpers with the new module, lift those
    helpers into `_llm_shared.py`. Do not touch the prose
    synthesizer's external surface; existing synthesis tests must
    keep passing unmodified.

- `tests/test_llm_json.py` **(new)**:
  - Mock Anthropic responses for: well-formed JSON, fenced JSON,
    JSON with trailing commas, JSON with extra prose around it,
    total garbage. Assert each parse case.
  - Mock retry behavior: first call raises a recoverable error,
    second succeeds → one returned result, one retry counted.
  - Mock no-credentials path: `build_default_json_client()` returns
    `None` when `ANTHROPIC_API_KEY` is unset.

**Verification**

- `pytest tests/test_llm_json.py -q` green.
- `pytest tests/test_llm_synthesis.py -q` green (existing prose path
  must not regress).
- Full baseline holds.

**Commit message** — `feat(llm-json): LLMJsonClient interface + Anthropic impl with tolerant parse`

---

## Phase 5 — LLM extractor + content-hash cache

**Goal**: an LLM-backed pass that returns six kinds of structured
findings per session, gated by backend availability, cached by
content_hash AND project_root (codex hardening).

**Files touched**

- `tesserae/session_graph_llm.py` **(new)**:
  - Function `extract_with_llm(session: HarnessSession,
    transcript_turns: List[Dict], doc_id_context: List[Tuple[str,
    str]], client: LLMJsonClient) -> List[Finding]`.
  - **Transcript source (codex fix)**: v1 uses
    `session.metadata["turns"]` — the already-normalized,
    safety-truncated turn list that `discover_harness_sessions`
    populates. We do NOT read `raw_transcript_path` from disk in
    v1. The truncation limits live in `harness_sessions.py` and are
    documented in the integration doc (Phase 7). Reading raw
    transcripts is a Phase-8+ opt-in.
  - Prompt scaffolding (`_PROMPT_SYSTEM` + `_PROMPT_USER`)
    enumerates the six kinds + the strict JSON-list schema.
  - JSON parsing already handled by `LLMJsonClient.complete_json`;
    this module only constructs the request and post-processes the
    parsed JSON into typed `Finding` objects.
  - `references` validation: cross-check each cited ID against the
    `{nid for nid, _ in doc_id_context}` set; drop unknowns and log
    one audit line per dropped citation.
  - Auto-chunking: if `len(transcript_turns) >
    config.max_turns_per_chunk`, split into windows with a 5-turn
    overlap, call `client.complete_json` once per window, concat
    the results.

- `tesserae/session_graph.py` **(new — orchestrator)**:
  - `class SessionGraphExtractor`:
    - `__init__(self, project_paths, project_root, config,
      doc_graph, json_client=None)`.
    - `extract(self) -> ResearchGraph`:
      1. Load sessions via `discover_harness_sessions`, filter by
         `session_matches_project(s, project_root)` (privacy).
      2. Build `DocPathIndex` + `doc_id_context` (top-N by
         per-session file-touch overlap).
      3. Pass 1: structural. Always.
      4. Pass 2: LLM. Run iff `_should_run_llm()`
         (`config.llm_enabled != False` AND `json_client is not None`).
         For each session, check cache; skip if both `content_hash`
         AND `project_root_hash` match; otherwise call
         `extract_with_llm`, mint `Session<Kind>` nodes with
         `id_seed = session:<sid>:<kind>:<sha1(body)[:12]>` and
         metadata including `session_id`, `turn_ids`, `extractor =
         "session-llm"`, `llm_model`, `content_hash`. Write cache.
      5. Merge structural + LLM slices via existing `merge_graphs`
         and return.
  - **NEW (codex fix)** `_session_cache_envelope(session, project_root)`:
    cache files store
    `{ "schema_version": 1, "content_hash": "...",
       "project_root_hash": "sha256-...", "findings": [...] }`.
    Validation rejects cache files whose `project_root_hash`
    doesn't match the current project. Stops cross-project replay.
  - `_prune_stale_caches(cache_dir, live_session_ids: Set[str])`:
    remove any cache file whose id is not in the live set.

- `tesserae/cli.py`:
  - Wire `--sessions-llm` flag to actually gate the LLM pass.

- `tests/test_session_graph_llm.py` **(new)**:
  - Mock `LLMJsonClient` with canned dict responses (well-formed,
    partial, mixed cite-good-and-bad).
  - Asserts: expected finding counts, dropped invalid citations,
    correct id_seeds (regression for the same body in two
    different sessions → two distinct nodes — codex fix).

- `tests/test_session_graph_cache.py` **(new)**:
  - Same content_hash + same project_root → no LLM call.
  - Different content_hash → re-extraction; new findings persisted.
  - Same content_hash but cache file's `project_root_hash` differs
    → cache rejected, re-extraction. (Cross-project leakage
    regression.)
  - Stale cache pruning.

**Verification**

- `pytest tests/test_session_graph_llm.py
  tests/test_session_graph_cache.py -q` green.
- End-to-end: `tesserae project compile` with a mocked
  `LLMJsonClient` against the demo corpus produces a graph with
  finding nodes that link to documents.
- Full baseline holds.

**Commit message** — `feat(session-graph): LLM extractor + project-scoped content-hash cache`

---

## Phase 6 — Vault projection: one page per finding

**Goal** (revised per codex): findings render under `sessions/` in
the Obsidian projection, **one page per finding node** so the
existing `write_projection` user-notes-survival contract is
preserved unchanged.

The bundled-files layout from plan-v1 conflicted with the
projection's "one file per node id" invariant — user notes are
keyed by file path, so any node-to-file restructure must redesign
that contract. v2 ships per-finding pages and groups them
visually via per-session subdirectories.

**Files touched**

- `tesserae/markdown_projection.py`:
  - `directory_for_node`: route `Session<Kind>` types to
    `sessions/<date>-<session-slug>/` (sub-keyed by the parent
    Session node's `started_at` + `slug` metadata; resolved by
    looking up the `Session` node via the finding's
    `metadata.session_id`).
  - Per-finding file name: `<kind>-<sha1(body)[:8]>.md`. Example:
    `sessions/2026-05-19-paper-deep-dive/insight-a7c2b1f0.md`.
  - Add Session<Kind> entries to `_CALLOUT_BY_NODE_TYPE`:
    `> [!note] Insight`, `> [!important] Decision`,
    `> [!question] Question`, `> [!todo] TODO`,
    `> [!example] Hypothesis`, `> [!summary] Takeaway`.
  - Per-session overview page: `sessions/<date>-<slug>/_session.md`
    is the projection of the `Session` node itself — wait, Session
    is private (`is_public_research_node = False`). So instead,
    write a tiny index page as a synthetic file (separate code path,
    not through `write_projection`) under each session subdirectory
    listing the finding counts. This is the only finding-related
    file NOT owned by a single node id, so it gets a dedicated
    pruning rule.

- `tesserae/vault_pull.py`:
  - Extend `expected_files` to include the new per-session
    subdirectories + the synthetic `_session.md` overview.

- Doc-side wiki page changes:
  - The existing `render_edge_section` already surfaces
    `discussed_in` and `references` Incoming edges, so Paper pages
    automatically get "Discussed in session ..." links once the
    new edges exist.

- `tests/test_session_projection.py` **(new)**:
  - Fixture graph: one Session + 2 Insights + 1 Decision + Paper
    references → expected per-finding files + a synthetic
    `_session.md` overview.
  - **NEW (codex fix)** user-notes round-trip: write a
    `USER_NOTES_START/END` block into one of the finding files,
    re-project, assert the user notes survive untouched. Add the
    same assertion for the synthetic `_session.md` overview's
    explicit "no user-notes zone here, pruning may overwrite"
    expectation — clear contract.

**Verification**

- `pytest tests/test_session_projection.py
  tests/test_markdown_projection.py -q` green.
- Manual: `tesserae project compile && tesserae project
  obsidian-sync` produces the new `sessions/` layout and
  `discussed_in` backlinks appear on Paper pages.
- Full baseline holds.

**Commit message** — `feat(projection): one page per finding, per-session subdirectory layout`

---

## Phase 7 — MCP tools + integration doc (8 languages)

**Goal**: `list_sessions` and `find_session_findings` MCP tools work;
README + `docs/integrations/sessions.md` (+ 7 i18n translations)
explain the feature.

**Files touched**

- `tesserae/mcp_server.py`:
  - `list_sessions(project_id, since=None, limit=20)` tool returning
    Session nodes from the graph.
  - `find_session_findings(node_id, kinds=None)` tool returning
    Session<Kind> nodes connected to `node_id` via `discussed_in` /
    `references`, optionally filtered by kind.

- `tesserae/cli.py`:
  - Extend `tesserae sessions list` to compute per-session finding
    counts by walking the current `graph.json`.

- `docs/integrations/sessions.md` **(new)**: user-facing doc — what
  the feature does, how to enable / disable, cache layout, privacy
  guarantees, troubleshooting.

- 7 i18n translations: `docs/i18n/integrations/sessions.{ko, zh, ja,
  ru, es, fr, de}.md`.

- `README.md` + 7 translations: one paragraph under Quickstart,
  pointing at the integration doc.

- `tests/test_mcp_sessions.py` **(new)**: MCP server fixture →
  expected tool payloads.

**Verification**

- `pytest tests/test_mcp_sessions.py -q` green.
- Full baseline holds.
- Render-check: `docs/integrations/sessions.md` and all 7
  translations open cleanly on GitHub.

**Commit message** — `feat(mcp, docs): list_sessions / find_session_findings + sessions integration doc in 8 languages`

---

## Cross-cutting invariants

These hold across every phase:

- **Test baseline**: `1042 passed, 13 pre-existing failed` after every
  commit. New failures fixed in the same commit or proven
  pre-existing.
- **Atomic writes**: every new write to `.tesserae/session_findings/`
  uses tmp + rename.
- **Privacy default**: with `sessions.llm_enabled = false` (or no
  backend), zero outbound calls. Transcripts never sent unless an
  LLM backend is explicitly configured.
- **Privacy project-scope (codex hardening)**:
  - Sessions filtered through `session_matches_project(session,
    project_root)` after loading from disk.
  - Cache files validate against `project_root_hash` on read; mismatch
    → discard.
  - LLM `references` cross-checked against the current project's doc
    graph only. Invalid IDs dropped + audit-logged.
- **Idempotence**: two consecutive `project compile` runs without
  intervening changes produce byte-identical `graph.json` and
  byte-identical `sessions/` projection.
- **Deletion-safe**: removing a HarnessSession JSON and recompiling
  removes all its Session* nodes from the graph and its files from
  the vault on the next prune.
- **Finding-merge protection (codex fix)**: `Session<Kind>` types are
  excluded from `_merge_same_type_aliased_duplicates`. Two identical-text
  findings from two different sessions never collapse — they're
  legitimately separate provenance.
- **Transcript-source contract (codex fix)**: v1 reads only
  `session.metadata["turns"]`. Raw transcripts on disk are off-limits
  until an explicit Phase-8+ opt-in.

## Rollback plan

Each phase is one commit on `main`. To roll back any phase:
`git revert <commit>`. The schema is additive, every extractor is
gated by `sessions.enabled` (defaulting to True), and the cache
envelope's `schema_version` field lets us bump and invalidate.

Most-likely-to-need-rollback phase is Phase 5 (LLM extraction)
because of cost/correctness risk. Phases 1–4 and 6–7 are mechanical
enough that roll-forward is usually cheaper than revert.

## Open implementation questions

- **Doc-ID context scoring** (deferred to Phase 5 implementation):
  v1 takes the first N doc IDs sorted by per-session
  `files_touched` overlap. If finding quality is poor, upgrade to
  title-token-match scoring against transcript text. Don't block
  Phase 5 on this.
- **Per-session overview page ownership**: the synthetic
  `_session.md` page isn't owned by any one node id. It's pruned
  when its session subdirectory is empty of findings.

---

**Estimated time**: ~2.5 working days for a competent implementer
familiar with the codebase. Phase 1 (~1.5 hours), Phase 2
(~4 hours, mostly the path index), Phase 3 (~2 hours), Phase 4
(~3 hours), Phase 5 (~6 hours), Phase 6 (~4 hours), Phase 7
(~3 hours + translations).
