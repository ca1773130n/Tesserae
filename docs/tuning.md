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

## Hooks that spend money

The Claude Code plugin ships hooks that can background a compile. Anything that
spends is **off by default**:

```sh
export TESSERAE_HOOK_AUTOCOMPILE=1   # opt in to automatic recompiles
```

Gated: `posttooluse-edit.sh` (fires on every Edit/Write) and `session-end.sh`.
Not gated, because they cost nothing: `session-start.sh` runs `tesserae code
sync`, which is deterministic, and `pretooluse-compile.sh` only intercepts a
`tesserae compile` you typed yourself.

This default exists because the alternative was measured. A knowledge base at
`~/.tesserae` makes `$HOME` look like a project root, and the hook resolver
walked *up* from the working directory to the first `.tesserae/` it found — so
any session outside a registered project resolved to `$HOME` and compiled the
entire home directory: 15k files, a 795 MB graph, **~10 hours of LLM spend**,
from a detached process that outlived the session that started it.

`resolve_project_root()` now refuses `$HOME` by either path, and returns empty
rather than falling back to the working directory, so callers no-op instead of
guessing. A hook that backgrounds model work should be switched on deliberately,
not switched off after the bill.

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
| `TESSERAE_CLAUDE_CONFIG_DIRS` | — | `os.pathsep`-separated Claude config directories, in rotation order — the env channel for a repeated `--claude-config-dir`. A *configured* list is authoritative; the ambient `CLAUDE_CONFIG_DIR` deliberately is not, because pinning to it collapses multi-account rotation to one account |

`tesserae config status` prints the resolved backend and pings it for liveness.

---

## Compile passes

| Variable | Default | What it gates |
|---|---|---|
| `TESSERAE_COMMUNITY_SUMMARIES` | **on** | The GraphRAG-style summary pass. One LLM call per cluster ≥ 5 members, cached by membership digest. `false`/`0`/`no`/`off` disables |
| `TESSERAE_ENABLE_LLM_PASSES` | off | Optional LLM enrichment passes beyond extraction |
| `TESSERAE_AGENT_DISTILL` | off | Per-agent L1 expertise artifacts (`tesserae distill`) |
| `TESSERAE_RUNBOOK_DISTILLATION` | off | Runbook/Gotcha distilled-memory nodes |
| `TESSERAE_SESSION_EVENT_PASS` | **on** | Per-turn `Event` nodes from session transcripts. LLM-free and byte-deterministic, but one node per significant turn — sizeable on a long corpus. `false`/`0`/`no`/`off` disables |
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
| `TESSERAE_REGISTRY` | `~/.tesserae/registry.json` | Project registry location. Honoured by **every** command — until 0.28.7 only the engine's fleet mode read it, so setting it elsewhere silently had no effect and commands kept using the real registry |
| `TESSERAE_HOST_ID` | generated once into `~/.tesserae/host_id` | This machine's identity. See [running several machines](#running-several-machines-against-one-project) |
| `TESSERAE_DISCOVERY_CACHE` | — | Session-discovery cache |
| `TESSERAE_ARXIV_CACHE` | — | arXiv metadata cache |
| `TESSERAE_NO_FEDERATION_CACHE` | off | Disables the federated-graph LRU |
| `TESSERAE_INCLUDE_COMBINED_GRAPH` | off | Emits the combined cross-project graph |
| `TESSERAE_FLEET_PIDFILE` | — | Engine fleet pidfile |
| `TESSERAE_CLIP_TOKEN` | — | Shared secret for the web clipper |
| `TESSERAE_SCHEMA_DRIFT_APPLY` | off | Applies the **approved** records in `.tesserae/schema-drift-proposals.json` at compile time (deterministic, no LLM). Write proposals with `tesserae schema-drift`; approving one means editing `ResearchNodeType` first, then setting `"approved": true` — an unresolvable name retypes nothing. |

---

## Who read the graph

| Variable | Default | Notes |
|---|---|---|
| `TESSERAE_READ_AUDIT` | **off** | Records each MCP read as one row — `{tool, actor, node_ids, at, tesserae_version}` — in a `read_audit` table in `.tesserae/sqlite.db`, readable back through the `read_audit` tool with a per-actor tally. Off by default because an always-on audit across every read surface turns every read into a write; the gate sits ahead of opening the store, since creating the table is itself a write. Nothing about it ever reaches `graph.json` |
| `TESSERAE_ACTOR` | — | Who to attribute a read to when the call carries no agent view. The actor is the `agent` argument if the call resolved one, else this; unset records the read as anonymous rather than inventing a name |

Turning `TESSERAE_READ_AUDIT` off stops recording without erasing what was
already recorded, and it takes effect without restarting the server. What the
audit is *for* is [forgetting by disuse](agent-memory.md#forgetting--never-deletion):
access counts drive what gets absorbed or demoted, and without an actor one
chatty agent polling a node and a human reading it once are the same input.

---

## Running several machines against one project

The shape this is written for: several servers each run a coding agent, each has
its own local session transcripts, and they share a disk — so they see the same
project directory and the same `.tesserae/`.

**Give one host the compile, and let the rest only harvest.**

```bash
# on the compiling host
tesserae engine

# on every other host
tesserae engine --harvest-only
```

`--harvest-only` tails that machine's local transcripts into the shared session
store and never takes the project's compile lock. That removes the contention
rather than arbitrating it, which is why it beats tuning timeouts.

**When you do want to queue rather than fail**, pass `--wait`:

```bash
tesserae compile --wait          # up to 30 min, reporting every 5s
tesserae compile --wait 120      # or name your own bound
```

Without it, a compile that finds the lock held exits 2 — correct for a hook,
infuriating for a human. `--wait` is a flag rather than something inferred from
whether stdout is a terminal, because the same command must not change behaviour
under `tee`, in tmux capture, or in CI. `TESSERAE_COMPILE_LOCK_WAIT=<seconds>`
does the same thing for a whole process tree.

**Keeping every project fresh** from one invocation:

```bash
tesserae refresh --all               # every registered project, sequentially
tesserae refresh --all --jobs 3      # three at a time
tesserae compile --all --name alpha --name beta
```

One project failing does not stop the others. Exit `2` if any failed, `1` if any
was locked by another run, `0` if everything ran. `--jobs` defaults to 1 because
a compile is LLM-heavy and raising it spends quota in parallel.

### What makes this safe

Per-machine state used to be stored under one shared name and read by every
host. Each of these is now partitioned by host id:

| State | Where | Why it has to be per-host |
|---|---|---|
| Session records | `.tesserae/harness_sessions/` | A host prunes only what it harvested. Otherwise host B deletes host A's sessions and reports success — every host's scan stamps the same producer and their `~/.claude` paths resolve identically, so nothing else distinguishes them |
| Engine pidfile | `.tesserae/daemon.<host>.pid` | Liveness is `os.kill(pid, 0)` against the **local** process table; a pid written by another machine is judged against an unrelated local process |
| Codex scan floor | `.tesserae/harness_sessions.db` | One shared watermark meant whichever host ran last moved it past transcripts the other had not read — those were never imported at all |

The host id is generated once into `~/.tesserae/host_id` (per machine, **not** in
the shared project directory) and can be pinned with `TESSERAE_HOST_ID`. It is a
persisted id rather than the hostname because a fleet built from one image reuses
hostnames, and a collision would hand one machine's records to another.

### The assumption you should test

All of the above assumes `flock(2)` is **enforced** by the filesystem holding
`.tesserae/`. Over NFS and SMB that is configuration-dependent, and without a
working lock daemon `flock` can silently degrade to a no-op — at which point two
hosts compile the same project simultaneously, each believing it holds an
exclusive lock.

`tesserae doctor` warns when the project sits on a network filesystem, but a
single host **cannot** prove cross-host enforcement. Test it directly on the real
hardware: hold a lock on host A and confirm host B is refused.

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
| `SUPPORTED` | the edge exists, carries its own evidence, and that text was re-grounded against the source file. **Check `citation.evidence_span.is_edge_endpoint` before quoting it** |
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

Worth knowing when reading results: a `SUPPORTED` verdict is not automatically an
informative one. On a real 15,284-edge graph, 827 of 2,088 `SUPPORTED` verdicts
(39.6%) cite a span that is the deciding edge's *own endpoint* — reading the span
just re-reads the edge. Every one of them is marked
`citation.evidence_span.is_edge_endpoint: true`; the other 1,261 carry `false` and
cite a third node a document backs separately.

Read the flag the right way round: `true` means **uninformative, not false**. The
verdict is unchanged and still true — "C evidenced_by S" really is licensed by
reading S. To select the citations worth quoting to a human, filter on

```
verdict == "SUPPORTED" and not citation["evidence_span"]["is_edge_endpoint"]
```

and do **not** reinvent the test as `node_id == edge.target`: a span can be the
deciding edge's *source* (729 spans source 974 `part_of` / `discussed_in` edges in
that same graph). The key is present exactly when `evidence_span` is non-null.

## Routing a question

`tesserae ask` picks a retrieval path by question shape: single-entity lookups go
to the cheap backend, multi-hop / "what changed" / "why" / corpus-wide questions
go to the graph. Independent benchmarks put graphs ahead on multi-hop, temporal
and synthesis questions, and *behind* on simple fact lookup and cost — so paying
graph prices for every question is a loss.

The decision appears in the returned envelope, so a cheap answer is auditable.
Override it with `--route` on the CLI, or the `route` parameter on the MCP tool.
