# Distillery — layered per-agent knowledge graphs

Date: 2026-07-19 · Status: design (no code). Goal: replace the "one giant KG"
scaling model with an **org chart of graphs**: per-agent KGs that grow from each
agent's own experience, get **distilled** (organized / compacted / polished /
refined / forgotten) into higher-level knowledge, and roll up so a manager agent
sees only its reports' distilled layer — recursively, to any depth — while the
byte-idempotent project compile (CMP-03) stays untouched.

## 1. Motivation — the org, not the archive

No human remembers everything; no agent context window holds everything. Today
Tesserae compiles one project graph and every consumer reads the same flat pile.
That works until the corpus outgrows any single `compile_context` budget — then
every agent pays retrieval cost over everything and specializes in nothing.

The user's framing is a real-world organization:

> Workers accumulate raw experience and develop field expertise. As a worker's
> llm-wiki-like KG grows it must be distilled — organize, compact, polish,
> refine, **forget** — into higher-level knowledge. A manager sees only the
> distilled layer of its reports; the manager's manager sees a layer higher
> still, and decides from that.

This layering is also the answer to the shared-knowledge objection against
per-agent graphs (fragmentation): raw facts are **not** duplicated per agent —
they stay in the one shared L0 project graph. What propagates upward is
distilled knowledge, deduplicated by construction (§8). Scalability comes from
the invariant that **every artifact an agent actually loads is bounded** (one
48k-char read, §7), no matter how large the corpus underneath grows.

## 2. Core decision — layers are files, not fields

Layering is a **compiler pass with a cache**, not a new database. Each layer is
a separate `graph.json`-shaped file; "which layer am I seeing" = "which file did
my MCP server load". No `layer` column, no schema migration, no new store.

```
L0  Project graph (graph.json)     — today's graph, unchanged + Agent nodes,
                                     performed_by/reports_to edges, and structural
                                     ExpertiseProfile nodes (all structural,
                                     deterministic — §8.2/§12 Phase 1).
                                     Byte-idempotent (CMP-03). NEVER contains any
                                     distill-derived node, edge, or metadata.

L1  Agent distilled graph          — .tesserae/agents/<agent_key>/distilled.graph.json
    (one per worker agent)           Output of `tesserae distill` over that agent's
                                     L0 slice. Bounded: fits one 48k-char read
                                     (tested, §7). Outside the project compile.

L2  Manager view                   — query-time federate_graphs over children's L1
                                     artifacts (+ the manager's own L1). In-memory,
                                     mtime-cached, never serialized.

L2' Manager distilled graph        — when the manager itself has a manager: the SAME
    (materialized on demand)         pass over its L2 federation, written to
                                     .tesserae/agents/<manager_key>/distilled.graph.json.
                                     Recursion to any depth = repeat.
```

Why files: manager visibility is **unforgeable** (the raw data is simply absent
from the artifact the manager's server loads), and every existing surface —
`compile_context`, `graph_ppr`, `search_nodes`, `ask` — already takes "a graph"
via `_load_requested_graph`, so all read tooling works on every layer unchanged.

Two hard rules, learned from the 4×-broken byte-idempotence history:

1. **L0 is sealed against distillation.** The distill pass reads L0 and writes
   only under `.tesserae/agents/`. It never mints nodes, edges (including
   `supersedes`), or metadata into `graph.json`. Deleting `.tesserae/agents/`
   fully reverts the feature.
2. **Every materialized layer is deterministic given declared inputs** (§7),
   with LLM text entering only through content-keyed caches.

## 3. Agent identity & the org registry

### 3.1 Identity must be role-grade, or the org has no members

`HarnessSession` already carries `(harness, metadata['config_root'],
agent_label)` — but `agent_label` is currently the tool name
(`harness_sessions.py:645,718`), so naive keying collapses to 1–2 "agents" per
user and no field expertise can ever emerge. Phase 1 therefore makes identity
role-grade:

```
agent_key = f"{harness}:{account_slug}:{role}"

account_slug  path-INDEPENDENT: harness account id/email from the harness config
              when available, else basename(config_root) — never the absolute
              path (renaming $HOME must not mint a new agent).
role          resolved in priority order:
              1. subagent descriptor — subagent transcripts captured under parent
                 sessions (metadata['subagents'], stable ids) carry a type
                 (reviewer, planner, test-writer, …); minted as first-class roles.
              2. registry match rules — declarative mapping of session envelopes
                 (harness, cwd glob, slash-command, label pattern) onto declared
                 logical agents.
              3. fallback: "default".
```

Agent node: `stable_id(AGENT, id_seed=f"agent:{agent_key}")` — agent_key in the
seed, so same-named agents never collide (stable_id keys on type+seed only).

**Ship gate:** Phases 2–4 do not ship until a normal fixture corpus yields ≥2
distinguishable agents (`tesserae agents list` shows role diversity). Without
that, the hierarchy is decoration.

### 3.2 Registry — `.tesserae/agents/registry.json`

ProjectRegistry-shaped (duck-types `list_projects()` so `load_federated_graph`
consumes it unmodified), atomic tmp-rename saves, sanitized keys:

```json
{
  "version": 1,
  "agents": {
    "claude-code:me:reviewer": {
      "label": "Code reviewer",
      "parent": "org:root",
      "aliases": ["claude-code:old-account:reviewer"],
      "match": [{"harness": "claude-code", "subagent": "reviewer"}]
    }
  }
}
```

- **Zero-config default:** every observed agent implicitly `reports_to org:root`.
  A user with no registry gets a working 2-level org; today's global view is
  semantically "root's view".
- **Aliases** merge envelope keys across harness/account changes (one human/role
  through two harnesses, or after an account switch, stays one logical agent —
  cf. the llm_codex_home account incident).
- **Ceremony killers:** `tesserae agents init` scans imported sessions for
  observed `(harness, account, role)` tuples and writes a proposed registry
  (everyone parented to root; reversible by deleting the file).
  `tesserae agents list` enumerates observed keys; `tesserae agents set-parent
  <child> <parent>` validates both against observed keys; `tesserae agents
  rename <old> <new>` migrates the `agents/<key>` dir + registry atomically.
- **Fail loud:** registry load rejects unknown keys; manager resolution with a
  child that has no distilled artifact errors explicitly — `children X, Y have
  no distilled artifact; run: tesserae distill --agent X` — never a silently
  empty federation.
- `reports_to` edges (Agent → parent Agent) are minted into each distilled
  artifact from the registry, so the org chart is queryable in-graph.

## 4. Data model — exact changes (all coordinated in `research_graph.py`)

**New node types** (all three: join the aggressive-dedup exemption set (~line
748) — same-text nodes from two agents must never fuse — and stay OUT of
`CANONICALIZABLE_TYPES`):

- `ResearchNodeType.AGENT = "Agent"` — projection-private v1 (graph-queryable,
  no wiki page).
- `ResearchNodeType.DISTILLED_NOTE = "DistilledNote"` — generic distillate
  sibling of RUNBOOK/GOTCHA (`kind` picks the flavor).
- `ResearchNodeType.EXPERTISE_PROFILE = "ExpertiseProfile"` — one per agent,
  purely structural (§8.2).

**New edge strings** in `ALLOWED_EDGE_TYPES`: `performed_by` (Session/finding →
Agent), `reports_to` (Agent → Agent). Distillation provenance reuses
`derived_from`; **no** `distills_to`, and — deliberately — **no supersedes
edges are ever minted by distillation into any graph** (§6).

**Metadata schemas (closed allowlists — enforced by the lint probe, §7):**

```
AGENT:             agent_key, harness, account, role, label
DISTILLED_NOTE:    agent, kind ∈ {runbook,gotcha,note,index,activity,arbitration},
                   lineage_key, content_hash, member_count, member_refs[],
                   absorbed_refs[], distill_quality ∈ {llm,fallback,structural},
                   first_seen_at, distilled_through
EXPERTISE_PROFILE: agent, session_count, finding_counts{}, top_concepts[],
                   distilled_through
```

- `lineage_key` = sha256 of the sorted **transitive raw L0 member ids**
  underlying the distillate — recomputed through recursion (a manager
  distillate's lineage = sorted union of its constituents' raw roots). This is
  the stable identity spine of the whole system: node `id_seed =
  f"distilled:{agent_key}:{lineage_key[:16]}"`, the manager-pass grouping key,
  and the federation identity key. LLM wording (`content_hash`) is *change
  detection only*, never identity — sibling agents' prose will never be
  byte-identical, so hashing text would make dedup dead code.
- `member_refs` = `[{node_id, content_hash}]` of raw L0 members — **the
  provenance contract across layers** (§6.4). `wiki://` URIs are an optional
  display alias only (findings can be projection-private, so wiki liveness
  would misreport alive-but-unpublished nodes as gone).
- `first_seen_at` = earliest member's `first_seen_at`; `distilled_through` = the
  corpus clock of the run (§7.1). Both source-derived; no `now()`, no counters,
  no run-varying provenance flags.

**Federation additions** (`federation.identity_key`): `AGENT → agent_key`,
`DISTILLED_NOTE/RUNBOOK/GOTCHA (distilled) → metadata['lineage_key']`,
`EXPERTISE_PROFILE → ('profile', agent_key)`. Anchor nodes (§5.4) converge by
their original L0 stable ids. Cross-agent *relatedness* stays the existing
opt-in `add_semantic_links` bridge — no fuzzy cross-agent merge, ever.

**Persistence:** zero schema migration. Distilled artifacts are ordinary
`graph.json` files read/written by the existing stores. All mutable bookkeeping
(watermarks, negative cache, forget ledger, drill-down audit log) lives in a new
`agent_distill_state` table in the existing `.tesserae/sqlite.db` sidecar —
never in any graph artifact.

## 5. The distillation pass — organize / compact / polish / refine / forget

`tesserae distill [--agent KEY | --all] [--dry-run] [--max-llm-calls N]
[--jobs 4] [--full] [--retry-fallbacks] [--recheck] [--as-of TS]` — runs
post-compile or on demand (env-gated `TESSERAE_AGENT_DISTILL`), **never inside
the project compile**. Input: L0 bytes (worker) or the federation of children's
L1 bytes (manager). Output: one canonicalized artifact, written atomically
**only if bytes changed** (write-if-changed preserves the `_FED_GRAPH_CACHE`
mtime signature — a no-op re-distill must not invalidate ancestors' caches).

### 5.1 Scope (deterministic closure — organize, part 1)

Agent's slice = sessions matching `agent_key` (via registry match/aliases) →
findings via `derived_from_session` → expansion ≤2 hops along an allowlisted
typed-edge set (`mentions`, `about`, `derived_from`, `supersedes`,
`part_of`, …), deterministic BFS with sorted traversal.

**Explicitly not PPR.** `personalized_pagerank` is a whole-graph power
iteration: any unrelated import perturbs every score, and a `top_k` cut turns
epsilon shifts into discrete scope flapping — churning clusters, invalidating
caches, and re-keying node ids for agents that did nothing. Closure over an
additive-only L0 is monotone by construction (edges are only added, so ≤2-hop
reachability only grows): nodes enter scope and never flap out, with no sidecar
state needed. `graph_ppr` keeps its role at **query time** — an agent-seeded
PPR neighborhood is the expertise *profile*, not the distillation *input*.

### 5.2 Cluster (deterministic — organize, part 2)

Union-find over the scope, reusing `memory/distill.py`'s Jaccard-on-names +
supersedes-edge grouping, hardened for scale:

- **Candidate generation via token inverted index** (compare only pairs sharing
  ≥1 non-stopword token) — near-linear, replacing the O(n²) all-pairs scan
  (measured 6.56s at 1,046 findings; untenable at 20k).
- `_tokenise` memoized; a stopword/domain-token filter (`fix`, `test`,
  `session`, …) so generic tokens cannot chain mega-clusters.
- **Cluster size cap 100**, split deterministically by session-time buckets.
- Assignment memoized in `agent_distill_state`, keyed
  `sha256(sorted (id, name) of slice)` — an unchanged slice skips clustering.

Clusters below `min_cluster_size` are not distilled (too young); their members
flow to §5.5 as remainder/index — never silently dropped.

### 5.3 Compact & polish (LLM, cached — the only paraphrase step in the system)

Per cluster: render members as blocks → `pack_blocks` → `map_reduce_text`
(48k-char budget, `llm_chunking.py`) with the distill prompt: *organize,
compact, polish; produce a runbook/gotcha/note; cite member ids*. Bounded reads
regardless of cluster size — the scalability guarantee.

**Validation** (the `extract_with_llm` contract, plus a faithfulness gate):
typed schema (kind, title, body, citations); citations restricted to input
member ids (fabrication → reject); **deterministic faithfulness lint** —
identifiers, numbers, error strings, and version tokens appearing in the body
must appear in some cited member, or the output is rejected; drop-don't-crash;
stats distinguish failed vs empty vs rejected.

**Cache** — shared, project-level: `.tesserae/distill_cache/<lineage[:2]>/
<lineage_key>.json` with `{schema_version, prompt_version, guidance_digest,
members_digest, output | fallback:true}`. Shared (not per-agent) because
cluster identity is agent-independent: overlapping agents' scopes must reuse
each other's outputs instead of paying per agent — the prompt only forks
(via `guidance_digest`) when Phase 5 per-agent guidance actually differs.
Atomic pid+random tmp-rename writes; pruned by **LRU age** (unused N runs,
tracked in `agent_distill_state`), not exact-set difference, so a cluster that
flaps and returns hits its old entry.

**Refine — incremental fold.** The LLM cache key is decoupled from node
identity: when a cluster grows by <30% and no merge occurred, run a cheap
**fold** call (prior cached output + only the new member blocks: "fold these in;
do not drop cited facts") instead of re-mapping all members; full re-distill
only past the threshold or on merge. Hot clusters — the ones an active agent
touches daily — stop re-paying whole-cluster cost per run. The folded output is
cached under the current `(lineage_key, members_digest)` like any other value,
so replays are byte-stable.

**Fallback** (LLM failed or rejected): deterministic body = concatenated member
titles + refs, `distill_quality: fallback`, **cached** as
`{fallback: true, members_digest}` and reused until `members_digest` changes or
`--retry-fallbacks` — two runs over identical inputs must not flip bytes just
because the provider recovered. Fallback distillates **never absorb members**
(§6.1), are deprioritized in budget pools (§9), and are visibly flagged — an
orchestrator must never confidently ground a decision on title-concatenation
noise.

**Provider hygiene:** negative cache with exponential backoff per cluster
(failure count + earliest-retry watermark in `agent_distill_state`, never in
the artifact); circuit breaker — N consecutive transport failures aborts the
LLM stage for the run with a provider-health summary (failed/succeeded/
fallback counts). `--dry-run` prints cluster count, estimated LLM calls, and
estimated wall-clock before anything runs; `--max-llm-calls` caps a run (the
cache makes capped runs converge over several invocations); `--jobs` runs a
small subprocess pool (the codex/claude CLIs tolerate parallel invocation).
`--recheck` (with `schema_version` bump) forces a cache re-audit when the
validation contract tightens.

### 5.4 Mint (deterministic)

One distillate per qualifying cluster, metadata per §4, edges only where **both
endpoints are present in the artifact** (federation drops dangling edges, so an
edge to an absent raw member would be dead on arrival — provenance to absent
nodes travels as `member_refs`, §6.4):

- `derived_from` distillate → **anchor nodes**: the Concept / Repository /
  Paper / CodeDoc / CodeFunction nodes the distillate cites, copied into the
  artifact **verbatim with their original L0 stable ids** (cap 40, by citation
  count then id). Anchor identity is what makes sibling expertise converge at
  federation by plain id-equality — `federation_members` directly answers
  "which of my reports knows X".
- Write-time check: every `member_refs` entry must resolve against the input
  graph, or the run fails.

### 5.5 Emit & forget (deterministic)

The L1 artifact contains, in canonical order:

1. Agent node + `reports_to` edge (from registry).
2. `EXPERTISE_PROFILE` (§8.2).
3. Distillates + their anchor nodes + in-artifact edges.
4. **Raw remainder** — top-K (default 50) non-absorbed scope findings by
   `(-recurring_confidence, node_id)`. The tiebreak is mandated: confidence
   takes few discrete values, so cutoff ties are guaranteed and must never
   lean on serialization order (the idempotence fixture includes a tie at the
   cutoff). Hysteresis band (§6.2) applies.
5. **Index note** (`kind: index`, `distill_quality: structural`, no LLM):
   title + `member_refs` entry for **every** non-absorbed scope node not in
   the remainder, newest-first by `first_seen_at` (id tiebreak). If the size
   bound forces truncation, the oldest entries roll into a deterministic count
   line ("+ 312 older undistilled findings — drill_down or lint backlog").
6. **Activity note** (`kind: activity`, structural): last-10 session titles +
   source-derived dates — the recency signal a router needs (§8.3).

Then `.canonicalized()`, write-if-changed. Committing
`.tesserae/agents/**/distilled.graph.json` to the repo is the recommended
default: artifacts are small by design, and git diff is the mechanism that
makes cold-cache LLM divergence *visible* instead of silent (§7.3).

## 6. Forgetting — suppression and demotion, never deletion

The user's "FORGET" decomposes into three tiers. None of them deletes anything,
and none of them touches `graph.json`.

### 6.1 Tier 1 — absorb-and-prefer (agent-scoped overlay, not L0 edges)

A distillate **absorbs** member `m` iff: `distill_quality == llm` AND `m` is a
finding-type node (anchors are never absorbed) AND `decay(m, corpus_now) < 0.2`
AND `recurring_confidence(m) <` the promote bar AND `m` is not the winner of a
live supersede chain (winners kept; losers are already suppressed by existing
L0 semantics). Absorbed refs are recorded in `metadata['absorbed_refs']`.

**Rejected mechanism — supersedes write-back into L0** (both the original
Phase-4 wiring and the judge panel's cold-gated variant). It fails three ways
at once: (a) distillate ids depend on cluster membership, so distill-derived
edges in `graph.json` break CMP-03 — exactly the mutable-state class that broke
byte-idempotence 4×; (b) `graph_filters.superseded_ids` suppresses edge
*targets* without checking the source exists, and the distillate lives outside
L0 — suppression-without-replacement is de facto deletion, including for plain
single-graph users and *other* agents sharing the raw node; (c) additive-only
L0 accumulates ghost edges to dead distillate ids forever as clusters re-key.

**Adopted mechanism — the merged worker view.** `_load_requested_graph(agent=
worker)` returns **L0 ∪ that agent's own L1**: the distillate is present, and
the absorption suppression set is *derived at load time* from the live
artifact's `absorbed_refs` (unioned with graph `superseded_ids`). Retrieval
auto-prefers distilled knowledge with zero ranking changes; absorbed raw stays
reachable via `include_superseded` / `drill_down`. Plain project-graph reads
(no `agent=`) see no suppression at all. Regenerating the artifact regenerates
the overlay — ghosts are structurally impossible. New invariant test: every
suppression source in any loaded view resolves to a live node.

### 6.2 Tier 2 — demote-to-index (bounded promotion; exclusion requires absorption)

**Invariant: age or frequency alone never makes knowledge invisible.** Decay is
pure age today (`last_accessed_at == first_seen_at` until an access-recording
surface lands) and reinforcement is distinct-session frequency — a gate built
on those systematically discards old, single-occurrence gotchas about rare
failure modes, the class you most need later. And the newly-uncapped importer
backfills old sessions whose findings arrive pre-decayed — a naive gate would
mass-forget them on first distill.

So: a scope node leaves the artifact **entirely** only via Tier-1 absorption
(its content lives on in a distillate that cites it). Everything else is at
worst **demoted** from full-body remainder to a title+ref line in the Index
note — bounded artifact, nothing invisible, manager can always drill down.
Decay and confidence *rank*; only absorption *excludes*.

**Hysteresis (two thresholds, no counters):** a node enters the remainder at
decay ≥ 0.3 and is demoted only below 0.15, evaluated against the **prior
committed artifact** as a declared input (`--from-scratch` ignores it). This
kills rank-51 churn without run-count state — counters in artifacts are exactly
what the lint probe (§7.2) exists to reject.

**Forget report:** every run appends a deterministic diff (promoted / demoted /
absorbed node ids, old→new artifact) to an append-only ledger in
`agent_distill_state`, surfaced by lint — shrinkage is visible before it costs
a decision, never discovered after.

### 6.3 Tier 3 — cache GC and the backlog metric

Cluster caches pruned by LRU age (§5.3). Artifacts are strip-and-regenerate
(distillates are DERIVED, like SYNTHESIS/COMMUNITY_SUMMARY — never accumulated
in place). Lint gains an **undistilled-backlog** metric per agent: scope nodes
referenced by no distillate's `member_refs`, with age — the knowledge sitting
below the distillation waterline is measured, not invisible.

### 6.4 Provenance across layers — refs, not edges

`federate_graphs` drops dangling edges, so `derived_from` edges to raw members
cannot survive above L1 — and namespaced child ids re-key on every child
re-distill. Therefore **`member_refs` is the provenance contract**: at every
level, refs store the transitive **raw L0 roots** (node id + content_hash),
flattened — an L2' distillate's refs point directly at L0. A new read-only,
audit-logged `drill_down(member_ref)` MCP tool resolves refs against the
owning child's L0 with alive/absorbed/gone status — the manager's explicit
escalation path (default visibility stays sealed; every use is logged to the
sidecar). `federation_explain` surfaces refs + `distilled_through` for the
"which agent, how stale" audit trail.

## 7. Determinism, caching & the corpus clock

### 7.1 The corpus clock — no wall-clock, anywhere

Universal rule for all lifecycle math (decay, forget gates, freshness, and any
future signal in a compiled path): `now = corpus clock`, never `datetime.now()`.

- **L1:** `corpus_now = max(ended_at or started_at over scope sessions)`.
  Knowledge ages relative to the agent's latest experience — an agent idle for
  a month does not rot, and re-running distill tomorrow with no new sessions
  produces identical bytes by construction.
- **L2' (recursive):** `corpus_now = max(distilled_through over input
  artifacts)` — L1 artifacts contain no Session nodes, so the naive "newest
  session in scope" resolves to nothing and the natural implementation fallback
  is `datetime.now()`: the precise historical failure class
  (`memory/decay.py:33-36` warns about exactly this). Defining the recursive
  default explicitly closes the leak.
- No timestamps in the input → **hard fail** with a message requiring
  `--as-of`. `--as-of` remains as an override only, not an operator obligation.

**Promotion signals are corpus-derived only.** `recurring_confidence` is
recomputed from graph bytes (`memory/reinforce.py` is pure); decay anchors on
source-derived `first_seen_at` at the corpus clock. The sqlite sidecar's
`access_count` / `last_accessed_at` — mutated by every MCP read — **never**
influence any graph artifact: otherwise merely *querying* between two runs
changes which nodes cross the threshold, and back-to-back idempotence tests
stay green while the contract is broken in normal operation (a false-green
identical to the historical failures). This also makes L1 reproducible from
the repo alone — two checkouts produce the same org view.

### 7.2 Idempotence contracts

| Artifact | Contract |
|---|---|
| L0 `graph.json` | Unchanged CMP-03. Agent/ExpertiseProfile nodes + `performed_by`/`reports_to` edges are structural (pure function of session envelopes + registry file). Zero distill-derived content; suite green with `TESSERAE_AGENT_DISTILL` set or unset. |
| L1 / L2' `distilled.graph.json` | Byte-identical given (input graph bytes, shared distill cache dir, prior artifact bytes [hysteresis input only]). LLM text enters only via content-keyed caches; fallback verdicts cached; sorted/canonicalized; corpus clock; write-if-changed. |
| L2 federated view | In-memory only, never serialized (federation's documented no-byte-idempotence-concern surface). |
| Mutable state | `agent_distill_state` sidecar: watermarks, negative cache, LRU tracking, forget ledger, drill-down audit. Never in any graph file. |

**Watermark skip:** `input_hash = sha256(sorted (node_id, content_hash) over
the agent's slice)` stored per agent; unchanged hash → the run is skipped
entirely (zero LLM calls, zero writes). A **pure** skip-if-identical-inputs
optimization — no volume/size thresholds gate *output*, so `tesserae distill
--full` (ignores watermarks, still uses the cache) converges a fresh clone to
byte-identical artifacts.

**Tests (the guards MEMORY.md says green tests failed to provide):**

- `test_distill_is_byte_idempotent` per artifact: double run, warm cache.
- **Cold-cache parity** with a stubbed deterministic LLM: cache dir deleted
  between runs — catches cache-state-leaking-into-bytes, which warm-only tests
  structurally miss.
- L2' double run across a `time.sleep` — asserts the corpus clock, not the
  wall clock, fed every gate.
- Remainder fixture with a tie at the K cutoff.
- **Path-specific lint probe:** reject any metadata key on
  AGENT/DISTILLED_NOTE/EXPERTISE_PROFILE matching timestamp/counter patterns
  (`*_at`, `*_time`, `*count*`, …) outside the closed allowlist of §4
  (`first_seen_at`, `distilled_through`, `member_count`, `session_count`,
  `finding_counts` — all pure functions of the corpus). This encodes the
  4×-broken blind spot as CI.
- **Size invariant, falsifiable:** rendered L1 ≤ 48k chars — lint warns at 90%,
  fails in strict/CI mode. "An agent's expertise fits one context read" is a
  tested bound, not aspiration.
- Write-time `member_refs` resolution check (§5.4).

### 7.3 Cold-cache honesty

Given a *missing* cache, an LLM is nondeterministic — no contract can promise
byte-equal regeneration. The design makes divergence **detectable** instead of
silent: artifacts are committed (recommended default), so a fresh machine's
re-distill shows up as a reviewable git diff of small, readable notes rather
than as two teammates' managers silently believing different things.

## 8. Manager visibility, recursion & the org loop

### 8.1 Resolution

`_load_requested_graph` gains `agent=`:

- **worker key** → L0 ∪ own L1 (merged view with absorption overlay, §6.1) —
  full access to own raw experience, distillate-preferred retrieval.
- **manager key** → mtime-cached `federate_graphs` over children's L1 artifacts
  ∪ the manager's own L1 (aliases = agent keys, so `federation_members` answers
  "which reports know this" and `federation_explain` gives per-agent
  provenance + staleness). The `_FED_GRAPH_CACHE` mtime signature
  self-invalidates when any child re-distills — and *only* then, thanks to
  write-if-changed.
- **`agent='org'`** (builtin pseudo-key, zero registry config) → federation of
  **all** registered L1s — the team overview, reachable for 1-level users.

Every MCP read tool inherits scoping for free.

### 8.2 What a router actually needs (knowledge alone is not a routing signal)

- **`EXPERTISE_PROFILE`** per agent, purely structural: session_count, finding
  counts by type, top-N concepts by mention count (`(-count, id)` tiebreak) —
  deterministic, no PPR (whole-graph scores would churn the artifact on every
  unrelated import). Reserved `multi_pool` slot, so a manager's first
  `compile_context` always contains every report's capability card.
- **`distilled_through`** watermark surfaced in `federation_explain` and
  `compile_context` headers — the manager always knows *how stale* each
  report's expertise is; delegation on weeks-old knowledge is at least labeled.
- **Activity note** (§5.5) — recency without LLM cost.
- **Freshness path:** when `TESSERAE_AGENT_DISTILL` is set, the refresh flow
  triggers distill for agents whose watermark changed AND whose undistilled
  slice exceeds half the 48k chunk budget — the MemGPT-style memory-pressure
  signal: consolidation fires exactly when raw recall stops fitting one read.
  Manual `tesserae distill` always available.

### 8.3 Recursion without a telephone game

L2' materialization is **the same pass with a different input** — but above L1
the pass is *selective, not paraphrasing*. LLM re-summarization depth is capped
at 1 (raw → L1). The manager pass:

1. **Dedup by identity** — lineage keys, anchor ids, profile keys (§4).
2. **Group** child distillates by overlap of raw-root ref *sets* (Jaccard on
   `member_refs`, threshold 0.5) — never by LLM-authored titles, which re-key
   on every child regeneration and would cascade cache invalidation O(depth).
3. **Carry verbatim** — per group, the representative distillate(s) (highest
   member_count, id tiebreak) are copied with bodies untouched; siblings
   become refs. No paraphrase-of-paraphrase; a hallucination minted once can
   propagate but never *compound*, and the faithfulness lint (§5.3) applies at
   the only level where prose is generated.
4. **Arbitrate contradictions** — when grouped sibling distillates conflict,
   run the existing `contradiction.py` content-keyed pass; emit a
   `kind: arbitration` note citing both sides (LLM, cached like any cluster).
   The manager sees an arbitrated claim, not two contradicting digests. LLM
   cost at manager levels ≈ arbitration only.
5. **Never fuse across agents without ground truth:** notes from different
   `federation_members` may be merged only if their `member_refs` overlap at
   L0; otherwise they stay side-by-side with an `add_semantic_links` bridge —
   two agents' distinct procedures must not blend into one confident body.
6. Manager clusters key on the *set of child lineage keys*; a child artifact
   that re-distills without changing a constituent's `content_hash` triggers
   zero manager work.

Emit/forget (§5.5/§6) apply unchanged; refs stay flattened to L0 roots.

## 9. Retrieval integration

- **`compile_context`:** `multi_pool` reserved slots extended to
  `DISTILLED_NOTE` and `EXPERTISE_PROFILE` (distilled knowledge gets budget
  even when raw findings crowd the window); `distill_quality: fallback` nodes
  deprioritized within the pool and flagged in rendering; `distilled_through`
  in headers. Additive, off-by-default-compatible.
- **`graph_ppr`:** unchanged on any resolved graph. Agent-seeded PPR over L0 =
  query-time expertise profile. Manager-scope queries get `edge_type_weights`
  biased toward `derived_from`/`summarizes` so traversal prefers distilled
  knowledge — complementing the pool reservation.
- **`ask` / `ask_planner`:** distillates have stable ids and full bodies →
  the mandatory-citation gate holds unchanged; `drill_down` grounds a citation
  to raw L0 evidence on demand.
- **Per-agent harness** (Phase 5): `agent_harness.write_harness` gains an agent
  mode — a harness dir whose MCP args point `--graph` at the agent's resolved
  view, plus a per-agent `purpose.md` via KarpathyLayerWriter (self-describing
  mission; pointer block stays a pure function of agent_key per the
  instruction-file determinism rule).

## 10. Cost & scale guardrails (summary)

| Risk | Mitigation |
|---|---|
| First-run runaway (1–3k serial LLM calls, hours) | `--dry-run` estimate · `--max-llm-calls` + cache-resume convergence · `--jobs` pool |
| Hot-cluster re-pay on every growth | fold-in incremental (§5.3), full re-distill only >30% new or merge |
| O(n²) clustering per run | token inverted index · memoized tokenise · assignment cache · unchanged-slice skip |
| Provider outage retry storm | negative cache + backoff + circuit breaker; fallbacks cached and flagged |
| Duplicate spend, overlapping agents | shared project-level cluster cache keyed (lineage, members_digest, prompt, guidance) |
| Leaf churn cascading O(depth) LLM cost | manager clusters keyed on child lineage sets; recompute only on constituent content_hash change; verbatim carry |
| No-op runs invalidating ancestor caches | write-if-changed + watermark skip |
| Mega-clusters as permanent cache misses | size cap 100 + time-bucket split + token filter |

## 11. Migration from today's single graph

1. **Phase 1 ships inside the normal compile** (structural pass extension):
   Agent nodes + `performed_by` edges only. Existing users see ~N new nodes;
   all tools work unchanged; the byte-idempotence suite is the gate — this is
   the risky enum/exemption-set touch, done first and alone.
2. `tesserae distill` is opt-in and additive; running it changes nothing about
   the project compile. Users without a registry get role-derived agents (or
   one per (harness, account) at minimum) implicitly parented to `org:root` —
   today's global view is root's view, and the solo user can inspect their own
   distilled expertise graph. The boundary vs `community_summaries.py` is
   documented: community summaries describe the *project's* topic structure;
   distillation produces an *agent's* operational knowledge — solo users are
   not being sold a second summary system.
3. Registry parent links are opt-in; `agent='org'` works with zero config.
4. Nothing is migrated destructively: L0 keeps everything; deleting
   `.tesserae/agents/` (and the cache dir) fully reverts the feature.

## 12. Phase plan (each shippable + testable)

- **Phase 1 — Agent identity + org substrate (structural, no LLM).**
  AGENT/DISTILLED_NOTE/EXPERTISE_PROFILE enums with dedup-exemption and
  canonicalization-exclusion wiring; `performed_by`/`reports_to` edge strings;
  role-grade `agent_key` (subagent-descriptor extraction, registry match rules,
  aliases, path-independent account slug); `session_graph_structural` mints
  Agent nodes + `performed_by`; `tesserae agents init|list|set-parent|rename`;
  implicit `org:root`; structural EXPERTISE_PROFILE; metadata lint probe.
  *Ship gate:* fixture corpus yields ≥2 role-distinct agents; CMP-03 suite
  green. Shippable: per-agent attribution queryable today via `graph_ppr`
  seeded on an Agent node.
- **Phase 2 — The distill pass.** `tesserae/agent_distill.py`: deterministic
  scope closure; indexed/capped/cached clustering; shared cluster cache with
  fold, fallback-caching, negative cache, circuit breaker; validation with
  citation whitelist + faithfulness lint; mint with lineage keys + anchors;
  corpus clock; remainder (+hysteresis) + Index/Activity notes; canonical
  write-if-changed; watermark skip; CLI with `--dry-run/--max-llm-calls/
  --jobs/--full/--retry-fallbacks/--recheck/--as-of`. *Tests:* warm + stubbed
  cold-cache byte parity, tie fixture, size lint, ref-resolution. Shippable:
  any agent's L1 exists and is queryable via the existing graph-path argument
  on every MCP tool.
- **Phase 3 — Manager view via federation.** `agent=` resolution (worker
  merged view + absorption overlay; manager federation; `agent='org'`);
  lineage/agent/profile `identity_key` entries; `federation_explain` +
  `compile_context` surfacing of `distilled_through`; `multi_pool`
  reservations + fallback deprioritization; `drill_down` tool (audit-logged);
  explicit missing-artifact errors. Shippable: a manager queries
  `compile_context`/`graph_ppr`/search over only its reports' distilled
  knowledge, with provenance and staleness.
- **Phase 4 — Recursion + forgetting hardening.** L2' materialization
  (select/dedup/verbatim-carry, lineage-set cluster keys, contradiction
  arbitration); recursive corpus clock (+ `time.sleep` parity test); forget
  ledger + lint surfacing; undistilled-backlog metric; refresh-flow trigger
  with memory-pressure gate. Shippable: arbitrary-depth org trees with
  provenance-preserving, visible forgetting.
- **Phase 5 (optional, independently droppable) — Growth loop.** Per-agent
  harness dirs + `purpose.md`; per-agent extraction-guidance streams
  (guidance_digest forks the cluster cache); opt-in `add_semantic_links`
  cross-agent bridges; per-agent topic-map rollup (`community_summaries` over
  the distillate set — the llm-wiki table of contents); deeper
  subagent-transcript promotion. Shippable: agents accumulate steerable,
  self-differentiating expertise.

## 13. YAGNI & rejected alternatives

Rejected with reasons (including judge-panel grafts not adopted):

- **Supersedes write-back into L0** (original Phase 4; also the panel's
  cold-cluster-gated variant) — injects distill-derived state into the CMP-03
  artifact and suppresses raw nodes for readers who cannot see the winner;
  replaced by the agent-scoped merged view + load-time absorption overlay
  (§6.1), which delivers the same distillate-preferred retrieval with zero L0
  bytes touched.
- **PPR-scoped distillation input** — global float coupling turns unrelated
  imports into cross-agent cache invalidation and id churn; replaced by the
  monotone deterministic closure (§5.1). PPR stays query-time.
- **content_hash as federation identity for distillates** — sibling LLM prose
  is never byte-identical, so dedup would be dead code; replaced by
  `lineage_key` (§4).
- **Title-Jaccard clustering at manager levels** — LLM-authored titles re-key
  on every child regeneration, cascading O(depth) re-distillation; replaced by
  raw-root ref-set overlap (§8.3).
- **Re-paraphrasing at manager levels** — telephone-game drift, certified
  forever by the cache; replaced by select + verbatim carry + arbitration
  (§8.3).
- **Silent top-K remainder drop** — age/frequency-only forgetting discards
  rare-but-critical knowledge; replaced by the absorption-only exclusion
  invariant + Index note (§6.2).
- **Per-agent raw (L0) graph files** — attribution edges + closure scoping
  over the shared graph suffice; hard partitioning breaks shared code/concept
  nodes.
- **A `layer` field / new persistence columns** — layers are files.
- **Wall-clock anywhere in lifecycle math** — corpus clock only.
- **Run-counter demotion hysteresis** — counters in artifacts are what the
  lint probe bans; replaced by the two-threshold band against the prior
  committed artifact (§6.2).
- **Layer-routing LLM classifier** ("my layer vs reports vs escalate") — v1 is
  explicit `agent=` selection; add routing when a real manager-agent workflow
  exists.
- **Embedding-based clustering inside distillation** — token-indexed Jaccard
  union-find is deterministic and proven; semantic links stay query-time
  opt-in.
- **Continuous/real-time distillation daemons** — post-compile or on-demand,
  with the memory-pressure trigger as the only automation.
- **Manager write-back (directives flowing down), cross-project agent
  identity, any UI** — later, if ever.

## 14. Open questions

1. **Hysteresis input scope** — is the prior committed artifact as a declared
   determinism input (§6.2) acceptable long-term, or should demotion become
   fully stateless (pure two-threshold on decay alone) at the cost of some
   rank-edge churn?
2. **Thresholds** — `min_cluster_size`, decay absorb-gate 0.2, hysteresis
   0.3/0.15, K=50, fold threshold 30%, anchor cap 40: all need a small eval on
   the real corpus (1,046 findings today; the 300-turn-cap removal backfill is
   the stress test). Should K scale with scope size?
3. **Manager-level prose** — should L2' arbitration notes remain the only LLM
   text above L1, or is a structural-only TOC (titles + refs, zero LLM) enough
   for the "higher-level knowledge" requirement at depth ≥2?
4. **Committing artifacts** — recommended default here; does any deployment
   need `.tesserae/agents/` gitignored (distilled content is derived from
   session transcripts — same sensitivity class as the vault)?
5. **Role taxonomy** — free-form subagent labels vs a curated role set for
   `agent_key`; free-form risks key sprawl, curation risks ceremony.
6. **Per-agent guidance forking** — when Phase 5 guidance streams diverge the
   shared cluster cache (`guidance_digest`), duplicate spend returns for
   overlapping agents; is specialization worth it per cluster, or only for
   clusters exclusive to one agent?
7. **Access-recording surface** — once reads actually record access
   (`memory/store.py` upsert exists, unwired), can access frequency inform
   *ranking* without ever touching artifact bytes (query-time only)?
