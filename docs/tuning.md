# Tuning reference — environment variables

<!-- translations:start -->
<p align="center"><a href="i18n/tuning.ko.md">한국어</a> · <a href="i18n/tuning.zh.md">中文</a> · <a href="i18n/tuning.ja.md">日本語</a> · <a href="i18n/tuning.ru.md">Русский</a> · <a href="i18n/tuning.es.md">Español</a> · <a href="i18n/tuning.fr.md">Français</a> · <a href="i18n/tuning.de.md">Deutsch</a></p>
<!-- translations:end -->

Every knob Tesserae reads from the environment, what it defaults to, and when
you would actually change it. Nothing here is required: the defaults are chosen
so a plain `tesserae compile` does the right thing.

Project and global config (`.tesserae/config.json`, `~/.tesserae/config.json`)
take precedence for the LLM-backend settings; the env vars below win over both
for the run they are set in.

---

## Extraction

### `TESSERAE_EXTRACT_TIMEOUT`

**Default `1800` (seconds), per ATTEMPT.** Bounds each codex/claude extraction
call so a wedged CLI child cannot hang a compile.

This exists because it happened: a compile was observed at 0% CPU for **5 h 43 m**
behind a `codex exec` child idle for **4 h 6 m**, holding `.tesserae/compile.lock`
the whole time. It had already built 32 community summaries in memory and never
lived to persist them.

Per *attempt*, not per document — on timeout the client rotates to the next
`CODEX_HOME` / claude config dir, so one document's worst case is
`timeout × configured profiles`.

```sh
export TESSERAE_EXTRACT_TIMEOUT=3600   # more headroom for very large documents
export TESSERAE_EXTRACT_TIMEOUT=0      # no cutoff — run to completion
```

A value that is set but unusable (`10m`, `600s`, negative, `inf`) warns on stderr
and keeps the default. A typo must not silently disarm a safety valve.

### `TESSERAE_EXTRACT_CONCURRENCY`

**Default `4`.** Documents extracted in parallel. Each one is a blocking CLI
subprocess taking roughly a minute, so a sequential loop makes wall-clock the
literal sum of every model round-trip — measured at ~2 h 40 m for 161 documents.

The ceiling is your provider account's rate limit, not your machine, which is why
the default is modest. Set `1` for strictly sequential behaviour.

Concurrency never changes output: the work-list is fixed in path order and
results are collected by index, so a parallel run is byte-identical to a
sequential one.

### `TESSERAE_LLM_CACHE`

**Default on.** Content-addressed cache of CLI provider responses under
`~/.tesserae/llm_cache`, keyed on (document, kind, guidance) plus the model and
reasoning effort — so switching models re-asks rather than serving the previous
model's answers. Only parseable responses are stored, so one bad generation
cannot become permanent.

```sh
export TESSERAE_LLM_CACHE=0   # always re-ask
```

### `TESSERAE_LLM_CHUNK_CHARS`

Characters per chunk when a document is too large for one call. Leave unset
unless you are hitting context limits.

---

## LLM backend

| Variable | Default | Notes |
|---|---|---|
| `TESSERAE_LLM_PROVIDER` | `claude` | `codex`, `claude`, `anthropic`, `custom` |
| `TESSERAE_LLM_MODEL` | provider-specific | Scoped by provider so a claude-shaped model never lands on the codex path |
| `TESSERAE_CODEX_REASONING_EFFORT` | `medium` | Structured extraction does not need the `xhigh` you may set for interactive work — `xhigh` makes a multi-document compile many times slower |

`tesserae config status` prints the resolved backend and pings it for liveness.

---

## Compile passes

| Variable | Default | What it gates |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **on** | The GraphRAG-style summary pass. One LLM call per cluster ≥ 5 members, cached by membership digest. `false`/`0`/`no`/`off` disables |
| `TESSERAE_ENABLE_LLM_PASSES` | off | Optional LLM enrichment passes beyond extraction |
| `TESSERAE_AGENT_DISTILL` | off | Per-agent L1 expertise artifacts (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | off | Runbook/Gotcha distilled-memory nodes |
| `TESSERAE_INSIGHT_SYMBOL_LINK` | on | Links session insights to code symbols |
| `TESSERAE_SUPERSEDE_PASS` | on | `superseded_by` edges between revised claims |
| `TESSERAE_PROMPT_SIGNATURES` | off | Records prompt signatures for drift detection |
| `TESSERAE_COMPILE_LOCK_WAIT` | — | Seconds to wait for `.tesserae/compile.lock` before giving up |

**On community summaries:** the compile pass eagerly covers the coarsest level;
`graph_map` additionally materialises a summary lazily the first time you descend
into a cold scope, cached per level. Turning the pass off is a legitimate cost
strategy — you pay only for branches you actually visit — with one caveat:
**federated descent never lazily materialises.** A sibling project's cards can
only be named from its in-graph summaries or already-warm caches, so a project
you navigate cross-project wants the eager pass on.

---

## Query and synthesis

| Variable | Default | Notes |
|---|---|---|
| `TESSERAE_QUERY_LLM` | off | LLM planner for `tesserae query` |
| `TESSERAE_QUERY_DRY_RUN` | off | Plan without calling the model |
| `TESSERAE_SYNTHESIS_LLM` | off | Prose synthesis in `tesserae ask` |
| `TESSERAE_SYNTHESIS_MODEL` | — | Overrides the synthesis model |
| `TESSERAE_SYNTHESIS_WORKERS` | — | Parallel synthesis workers |
| `TESSERAE_SYNTHESIS_DRY_RUN` | off | Skip the model, exercise the pipeline |

---

## Paths and infrastructure

| Variable | Default | Notes |
|---|---|---|
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Project registry location |
| `TESSERAE_DISCOVERY_CACHE` | — | Session-discovery cache |
| `TESSERAE_ARXIV_CACHE` | — | arXiv metadata cache |
| `TESSERAE_NO_FEDERATION_CACHE` | off | Disables the federated-graph LRU |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | off | Emits the combined cross-project graph |
| `TESSERAE_FLEET_PIDFILE` | — | Engine fleet pidfile |
| `TESSERAE_CLIP_TOKEN` | — | Shared secret for the web clipper |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | off | Applies schema-drift proposals (`tesserae lab`) |

---

## Recovering a degraded corpus

When extraction fails for a document, it is served by the deterministic baseline
and **marked** in `.tesserae/manifest.json`. Without the mark it would be
indistinguishable from a clean extraction, so `--changed-only` would skip it
forever and the degradation would be permanent until the file's own content
changed.

```sh
tesserae compile --changed-only --retry-fallbacks
```

Re-attempts only the marked documents; clean ones stay skipped.

## Inspecting the hierarchy

```sh
tesserae graph-map                          # root map
tesserae graph-map --scope <scope_id>       # descend
tesserae graph-map --scope '<alias>::'      # a sibling registered project
```

Each card reports `size` and `leaf_member_count` from the hierarchy sidecar, plus
`live_member_count` — how many members the *current* graph actually carries. A
`0` there means the scope is dead (a sidecar/graph skew): skip it rather than
descend.

## Agents writing to the graph

`graph_write` (MCP) takes schema-validated typed nodes and edges with mandatory
provenance, so an agent records a finding as *structure* rather than as prose an
extractor has to guess the types back out of.

It refuses rather than coerces: untyped edges, node or edge types outside the
controlled vocabulary, dangling endpoints, and writes missing provenance are all
rejected. Duplicate writes are idempotent. Agent-written nodes survive a full
recompile, a deleted `graph.json`, `--limit`, and total corpus deletion.

## Verifying a claim against the graph

`verify_claim` (MCP) answers whether the graph licenses a triple. It takes
`(subject, predicate, object)` — **there is no natural-language parameter**, by
design, because a parser is what made the previous version answer SUPPORTED to
the negation of a claim it supported.

The verdict is a pure function of graph bytes: no LLM, no embedding, no fuzzy
matching anywhere on the decision path.

| Verdict | Meaning |
|---|---|
| `SUPPORTED` | the edge exists, carries its own evidence, and that text was re-grounded against the source file |
| `PRESENT_UNEVIDENCED` | the edge exists but nothing document-backed stands behind it |
| `CONTRADICTED` | a document-backed `contradicts_claim` between the same two endpoints |
| `DISPUTED_UNEVIDENCED` | disagreement asserted, none of it evidenced |
| `CONFLICTING` | both polarities document-backed — the tool declines to adjudicate |
| `ABSENT` | this graph does not assert the triple. Not a refutation |
| `NOT_RESOLVABLE` | an endpoint or predicate could not be resolved exactly |

Two things it deliberately will not do. It never treats `supersedes` as
refutation — that relation says a *node* was replaced, not that a triple is
false. And an agent write can only ever *weaken* a provenance class, never
upgrade one, so nothing an agent asserts can present as document-grounded.

Worth knowing when reading results: on a real 15,284-edge graph about 40% of
`SUPPORTED` verdicts are tautological — `evidenced_by` edges whose cited span is
the edge's own target. True, but not informative.

## Routing a question

`tesserae ask` picks a retrieval path by question shape: single-entity lookups go
to the cheap backend, multi-hop / "what changed" / "why" / corpus-wide questions
go to the graph. Independent benchmarks put graphs ahead on multi-hop, temporal
and synthesis questions, and *behind* on simple fact lookup and cost — so paying
graph prices for every question is a loss.

The decision appears in the returned envelope, so a cheap answer is auditable.
Override it with `--route` on the CLI, or the `route` parameter on the MCP tool.
